"""What the drain X-ray is built from: every moved pipe, a summary, and names a reader can use.

Three defects the drain-health product carried into the 08:40 cycle of 2 July 2019, measured on
the shipped run:

* **Pipes an observation cleared were not written.** The file kept the worst 6,000 by posterior,
  and a pipe driven to a blockage near zero falls below that cut: 52 of the 201 pipes Pulse moved
  were missing, while 13 of that cycle's 21 observations had lowered blockage.
* **The headline numbers did not exist.** Pipes moved up and down, the largest rise and what the
  learning did to capacity had to be recomputed by the screen from 6,000 features, and capacity
  lost over the whole network could not be computed from them at all.
* **83 % of written pipes had no name**, and traffic observations were titled by segment id.

Pinned here on hand-built inputs, so each number can be checked by eye.
"""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np
import pytest
from varuna_pulse.cycle import REPORT_SNAP_M, _nearest_edge, _node_distance_sq_m, _snap_report
from varuna_pulse.health import (
    MOVED_EPS,
    _clean_name,
    build_place_index,
    capacity_reduction_pct,
    desilting_csv,
    drain_health,
    drain_summary,
    is_moved,
    pipe_places,
    segment_places,
    write_drain_health,
    written_features,
)

if TYPE_CHECKING:
    from pathlib import Path


def _posterior(beta: list[float], prior: list[float]) -> SimpleNamespace:
    n = len(beta)
    return SimpleNamespace(
        beta_mean=np.asarray(beta, dtype=np.float64),
        beta_sd=np.full(n, 0.1),
        prior_mean=np.asarray(prior, dtype=np.float64),
        operator="capacity_deficit",
        notes=("one note",),
    )


def _health(beta: list[float], prior: list[float], **kwargs) -> dict:
    n = len(beta)
    ids = tuple(f"MUM-E{i:06d}" for i in range(n))
    geometry = [[[72.8 + i * 1e-4, 19.0], [72.8 + i * 1e-4, 19.0001]] for i in range(n)]
    return drain_health(
        _posterior(beta, prior),
        ids,
        geometry,
        diameter_m=np.full(n, 0.6),
        last_update="2019-07-02T08:40:00+05:30",
        run_id="MUM-TEST",
        **kwargs,
    )


# ---- every moved pipe is written -----------------------------------------------------------


def test_a_pipe_an_observation_cleared_is_written_even_below_the_cap() -> None:
    """Five pipes, a cap of two: the one cleared to 0.02 and the one raised both survive."""
    prior = [0.35, 0.35, 0.35, 0.15, 0.15]
    beta = [0.35, 0.35, 0.35, 0.02, 0.49]  # edge 3 cleared, edge 4 raised
    health = _health(beta, prior)

    written = written_features(health["features"], cap=2)
    ids = [f["properties"]["edge_id"] for f in written]

    assert set(ids) == {"MUM-E000003", "MUM-E000004"}
    # Worst blockage first, as the file always was.
    assert ids == ["MUM-E000004", "MUM-E000003"]


def test_the_cap_is_filled_with_the_worst_prior_after_the_moved_pipes() -> None:
    prior = [0.15, 0.35, 0.20, 0.15]
    beta = [0.15, 0.35, 0.20, 0.05]  # only edge 3 moved (down)
    written = written_features(_health(beta, prior)["features"], cap=2)
    assert [f["properties"]["edge_id"] for f in written] == ["MUM-E000001", "MUM-E000003"]


def test_more_moved_pipes_than_the_cap_are_all_written() -> None:
    prior = [0.2] * 5
    beta = [0.3, 0.1, 0.4, 0.05, 0.25]
    written = written_features(_health(beta, prior)["features"], cap=2)
    assert len(written) == 5


def test_the_moved_flag_is_decided_on_unrounded_values() -> None:
    """1.2e-4 and 0.6e-4 both round to a beta_delta of 0.0001; only the first moved."""
    prior = [0.2, 0.2]
    beta = [0.2 + 1.2e-4, 0.2 + 0.6e-4]
    health = _health(beta, prior)
    props = [f["properties"] for f in health["features"]]

    assert [p["beta_delta"] for p in props] == [0.0001, 0.0001]
    assert [p["moved"] for p in props] == [True, False]
    assert health["n_updated"] == 1 == health["summary"]["n_moved"]


