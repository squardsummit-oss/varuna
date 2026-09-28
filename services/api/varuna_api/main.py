"""FastAPI application factory for the VARUNA API (SPEC.md 12; task P0.4).

``create_app()`` builds an app around an :class:`~varuna_api.state.AppState`; the module-level
``app`` is what ``uvicorn varuna_api.main:app`` serves. Every error response uses the
``{"error": {"code", "message", "run_id"}}`` envelope, including FastAPI's own 404/422.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from varuna_cycle.bus import Bus
from varuna_cycle.registry import RunRegistry
from varuna_schemas.models import ErrorEnvelope
from varuna_schemas.settings import Settings

from varuna_api import __version__
from varuna_api.routers import (
    basemap,
    city,
    cycle,
    depth,
    health,
    live,
    nowcast,
    onboard,
    ops,
    outlook,
    replay,
    reports,
    route,
    runs,
    stubs,
    terrain,
    verify,
    weather,
    whatif,
)
from varuna_api.seed import seed_demo_runs
from varuna_api.state import AppState

_LOGGING_CONFIGURED = False

DESCRIPTION = (
    "Street-level urban flood nowcasting digital twin (SIH 2026, PS SIH26085, MoES). "
    "Every response carries `run_id` and `valid_ts` where a run is involved; errors use "
    "`{ error: { code, message, run_id } }`. Endpoints answering 501 are contract-only "
    "until their engine's phase lands."
)

TAGS = [
    {"name": "health", "description": "Liveness, mode and the last published run."},
    {"name": "runs", "description": "Run registry and provenance (data/runs/<run_id>/run.json)."},
    {"name": "cycle", "description": "Cycle orchestrator status and Compute live."},
    {"name": "nowcast", "description": "Street depth products: segments, rasters, hotspots."},
    {"name": "drains", "description": "Drain-health product (Pulse) and the desilting CSV."},
    {"name": "observations", "description": "Assimilated observations and citizen reports."},
    {"name": "route", "description": "Flood-safe routing, reachability and provider feeds."},
    {"name": "alerts", "description": "Alert feed, CAP 1.2 documents and state changes."},
    {"name": "pumps", "description": "Pump inventory, optimisation and dispatch."},
    {"name": "whatif", "description": "What-if via the emulator and the Twin physics check."},
    {"name": "replay", "description": "Replay bundles and the shared simulation clock."},
    {"name": "onboard", "description": "City-in-a-box onboarding jobs."},
    {"name": "verification", "description": "Verification scores per event."},
    {"name": "city", "description": "Static city layers simplified for the map."},
    {"name": "basemap", "description": "Offline PMTiles basemap built from OSM and WorldCover."},
    {"name": "weather", "description": "Live conditions proxied from Open-Meteo (CC BY 4.0)."},
    {
        "name": "ops",
        "description": "Authority edits: closures, pump status and the append-only log.",
    },
]


def configure_logging(level: int = logging.INFO) -> None:
    """structlog JSON to stdout, once per process."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _LOGGING_CONFIGURED = True


def _envelope(status: int, code: str, message: str, run_id: str | None = None) -> JSONResponse:
    body = ErrorEnvelope.make(code, message, run_id).model_dump(mode="json")
    return JSONResponse(status_code=status, content=body)


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorised",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    501: "not_implemented",
    503: "unavailable",
}


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    detail: Any = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return _envelope(
            exc.status_code, str(detail["code"]), str(detail["message"]), detail.get("run_id")
        )
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    if exc.status_code == 404 and not isinstance(detail, dict):
        message = f"No endpoint at {request.url.path}. See /docs for the API contract."
    else:
        message = str(detail) if detail else f"HTTP {exc.status_code}"
    return _envelope(exc.status_code, code, message)


async def _validation_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', 'invalid')}"
        for err in exc.errors()[:5]
    )
    return _envelope(422, "validation_error", f"Request is invalid: {problems}. Fix and retry.")


