from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from varuna_api.cli import write_openapi

SECTION_12_PATHS = [
    "/healthz",
    "/v1/runs",
    "/v1/runs/{run_id}",
    "/v1/nowcast/segments",
    "/v1/nowcast/raster",
    "/v1/nowcast/hotspots",
    "/v1/nowcast/segments/{segment_id}/series",
    "/v1/drains/health",
    "/v1/drains/health.csv",
    "/v1/observations",
    "/v1/reports",
    "/v1/route",
    "/v1/reachability",
    "/v1/feeds/road-conditions",
    "/v1/alerts",
    "/v1/alerts/{alert_id}.cap",
    "/v1/alerts/{alert_id}/ack",
    "/v1/alerts/{alert_id}/escalate",
    "/v1/pumps",
    "/v1/pumps/optimise",
    "/v1/pumps/dispatch",
    "/v1/whatif",
    "/v1/whatif/physics-check",
    "/v1/replay/bundles",
    "/v1/replay/play",
    "/v1/replay/pause",
    "/v1/replay/seek",
    "/v1/replay/speed",
    "/v1/replay/clock",
    "/v1/replay/bundle",
    "/v1/cycle/compute",
    "/v1/cycle/status",
    "/v1/onboard",
    "/v1/onboard/{job_id}",
    "/v1/verification",
    "/v1/city/{city}/layers/{name}",
]

# Endpoints that are still 501. Phase 5 implemented the depth products, Phase 8 the route,
# reachability and the road-conditions feed, Phase 9 verification, report ingestion and city
# onboarding, task D-07 the four authority actions (alert ack and escalate, pump optimise and
# dispatch, now served by `varuna_api.routers.ops` behind the passphrase gate), and P6.11 the
# live cycle - so each left this list as it landed. The *paths* stay in SECTION_12_PATHS above,
# which is what asserts the contract in SPEC.md 12 is complete either way.
#
# `POST /v1/cycle/compute` left on 2026-09-22: it runs `run_cycle(mode="live")` on a thread and
# streams `cycle.stage` over the WebSocket. It is gated by `VARUNA_COMPUTE_LIVE`, and where that
# gate is closed it refuses with its own reason rather than the stub's.
#
# `POST /v1/whatif/physics-check` left on 2026-09-24 (task P7.8): it runs the coupled Twin twice
# on a 33 x 33 cell window around the run's hotspots and reports the emulator's delta against the
# physics'. The test that used to guard the *refusal's* quoted Twin cost moved to
# `test_physics_check.py`, where it guards the served endpoint's own measured cost instead.
STUB_CALLS: list[tuple[str, str, dict[str, object] | None, dict[str, str] | None]] = [
    ("GET", "/v1/nowcast/segments/88213/series", None, None),
]

OPS_PATHS = [
    "/v1/ops/closures",
    "/v1/ops/pumps/{pump_id}/status",
    "/v1/ops/log",
    "/v1/ops/alerts",
]
"""The authority write path TECH_SPEC 3.6 adds to section 12's table (task D-07)."""


@pytest.mark.parametrize(("method", "path", "body", "params"), STUB_CALLS)
def test_stub_returns_501_envelope(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, object] | None,
    params: dict[str, str] | None,
) -> None:
    res = client.request(method, path, json=body, params=params)
    assert res.status_code == 501, res.text
    err = res.json()["error"]
    assert err["code"] == "not_implemented"
    assert "lands in Phase" in err["message"]
    assert err["run_id"] is None


def test_the_authority_endpoints_are_published_and_gated(client: TestClient) -> None:
    """The desk's endpoints are in the contract, and every write refuses an unauthorised caller.

    The refusal here is the one a clean checkout gets: no `VARUNA_OPS_PASSPHRASE` in the
    environment, so the API is read-only and says which variable would change that. That is the
    deployed API's state on purpose (TECH_SPEC 3.6).
    """
    doc = client.get("/openapi.json").json()
    missing = [p for p in OPS_PATHS if p not in doc["paths"]]
    assert not missing, f"missing from OpenAPI: {missing}"

    for method, path, body in (
        ("POST", "/v1/ops/closures", {"segment_id": "S1-000", "reason": "Water"}),
        ("POST", "/v1/ops/pumps/P-12/status", {"status": "unavailable"}),
        ("POST", "/v1/alerts/ALT-1/ack", {"user": "ward officer"}),
        ("POST", "/v1/alerts/ALT-1/escalate", {"user": "ward officer"}),
        ("POST", "/v1/pumps/optimise", {}),
        ("POST", "/v1/pumps/dispatch", {"user": "control room"}),
    ):
        res = client.request(method, path, json=body)
        assert res.status_code in {401, 503}, f"{path} answered {res.status_code}: {res.text}"
        error = res.json()["error"]
        assert "VARUNA_OPS_PASSPHRASE" in error["message"] or "X-Varuna-Ops" in error["message"]


def test_openapi_contains_every_section_12_path(client: TestClient) -> None:
    doc = client.get("/openapi.json").json()
    assert doc["info"]["title"] == "VARUNA API"
    assert doc["info"]["version"] == "0.1.0"
    assert doc["openapi"].startswith("3.1")
    missing = [p for p in SECTION_12_PATHS if p not in doc["paths"]]
    assert not missing, f"missing from OpenAPI: {missing}"
    schemas = doc["components"]["schemas"]
    # `RouteResponse` was in this list while `/v1/route` was a stub declaring it. The served
    # route (task P8.2) returns its own flatter shape - two comparable routes side by side, which
    # is what the screen renders - so the draft model is no longer what the endpoint publishes.
    # `varuna_schemas.models.route` keeps it as the pilot contract; see ADR-0027.
    for name in ("ErrorEnvelope", "RunMeta", "RunList", "CycleStatus"):
        assert name in schemas
    # `/v1/onboard` published a 501 while it was a stub; it now starts a real build and answers
    # 202 with the job (task P9.5), so 202 is what the contract publishes.
    assert "202" in doc["paths"]["/v1/onboard"]["post"]["responses"]
    assert "404" in doc["paths"]["/v1/runs/{run_id}"]["get"]["responses"]
    assert (
        doc["paths"]["/v1/nowcast/raster"]["get"]["responses"]["200"]["content"].get("image/png")
        is not None
    )


def test_write_openapi_is_deterministic(tmp_path: Path) -> None:
    first = write_openapi(tmp_path / "a" / "openapi.json").read_text(encoding="utf-8")
    second = write_openapi(tmp_path / "b" / "openapi.json").read_text(encoding="utf-8")
    assert first == second
    assert first.endswith("\n")
    doc = json.loads(first)
    assert list(doc.keys()) == sorted(doc.keys())
    assert "/v1/runs" in doc["paths"]
