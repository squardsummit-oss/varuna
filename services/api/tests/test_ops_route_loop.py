"""Close a street, then ask for a route that used it (task D-07; TECH_SPEC 3.6).

This is the loop the authority desk exists for, end to end through the API: one write appended
to the ops log, one `POST /v1/route`, and the answer goes round the closed street and says whose
closure it was. Nothing is stubbed between the two - the same overlay module the write appends
to is the one the router reads at request time.

The city is four segments in a diamond rather than Mumbai's 21,296: `city/` is gitignored and a
clean checkout has no road graph, so a test that needed one could only ever skip. Four is enough
to have a short way and a long way round, which is the whole question.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import ops
from varuna_schemas.constants import IST

RUN_ID = "MUM-20190702T1200Z-sky1.0-twin1.0-flash0.1-baked"
PASSPHRASE = "monsoon desk 2026"
AUTH = {ops.OPS_HEADER: PASSPHRASE}
T0 = datetime(2019, 7, 2, 8, 45, tzinfo=IST)

ORIGIN = (72.840, 19.000)
VIA_SHORT = (72.845, 19.000)
VIA_LONG = (72.845, 19.010)
DESTINATION = (72.850, 19.000)

SEGMENTS: list[tuple[str, int, int, tuple[float, float], tuple[float, float], float, str]] = [
    ("SHORT-A", 0, 1, ORIGIN, VIA_SHORT, 525.0, "Tilak Road"),
    ("SHORT-B", 1, 3, VIA_SHORT, DESTINATION, 525.0, "Tilak Road"),
    ("LONG-A", 0, 2, ORIGIN, VIA_LONG, 1250.0, "Gokhale Road"),
    ("LONG-B", 2, 3, VIA_LONG, DESTINATION, 1250.0, "Gokhale Road"),
]
"""A diamond: Tilak Road is the short way, Gokhale Road the long way round."""


def _write_city(root: Path) -> None:
    """The four-segment road table `varuna_route.graph.load_graph` reads."""
    import geopandas as gpd
    from shapely.geometry import LineString

    root.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame(
        {
            "segment_id": [s[0] for s in SEGMENTS],
            "u": [s[1] for s in SEGMENTS],
            "v": [s[2] for s in SEGMENTS],
            "length_m": [s[5] for s in SEGMENTS],
            "oneway": [False] * len(SEGMENTS),
            "class": ["residential"] * len(SEGMENTS),
            "speed_kmh": [30.0] * len(SEGMENTS),
            "name": [s[6] for s in SEGMENTS],
            "lanes": [2.0] * len(SEGMENTS),
        },
        geometry=[LineString([s[3], s[4]]) for s in SEGMENTS],
        crs="EPSG:4326",
    )
    frame.to_parquet(root / "segments.parquet")


def _write_run(data: Path) -> None:
    """A run where every street is dry, so only the closure can change the route."""
    run = data / "runs" / RUN_ID
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps({"run_id": RUN_ID, "city": "mumbai", "ensemble_n": 20}), encoding="utf-8"
    )
    (run / "segments_wet.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "valid_ts": [(T0 + timedelta(minutes=5 * k)).isoformat() for k in range(12)],
                "min_depth_cm": 5.0,
                "n_segments_total": len(SEGMENTS),
                "n_segments_wet": 0,
                "depth_cm": {},
                "p_gt": {},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def city_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from varuna_route.graph import load_graph
    from varuna_route.reasons import design_intensity_by_segment

    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setenv(ops.PASSPHRASE_ENV, PASSPHRASE)
    ops.reset_rate_limit()
    # Both are cached per city for the life of the process, and this city is not the real one.
    load_graph.cache_clear()
    design_intensity_by_segment.cache_clear()
    _write_city(tmp_path / "city" / "mumbai")
    _write_run(tmp_path / "data")
    yield tmp_path
    load_graph.cache_clear()
    design_intensity_by_segment.cache_clear()
    ops.reset_rate_limit()


def _route(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/route",
        json={
            "origin": list(ORIGIN),
            "destination": list(DESTINATION),
            "depart_at": T0.isoformat(),
            "profile": "car",
            "run_id": RUN_ID,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_closing_a_street_sends_the_next_route_round_it_with_the_officer_s_reason(
    city_and_run: Path, client: TestClient
) -> None:
    before = _route(client)
    assert before["varuna"]["streets"] == ["Tilak Road"], (
        "with every street dry the short way is the way; the closure is the only thing that "
        "can move this route"
    )

    closed = client.post(
        "/v1/ops/closures",
        json={
            "segment_id": "SHORT-B",
            "reason": "Manhole cover lifted, crew on site",
            "user": "ward officer F/S",
            "city": "mumbai",
        },
        headers=AUTH,
    )
    assert closed.status_code == 200, closed.text

    after = _route(client)

    assert after["varuna"]["streets"] == ["Gokhale Road"], "the route now goes round the closure"
    assert after["naive"]["streets"] == ["Tilak Road"], (
        "the naive comparison still walks into it - that is what makes the detour legible"
    )
    assert after["varuna"]["distance_m"] > before["varuna"]["distance_m"]

    closure = next((r for r in after["reasons"] if r["kind"] == "closure"), None)
    assert closure is not None, f"no closure reason in {[r['kind'] for r in after['reasons']]}"
    assert closure["reason"] == "Manhole cover lifted, crew on site"
    assert closure["user"] == "ward officer F/S"
    assert closure["name"] == "Tilak Road"
    assert closure["at"], "the sentence needs the time the officer entered it"

    assert any("closed by an authority" in note for note in after["notes"])
    assert any("no forecast product was changed" in note for note in after["notes"])


def test_reopening_the_street_puts_the_route_back(city_and_run: Path, client: TestClient) -> None:
    """The overlay is folded at request time, so lifting a closure needs no restart."""
    for reopen in (False, True):
        body = {"segment_id": "SHORT-B", "city": "mumbai", "reopen": reopen}
        if not reopen:
            body["reason"] = "Manhole cover lifted, crew on site"
        assert client.post("/v1/ops/closures", json=body, headers=AUTH).status_code == 200

    after = _route(client)
    assert after["varuna"]["streets"] == ["Tilak Road"]
    assert not [r for r in after["reasons"] if r["kind"] == "closure"]


def test_the_road_conditions_feed_carries_the_closure_with_its_cause(
    city_and_run: Path, client: TestClient
) -> None:
    """The feed a navigation app consumes has to say a closure is a closure, not deep water."""
    client.post(
        "/v1/ops/closures",
        json={
            "segment_id": "SHORT-B",
            "reason": "Manhole cover lifted, crew on site",
            "city": "mumbai",
        },
        headers=AUTH,
    )

    feed = client.get("/v1/feeds/road-conditions", params={"run_id": RUN_ID})

    assert feed.status_code == 200, feed.text
    features = feed.json()["features"]
    closed = [f for f in features if f["properties"].get("cause") == "closure"]
    assert [f["properties"]["segment_id"] for f in closed] == ["SHORT-B"]
    assert closed[0]["properties"]["closed_reason"] == "Manhole cover lifted, crew on site"
    assert closed[0]["properties"]["condition"] == "impassable"
