"""``GET /v1/cycle/status``, ``GET /v1/cycle/compute`` and ``POST /v1/cycle/compute``.

"Compute live" (SPEC.md 7.2, 11.11, task P6.11): the console's button runs one real cycle -
Sky, Twin, Flash, products, Pulse - through ``varuna_cycle.twin_cycle.run_cycle``, the same
function ``make bake`` calls, stamped ``live``. The budget bar fills stage by stage from the
``cycle.stage`` events this module publishes on the bus, which the WebSocket relays.

**It is off unless the host says otherwise** (``VARUNA_COMPUTE_LIVE=1``). A cycle measures 64.8 to
147.2 s across the seven baked demo cycles against section 14's 15 s, and holds most of a small
host's memory while it runs; the deployed API is one CPU serving every screen, and a visitor who
presses the button there would stall all of them for a minute or two. The demo laptop turns it on.
``GET /v1/cycle/compute`` says which, and how long a cycle has measured here, so the button can say
both before anyone presses it.

**How the stages are seen.** ``run_cycle`` records its stage timings in ``run.json`` but reports
nothing while it runs. Rather than editing the orchestrator for a progress hook, the functions that
open each stage are wrapped once, in this process, and the wrappers publish only when they are
running on the compute thread - a bake or a physics check in the same process is untouched. The
live timings are the wrappers' own wall clocks; when the run is published its ``stage_ms`` from
``run.json`` replaces them, so the bar ends on the numbers of record. The orchestrator has no
decode stage, so that segment of the bar stays empty and says so.

**One heavy cycle at a time.** The live rain cycle behind ``/v1/nowcast/rain?compute=true`` and this
one share a lock: either running refuses the other with 503 ``cycle_busy`` (3adbdd8).
"""

from __future__ import annotations

import os
import statistics
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from varuna_schemas.constants import IST, STAGE_BUDGET_MS, TOTAL_CYCLE_BUDGET_MS
from varuna_schemas.models import ComputeRequest, CycleStatus, ErrorEnvelope, stage_total_ms

from varuna_api.rain import _LIVE_CYCLE
from varuna_api.state import AppState, api_error, get_state

log = structlog.get_logger("varuna.api.cycle")

router = APIRouter(prefix="/v1/cycle", tags=["cycle"])

COMPUTE_ENV = "VARUNA_COMPUTE_LIVE"
"""Set to 1 on a host that may run a whole cycle on request (the demo laptop)."""

STAGE_TOPIC = "cycle.stage"
RUNS_TOPIC = "runs.published"

RECENT_RUNS = 12
"""Runs the expected-time estimate reads: enough for a median, few enough to be this host's."""


