"""``GET /v1/drains/health`` carries the cycle's summary, and sends every moved pipe first.

A run baked since the product carried its own ``summary`` gets it passed through untouched. A run
baked before gets one rebuilt from the pipes it wrote plus the city's prior for the rest, labelled
``source: "written_features"`` with a note saying what that leaves out - the drain X-ray leads
with these numbers, and rule 6 does not allow it to invent them or to hide how they were made.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"


def _feature(edge: str, beta: float, prior: float, **extra: object) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[72.84, 19.01], [72.841, 19.01]]},
        "properties": {
            "edge_id": edge,
            "street": None,
            "beta_mean": beta,
            "beta_sd": 0.1,
            "beta_prior": prior,
            "beta_delta": round(beta - prior, 4),
            "capacity_reduction_pct": 30.0,
            "diameter_m": 0.6,
            "observations": 0,
            "explains": [],
            "confidence": "inferred",
            "last_update": "2019-07-02T08:40:00+05:30",
            **extra,
        },
    }


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    run = tmp_path / "data" / "runs" / RUN
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text(json.dumps({"wgs84": [72.8, 19.0, 72.9, 19.1]}))
    (run / "observations.json").write_text(
        json.dumps(
            {
                "run_id": RUN,
                "observations": [
                    {"kind": "traffic", "synthetic": True},
                    {"kind": "report", "synthetic": False},
                ],
            }
        )
    )
    return run


def _old_product(run: Path) -> None:
    """Four pipes written the old way: worst blockage first, no summary, no flag; 3 moved of 4."""
    (run / "drain_health.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "run_id": RUN,
                "n_edges": 6,
                "n_updated": 4,
                "features": [
                    _feature("MUM-E000001", 0.49, 0.15, display_name="off Eastern Freeway"),
                    _feature("MUM-E000002", 0.35, 0.35),
                    _feature("MUM-E000003", 0.30, 0.20),
                    _feature("MUM-E000004", 0.10, 0.20),
                ],
            }
        )
    )


def _city_table(tmp_path: Path) -> None:
    import pandas as pd

    root = tmp_path / "city" / "mumbai"
    root.mkdir(parents=True)
    pd.DataFrame(
        {
            "edge_id": [f"MUM-E{i:06d}" for i in range(6)],
            "beta_mean": [0.20, 0.15, 0.35, 0.20, 0.20, 0.15],
            "q_full_m3s": [1.0, 10.0, 1.0, 1.0, 1.0, 1.0],
        }
    ).to_parquet(root / "drain_edges.parquet")


def test_an_older_run_gets_a_summary_rebuilt_and_labelled(
    client: TestClient, run_dir: Path, tmp_path: Path
) -> None:
    _old_product(run_dir)
    _city_table(tmp_path)
    body = client.get("/v1/drains/health", params={"run_id": RUN}).json()
    summary = body["summary"]

    assert summary["source"] == "written_features"
    assert (summary["n_moved_up"], summary["n_moved_down"]) == (2, 1)
    # The product said four moved; three were written, so one is missing and the note says so.
    assert summary["n_moved_unwritten"] == 1
    assert "1 of the 4 pipes Pulse moved were not written" in summary["note"]
    assert summary["largest_rise"]["edge"] == "MUM-E000001"
    assert summary["largest_rise"]["name"] == "off Eastern Freeway"
    # Weighted over the whole network from the city table, not over the four written pipes.
    assert summary["n_pipes"] == 6
    assert summary["capacity_full_m3s"] == 15.0
    assert summary["capacity_learned_m3s"] > 0
    assert summary["n_obs_by_kind"] == {"report": 1, "traffic": 1}
    assert (summary["n_obs_synthetic"], summary["n_obs_real"]) == (1, 1)


def test_without_a_city_build_the_capacity_is_left_out_rather_than_guessed(
    client: TestClient, run_dir: Path
) -> None:
    _old_product(run_dir)
    summary = client.get("/v1/drains/health", params={"run_id": RUN}).json()["summary"]
    # The split is over the four written pipes, but the network is the product's six: a tile that
    # reads "3 of 4 pipes moved" would misstate the network's size (rule 6).
    assert summary["n_pipes"] == 6
    assert summary["capacity_lost_prior_pct"] is None
    assert summary["capacity_learned_m3s"] is None


def test_a_product_summary_is_passed_through_untouched(client: TestClient, run_dir: Path) -> None:
    written = {"source": "product", "n_moved": 201, "note": None}
    (run_dir / "drain_health.geojson").write_text(
        json.dumps(
            {
                "run_id": RUN,
                "n_edges": 1,
                "n_updated": 201,
                "summary": written,
                "features": [_feature("MUM-E000001", 0.49, 0.15, moved=True)],
            }
        )
    )
    assert client.get("/v1/drains/health", params={"run_id": RUN}).json()["summary"] == written


def test_by_default_a_limit_sends_the_worst_pipes(client: TestClient, run_dir: Path) -> None:
    """The command palette's "Pipes by blockage" is the worst 25, moved or not.

    An unmoved 0.35 land-use prior outranks a pipe an observation cleared to 0.10, because the
    default order is blockage and nothing else.
    """
    _old_product(run_dir)
    body = client.get("/v1/drains/health", params={"run_id": RUN, "limit": 3}).json()
    ids = [f["properties"]["edge_id"] for f in body["features"]]
    assert ids == ["MUM-E000001", "MUM-E000002", "MUM-E000003"]
    assert body["n_sent"] == 3


def test_order_learned_sends_the_pipes_pulse_moved_first(client: TestClient, run_dir: Path) -> None:
    """Opted into, a cleared pipe at 0.10 outranks an unmoved 0.35 prior for a place in the cap."""
    _old_product(run_dir)
    body = client.get(
        "/v1/drains/health", params={"run_id": RUN, "limit": 3, "order": "learned"}
    ).json()
    ids = [f["properties"]["edge_id"] for f in body["features"]]
    # Chosen moved-first, sent worst-first.
    assert ids == ["MUM-E000001", "MUM-E000003", "MUM-E000004"]
    assert body["n_sent"] == 3


def test_an_unknown_order_is_refused(client: TestClient, run_dir: Path) -> None:
    _old_product(run_dir)
    response = client.get("/v1/drains/health", params={"run_id": RUN, "order": "moved"})
    assert response.status_code == 422


def _city_segments(tmp_path: Path) -> None:
    """Two road segments 100 m apart in UTM 43N: one OSM names, one it does not."""
    import geopandas as gpd
    from shapely.geometry import LineString

    root = tmp_path / "city" / "mumbai"
    root.mkdir(parents=True, exist_ok=True)
    x, y = 272_000.0, 2_103_000.0
    gpd.GeoDataFrame(
        {
            "segment_id": ["S-NAMED", "S-UNNAMED"],
            "name": ["Eastern Freeway", None],
            "geometry": [
                LineString([(x, y), (x + 60.0, y)]),
                LineString([(x, y + 100.0), (x + 60.0, y + 100.0)]),
            ],
        },
        crs="EPSG:32643",
    ).to_parquet(root / "segments.parquet")


def test_an_older_runs_traffic_anomalies_are_named_from_the_city(
    client: TestClient, run_dir: Path, tmp_path: Path
) -> None:
    """A run baked before the cycle named its anomalies reads "off Eastern Freeway", not an id."""
    _city_segments(tmp_path)
    (run_dir / "observations.json").write_text(
        json.dumps(
            {
                "run_id": RUN,
                "observations": [
                    {"kind": "traffic", "segment_id": "S-NAMED", "synthetic": True},
                    {"kind": "traffic", "segment_id": "S-UNNAMED", "synthetic": True},
                    {"kind": "traffic", "segment_id": "S-GONE", "synthetic": True},
                    {
                        "kind": "traffic",
                        "segment_id": "S-UNNAMED",
                        "place": "as the bake wrote it",
                        "synthetic": True,
                    },
                    {"kind": "report", "place": None, "synthetic": False},
                ],
            }
        )
    )
    records = client.get("/v1/observations", params={"run_id": RUN}).json()["observations"]
    assert [r.get("place") for r in records] == [
        "Eastern Freeway",
        "off Eastern Freeway",
        # A segment the city does not have reads as a road in the city, never "Unnamed road".
        "Road in Mumbai",
        # What the bake wrote always stands.
        "as the bake wrote it",
        # Only traffic anomalies carry a segment to look up.
        None,
    ]


def test_without_a_city_build_the_observations_are_served_as_written(
    client: TestClient, run_dir: Path
) -> None:
    records = client.get("/v1/observations", params={"run_id": RUN}).json()["observations"]
    assert records == [
        {"kind": "traffic", "synthetic": True},
        {"kind": "report", "synthetic": False},
    ]
