from __future__ import annotations

from fastapi.testclient import TestClient
from varuna_cycle.registry import RunRegistry
from varuna_schemas.models import RunMeta


def test_healthz_shape_without_runs(client: TestClient) -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    for key in ("status", "mode", "bundle", "city", "last_run", "ts", "version", "offline"):
        assert key in body
    assert body["status"] == "starting"
    assert body["mode"] == "replay"
    assert body["bundle"] == "MUM-2019-07-02"
    assert body["city"] == "mumbai"
    assert body["last_run"] is None
    assert body["last_run_id"] is None
    assert body["offline"] is True
    assert body["version"] == "0.1.0"
    assert body["ts"].endswith("+05:30")
    assert body["uptime_s"] >= 0
    assert "X-Response-Ms" in res.headers


def test_healthz_reports_last_run(client: TestClient, baked_run: RunMeta) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["last_run_id"] == baked_run.run_id
    assert body["last_run"]["run_id"] == baked_run.run_id
    assert body["last_run"]["total_ms"] == baked_run.total_ms
    assert body["last_run_mode"] == "baked"


def test_runs_empty(client: TestClient) -> None:
    body = client.get("/v1/runs").json()
    assert body == {"runs": [], "count": 0, "latest_run_id": None}


def test_runs_lists_and_filters(client: TestClient, baked_run: RunMeta) -> None:
    body = client.get("/v1/runs").json()
    assert body["count"] == 1
    assert body["latest_run_id"] == baked_run.run_id
    assert body["runs"][0]["cycle_ts"].endswith("+05:30")
    assert client.get("/v1/runs", params={"city": "chennai"}).json()["count"] == 0
    assert client.get("/v1/runs", params={"bundle": baked_run.bundle}).json()["count"] == 1
    assert client.get("/v1/runs", params={"limit": 0}).status_code == 422


def test_run_detail_and_404_envelope(client: TestClient, baked_run: RunMeta) -> None:
    ok = client.get(f"/v1/runs/{baked_run.run_id}")
    assert ok.status_code == 200
    assert ok.json()["stage_ms"] == baked_run.stage_ms
    assert ok.json()["total_ms"] == baked_run.total_ms

    missing = "MUM-20190702T1300Z-sky1.0-twin1.0-flash0.3-baked"
    res = client.get(f"/v1/runs/{missing}")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "run_not_found"
    assert err["run_id"] == missing
    assert "make bake" in err["message"]


def test_unknown_path_uses_envelope(client: TestClient) -> None:
    res = client.get("/v1/nope")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "not_found"
    assert "/docs" in err["message"]
    assert err["run_id"] is None


def test_validation_error_uses_envelope(client: TestClient) -> None:
    # `/v1/onboard` still validates through a Pydantic model; `/v1/route` parses its own body
    # so that a bad coordinate gets a message about coordinates.
    res = client.post("/v1/onboard", json={"city": 7})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "validation_error"
    assert "Fix and retry" in err["message"]


def test_cors_header_for_allowed_origin(client: TestClient) -> None:
    res = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    preflight = client.options(
        "/v1/runs",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert "GET" in preflight.headers.get("access-control-allow-methods", "")
    other = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_cycle_status_idle_with_budgets(client: TestClient) -> None:
    body = client.get("/v1/cycle/status").json()
    assert body["stage"] == "idle"
    assert body["busy"] is False
    assert body["budget_ms"]["sky"] == 5000
    assert body["budget_ms"]["twin"] == 8000
    assert body["budget_ms"]["publish"] == 200
    assert body["total_budget_ms"] == 15000
    assert body["bundle"] == "MUM-2019-07-02"


def test_cycle_status_reflects_last_run(client: TestClient, baked_run: RunMeta) -> None:
    body = client.get("/v1/cycle/status").json()
    assert body["run_id"] == baked_run.run_id
    assert body["stage_ms"] == baked_run.stage_ms
    assert body["elapsed_ms"] == baked_run.total_ms


def test_served_totals_count_each_stage_once(
    client: TestClient, registry: RunRegistry, run_meta: RunMeta
) -> None:
    """The 09:10 IST baked cycle's stage_ms: the Twin's sub-timings must not be summed again."""
    stage_ms = {
        "sky": 5978,
        "twin": 58462,
        "twin_hydrology_ms": 612,
        "twin_surface_ms": 18502,
        "twin_drain_ms": 30215,
        "twin_coupling_ms": 5242,
        "twin_total_ms": 58282,
        "flash": 226,
        "products": 6619,
        "pulse": 5566,
    }
    top_level = stage_ms["sky"] + stage_ms["twin"] + stage_ms["flash"]
    top_level += stage_ms["products"] + stage_ms["pulse"]
    registry.write_meta(run_meta.model_copy(update={"stage_ms": stage_ms}))

    status = client.get("/v1/cycle/status").json()
    assert status["elapsed_ms"] == top_level == 76_851
    assert status["stage_ms"] == stage_ms
    assert client.get("/v1/runs").json()["runs"][0]["total_ms"] == 76_851
    assert client.get(f"/v1/runs/{run_meta.run_id}").json()["total_ms"] == 76_851
    assert client.get("/healthz").json()["last_run"]["total_ms"] == 76_851
