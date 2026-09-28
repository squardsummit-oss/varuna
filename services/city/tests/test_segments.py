"""P1.6 — road segments: stable ids, DEM sampling and exposure weights."""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString, Point, Polygon
from varuna_city.segments import (
    _as_int,
    build_segments,
    classify_highway,
    edges_to_frame,
    osm_items,
    sample_dem_along,
    segments_near,
)

CRS = "EPSG:32643"
RES = 30.0
# A small metric grid roughly where MUM-CENTRAL sits in UTM 43N.
ORIGIN_X, ORIGIN_Y = 270_000.0, 2_105_000.0
TRANSFORM = Affine(RES, 0.0, ORIGIN_X, 0.0, -RES, ORIGIN_Y)
SHAPE = (40, 40)


def _dem() -> np.ndarray:
    """A plane tilting down to the south-east, so z varies along every segment."""
    rows, cols = np.indices(SHAPE)
    return (20.0 - 0.2 * rows - 0.1 * cols).astype(np.float64)


def _graph() -> nx.MultiDiGraph:
    """Four junctions, three streets: a primary road and two residential stubs.

    Node 1 -> 2 is the "KEM Hospital" street, node 3 -> 4 is the far residential one.
    """
    graph = nx.MultiDiGraph(crs=CRS)
    coords = {
        1: (ORIGIN_X + 100.0, ORIGIN_Y - 100.0),
        2: (ORIGIN_X + 400.0, ORIGIN_Y - 100.0),
        3: (ORIGIN_X + 100.0, ORIGIN_Y - 1000.0),
        4: (ORIGIN_X + 400.0, ORIGIN_Y - 1000.0),
        5: (ORIGIN_X + 400.0, ORIGIN_Y - 400.0),
    }
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    graph.add_edge(1, 2, 0, osmid=1001, highway="residential", lanes="2", oneway=False)
    graph.add_edge(2, 1, 0, osmid=1001, highway="residential", lanes="2", oneway=False)
    graph.add_edge(3, 4, 0, osmid=1002, highway="residential", lanes="2", oneway=False)
    graph.add_edge(2, 5, 0, osmid=[1003, 1004], highway=["primary", "primary_link"], oneway=True)
    return graph


def _assets() -> gpd.GeoDataFrame:
    """KEM Hospital, Parel - the asset that lifts the exposure weight of its street."""
    return gpd.GeoDataFrame(
        {"asset_kind": ["hospitals"], "name": ["KEM Hospital"]},
        geometry=[Point(ORIGIN_X + 250.0, ORIGIN_Y - 150.0)],
        crs=CRS,
    )


def _buildings() -> gpd.GeoDataFrame:
    """A dense block beside the hospital street."""
    squares = [
        Polygon(
            [
                (ORIGIN_X + 120.0 + 20.0 * i, ORIGIN_Y - 130.0),
                (ORIGIN_X + 132.0 + 20.0 * i, ORIGIN_Y - 130.0),
                (ORIGIN_X + 132.0 + 20.0 * i, ORIGIN_Y - 118.0),
                (ORIGIN_X + 120.0 + 20.0 * i, ORIGIN_Y - 118.0),
            ]
        )
        for i in range(12)
    ]
    return gpd.GeoDataFrame({"building": ["yes"] * len(squares)}, geometry=squares, crs=CRS)


def test_classify_highway_handles_lists_and_unknowns() -> None:
    assert classify_highway("motorway_link") == "motorway"
    assert classify_highway(["unclassified", "service"]) == "residential"
    assert classify_highway(None) == "service"


def test_edges_to_frame_builds_geometry_from_nodes() -> None:
    edges = edges_to_frame(_graph())
    assert len(edges) == 4
    assert str(edges.crs) == CRS
    assert edges.geometry.length.min() > 0


def test_segment_ids_are_stable_across_runs() -> None:
    graph = _graph()
    first = build_segments(graph, _dem(), TRANSFORM, _buildings(), _assets())
    second = build_segments(_graph(), _dem(), TRANSFORM, _buildings(), _assets())
    assert list(first["segment_id"]) == list(second["segment_id"])
    assert first["segment_id"].is_unique
    # The two-way street collapses to one segment: 4 edges in, 3 segments out.
    assert len(first) == 3
    assert all(sid.startswith("S") for sid in first["segment_id"])


def test_z_min_never_exceeds_z_mean() -> None:
    segments = build_segments(_graph(), _dem(), TRANSFORM)
    assert segments["z_min"].notna().all()
    assert (segments["z_min"] <= segments["z_mean"] + 1e-9).all()


def test_missing_dem_leaves_elevation_null() -> None:
    segments = build_segments(_graph())
    assert segments["z_min"].isna().all()
    assert segments["length_m"].min() > 0


