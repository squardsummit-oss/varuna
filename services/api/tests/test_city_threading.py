"""``?city=`` decides whose newest run answers, on every depth route (task D-09).

Runs from every city share ``data/runs/`` and their ids sort chronologically, so ``CHN-`` sorts
after ``MUM-`` for the same instant. Before this, "the newest run" was whatever sorted last: a
Mumbai console asking for depth after Chennai was onboarded got Chennai's water, with nothing on
screen to say so. Each test below asks one route without a ``run_id`` and pins which city it
answers with.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

MUM = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
CHN = "CHN-20260701T0120Z-sky1.0-twin1.0-flash0.0-baked"
"""Chennai's id sorts after Mumbai's, which is the whole reason this matters."""


def _seed(data: Path, run_id: str) -> Path:
    """One run directory carrying every product the depth routes read, stamped with its run id."""
    run = data / "runs" / run_id
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text(
        json.dumps({"wgs84": [72.8, 19.0, 72.9, 19.1]}), encoding="utf-8"
    )
    (run / "depth" / "p50_00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "run.json").write_text(json.dumps({"run_id": run_id, "notes": []}), encoding="utf-8")
    (run / "segments_wet.json").write_text(
        json.dumps({"run_id": run_id, "valid_ts": [], "depth_cm": {}}), encoding="utf-8"
    )
    (run / "hotspots.json").write_text(json.dumps([]), encoding="utf-8")
    (run / "node_surcharge.json").write_text(
        json.dumps({"run_id": run_id, "nodes": [], "notes": []}), encoding="utf-8"
    )
    (run / "alerts.json").write_text(json.dumps({"alerts": []}), encoding="utf-8")
    (run / "pump_plan.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (run / "drain_health.geojson").write_text(
        json.dumps({"run_id": run_id, "features": [], "n_edges": 0}), encoding="utf-8"
    )
    (run / "desilting.csv").write_text(f"run_id\n{run_id}\n", encoding="utf-8")
    (run / "observations.json").write_text(
        json.dumps({"run_id": run_id, "observations": []}), encoding="utf-8"
    )
    return run


@pytest.fixture
def two_cities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)
    _seed(tmp_path, CHN)
    return tmp_path


# Every depth route that reads a product carrying its own run id back out.
ROUTES = [
    "/v1/nowcast/raster/bounds",
    "/v1/nowcast/segments",
    "/v1/nowcast/hotspots",
    "/v1/nowcast/surcharge",
    "/v1/alerts",
    "/v1/pumps",
    "/v1/drains/health",
    "/v1/observations",
]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(("city", "expected"), [("mumbai", MUM), ("chennai", CHN)])
def test_city_picks_whose_newest_run_answers(
    client: TestClient, two_cities: Path, path: str, city: str, expected: str
) -> None:
    response = client.get(path, params={"city": city})
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == expected


def test_a_named_run_ignores_the_city(client: TestClient, two_cities: Path) -> None:
    """A run id names its own city; the query string cannot overrule the directory."""
    response = client.get("/v1/nowcast/raster/bounds", params={"run_id": CHN, "city": "mumbai"})
    assert response.status_code == 200
    assert response.json()["run_id"] == CHN


def test_without_a_city_the_settings_city_still_answers(
    client: TestClient, two_cities: Path
) -> None:
    """Mumbai screens pass no city, and must keep getting Mumbai after Chennai is onboarded."""
    assert client.get("/v1/nowcast/raster/bounds").json()["run_id"] == MUM


def test_the_csv_and_the_png_follow_the_city_too(client: TestClient, two_cities: Path) -> None:
    csv = client.get("/v1/drains/health.csv", params={"city": "chennai"})
    assert csv.status_code == 200
    assert CHN in csv.text
    png = client.get("/v1/nowcast/raster", params={"city": "chennai", "step": 0})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"


def test_a_city_with_no_run_says_so_rather_than_serving_another_citys(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)
    response = client.get("/v1/nowcast/segments", params={"city": "chennai"})
    assert response.status_code == 404
    assert "make bake" in response.json()["error"]["message"]


def test_cities_lists_every_config_and_whether_it_is_built(client: TestClient) -> None:
    body = client.get("/v1/cities").json()
    rows = {row["id"]: row for row in body["cities"]}
    assert {"mumbai", "chennai"} <= set(rows)
    assert rows["mumbai"]["name"] == "Mumbai"
    assert rows["chennai"]["code"] == "CHN"
    # `built` is a file test, so it is whatever this clone has; the field must exist and be a bool.
    assert isinstance(rows["chennai"]["built"], bool)
    assert body["default"] == "mumbai"
