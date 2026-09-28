"""What the map layers are allowed to drop (SPEC.md P1.11, 10.1 step 11).

``MAP_KEEP_COLUMNS`` is an allow-list, so a column that is not named there vanishes silently
from ``city/<city>/map/<layer>.geojson`` - the file the console draws from and the one
``GET /v1/city/{city}/layers/{name}`` serves. That is the failure this module exists to catch:
the drain elevations were dropped for months and nothing noticed, because a pipe drawn flat on
the street looks exactly like a pipe drawn at its invert until somebody tilts the camera.

The tests below pin the two layers the underground X-ray reads and prove the elevations
survive a real ``export_city`` round trip rather than only appearing in a constant.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point
from varuna_city.export import (
    MAP_KEEP_COLUMNS,
    export_city,
    map_export_fingerprint,
    simplify_for_map,
)

CRS = "EPSG:32643"

DRAIN_ELEVATION_PROPS = ("z_invert_up_m", "z_invert_dn_m", "slope")
"""The three the X-ray needs: without them a pipe can only be drawn lying on the street."""

NODE_ELEVATION_PROPS = ("z_ground_m", "z_invert_m")
"""A manhole needs both to be drawn as a shaft: the cover it opens at and the bed it reaches."""


def _drain_edges() -> gpd.GeoDataFrame:
    """Two inferred pipes in the AOI's own CRS, carrying every column the parquet carries.

    The second one runs adverse on purpose (its downstream invert is 0.4 m *above* its
    upstream one) while its stored ``slope`` still reads the 0.3 % floor, which is the exact
    disagreement ADR-0048 measured on 26,622 of Mumbai's 49,770 edges. A test that only used
    well-behaved pipes would not notice an exporter that quietly recomputed the column.
    """
    return gpd.GeoDataFrame(
        {
            "edge_id": ["MUM-E000001", "MUM-E000002"],
            "from_node": ["MUM-N000001", "MUM-N000002"],
            "to_node": ["MUM-N000002", "MUM-N000003"],
            "length_m": [40.0, 40.0],
            "slope": [0.0125, 0.003],
            "z_invert_up_m": [8.32, 7.82],
            "z_invert_dn_m": [7.82, 8.22],
            "shape": ["circular", "circular"],
            "diameter_m": [0.6, 0.6],
            "width_m": [0.0, 0.0],
            "height_m": [0.0, 0.0],
            "area_m2": [0.2827, 0.2827],
            "manning_n": [0.013, 0.013],
            "is_trunk": [False, False],
            "beta_mean": [0.2, 0.35],
            "beta_sd": [0.1, 0.12],
            "land_use": ["residential", "market"],
            "confidence": ["inferred", "inferred"],
            "geometry": [
                LineString([(270000.0, 2110000.0), (270040.0, 2110000.0)]),
                LineString([(270040.0, 2110000.0), (270080.0, 2110000.0)]),
            ],
        },
        crs=CRS,
    )


def _drain_nodes() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "node_id": ["MUM-N000001", "MUM-N000002", "MUM-N000003"],
            "kind": ["inlet", "manhole", "outfall"],
            "is_outfall": [False, False, True],
            "tidal": [False, False, True],
            "flap_gate": [False, False, True],
            "z_ground_m": [9.82, 9.32, 9.72],
            "z_invert_m": [8.32, 7.82, 8.22],
            "invert_depth_m": [1.5, 1.5, 1.5],
            # inlet_links_table (the Flash coupling table) reads these, so export_city needs
            # them even though the map layer drops most of them.
            "cell_row": [10, 10, 11],
            "cell_col": [20, 21, 22],
            "inlet_type": ["grate", "grate", "outfall"],
            "inlet_length_m": [0.9, 0.9, 0.0],
            "inlet_area_m2": [0.18, 0.18, 0.0],
            "contributing_area_m2": [1200.0, 1500.0, 900.0],
            "kappa_mean": [0.25, 0.25, 0.25],
            "kappa_a": [1.0, 1.0, 1.0],
            "kappa_b": [3.0, 3.0, 3.0],
            "segment_id": ["MUM-S0001", "MUM-S0001", "MUM-S0002"],
            "surface_unit_id": ["MUM-U0001", "MUM-U0001", "MUM-U0002"],
            "downstream_node": ["MUM-N000002", "MUM-N000003", None],
            "confidence": ["inferred", "inferred", "inferred"],
            "geometry": [
                Point(270000.0, 2110000.0),
                Point(270040.0, 2110000.0),
                Point(270080.0, 2110000.0),
            ],
        },
        crs=CRS,
    )


@pytest.mark.parametrize("prop", DRAIN_ELEVATION_PROPS)
def test_drains_layer_keeps_the_elevations_the_xray_draws_with(prop: str) -> None:
    assert prop in MAP_KEEP_COLUMNS["drains"], (
        f"{prop} was dropped from the drains map layer. Without it the console can only draw "
        "pipes flat on the street, not at the depth they sit at."
    )


@pytest.mark.parametrize("prop", NODE_ELEVATION_PROPS)
def test_drain_nodes_layer_keeps_the_shaft_elevations(prop: str) -> None:
    assert prop in MAP_KEEP_COLUMNS["drain_nodes"], (
        f"{prop} was dropped from the drain_nodes map layer; a manhole needs both its cover "
        "and its bed to be drawn as a shaft."
    )


def test_drains_layer_property_list_is_exactly_what_the_console_is_promised() -> None:
    """The whole list, so an addition is a decision somebody made rather than a drift.

    This is deliberately an equality and not a subset: the layer is 20.9 MB for 49,770 edges,
    so a column added here costs about 43 bytes of key name on every feature before any value.
    """
    assert MAP_KEEP_COLUMNS["drains"] == (
        "edge_id",
        "from_node",
        "to_node",
        "length_m",
        "shape",
        "diameter_m",
        "width_m",
        "height_m",
        "is_trunk",
        "beta_mean",
        "beta_sd",
        "confidence",
        "z_invert_up_m",
        "z_invert_dn_m",
        "slope",
    )


def test_simplify_keeps_the_elevations_as_numbers_not_strings() -> None:
    simplified = simplify_for_map(
        _drain_edges(), metric_crs=CRS, keep_columns=MAP_KEEP_COLUMNS["drains"]
    )
    for prop in DRAIN_ELEVATION_PROPS:
        assert prop in simplified.columns
        assert simplified[prop].dtype.kind == "f", f"{prop} must stay a float for deck.gl"
    assert simplified["z_invert_up_m"].iloc[0] == pytest.approx(8.32)


def test_exported_geojson_carries_the_inverts_and_keeps_the_adverse_bed(tmp_path: Path) -> None:
    """A real export, read back off disk the way the API serves it.

    Also pins that the exporter does not "fix" the adverse edge on the way out: the second
    pipe's downstream invert stays above its upstream one. The regrade is a pipeline decision
    (ADR-0048), and an exporter that silently repaired it here would hide the defect from the
    only view that can show it.
    """
    result = export_city(
        "testcity",
        metric_crs=CRS,
        out_dir=tmp_path,
        layers=("drains", "drain_nodes"),
        frames={"drains": _drain_edges(), "drain_nodes": _drain_nodes()},
    )
    assert result.missing == []

    payload = json.loads((tmp_path / "map" / "drains.geojson").read_text(encoding="utf-8"))
    props = {f["properties"]["edge_id"]: f["properties"] for f in payload["features"]}
    assert set(props) == {"MUM-E000001", "MUM-E000002"}

    first = props["MUM-E000001"]
    for prop in DRAIN_ELEVATION_PROPS:
        assert first[prop] is not None, f"{prop} reached the map layer as null"
    assert first["z_invert_up_m"] == pytest.approx(8.32)
    assert first["z_invert_dn_m"] == pytest.approx(7.82)
    # Columns the parquet has and the map layer is meant to drop stay dropped.
    assert "manning_n" not in first and "land_use" not in first

    adverse = props["MUM-E000002"]
    assert adverse["z_invert_dn_m"] > adverse["z_invert_up_m"], "the adverse bed was flattened"
    assert adverse["slope"] > 0, "the stored slope is the design slope and never goes negative"

    nodes = json.loads((tmp_path / "map" / "drain_nodes.geojson").read_text(encoding="utf-8"))
    node_props = {f["properties"]["node_id"]: f["properties"] for f in nodes["features"]}
    for prop in NODE_ELEVATION_PROPS:
        assert all(row[prop] is not None for row in node_props.values())
    assert node_props["MUM-N000001"]["z_ground_m"] > node_props["MUM-N000001"]["z_invert_m"]


def test_geometry_direction_is_what_the_inverts_are_draped_on(tmp_path: Path) -> None:
    """The up invert belongs to the first vertex, so the geometry must run from_node -> to_node.

    Verified on the real Mumbai graph at 49,770 of 49,770 edges (endpoint match 0.0000 m); this
    pins that the export does not reverse or re-order a line on the way through simplification,
    which would draw every pipe upside down without changing a single property value.
    """
    edges, nodes = _drain_edges(), _drain_nodes().set_index("node_id")
    export_city(
        "testcity",
        metric_crs=CRS,
        out_dir=tmp_path,
        layers=("drains",),
        frames={"drains": edges},
    )
    payload = json.loads((tmp_path / "map" / "drains.geojson").read_text(encoding="utf-8"))
    wgs84_nodes = nodes.to_crs("EPSG:4326")
    for feature in payload["features"]:
        line = feature["geometry"]["coordinates"]
        for end, node_id in (("from_node", 0), ("to_node", -1)):
            point = wgs84_nodes.geometry.loc[feature["properties"][end]]
            lon, lat = line[node_id]
            assert (lon, lat) == pytest.approx((point.x, point.y), abs=1e-6)


def test_the_export_marker_records_the_shape_the_layers_were_written_with(tmp_path: Path) -> None:
    """``map/EXPORT.json`` is how a deployed volume knows its layers predate the running code.

    The deployed entrypoint only rebuilds a city whose ``pipeline.json`` records a failure, so a
    change to :data:`MAP_KEEP_COLUMNS` otherwise ships in the image while the volume keeps
    serving the old columns - which is exactly what happened to the drain inverts on 2026-09-23,
    through a deployment that reported SUCCESS.
    """
    frame = gpd.GeoDataFrame(
        {
            "edge_id": ["MUM-E1"],
            "from_node": ["a"],
            "to_node": ["b"],
            "length_m": [40.0],
            "z_invert_up_m": [8.32],
            "z_invert_dn_m": [7.94],
            "slope": [0.0107],
            "diameter_m": [0.45],
            "geometry": [LineString([(72.84, 19.01), (72.841, 19.011)])],
        },
        crs="EPSG:4326",
    )
    export_city(city="mumbai", out_dir=tmp_path, layers=("drains",), frames={"drains": frame})

    marker = json.loads((tmp_path / "map" / "EXPORT.json").read_text(encoding="utf-8"))
    assert marker["fingerprint"] == map_export_fingerprint()
    assert marker["keep_columns"]["drains"] == list(MAP_KEEP_COLUMNS["drains"])


def test_the_fingerprint_moves_when_the_kept_columns_move() -> None:
    """A hand-bumped version number is a thing somebody has to remember on exactly the commit
    where they are thinking about columns and not about deployment. This cannot be forgotten."""
    before = map_export_fingerprint()
    kept = MAP_KEEP_COLUMNS["drains"]
    MAP_KEEP_COLUMNS["drains"] = (*kept, "a_new_column")
    try:
        assert map_export_fingerprint() != before
    finally:
        MAP_KEEP_COLUMNS["drains"] = kept
    assert map_export_fingerprint() == before