def test_exposure_is_higher_near_kem_hospital() -> None:
    segments = build_segments(_graph(), _dem(), TRANSFORM, _buildings(), _assets())
    by_way = segments.set_index("osm_way_id")
    near = float(by_way.loc[1001, "exposure_weight"])
    far = float(by_way.loc[1002, "exposure_weight"])
    assert near > far
    assert 0.0 <= far <= near <= 1.0


def test_class_and_ward_attributes() -> None:
    wards = gpd.GeoDataFrame(
        {"name": ["F/South"]},
        geometry=[
            Polygon(
                [
                    (ORIGIN_X, ORIGIN_Y - 600.0),
                    (ORIGIN_X + 600.0, ORIGIN_Y - 600.0),
                    (ORIGIN_X + 600.0, ORIGIN_Y),
                    (ORIGIN_X, ORIGIN_Y),
                ]
            )
        ],
        crs=CRS,
    )
    segments = build_segments(_graph(), _dem(), TRANSFORM, wards=wards)
    classes = set(segments["class"])
    assert classes == {"residential", "primary"}
    assert "F/South" in set(segments["ward"].dropna())
    assert segments["ward"].isna().any()  # the far street sits outside the ward


def test_sample_dem_along_returns_nan_outside_the_grid() -> None:
    from shapely.geometry import LineString

    far = LineString([(0.0, 0.0), (100.0, 0.0)])
    z_min, z_mean = sample_dem_along(far, _dem(), TRANSFORM)
    assert np.isnan(z_min) and np.isnan(z_mean)


def test_segments_near_respects_max_distance() -> None:
    segments = build_segments(_graph(), _dem(), TRANSFORM)
    ids = segments_near(
        segments,
        [Point(ORIGIN_X + 200.0, ORIGIN_Y - 105.0), Point(ORIGIN_X + 5000.0, ORIGIN_Y)],
        max_distance_m=60.0,
    )
    assert ids[0] is not None
    assert ids[1] is None


@pytest.mark.parametrize("empty", [gpd.GeoDataFrame(geometry=[], crs=CRS)])
def test_empty_graph_returns_empty_table(empty: gpd.GeoDataFrame) -> None:
    segments = build_segments(empty, crs=CRS)
    assert segments.empty
    assert "segment_id" in segments.columns


def test_lane_count_does_not_depend_on_set_iteration_order() -> None:
    """SPEC.md rule 8: two runs of the pipeline must agree.

    OSMnx returns a collection of tag values when it simplifies several ways into one edge, and
    that collection is sometimes a set, whose iteration order changes with Python's per-process
    string hash seed. Taking the first item made `lanes` differ between runs on real Mumbai data.
    """
    assert _as_int({"2", "3", "4"}) == 4
    assert _as_int({"4", "3", "2"}) == 4
    assert _as_int(["2", "3"]) == _as_int(["3", "2"]) == 3
    # Non-numeric tag values are ignored, not crashed on, and an empty result stays None.
    assert _as_int({"2", "unknown"}) == 2
    assert _as_int({"unknown"}) is None
    assert _as_int(None) is None
    assert _as_int("2") == 2


def test_a_way_with_several_osm_names_is_stored_as_one_street_plus_its_aliases(tmp_path) -> None:
    """D-03: 95 of Mumbai's segments carried a stringified Python list as their name.

    Both shapes are built here because both occur: a live OSMnx fetch hands over a real list,
    and the GeoPackage cache every later build reads has no list type, so the same value comes
    back as text. The second is what `city/mumbai` actually holds. The written table is read back
    through GeoParquet, because `name_aliases` is a list column and one that cannot survive the
    round trip would be worse than no column at all.
    """
    edges = gpd.GeoDataFrame(
        {
            "u": [1, 2, 1],
            "v": [2, 3, 3],
            "key": [0, 0, 0],
            "osmid": [1, 2, 3],
            "highway": ["primary", "residential", "service"],
            "name": [["Tilak Bridge", "Tilak Road"], "['Sion Road', 'Gandhi Market Road']", None],
            "oneway": [False, False, False],
        },
        geometry=[
            LineString([(ORIGIN_X, ORIGIN_Y), (ORIGIN_X + 300.0, ORIGIN_Y)]),
            LineString([(ORIGIN_X + 300.0, ORIGIN_Y), (ORIGIN_X + 300.0, ORIGIN_Y - 400.0)]),
            LineString([(ORIGIN_X, ORIGIN_Y), (ORIGIN_X + 300.0, ORIGIN_Y - 400.0)]),
        ],
        crs=CRS,
    )

    segments = build_segments(edges, crs=CRS).set_index("segment_id")
    segments.to_parquet(tmp_path / "segments.parquet")
    back = gpd.read_parquet(tmp_path / "segments.parquet")

    assert [v for v in segments["name"] if v is not None and str(v).startswith("[")] == []
    assert segments.loc["S1-000", "name"] == "Tilak Bridge"
    assert list(segments.loc["S1-000", "name_aliases"]) == ["Tilak Road"]
    assert segments.loc["S2-000", "name"] == "Sion Road"
    assert list(segments.loc["S2-000", "name_aliases"]) == ["Gandhi Market Road"]
    assert segments["name"].isna()["S3-000"]  # unnamed stays empty, never "Unnamed road"
    assert list(segments.loc["S3-000", "name_aliases"]) == []
    # The list column survives GeoParquet, which is the only reason it is worth writing.
    assert [list(v) for v in back["name_aliases"]] == [["Tilak Road"], ["Gandhi Market Road"], []]