def compute_enabled() -> bool:
    return os.environ.get(COMPUTE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class LiveJob:
    """The live cycle in progress, or the last one, readable from any request."""

    bundle: str
    city: str
    cycle_ts: datetime | None
    started_at: datetime
    stage: str = "sky"
    stage_ms: dict[str, int] = field(default_factory=dict)
    run_id: str | None = None
    finished_at: datetime | None = None
    error: str | None = None
    thread_id: int | None = None

    @property
    def running(self) -> bool:
        return self.finished_at is None


_JOB: LiveJob | None = None
_JOB_GUARD = threading.Lock()
_WRAPPED = False


def current_job() -> LiveJob | None:
    return _JOB


def _publish(state: AppState, topic: str, payload: dict[str, Any], run_id: str | None) -> None:
    try:
        state.bus.publish_threadsafe(topic, payload, run_id=run_id)
    except RuntimeError:
        # No loop bound (a test client without a lifespan): the status endpoint still reports.
        log.debug("cycle.stage_not_published", topic=topic)


def _stage_event(
    state: AppState, job: LiveJob, stage: str, status: str, ms: int | None = None
) -> None:
    job.stage = stage
    if ms is not None:
        job.stage_ms[stage] = ms
    _publish(
        state,
        STAGE_TOPIC,
        {
            "stage": stage,
            "status": status,
            "ms": ms,
            "budget_ms": STAGE_BUDGET_MS.get(stage),
            "cycle_ts": job.cycle_ts.isoformat() if job.cycle_ts else None,
            "run_id": job.run_id,
        },
        job.run_id,
    )


_ACTIVE: dict[str, Any] = {}
"""The job and state the wrappers publish for, set only while a compute thread runs."""


def _on_compute_thread() -> bool:
    job: LiveJob | None = _ACTIVE.get("job")
    return job is not None and job.thread_id == threading.get_ident()


def _wrap(owner: Any, name: str, stage: str, *, closes: str | None = None) -> None:
    """Wrap ``owner.name`` so that, on the compute thread, it reports ``stage`` starting and
    finishing. ``closes`` names a stage whose end is this call's start (products ends where Pulse
    begins; nothing else marks it)."""
    original: Callable[..., Any] = getattr(owner, name)
    if getattr(original, "__varuna_stage__", None):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not _on_compute_thread():
            return original(*args, **kwargs)
        state: AppState = _ACTIVE["state"]
        job: LiveJob = _ACTIVE["job"]
        if closes and closes in _ACTIVE.get("open", {}):
            opened = _ACTIVE["open"].pop(closes)
            _stage_event(state, job, closes, "finished", round((perf_counter() - opened) * 1000))
        _stage_event(state, job, stage, "started")
        mark = perf_counter()
        result = original(*args, **kwargs)
        _stage_event(state, job, stage, "finished", round((perf_counter() - mark) * 1000))
        if stage == "flash":
            # Products starts where Flash ends; Pulse's wrapper closes it.
            _ACTIVE.setdefault("open", {})["products"] = perf_counter()
            _stage_event(state, job, "products", "started")
        return result

    wrapped.__varuna_stage__ = stage  # type: ignore[attr-defined]
    setattr(owner, name, wrapped)


def _install_stage_wrappers() -> None:
    """Once per process: wrap the functions that open each stage of ``run_cycle``."""
    global _WRAPPED
    if _WRAPPED:
        return
    import varuna_pulse.cycle as pulse_cycle
    import varuna_twin.runner as twin_runner
    from varuna_cycle import twin_cycle

    _wrap(twin_cycle, "_sky_rain_on_city", "sky")
    # `run_cycle` imports `run_twin` from the runner module at call time, so wrapping the module
    # attribute is what it picks up.
    _wrap(twin_runner, "run_twin", "twin")
    _wrap(twin_cycle, "_flash_members", "flash")
    _wrap(pulse_cycle, "run_pulse", "pulse", closes="products")
    _WRAPPED = True


def expected_cycle_ms(state: AppState) -> dict[str, Any]:
    """What a cycle has measured on this host: the recent runs' own stage totals."""
    runs = state.registry.list(city=state.settings.varuna_city, limit=RECENT_RUNS)
    totals = [stage_total_ms(dict(meta.stage_ms)) for meta in runs if meta.stage_ms]
    totals = [t for t in totals if t > 0]
    if not totals:
        return {"median_ms": None, "min_ms": None, "max_ms": None, "n_runs": 0}
    return {
        "median_ms": int(statistics.median(totals)),
        "min_ms": min(totals),
        "max_ms": max(totals),
        "n_runs": len(totals),
    }


def _status_from_job(job: LiveJob) -> CycleStatus:
    return CycleStatus(
        run_id=job.run_id,
        stage=job.stage if job.running else "idle",  # type: ignore[arg-type]
        stage_ms=dict(job.stage_ms),
        started_at=job.started_at,
        finished_at=job.finished_at,
        cycle_ts=job.cycle_ts,
        mode="live",
        replay_mode="live",
        bundle=job.bundle,
        budget_ms=dict(STAGE_BUDGET_MS),
        total_budget_ms=TOTAL_CYCLE_BUDGET_MS,
        error=job.error,
    )


@router.get("/status", response_model=CycleStatus, summary="Orchestrator status and budgets")
def cycle_status(state: Annotated[AppState, Depends(get_state)]) -> CycleStatus:
    """The live cycle while one runs (its stage and the stages finished so far), or the last one
    this process ran, with its timings of record; otherwise the newest run's. The newest run is
    by cycle time, so a live 06:00 cycle computed after the 09:10 bake would otherwise vanish
    from here the moment it published."""
    job = current_job()
    if job is not None and (job.running or job.error or job.run_id):
        return _status_from_job(job)
    last = state.latest_run()
    return CycleStatus(
        run_id=last.run_id if last else None,
        stage="idle",
        stage_ms=dict(last.stage_ms) if last else {},
        cycle_ts=last.cycle_ts if last else None,
        mode=last.mode if last else None,
        replay_mode=last.replay_mode if last else None,
        bundle=last.bundle if last else state.settings.varuna_bundle,
        budget_ms=dict(STAGE_BUDGET_MS),
        total_budget_ms=TOTAL_CYCLE_BUDGET_MS,
        degraded_feeds=list(last.degraded_feeds) if last else [],
    )


@router.get("/compute", summary="Whether Compute live is on here, and how long a cycle takes")
def compute_info(state: Annotated[AppState, Depends(get_state)]) -> JSONResponse:
    enabled = compute_enabled()
    job = current_job()
    return JSONResponse(
        {
            "enabled": enabled,
            "reason": None
            if enabled
            else (
                f"Compute live is off on this server ({COMPUTE_ENV} is not set): a cycle holds "
                "most of its memory for a minute or more and it serves every screen. Run it on "
                "the demo laptop, or read the baked runs."
            ),
            "busy": bool(job and job.running) or _LIVE_CYCLE.locked(),
            "budget_ms": TOTAL_CYCLE_BUDGET_MS,
            "expected": expected_cycle_ms(state),
        }
    )


def _run_job(state: AppState, job: LiveJob) -> None:
    from varuna_cycle.twin_cycle import run_cycle

    job.thread_id = threading.get_ident()
    _ACTIVE.update({"job": job, "state": state, "open": {}})
    started = perf_counter()
    try:
        result = run_cycle(job.bundle, job.cycle_ts, city=job.city, mode="live", overwrite=True)
        job.run_id = result.run_id
        # The instant asked for is snapped onto the cycle ladder; report the one that ran.
        meta = state.registry.get(result.run_id)
        if meta is not None:
            job.cycle_ts = meta.cycle_ts
        # The numbers of record replace the wrappers' own clocks.
        job.stage_ms = {k: int(v) for k, v in result.stage_ms.items()}
        _publish(
            state,
            RUNS_TOPIC,
            {"run_id": result.run_id, "mode": "live", "stage_ms": job.stage_ms},
            result.run_id,
        )
        log.info(
            "cycle.live_published",
            run_id=result.run_id,
            total_ms=stage_total_ms(job.stage_ms),
            wall_ms=round((perf_counter() - started) * 1000),
        )
    except Exception as error:  # every failure becomes the job's own message
        job.error = f"The live cycle failed at {job.stage}: {error}"
        _stage_event(state, job, job.stage, "failed")
        log.warning("cycle.live_failed", stage=job.stage, error=str(error))
    finally:
        job.finished_at = datetime.now(IST)
        _ACTIVE.clear()
        _LIVE_CYCLE.release()


@router.post(
    "/compute",
    response_model=CycleStatus,
    status_code=202,
    responses={
        403: {"model": ErrorEnvelope, "description": "Compute live is off on this server"},
        503: {"model": ErrorEnvelope, "description": "A live cycle is already running"},
    },
    summary="Compute live: run one real cycle now",
)
def compute(body: ComputeRequest, state: Annotated[AppState, Depends(get_state)]) -> CycleStatus:
    global _JOB
    if not compute_enabled():
        raise api_error(
            403,
            "compute_disabled",
            f"Compute live is off on this server. Set {COMPUTE_ENV}=1 where a cycle may hold "
            "the host for a minute or more; the baked runs are unaffected.",
        )
    with _JOB_GUARD:
        if (_JOB is not None and _JOB.running) or not _LIVE_CYCLE.acquire(blocking=False):
            raise api_error(
                503,
                "cycle_busy",
                "A live cycle is already running on this server. Its stages are on the budget "
                "bar; press again when it has published.",
            )
        clock = state.replay.clock
        bundle = body.bundle or (clock.bundle_id if clock else state.settings.varuna_bundle)
        when = body.cycle_ts
        if when is None and clock is not None and clock.bundle_id == bundle:
            when = clock.snapshot().sim_time
        _install_stage_wrappers()
        job = LiveJob(
            bundle=bundle,
            city=state.settings.varuna_city,
            cycle_ts=when,
            started_at=datetime.now(IST),
        )
        _JOB = job
        threading.Thread(
            target=_run_job, args=(state, job), name="compute-live", daemon=True
        ).start()
    log.info("cycle.live_started", bundle=bundle, cycle_ts=when.isoformat() if when else None)
    return _status_from_job(job)


__all__ = ["compute_enabled", "current_job", "router"]
