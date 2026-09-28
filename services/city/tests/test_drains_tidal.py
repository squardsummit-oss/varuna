"""Tidal outfalls from the sea mask (``drains.build_drain_graph`` with ``sea_mask``).

A 30 m grid in EPSG:32643 with the sea in its west columns. Two nullahs: one ends on the shore,
one ends inland. Nothing here reads ``city/``.
"""

from __future__ import annotations

from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
from pyproj import Transformer
from rasterio.transform import Affine
from shapely.geometry import LineString
from varuna_city.drains import TIDAL_SNAP_M, build_drain_graph
from varuna_city.sea import SeaConfig

CRS = "EPSG:32643"
RES = 30.0
SIZE = 40
X0, Y0 = 270_000.0, 2_100_000.0 + SIZE * RES
TRANSFORM = Affine(RES, 0.0, X0, 0.0, -RES, Y0)
SEA_COLS = 4
SEED = 2019


def xy(row: float, col: float) -> tuple[float, float]:
    return X0 + (col + 0.5) * RES, Y0 - (row + 0.5) * RES


def lonlat(row: float, col: float) -> tuple[float, float]:
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(*xy(row, col))
    return float(lon), float(lat)


def dem() -> np.ndarray:
    """Sea at -1 m in cols 0-3; land rising east from 1 m at the shore."""
    z = np.tile(1.0 + 0.25 * np.arange(SIZE, dtype=np.float64), (SIZE, 1))
    z[:, :SEA_COLS] = -1.0
    return z


def sea() -> np.ndarray:
    mask = np.zeros((SIZE, SIZE), dtype=bool)
    mask[:, :SEA_COLS] = True
    return mask


def config(tidal_outfalls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="tst",
        code="TST",
        crs_string=CRS,
        tidal_outfalls=tidal_outfalls or [],
        design_intensity_mm_h=SimpleNamespace(legacy=25.0, upgraded=50.0),
    )


