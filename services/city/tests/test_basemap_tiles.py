"""The offline PMTiles basemap (task P9.10): tile arithmetic, content, attribution, determinism.

Built from a handful of synthetic features around Hindmata, so the tests never need a real
Mumbai build; the real archive is measured by running the module, not here.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mapbox_vector_tile
import numpy as np
import pytest
import shapely
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import Compression, TileType
from pyproj import Transformer
from varuna_city.basemap_tiles import (
    ATTRIBUTION,
    Layer,
    _first,
    build,
    lonlat_to_tile,
    tile_bounds,
    tiles_covering,
)

HINDMATA = (72.8421, 19.0101)
BBOX = (72.835, 19.005, 72.850, 19.018)
TO_MERC = Transformer.from_crs(4326, 3857, always_xy=True)


def _merc(lon: float, lat: float) -> tuple[float, float]:
    x, y = TO_MERC.transform(lon, lat)
    return float(x), float(y)


def _layers() -> list[Layer]:
    x, y = _merc(*HINDMATA)
    road = shapely.LineString([(x - 300, y), (x + 300, y)])
    lane = shapely.LineString([(x, y - 200), (x, y + 200)])
    building = shapely.box(x + 20, y + 20, x + 60, y + 60)
    water = shapely.box(x - 500, y - 500, x - 350, y - 350)
    return [
        Layer("water", np.array([water]), [{"kind": "open water"}], 11),
        Layer(
            "roads",
            np.array([road, lane]),
            [{"class": "primary", "name": "Dr Babasaheb Ambedkar Road"}, {"class": "residential"}],
            11,
            road_class=np.array(["primary", "residential"]),
        ),
        Layer("buildings", np.array([building]), [{}], 14),
    ]


@contextmanager
def _reader(path: Path) -> Iterator[Reader]:
    with path.open("rb") as handle:
        yield Reader(MmapSource(handle))


def _tile(reader: Reader, z: int, lon: float, lat: float) -> dict[str, Any]:
    x, y = lonlat_to_tile(lon, lat, z)
    raw = reader.get(z, x, y)
    assert raw is not None, f"no tile at {z}/{x}/{y}"
    decoded: dict[str, Any] = mapbox_vector_tile.decode(gzip.decompress(raw))
    return decoded


def test_tile_arithmetic_agrees_with_itself() -> None:
    z = 15
    x, y = lonlat_to_tile(*HINDMATA, z)
    minx, miny, maxx, maxy = tile_bounds(z, x, y)
    px, py = _merc(*HINDMATA)
    assert minx <= px <= maxx
    assert miny <= py <= maxy
    assert (x, y) in set(tiles_covering(BBOX, z))


def test_archive_carries_the_layers_and_the_attribution(tmp_path: Path) -> None:
    out = tmp_path / "basemap.pmtiles"
    result = build(_layers(), BBOX, out, min_zoom=12, max_zoom=15)
    assert result.tiles > 0
    with _reader(out) as reader:
        header = reader.header()
        metadata = reader.metadata()
        assert header["tile_type"] == TileType.MVT
        assert header["tile_compression"] == Compression.GZIP
        assert header["min_zoom"] == 12
        assert header["max_zoom"] == 15
        # ODbL and CC BY both require the credit; it travels inside the file.
        assert metadata["attribution"] == ATTRIBUTION
        assert "OpenStreetMap contributors" in ATTRIBUTION
        assert "ESA WorldCover" in ATTRIBUTION

        tile = _tile(reader, 15, *HINDMATA)
        assert {"roads", "buildings"} <= set(tile)
        names = {f["properties"].get("name") for f in tile["roads"]["features"]}
        assert "Dr Babasaheb Ambedkar Road" in names

        # Buildings are held back below zoom 14, and only arterials are drawn at 12.
        low = _tile(reader, 12, *HINDMATA)
        assert "buildings" not in low
        assert {f["properties"]["class"] for f in low["roads"]["features"]} == {"primary"}


def test_two_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build(_layers(), BBOX, tmp_path / "a.pmtiles", min_zoom=12, max_zoom=15)
    second = build(_layers(), BBOX, tmp_path / "b.pmtiles", min_zoom=12, max_zoom=15)
    assert first.path.read_bytes() == second.path.read_bytes()


def test_an_empty_box_is_refused_rather_than_written(tmp_path: Path) -> None:
    # A box well east of every feature: an archive of nothing would draw a blank basemap and
    # look like a city with no streets, so the build says so instead.
    out = tmp_path / "e.pmtiles"
    with pytest.raises(ValueError, match="Nothing to draw"):
        build(_layers(), (72.95, 19.2, 72.96, 19.21), out, min_zoom=12, max_zoom=13)
    assert not out.exists()


def test_empty_tiles_are_skipped(tmp_path: Path) -> None:
    # The AOI box is larger than the features: tiles over nothing are not written at all.
    result = build(
        _layers(), (72.80, 18.98, 72.90, 19.05), tmp_path / "s.pmtiles", min_zoom=15, max_zoom=15
    )
    covering = len(list(tiles_covering((72.80, 18.98, 72.90, 19.05), 15)))
    assert 0 < result.tiles < covering


@pytest.mark.parametrize("value", ["['primary', 'secondary']", "primary"])
def test_merged_osm_tags_keep_the_first(value: str) -> None:
    assert _first(value) == "primary"
    assert _first(None) == ""
    assert _first(float("nan")) == ""


def test_gzip_members_carry_no_timestamp(tmp_path: Path) -> None:
    out = tmp_path / "t.pmtiles"
    build(_layers(), BBOX, out, min_zoom=15, max_zoom=15)
    with _reader(out) as reader:
        x, y = lonlat_to_tile(*HINDMATA, 15)
        raw = reader.get(15, x, y)
    assert raw is not None
    # Bytes 4-7 of a gzip member are its mtime; zero is what makes rebuilds identical.
    assert raw[4:8] == b"\x00\x00\x00\x00"
