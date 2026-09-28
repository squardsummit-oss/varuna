"""Every road segment gets a name a person can read; none reads "Unnamed road".

A tiny synthetic city in UTM 43N, built into a temporary ``VARUNA_CITY_DIR``: one named street,
a lane off it, a way whose name is a list's text, a service road 1 km from a hotspot and a
residential street too far from anything to be placed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from varuna_api import street_names
from varuna_api.routers.route import name_streets

X, Y = 272_000.0, 2_103_000.0
"""A point near Dadar in EPSG:32643."""


def _write_city(root: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import LineString, Point

    root.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(
        {
            "segment_id": ["S-A", "S-LANE", "S-LIST", "S-SERVICE", "S-FAR"],
            "name": [
                "Dr Ambedkar Road",
                None,
                "['Tilak Road', 'Tilak Road', 'LBS Marg']",
                None,
                None,
            ],
            "class": ["primary", "residential", "secondary", "service", "residential"],
            "geometry": [
                LineString([(X, Y), (X + 100, Y), (X + 200, Y), (X + 300, Y)]),
                # Branches north off Dr Ambedkar Road at its second vertex.
                LineString([(X + 100, Y), (X + 100, Y + 80)]),
                LineString([(X, Y + 3_000), (X + 100, Y + 3_000)]),
                # 1 km east of the hotspot and more than 200 m from every named street.
                LineString([(X + 1_300, Y - 600), (X + 1_300, Y - 700)]),
                LineString([(X + 9_000, Y + 9_000), (X + 9_050, Y + 9_000)]),
            ],
        },
        crs="EPSG:32643",
    ).to_parquet(root / "segments.parquet")
    hotspots = gpd.GeoDataFrame(
        {"name": ["Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)"]},
        geometry=[Point(X + 300, Y - 650)],
        crs="EPSG:32643",
    ).to_crs("EPSG:4326")
    (root / "hotspots.geojson").write_text(hotspots.to_json(), encoding="utf-8")
    assets = gpd.GeoDataFrame(
        {"name": ["Dadar East", "KEM Hospital"], "kind": ["station", "hospital"]},
        geometry=[Point(X - 5_000, Y), Point(X + 1_300, Y - 650)],
        crs="EPSG:32643",
    ).to_crs("EPSG:4326")
    (root / "assets.geojson").write_text(assets.to_json(), encoding="utf-8")


@pytest.fixture
def city(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "city"
    _write_city(root / "mumbai")
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    street_names.clear_cache()
    yield root / "mumbai"
    street_names.clear_cache()


def test_each_kind_of_label(city: Path) -> None:
    names = street_names.build_street_names(city, "mumbai", "Mumbai")
    assert names.names == {
        "S-A": "Dr Ambedkar Road",
        "S-LANE": "off Dr Ambedkar Road",
        "S-LIST": "Tilak Road / LBS Marg",
        # The hotspot, not the hospital at the same spot: only hotspots and stations place a
        # street. The register's parenthesis is dropped.
        "S-SERVICE": "Service road near Hindmata junction",
        "S-FAR": "Residential street in Mumbai",
    }
    assert names.counts() == {"osm": 2, "off": 1, "near": 1, "in": 1}
    assert not any("nnamed" in value for value in names.names.values())
    # The proper noun inside each label, for a screen that words the rest in Hindi or Marathi.
    assert names.label("S-A") == ("osm", None)
    assert names.label("S-LANE") == ("off", "Dr Ambedkar Road")
    assert names.label("S-SERVICE") == ("near", "Hindmata junction")
    assert names.label("S-FAR") == ("in", "Mumbai")
    assert names.label("S-MISSING") is None


def test_a_station_places_a_street_when_it_is_nearest(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import LineString

    root = tmp_path / "mumbai"
    _write_city(root)
    gpd.GeoDataFrame(
        {"segment_id": ["S-W"], "name": [None], "class": ["tertiary"]},
        geometry=[LineString([(X - 5_600, Y), (X - 5_500, Y)])],
        crs="EPSG:32643",
    ).to_parquet(root / "segments.parquet")
    names = street_names.build_street_names(root, "mumbai", "Mumbai")
    assert names.get("S-W") == "Local road near Dadar East"


def test_a_hospital_places_a_street_only_when_no_hotspot_or_station_is_near(
    tmp_path: Path,
) -> None:
    import geopandas as gpd
    from shapely.geometry import LineString

    root = tmp_path / "mumbai"
    _write_city(root)
    gpd.GeoDataFrame(
        {"segment_id": ["S-E"], "name": [None], "class": ["residential"]},
        # 1.4 km from KEM Hospital, 2.4 km from the hotspot, far from every station.
        geometry=[LineString([(X + 2_700, Y - 600), (X + 2_700, Y - 700)])],
        crs="EPSG:32643",
    ).to_parquet(root / "segments.parquet")
    names = street_names.build_street_names(root, "mumbai", "Mumbai")
    assert names.get("S-E") == "Residential street near KEM Hospital"
    assert names.label("S-E") == ("near", "KEM Hospital")


def test_the_names_are_deterministic(city: Path) -> None:
    first = street_names.build_street_names(city, "mumbai", "Mumbai")
    again = street_names.build_street_names(city, "mumbai", "Mumbai")
    assert first.names == again.names
    assert first.kinds == again.kinds


def test_the_cache_is_rebuilt_only_when_the_city_changes(city: Path) -> None:
    first = street_names.street_names("mumbai")
    assert first is not None
    assert street_names.street_names("mumbai") is first
    tag = street_names.signature("mumbai")

    import geopandas as gpd

    frame = gpd.read_parquet(city / "segments.parquet")
    frame.loc[frame["segment_id"] == "S-FAR", "name"] = "Senapati Bapat Marg"
    frame.to_parquet(city / "segments.parquet")
    rebuilt = street_names.street_names("mumbai")
    assert rebuilt is not first
    assert rebuilt is not None and rebuilt.get("S-FAR") == "Senapati Bapat Marg"
    assert street_names.signature("mumbai") != tag


def test_an_unbuilt_city_has_no_names_and_never_says_unnamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path))
    street_names.clear_cache()
    assert street_names.street_names("chennai") is None
    assert street_names.street_names("../etc") is None
    label = street_names.display_name_for("chennai", "S-1", "Unnamed road", "service")
    assert label == "Service road in Chennai"
    assert street_names.display_name_for("chennai", "S-1", "Anna Salai") == "Anna Salai"


@pytest.mark.parametrize(
    "value",
    [
        "Dr Ambedkar Road",
        "['Lal Bahadur Shastri Marg', 'Sant Rohidas Marg']",
        "['A', 'A']",
        "[Closed] Link Road",
        "  Tilak Road  ",
        "",
        None,
        float("nan"),
        ["Tilak Road", None, "LBS Marg"],
    ],
)
def test_the_name_rule_is_the_drain_x_rays(value: object) -> None:
    """One street is written one way on every screen: the pipe rule and this one agree."""
    from varuna_pulse.health import _clean_name

    assert street_names.clean_name(value) == _clean_name(value)


def test_the_segments_layer_serves_a_display_name_on_every_feature(
    client: TestClient, city: Path
) -> None:
    maps = city / "map"
    maps.mkdir()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[72.84, 19.01], [72.841, 19.011]]},
            "properties": {"segment_id": sid, "name": name, "class": cls},
        }
        for sid, name, cls in [
            ("S-A", "Dr Ambedkar Road", "primary"),
            ("S-LANE", None, "residential"),
            ("S-NEW", None, "service"),
        ]
    ]
    (maps / "segments.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    res = client.get("/v1/city/mumbai/layers/segments")
    assert res.status_code == 200, res.text
    props = [f["properties"] for f in res.json()["features"]]
    assert [p["name"] for p in props] == ["Dr Ambedkar Road", None, None]
    assert [p["display_name"] for p in props] == [
        "Dr Ambedkar Road",
        "off Dr Ambedkar Road",
        # Not in segments.parquet: named by its own class, in the city.
        "Service road in Mumbai",
    ]
    # The parts a Hindi or Marathi screen words for itself; an OSM name carries none.
    assert [(p.get("display_kind"), p.get("display_anchor")) for p in props] == [
        (None, None),
        ("off", "Dr Ambedkar Road"),
        ("in", "Mumbai"),
    ]
    etag = res.headers["etag"]
    assert (
        client.get("/v1/city/mumbai/layers/segments", headers={"If-None-Match": etag}).status_code
        == 304
    )

    boxed = client.get(
        "/v1/city/mumbai/layers/segments", params={"bbox": "72.83,19.0,72.85,19.02"}
    ).json()
    assert [f["properties"]["display_name"] for f in boxed["features"]][1] == (
        "off Dr Ambedkar Road"
    )


def test_a_route_response_names_what_the_router_left_unnamed(city: Path) -> None:
    payload = {
        "run_id": "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.3-baked",
        "avoided": [
            {"segment_id": "S-LANE", "name": "Unnamed road", "depth_cm": 40.0},
            {"segment_id": "S-A", "name": "Dr Ambedkar Road", "depth_cm": 30.0},
        ],
        "reasons": [
            {"kind": "timing", "segment_id": "S-SERVICE", "name": "Unnamed road"},
            {"kind": "design", "segment_id": "S-A", "name": "Dr Ambedkar Road"},
        ],
    }
    named = name_streets(payload)
    assert [a["name"] for a in named["avoided"]] == ["off Dr Ambedkar Road", "Dr Ambedkar Road"]
    assert [r["name"] for r in named["reasons"]] == [
        "Service road near Hindmata junction",
        "Dr Ambedkar Road",
    ]


def _write_pipes(root: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import LineString

    gpd.GeoDataFrame(
        {
            "edge_id": ["P-A", "P-LANE", "P-TRUNK", "P-NOWHERE"],
            "road_class": ["primary", "residential", "secondary", "service"],
            "geometry": [
                # Under Dr Ambedkar Road.
                LineString([(X + 10, Y), (X + 90, Y)]),
                # Under the lane: 0 m from it at the midpoint, 40 m from Dr Ambedkar Road.
                LineString([(X + 100, Y + 10), (X + 100, Y + 70)]),
                # 350 m from every road and 300 m from the Hindmata register point.
                LineString([(X + 300, Y - 400), (X + 300, Y - 300)]),
                # Nothing within 1.5 km.
                LineString([(X + 20_000, Y + 20_000), (X + 20_050, Y + 20_000)]),
            ],
        },
        crs="EPSG:32643",
    ).to_parquet(root / "drain_edges.parquet")


def test_a_pipe_is_named_by_the_road_above_it_and_never_unnamed(city: Path) -> None:
    _write_pipes(city)
    names = street_names.build_pipe_names(
        city, street_names.build_street_names(city, "mumbai", "Mumbai")
    )
    assert names == {
        "P-A": "Dr Ambedkar Road",
        # The lane's own label, because the pipe runs under the lane and not the named road.
        "P-LANE": "off Dr Ambedkar Road",
        "P-TRUNK": "Secondary road near Hindmata junction",
        "P-NOWHERE": "Service road in Mumbai",
    }
    # Cached per process: a second call is the same dict.
    first = street_names.pipe_names("mumbai")
    assert first == names
    assert street_names.pipe_names("mumbai") is first


def test_the_drain_health_route_names_only_the_pipes_the_bake_left_unnamed(city: Path) -> None:
    from varuna_api.routers.depth import _name_unnamed_pipes

    _write_pipes(city)
    run = city.parent / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.3-baked"
    features = [
        {"properties": {"edge_id": "P-A", "street": None}},
        {"properties": {"edge_id": "P-LANE", "street": None, "display_name": "off Tilak Road"}},
        {"properties": {"edge_id": "P-TRUNK", "street": "Tilak Road"}},
        {"properties": {"edge_id": "P-GONE", "street": None}},
    ]
    served = _name_unnamed_pipes(run, features)
    assert [f["properties"].get("display_name") for f in served] == [
        "Dr Ambedkar Road",
        "off Tilak Road",  # what the bake wrote wins
        None,  # its own street already names it
        None,  # a pipe the city build does not carry is left for the screen to say so
    ]
    # The cached product is never written into.
    assert "display_name" not in features[0]["properties"]
    assert served[2] is features[2]
    assert not any("nnamed" in str(f["properties"].get("display_name")) for f in served)
