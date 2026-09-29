"""``GET /v1/pumps/map``: the pump plan on the city, for Jalayantra.

The endpoint recomputes, per assignment, the depth series with and without the pump using the
optimiser's own functions. The test that matters is that the recount reproduces the minutes the
plan wrote - the gauge and the timeline on the screen are drawn from these series, so a series
that disagreed with the plan's number beside it would be a screen contradicting itself. The
second is that a disagreement is flagged and served, never fitted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from varuna_api import pump_map
from varuna_products.pumps import build_pump_plan, write_pump_plan
from varuna_schemas.constants import IST

RUN_ID = "MUM-20190702T1300Z-sky1.0-twin1.0-flash0.1-baked"
T0 = datetime(2019, 7, 2, 18, 30, tzinfo=IST)


def _series(dry: int, wet: int, tail: int, depth: float = 52.0) -> list[float]:
    return [10.0] * dry + [depth] * wet + [10.0] * tail


HOTSPOTS = [
    {
        "hotspot_id": "MUM-HS-01",
        "name": "Hindmata junction",
        "lon": 72.841,
        "lat": 19.012,
        "segment_ids": ["S-NOT-IN-THE-EMULATOR"],
        "exposure": {"weight": 0.9},
        "depth_cm": _series(6, 24, 6),
    },
    {
        "hotspot_id": "MUM-HS-02",
        "name": "King's Circle",
        "lon": 72.857,
        "lat": 19.027,
        "segment_ids": ["S-NOT-IN-THE-EMULATOR-EITHER"],
        "exposure": {"weight": 0.6},
        "depth_cm": _series(10, 20, 6, 58.0),
    },
]

FLEET = [
    ("P-01", 2400.0, "Parel depot", 72.838, 19.005),
    ("P-02", 2400.0, "Dadar depot", 72.848, 19.020),
    ("P-03", 600.0, "Worli depot", 72.818, 19.000),
]


def _seed(data: Path, city: Path) -> Path:
    run = data / "runs" / RUN_ID
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text("{}", encoding="utf-8")
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "city": "mumbai",
                "cycle_ts": T0.isoformat(),
                "notes": ["Reconstructed replay"],
                "rain_aoi_mm_h": [31.7, 26.3, 21.2] + [8.0] * 33,
            }
        ),
        encoding="utf-8",
    )
    (run / "hotspots.json").write_text(json.dumps(HOTSPOTS), encoding="utf-8")
    (run / "segments_wet.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "valid_ts": [(T0 + timedelta(minutes=5 * (k + 1))).isoformat() for k in range(36)],
                "depth_cm": {},
                "p_gt": {},
            }
        ),
        encoding="utf-8",
    )
    city.mkdir(parents=True, exist_ok=True)
    (city / "assets.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "asset_id": pid,
                            "kind": "mobile_pump",
                            "capacity_m3_per_h": cap,
                            "depot": depot,
                            "status": "available",
                            "synthetic": True,
                        },
                    }
                    for pid, cap, depot, lon, lat in FLEET
                ],
            }
        ),
        encoding="utf-8",
    )
    # The plan exactly as a bake writes it: the same function, the same inputs.
    plan = build_pump_plan(HOTSPOTS, city, RUN_ID, 5)
    write_pump_plan(run, plan)
    return run


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data = tmp_path / "data"
    city = tmp_path / "city"
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(city))
    pump_map.clear_cache()
    run = _seed(data, city / "mumbai")
    yield run
    pump_map.clear_cache()


def _get(client: TestClient, **params: object) -> dict:
    res = client.get("/v1/pumps/map", params={"run_id": RUN_ID, "routes": False, **params})
    assert res.status_code == 200, res.text
    return res.json()


def test_every_leg_reproduces_the_plan_it_draws(seeded: Path, client: TestClient) -> None:
    plan = json.loads((seeded / "pump_plan.json").read_text(encoding="utf-8"))
    body = _get(client)

    assert body["run_id"] == RUN_ID
    assert body["inventory"] == "synthetic"
    assert body["benefit_label"] == plan["benefit_label"]
    assert len(body["legs"]) == len(plan["assignments"]) == 2
    for leg, a in zip(body["legs"], plan["assignments"], strict=True):
        assert leg["agrees"] is True, leg
        assert leg["pump_id"] == a["pump_id"]
        assert leg["target"]["id"] == a["hotspot_id"]
        assert leg["target"]["kind"] == "register"
        assert leg["depot"]["lon"] is not None and leg["depot"]["lat"] is not None
        before, after = leg["depth_before_cm"], leg["depth_after_cm"]
        assert len(before) == len(after) == 36
        # The minutes on the plan are the series above 45 cm, step by step.
        assert sum(5 for v in before if v > 45.0) == a["minutes_before"]
        assert sum(5 for v in after if v > 45.0) == a["minutes_after"]
        # A pump never raises the water, and nothing changes before it arrives.
        assert all(y <= x for x, y in zip(before, after, strict=True))
        k = leg["effective_from_min"] // 5 - 1
        assert before[:k] == after[:k]
        first = next(i for i, v in enumerate(before) if v > 45.0)
        assert leg["window_before"]["from_min"] == (first + 1) * 5

    summary = body["summary"]
    assert summary["pumps_dispatched"] == 2
    assert summary["minutes_saved"] == plan["total_minutes_saved"]
    assert summary["minutes_before"] - summary["minutes_after"] == plan["total_minutes_saved"]
    assert summary["helped_most"]["minutes_saved"] == max(
        a["minutes_saved"] for a in plan["assignments"]
    )
    # The idle lorry is still on the map at its depot.
    assert {d["name"] for d in body["depots"]} == {f[2] for f in FLEET}


def test_a_plan_the_recount_does_not_reproduce_is_flagged_not_fitted(
    seeded: Path, client: TestClient
) -> None:
    record = seeded / "pump_plan.json"
    plan = json.loads(record.read_text(encoding="utf-8"))
    plan["assignments"][0]["minutes_before"] += 5
    record.write_text(json.dumps(plan), encoding="utf-8")

    body = _get(client)
    leg = body["legs"][0]
    assert leg["agrees"] is False
    assert leg["minutes_before"] == plan["assignments"][0]["minutes_before"]
    assert sum(5 for v in leg["depth_before_cm"] if v > 45.0) != leg["minutes_before"]
    assert any(leg["pump_id"] in note for note in body["notes"])


def test_the_answer_is_remembered_until_the_plan_changes(seeded: Path, client: TestClient) -> None:
    assert _get(client)["cached"] is False
    assert _get(client)["cached"] is True
    record = seeded / "pump_plan.json"
    record.write_text(record.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _get(client)["cached"] is False


def test_no_road_graph_still_draws_the_leg_and_says_why(seeded: Path, client: TestClient) -> None:
    body = _get(client, routes=True)
    for leg in body["legs"]:
        assert leg["depth_before_cm"] is not None
        if leg["route"] is None:
            assert leg["route_note"]


def test_a_place_that_no_longer_crosses_keeps_its_series(seeded: Path, client: TestClient) -> None:
    """The recount drawing a place under the line serves its series flagged, not an empty gauge.

    On the 07:40 demo cycle the plan's one place recounted 1 cm under 45 cm on the rebuilt city,
    and the map dropped both its series and its road while the plan beside it said "5 min above
    45 cm". The series is the forecast; it is drawn, and the disagreement is said.
    """
    record = seeded / "hotspots.json"
    hotspots = json.loads(record.read_text(encoding="utf-8"))
    hotspots[0]["depth_cm"] = [30.0] * 36  # never above 45 cm
    record.write_text(json.dumps(hotspots), encoding="utf-8")

    body = _get(client)
    leg = next(leg for leg in body["legs"] if leg["target"]["id"] == "MUM-HS-01")
    assert leg["depth_before_cm"] == [30.0] * 36
    assert leg["window_before"] is None
    assert leg["agrees"] is False
    assert "series_note" not in leg
    assert any(leg["pump_id"] in note for note in body["notes"])


def test_a_place_with_no_series_is_still_routed(seeded: Path, client: TestClient) -> None:
    """The road does not wait for the depth: a leg with no series is still sent to the router."""
    record = seeded / "hotspots.json"
    hotspots = json.loads(record.read_text(encoding="utf-8"))
    record.write_text(json.dumps(hotspots[1:]), encoding="utf-8")  # drop MUM-HS-01 entirely

    body = _get(client, routes=True)
    leg = next(leg for leg in body["legs"] if leg["target"]["id"] == "MUM-HS-01")
    assert leg["depth_before_cm"] is None
    assert "no depth series" in leg["series_note"]
    assert "no longer crosses" not in leg["series_note"]
    # No road graph in the fixture, so the router was asked and said why - it was not skipped.
    assert leg["route"] is not None or leg["route_note"]


def test_the_no_series_note_names_what_is_missing() -> None:
    street = pump_map._no_series_note("street:Danda Avenue", streets_read=False)
    assert "street table could not be read" in street
    assert "no depth series" in pump_map._no_series_note("street:Danda Avenue", streets_read=True)
    assert "no depth series" in pump_map._no_series_note("MUM-HS-01", streets_read=False)


def test_a_run_without_a_pump_plan_is_a_404(seeded: Path, client: TestClient) -> None:
    (seeded / "pump_plan.json").unlink()
    res = client.get("/v1/pumps/map", params={"run_id": RUN_ID, "routes": False})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "run_not_found"


def test_the_cycles_list_names_every_plan_and_offers_the_busiest(
    seeded: Path, client: TestClient
) -> None:
    """A cycle that sends nothing points at the cycles that do, with the plan's own counts."""
    quiet = seeded.parent / "MUM-20190702T1200Z-sky1.0-twin1.0-flash0.1-baked"
    quiet.mkdir()
    (quiet / "run.json").write_text(json.dumps({"cycle_ts": "2019-07-02T17:30:00+05:30"}))
    (quiet / "pump_plan.json").write_text(
        json.dumps({"assignments": [], "unassigned": [], "total_minutes_saved": 0})
    )
    (seeded.parent / "MUM-20190702T1100Z-sky1.0-twin1.0-flash0.1-baked").mkdir()  # no plan

    res = client.get("/v1/pumps/cycles", params={"city": "mumbai"})
    assert res.status_code == 200, res.text
    body = res.json()
    plan = json.loads((seeded / "pump_plan.json").read_text(encoding="utf-8"))

    assert [c["run_id"] for c in body["cycles"]] == [quiet.name, RUN_ID]
    busy = body["cycles"][1]
    assert busy["n_assigned"] == len(plan["assignments"]) > 0
    assert busy["minutes_saved"] == plan["total_minutes_saved"]
    assert busy["cycle_ts"] == T0.isoformat()
    assert body["cycles"][0]["n_assigned"] == 0
    assert body["busiest_run_id"] == RUN_ID