def test_an_older_product_without_the_flag_falls_back_to_the_rounded_delta() -> None:
    assert is_moved({"beta_delta": 0.0001})
    assert is_moved({"beta_delta": -0.3})
    assert not is_moved({"beta_delta": 0.0})
    assert not is_moved({"beta_delta": 0.0001, "moved": False})


# ---- the summary ------------------------------------------------------------------------------


def test_the_summary_splits_moves_and_names_the_largest_rise_and_fall() -> None:
    prior = [0.15, 0.35, 0.20, 0.20]
    beta = [0.49, 0.30, 0.20, 0.25]
    summary = drain_summary(
        np.array(beta),
        np.array(prior),
        ("A", "B", "C", "D"),
        display_name=["off Eastern Freeway", None, None, "Road No 28A"],
        locality=["near Wadala", None, None, None],
    )
    assert summary["n_moved"] == 3
    assert (summary["n_moved_up"], summary["n_moved_down"]) == (2, 1)
    assert summary["largest_rise"] == {
        "edge": "A",
        "name": "off Eastern Freeway",
        "locality": "near Wadala",
        "prior": 0.15,
        "post": 0.49,
    }
    assert summary["largest_fall"]["edge"] == "B"
    assert summary["source"] == "product"


def test_capacity_lost_is_weighted_by_full_flow_and_split_prior_from_learned() -> None:
    """A 10 m3/s trunk and a 1 m3/s lane, checked against the formula by hand."""
    prior = np.array([0.15, 0.35])
    beta = np.array([0.49, 0.35])
    q = np.array([10.0, 1.0])
    summary = drain_summary(beta, prior, ("T", "L"), q_full_m3s=q)

    lost_prior = (q * capacity_reduction_pct(prior)).sum() / 100.0
    lost_post = (q * capacity_reduction_pct(beta)).sum() / 100.0
    assert summary["capacity_full_m3s"] == 11.0
    assert summary["capacity_lost_prior_pct"] == pytest.approx(100 * lost_prior / 11.0, abs=1e-3)
    assert summary["capacity_lost_post_pct"] == pytest.approx(100 * lost_post / 11.0, abs=1e-3)
    assert summary["capacity_learned_m3s"] == pytest.approx(lost_post - lost_prior, abs=0.05)
    assert summary["capacity_learned_m3s"] > 0


def test_no_capacity_is_reported_without_a_capacity_to_weight_by() -> None:
    summary = drain_summary(np.array([0.3]), np.array([0.2]), ("A",))
    for key in (
        "capacity_full_m3s",
        "capacity_lost_prior_pct",
        "capacity_lost_post_pct",
        "capacity_learned_m3s",
    ):
        assert summary[key] is None, key


def test_observations_are_counted_by_kind_and_by_provenance() -> None:
    records = [
        {"kind": "traffic", "synthetic": True},
        {"kind": "traffic", "synthetic": True},
        {"kind": "report", "synthetic": True},
        {"kind": "report", "synthetic": False},
    ]
    summary = drain_summary(np.array([0.2]), np.array([0.2]), ("A",), observations=records)
    assert summary["n_obs"] == 4
    assert summary["n_obs_by_kind"] == {"report": 2, "traffic": 2}
    assert (summary["n_obs_synthetic"], summary["n_obs_real"]) == (3, 1)


def test_the_product_carries_the_summary_over_every_pipe_not_just_the_written() -> None:
    health = _health([0.49, 0.02, 0.35], [0.15, 0.35, 0.35], q_full_m3s=np.array([5.0, 1.0, 1.0]))
    assert health["summary"]["n_pipes"] == 3
    assert health["summary"]["capacity_full_m3s"] == 7.0


# ---- determinism (rule 8) -----------------------------------------------------------------