async def _unhandled_handler(request: Request, exc: Exception) -> Response:
    structlog.get_logger("varuna.api").error(
        "request.failed", path=request.url.path, error=repr(exc), exc_info=exc
    )
    return _envelope(
        500,
        "internal_error",
        f"{type(exc).__name__} while handling {request.url.path}. Check the API log "
        "and retry; if it persists, restart with `make demo`.",
    )


def create_app(
    settings: Settings | None = None,
    registry: RunRegistry | None = None,
    bus: Bus | None = None,
    state: AppState | None = None,
) -> FastAPI:
    """Build the API. Pass a registry/bus for tests; defaults read settings from ``.env``."""
    configure_logging()
    if state is None:
        kwargs: dict[str, Any] = {}
        if settings is not None:
            kwargs["settings"] = settings
        if registry is not None:
            kwargs["registry"] = registry
        if bus is not None:
            kwargs["bus"] = bus
        state = AppState(**kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.bus.bind()
        # A deployment seeds its volume from `demo/runs` on boot. A test suite must not: it
        # would seed whatever `VARUNA_DATA_DIR` points at, which is the developer's own
        # `data/runs` unless the test moved it first (`services/api/tests/conftest.py`).
        seeded = seed_demo_runs() if os.environ.get("VARUNA_SEED_DEMO_RUNS", "1") != "0" else 0
        structlog.get_logger("varuna.api").info(
            "api.started",
            version=state.version,
            mode=state.settings.varuna_mode,
            city=state.settings.varuna_city,
            bundle=state.settings.varuna_bundle,
            runs_dir=str(state.registry.runs_dir),
            offline=state.settings.varuna_offline,
            seeded_runs=seeded,
        )
        yield
        await state.replay.aclose()
        structlog.get_logger("varuna.api").info("api.stopped")

    app = FastAPI(
        title="VARUNA API",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        contact={"name": "Team VIT, SIH 2026 PS SIH26085"},
    )
    app.state.varuna = state

    # Added before CORS so CORS wraps it: a browser can only read a 413 that carries CORS headers.
    # It refuses a report body over 1 MB before FastAPI parses a byte of it.
    app.add_middleware(reports.ReportBodyLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(state.settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Run-Id", "X-Response-Ms", "X-Layer", "ETag"],
    )
    # The static city layers are megabytes of GeoJSON (50k inferred drain edges); they
    # compress about five to one, and the console loads them once per city. Level 6, not
    # Starlette's default 9: on the 3.51 MB segments body level 9 took 103.6 ms for 0.36 MB and
    # level 6 took 46.3 ms for 0.38 MB (median of five, P10.4) - 57 ms of a 200 ms budget
    # bought back for 5 % more bytes on a link that is local on stage.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    request_log = structlog.get_logger("varuna.api.request")

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - started) * 1000
        response.headers["X-Response-Ms"] = f"{ms:.1f}"
        request_log.info(
            "request",
            method=request.method,
            path=request.url.path,
            query=request.url.query or None,
            status=response.status_code,
            ms=round(ms, 1),
        )
        return response

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)

    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(cycle.router)
    app.include_router(live.router)
    app.include_router(city.router)
    app.include_router(terrain.router)
    app.include_router(basemap.router)
    app.include_router(replay.router)
    # Before the stubs: the rain routes are real, and the stub router owns the rest of the
    # /v1/nowcast namespace until Phase 5 fills it in.
    app.include_router(depth.router)
    app.include_router(whatif.router)
    app.include_router(reports.router)
    app.include_router(route.router)
    app.include_router(onboard.router)
    app.include_router(verify.router)
    app.include_router(weather.router)
    app.include_router(outlook.router)
    app.include_router(nowcast.router)
    # Before the stubs: ops serves the alert and pump actions that were 501 there (task D-07).
    app.include_router(ops.router)
    app.include_router(stubs.router)
    return app


app = create_app()

__all__ = ["DESCRIPTION", "__version__", "app", "configure_logging", "create_app"]