def _merged_ways_graph() -> nx.MultiDiGraph:
    """A graph shaped like a live OSMnx fetch: merged ways carry their tags as collections.

    Way 200 has two plain edges and is also the smallest way id of a merged edge (3 -> 4), so a
    merged edge that loses its way id also compacts the ordinals of way 200's plain edges - the
    shape behind the 839 Mumbai ids that kept their form but named another street.
    """
    graph = nx.MultiDiGraph(crs=CRS)
    for node in range(1, 8):
        graph.add_node(node, x=ORIGIN_X + 150.0 * node, y=ORIGIN_Y - 40.0 * (node % 3))
    two_way = {"highway": "residential", "lanes": "2", "oneway": False}
    graph.add_edge(1, 2, 0, osmid=200, **two_way)
    graph.add_edge(2, 1, 0, osmid=200, **two_way)
    graph.add_edge(2, 3, 0, osmid=200, **two_way)
    graph.add_edge(3, 2, 0, osmid=200, **two_way)
    merged = {"highway": ["residential", "service"], "lanes": ["2", "4"], "oneway": False}
    graph.add_edge(3, 4, 0, osmid=[300, 200], **merged)
    graph.add_edge(4, 3, 0, osmid=[200, 300], **merged)
    graph.add_edge(4, 5, 0, osmid={500, 400}, highway=["primary", "primary_link"], oneway=True)
    graph.add_edge(5, 6, 0, osmid=600, highway="tertiary", lanes=["3", "2"], oneway=True)
    graph.add_edge(6, 7, 0, osmid=[700, 701], highway="secondary", oneway=True)
    return graph


def test_segment_ids_survive_the_geopackage_round_trip(tmp_path) -> None:
    """A rebuild from ``osm.gpkg`` gives every street the id the live build gave it.

    The GeoPackage has no list type, so a merged way's ``osmid`` comes back as the text
    ``"[300, 200]"``. Reading that as "not an int" gave the way id 0: 1,247 Mumbai segments read
    ``S0-*`` after the first rebuild from cache, 839 more shifted ordinal, and every baked run,
    the segment index and the emulator kept the live build's ids. The same text shape broke
    ``highway`` (a merged residential street became ``service``) and ``lanes``.
    """
    from varuna_city.osm import OsmLayers, write_osm_gpkg

    live_edges = edges_to_frame(_merged_ways_graph())
    write_osm_gpkg(OsmLayers(city="test", crs=CRS, roads=live_edges), tmp_path)
    cached_edges = gpd.read_file(tmp_path / "osm.gpkg", layer="roads")
    # The fixture only proves something if the cache really holds text where the fetch held lists.
    assert isinstance(live_edges.loc[4, "osmid"], list)
    assert isinstance(cached_edges.loc[4, "osmid"], str)

    live = build_segments(live_edges, crs=CRS)
    cached = build_segments(cached_edges, crs=CRS)

    assert list(cached["segment_id"]) == list(live["segment_id"])
    assert not any(sid.startswith("S0-") for sid in cached["segment_id"])
    assert list(live["segment_id"]) == [
        "S200-000",
        "S200-001",
        "S200-002",
        "S400-000",
        "S600-000",
        "S700-000",
    ]
    for column in ("osm_way_id", "u", "v", "key", "class", "lanes", "oneway"):
        assert cached[column].equals(live[column]), column  # NaN-aware: lanes is often missing
    merged = cached.set_index("segment_id")
    assert merged.loc["S200-002", "class"] == "residential"
    assert merged.loc["S200-002", "lanes"] == 4
    assert merged.loc["S400-000", "class"] == "primary"
    assert merged.loc["S600-000", "lanes"] == 3


def test_osm_items_reads_every_shape_a_collection_arrives_in() -> None:
    """Live list, GeoPackage text, the feature layers' comma text, and a plain scalar."""
    assert osm_items([300, 200]) == [300, 200]
    assert sorted(osm_items({500, 400})) == [400, 500]
    assert osm_items("[300, 200]") == [300, 200]
    assert osm_items("['residential', 'service']") == ["residential", "service"]
    assert sorted(osm_items("{400, 500}")) == [400, 500]
    assert osm_items("300,200") == ["300", "200"]
    assert osm_items("300") == ["300"]
    assert osm_items(None) == [None]
    # A bracket that is not a list literal is a value, not a list.
    assert osm_items("[Closed] Link Road") == ["[Closed] Link Road"]
    assert classify_highway("['residential', 'service']") == "residential"
    assert _as_int("['2', '4']") == 4