def test_writing_the_same_product_twice_writes_the_same_bytes(tmp_path: Path) -> None:
    rng = np.random.default_rng(2019)
    prior = rng.choice([0.15, 0.20, 0.35], size=300).tolist()
    beta = (np.array(prior) + np.where(rng.random(300) < 0.1, rng.normal(0, 0.1, 300), 0)).tolist()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        write_drain_health(
            tmp_path / name,
            _health(
                beta,
                prior,
                q_full_m3s=np.linspace(0.5, 5.0, 300),
                observations=[{"kind": "traffic", "synthetic": True}],
            ),
        )
    for file in ("drain_health.geojson", "desilting.csv"):
        assert (tmp_path / "a" / file).read_bytes() == (tmp_path / "b" / file).read_bytes()

    written = json.loads((tmp_path / "a" / "drain_health.geojson").read_text(encoding="utf-8"))
    moved = sum(1 for b, p in zip(beta, prior, strict=True) if abs(b - p) > MOVED_EPS)
    assert sum(f["properties"]["moved"] for f in written["features"]) == moved
    assert written["n_updated"] == moved == written["summary"]["n_moved"]
    means = [f["properties"]["beta_mean"] for f in written["features"]]
    assert means == sorted(means, reverse=True)


# ---- names ------------------------------------------------------------------------------------

# A small city in UTM 43N: a named street along y = 0, an unnamed one at y = 150 and one far off
# at y = 1,000, and one chronic hotspot at (50, 400).
X0, Y0 = 272_000.0, 2_100_000.0


def _city(tmp_path: Path) -> Path:
    import geopandas as gpd
    from shapely.geometry import LineString, Point

    root = tmp_path / "mumbai"
    (root / "export").mkdir(parents=True)
    crs = "EPSG:32643"
    gpd.GeoDataFrame(
        {
            "segment_id": ["S1", "S2", "S3"],
            "name": ["Dr Babasaheb Ambedkar Marg", None, None],
        },
        geometry=[
            LineString([(X0, Y0), (X0 + 100, Y0)]),
            LineString([(X0, Y0 + 150), (X0 + 100, Y0 + 150)]),
            LineString([(X0, Y0 + 1000), (X0 + 100, Y0 + 1000)]),
        ],
        crs=crs,
    ).to_parquet(root / "segments.parquet")
    gpd.GeoDataFrame(
        {"edge_id": ["E0", "E1", "E2"]},
        geometry=[
            LineString([(X0, Y0), (X0 + 100, Y0)]),
            LineString([(X0, Y0 + 150), (X0 + 100, Y0 + 150)]),
            LineString([(X0, Y0 + 1000), (X0 + 100, Y0 + 1000)]),
        ],
        crs=crs,
    ).to_parquet(root / "drain_edges.parquet")
    gpd.GeoDataFrame(
        {"name": ["Hindmata junction (Hindmata Cinema, Dadar East)"]},
        geometry=[Point(X0 + 50, Y0 + 400)],
        crs=crs,
    ).to_crs("EPSG:4326").to_parquet(root / "export" / "hotspots.parquet")
    return root


def test_a_pipe_is_named_by_its_street_then_off_the_nearest_then_not_at_all(
    tmp_path: Path,
) -> None:
    root = _city(tmp_path)
    display, locality = pipe_places(root, ["E0", "E1", "E2"], ["Dr Babasaheb Ambedkar Marg", None])

    # E0 runs under a named street; E1's midpoint is 158 m from its nearest vertex (within
    # 200 m); E2 is 1 km from any named street.
    assert display == ("Dr Babasaheb Ambedkar Marg", "off Dr Babasaheb Ambedkar Marg", None)
    # The hotspot is 250 m from E1 (within 300 m) and 400 m from E0; its parenthesis is dropped.
    assert locality == (None, "near Hindmata junction", None)


def test_a_traffic_segment_is_placed_by_the_same_rules(tmp_path: Path) -> None:
    root = _city(tmp_path)
    places = segment_places(root, ["S1", "S2", "S3", "S-missing"])
    assert places["S1"] == ("Dr Babasaheb Ambedkar Marg", None)
    assert places["S2"] == ("off Dr Babasaheb Ambedkar Marg", "near Hindmata junction")
    assert places["S3"] == (None, None)
    assert places["S-missing"] == (None, None)


def test_the_place_index_is_built_the_same_way_twice(tmp_path: Path) -> None:
    root = _city(tmp_path)
    a, b = build_place_index(root), build_place_index(root)
    assert a.street_names == b.street_names
    assert a.hotspot_names == b.hotspot_names == ("Hindmata junction",)
    xy = np.array([[X0 + 50, Y0 + 150], [np.nan, np.nan]])
    assert a.streets_near(xy) == b.streets_near(xy) == ["Dr Babasaheb Ambedkar Marg", None]