def inputs(*, shore_road: bool = False) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Roads across the grid and two nullahs: A ends on the shore (col 4), B inland (col 20).

    ``shore_road`` adds a promenade along the shore from row 25 to row 35, joined to the grid at
    row 30: a street on the sea that no nullah ends at.
    """
    lines = [
        LineString([xy(10, 5), xy(10, 38)]),
        LineString([xy(30, 21), xy(30, 38)]),
        LineString([xy(5, 20), xy(35, 20)]),
    ]
    if shore_road:
        lines += [LineString([xy(25, 5), xy(35, 5)]), LineString([xy(30, 5), xy(30, 20)])]
    roads = gpd.GeoDataFrame(
        {
            "segment_id": [f"S{k + 1}" for k in range(len(lines))],
            "road_class": ["primary"] * len(lines),
        },
        geometry=lines,
        crs=CRS,
    )
    water = gpd.GeoDataFrame(
        {"name": ["Nala A", "Nala B"]},
        geometry=[
            LineString([xy(12, 38), xy(12, SEA_COLS)]),  # to the shore
            LineString([xy(28, 38), xy(28, 20)]),  # ends 16 cells inland
        ],
        crs=CRS,
    )
    return roads, water


def build(*, shore_road: bool = False, **kwargs):
    roads, water = inputs(shore_road=shore_road)
    kwargs.setdefault("config", config())
    return build_drain_graph(
        roads,
        dem(),
        TRANSFORM,
        water,
        None,
        None,
        np.full((SIZE, SIZE), 0.8),
        seed=SEED,
        **kwargs,
    )


def outfalls(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return nodes[nodes["is_outfall"].astype(bool)].sort_values("cell_col")


def test_the_outfall_on_the_shore_is_tidal_and_the_inland_one_is_not() -> None:
    nodes, _ = build(sea_mask=sea(), sea_config=SeaConfig())
    out = outfalls(nodes)
    assert len(out) == 2
    shore, inland = out.iloc[0], out.iloc[1]
    assert shore["cell_col"] <= SEA_COLS + 1
    assert shore["tidal"] and shore["boundary_type"] == "tide"
    assert not inland["tidal"] and inland["boundary_type"] == "river"
    tidal = nodes.attrs["tidal"]
    assert tidal["tidal_outfalls"] == 1
    assert tidal["outfalls"][0]["node_id"] == shore["node_id"]
    assert tidal["outfalls"][0]["sea_distance_cells"] <= 2


def test_a_tidal_outfall_invert_is_the_configured_level() -> None:
    nodes, _ = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_invert_m=0.0))
    shore = outfalls(nodes).iloc[0]
    assert shore["z_invert_m"] == pytest.approx(0.0)
    assert shore["z_ground_m"] > 0.0

    deeper, _ = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_invert_m=-0.5))
    assert outfalls(deeper).iloc[0]["z_invert_m"] == pytest.approx(-0.5)


def test_a_tidal_invert_is_never_above_the_ground() -> None:
    nodes, _ = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_invert_m=5.0))
    shore = outfalls(nodes).iloc[0]
    assert shore["z_invert_m"] == pytest.approx(shore["z_ground_m"])
    assert nodes.attrs["tidal"]["inverts_capped_at_ground"] == 1


def test_the_tidal_level_moves_only_the_outfall_invert() -> None:
    """Every other invert stays at its cover, whatever the tidal level.

    The level is set before the slope pass, but that pass only raises inverts and the cover clamp
    after it returns every one of them to ``ground - depth`` (ADR-0048: inverts sit at a fixed
    cover and are never deepened). So an edge into a tidal outfall can run adverse, and the solver,
    which is head-driven, is what handles it. This pins that, so a change to it is a decision.
    """
    level_0, edges_0 = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_invert_m=0.0))
    level_5, edges_5 = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_invert_m=-0.5))
    shore = outfalls(level_0).iloc[0]["node_id"]
    moved = level_0["z_invert_m"] != level_5["z_invert_m"]
    assert list(level_0.loc[moved, "node_id"]) == [shore]
    others = level_0[~moved]
    depth = others["z_ground_m"] - others["z_invert_m"]
    assert depth.round(3).isin([1.5, 3.0]).all()
    into_0 = edges_0[edges_0["to_node"] == shore]
    into_5 = edges_5[edges_5["to_node"] == shore]
    assert (into_0["z_invert_up_m"].to_numpy() == into_5["z_invert_up_m"].to_numpy()).all()
    assert np.allclose(into_0["z_invert_dn_m"], 0.0)


def test_the_distance_rule_is_the_configured_number_of_cells() -> None:
    """With ``tidal_outfall_cells=0`` only an outfall on a sea cell would be tidal: none is."""
    nodes, _ = build(sea_mask=sea(), sea_config=SeaConfig(tidal_outfall_cells=0))
    assert not outfalls(nodes)["tidal"].any()


def test_a_config_point_near_a_tidal_outfall_hangs_its_flap_gate() -> None:
    lon, lat = lonlat(12, SEA_COLS + 3)  # about 90 m from the shore outfall
    spec = SimpleNamespace(id="TST-GATE", name="Gate", lon=lon, lat=lat, flap_gate=True)
    nodes, _ = build(config=config([spec]), sea_mask=sea(), sea_config=SeaConfig())
    shore = outfalls(nodes).iloc[0]
    assert shore["flap_gate"]
    assert shore["outfall_id"] == "TST-GATE"
    snapped = nodes.attrs["tidal"]["config_snapped"]
    assert [s["outfall_id"] for s in snapped] == ["TST-GATE"]
    assert snapped[0]["distance_m"] <= TIDAL_SNAP_M


def test_a_config_point_far_from_every_tidal_outfall_is_dropped_not_snapped() -> None:
    """The Worli case: the old snap put its flap gate on an outfall 2.8 km away."""
    lon, lat = lonlat(35, 36)  # about 1 km from the shore outfall
    spec = SimpleNamespace(id="TST-FAR", name="Far", lon=lon, lat=lat, flap_gate=True)
    nodes, _ = build(config=config([spec]), sea_mask=sea(), sea_config=SeaConfig())
    assert not nodes["flap_gate"].any()
    assert (nodes["outfall_id"] != "TST-FAR").all()
    dropped = nodes.attrs["tidal"]["config_dropped"]
    assert dropped[0]["outfall_id"] == "TST-FAR"
    assert dropped[0]["nearest_tidal_outfall_m"] > TIDAL_SNAP_M
    assert dropped[0]["nearest_node_sea_distance_cells"] > 2
    # and it made no outfall of its own
    assert len(outfalls(nodes)) == 2


def test_a_config_point_on_the_shore_with_no_trunk_near_it_is_dropped_too() -> None:
    """Chennai's ADYAR: a node 2 cells from the sea lies 210 m from the point, but no nullah ends
    there. The point is an illustrative edge marker, not a surveyed pipe mouth, so it makes no
    outfall; the drop records the shore node so the report can say what was near."""
    lon, lat = lonlat(30, SEA_COLS + 1)  # on the promenade, about 540 m from nullah A's mouth
    spec = SimpleNamespace(id="TST-SHORE", name="Shore", lon=lon, lat=lat, flap_gate=True)
    nodes, _ = build(shore_road=True, config=config([spec]), sea_mask=sea(), sea_config=SeaConfig())
    dropped = nodes.attrs["tidal"]["config_dropped"]
    assert [d["outfall_id"] for d in dropped] == ["TST-SHORE"]
    assert dropped[0]["nearest_node_sea_distance_cells"] <= 2
    assert dropped[0]["nearest_node_m"] < TIDAL_SNAP_M < dropped[0]["nearest_tidal_outfall_m"]
    assert (nodes["outfall_id"] != "TST-SHORE").all()
    assert nodes.attrs["tidal"]["tidal_outfalls"] == 1


def test_without_a_sea_mask_the_config_points_make_the_tidal_outfalls() -> None:
    """A city built before the sea step keeps the behaviour it had."""
    lon, lat = lonlat(35, 36)
    spec = SimpleNamespace(id="TST-LEGACY", name="Legacy", lon=lon, lat=lat, flap_gate=True)
    nodes, _ = build(config=config([spec]))
    tidal = nodes[nodes["tidal"].astype(bool)]
    assert list(tidal["outfall_id"]) == ["TST-LEGACY"]
    assert tidal.iloc[0]["flap_gate"]
    assert "tidal" not in nodes.attrs
    shore = outfalls(nodes).iloc[0]
    assert not shore["tidal"]  # the shore outfall is a river outfall without the mask


def test_a_sea_mask_on_another_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="share a grid"):
        build(sea_mask=np.zeros((SIZE, SIZE + 1), dtype=bool), sea_config=SeaConfig())


def test_the_build_is_deterministic_with_a_sea() -> None:
    a_nodes, a_edges = build(sea_mask=sea(), sea_config=SeaConfig())
    b_nodes, b_edges = build(sea_mask=sea(), sea_config=SeaConfig())
    assert a_nodes.drop(columns="geometry").equals(b_nodes.drop(columns="geometry"))
    assert a_edges.drop(columns="geometry").equals(b_edges.drop(columns="geometry"))
    assert a_nodes.attrs["tidal"] == b_nodes.attrs["tidal"]