def test_a_city_with_no_tables_names_nothing_rather_than_failing(tmp_path: Path) -> None:
    display, locality = pipe_places(tmp_path, ["E0"], ["Own street"])
    assert display == ("Own street",)
    assert locality == (None,)
    assert segment_places(tmp_path, ["S1"]) == {"S1": (None, None)}


def test_the_desilting_list_uses_the_display_name_and_carries_the_locality() -> None:
    health = _health(
        [0.49, 0.30],
        [0.15, 0.20],
        street=[None, "Road No 28A"],
        display_name=["off Eastern Freeway", None],
        locality=["near Wadala", None],
    )
    rows = list(csv.DictReader(io.StringIO(desilting_csv(health))))
    assert rows[0]["street"] == "off Eastern Freeway"
    assert rows[0]["locality"] == "near Wadala"
    # A pipe with its own street keeps it, and the display name falls back to it.
    assert rows[1]["street"] == "Road No 28A"
    assert health["features"][1]["properties"]["display_name"] == "Road No 28A"


def test_a_segment_with_several_osm_names_is_printed_as_names_not_as_a_list() -> None:
    """95 of Mumbai's segments store their merged OSM names as a list's text."""
    text = "['Lal Bahadur Shastri Marg', 'Sant Rohidas Marg']"
    assert _clean_name(text) == "Lal Bahadur Shastri Marg / Sant Rohidas Marg"
    assert (
        _clean_name(["Tilak Bridge", "Tilak Road", "Tilak Bridge"]) == "Tilak Bridge / Tilak Road"
    )
    assert _clean_name(np.array(["Tilak Bridge", None], dtype=object)) == "Tilak Bridge"
    assert _clean_name("[]") is None
    # A name that merely starts with a bracket and is not a list is kept as written.
    assert _clean_name("[Old] Hindmata Road") == "[Old] Hindmata Road"
    assert _clean_name(float("nan")) is None
    assert _clean_name("  Road No 28A ") == "Road No 28A"


# ---- the 500 m report snap ---------------------------------------------------------------------


def test_a_report_is_snapped_to_the_nearest_node_only_within_500_m() -> None:
    lat = 19.0
    node_lon = np.array([72.84, 72.90])
    node_lat = np.array([lat, lat])
    outgoing = {0: 7, 1: 8}
    one_deg_lon_m = float(
        np.sqrt(_node_distance_sq_m(np.array([1.0]), np.array([lat]), 0.0, lat))[0]
    )

    near = 72.84 + 300.0 / one_deg_lon_m
    far = 72.84 + (REPORT_SNAP_M + 200.0) / one_deg_lon_m
    assert _nearest_edge(node_lon, node_lat, outgoing, near, lat) == 7
    assert _nearest_edge(node_lon, node_lat, outgoing, far, lat) is None
    # A phone whose location came back as 0, 0 is not placed on Mumbai's nearest pipe.
    assert _nearest_edge(node_lon, node_lat, outgoing, 0.0, 0.0) is None


def test_a_report_the_cycle_cannot_place_says_why() -> None:
    """Too far from the graph and nearest an outfall are counted apart, and neither silently."""
    lat = 19.0
    node_lon = np.array([72.84, 72.90])
    node_lat = np.array([lat, lat])
    one_deg_lon_m = float(
        np.sqrt(_node_distance_sq_m(np.array([1.0]), np.array([lat]), 0.0, lat))[0]
    )
    near_first = 72.84 + 100.0 / one_deg_lon_m
    # Node 0 drains through pipe 7; node 1 is an outfall with no pipe of its own.
    outgoing = {0: 7}
    assert _snap_report(node_lon, node_lat, outgoing, near_first, lat) == (7, None)
    assert _snap_report(node_lon, node_lat, outgoing, 72.90, lat) == (None, "outfall")
    assert _snap_report(node_lon, node_lat, outgoing, 0.0, 0.0) == (None, "too_far")
    assert _snap_report(None, None, outgoing, 72.84, lat) == (None, "no_graph")


def test_the_snap_distance_is_in_metres() -> None:
    """A kilometre north is a kilometre, give or take the equirectangular approximation."""
    d = np.sqrt(
        _node_distance_sq_m(np.array([72.84]), np.array([19.0 + 1000 / 110_574]), 72.84, 19.0)
    )
    assert float(d[0]) == pytest.approx(1000.0, rel=1e-3)
