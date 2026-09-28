"""Intertidal land is not a street (``intertidal_mask.tif``, the coast wall's intertidal zone).

The Twin keeps mangroves, wetland and water behind the coast wall as land, so at high water the
tide walks onto them - on Mumbai 25 cells at the 08:40 cycle's +0.097 m, 124 at +1.236 m and 346
at the +2.218 m crest. A street whose 15 m buffer touched one read that seawater as its depth:
Kalina Kurla Road 33 cm and the Mahim Causeway 25 cm at the crest. These tests pin that the
segment index and the hotspot windows leave intertidal cells out exactly as they leave out the
sea, that the index's cache key refuses an index built before the raster, and that a city with
no intertidal land reads as it always did. Every city is a temporary folder; nothing here reads
or writes ``city/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString
from varuna_products import depth as D
from varuna_products.segment_table import sample_segments

CRS = "EPSG:32643"
RES = 30.0
X0, Y0 = 270_000.0, 2_105_000.0
TRANSFORM = (RES, 0.0, X0, 0.0, -RES, Y0)
SHAPE = (20, 20)
SEA_COLS = 5  # cols 0-4 are sea
MANGROVE_COLS = (5, 8)  # cols 5-7 are intertidal mangroves


def _sea() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[:, :SEA_COLS] = True
    return mask


def _mangroves() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[:, MANGROVE_COLS[0] : MANGROVE_COLS[1]] = True
    return mask


def _x(col: float) -> float:
    return X0 + (col + 0.5) * RES


def _y(row: float) -> float:
    return Y0 - (row + 0.5) * RES


def _streets() -> gpd.GeoDataFrame:
    """A road behind the mangroves, an inland street, and a causeway across the mangroves."""
    behind = X0 + MANGROVE_COLS[1] * RES + 5.0  # 5 m inside the first dry column
    return gpd.GeoDataFrame(
        {"segment_id": ["BEHIND", "INLAND", "CAUSEWAY"]},
        geometry=[
            # its 15 m buffer reaches 10 m back onto the last mangrove column
            LineString([(behind, _y(2)), (behind, _y(17))]),
            LineString([(_x(14), _y(2)), (_x(14), _y(17))]),
            # along the middle mangrove column: its whole buffer is intertidal
            LineString([(_x(6), _y(4)), (_x(6), _y(12))]),
        ],
        crs=CRS,
    )


def _write(root: Path, name: str, codes: np.ndarray) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        root / name,
        "w",
        driver="GTiff",
        height=codes.shape[0],
        width=codes.shape[1],
        count=1,
        dtype="uint8",
        crs=CRS,
        transform=Affine(*TRANSFORM),
    ) as dst:
        dst.write(codes.astype("uint8"), 1)


def _cells(index, segment: str) -> np.ndarray:
    ids, offsets, cells = index
    k = ids.index(segment)
    return cells[offsets[k] : offsets[k + 1]]


def _high_water() -> np.ndarray:
    """Three steps: 3 m of sea, the tide 35 cm deep on the mangroves, the streets dry."""
    depth = np.zeros((3, *SHAPE), dtype=np.float64)
    depth[:, _sea()] = 3.0
    depth[:, _mangroves()] = 0.35
    return depth


# ============================================================================ segment index
def test_a_flooded_mangrove_is_not_read_by_a_street_whose_buffer_touches_it() -> None:
    sea_only = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=_sea())
    coast = D.build_segment_cell_index(
        _streets(), TRANSFORM, SHAPE, sea=_sea(), intertidal=_mangroves()
    )
    flat = _mangroves().ravel()
    ids = coast[0]

    # without the raster the road behind the mangroves samples one of them ...
    assert flat[_cells(sea_only, "BEHIND")].any()
    before = sample_segments(_high_water(), sea_only)
    assert np.allclose(before[:, ids.index("BEHIND")], 35.0)
    # ... with it, only its own dry cells, so it reads the street and not the tide
    assert _cells(coast, "BEHIND").size > 0
    assert not flat[_cells(coast, "BEHIND")].any()
    after = sample_segments(_high_water(), coast)
    assert np.all(after[:, ids.index("BEHIND")] == 0.0)
    # an inland street is untouched
    assert np.array_equal(_cells(coast, "INLAND"), _cells(sea_only, "INLAND"))


def test_a_street_whose_whole_buffer_is_intertidal_reads_dry_like_a_pier() -> None:
    sea_only = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=_sea())
    coast = D.build_segment_cell_index(
        _streets(), TRANSFORM, SHAPE, sea=_sea(), intertidal=_mangroves()
    )
    ids = coast[0]
    assert _cells(sea_only, "CAUSEWAY").size > 0
    assert _cells(coast, "CAUSEWAY").size == 0
    assert np.allclose(sample_segments(_high_water(), sea_only)[:, ids.index("CAUSEWAY")], 35.0)
    assert np.all(sample_segments(_high_water(), coast)[:, ids.index("CAUSEWAY")] == 0.0)


def test_intertidal_land_alone_is_left_out_without_a_sea() -> None:
    plain = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE)
    inter = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, intertidal=_mangroves())
    flat = _mangroves().ravel()
    for segment in ("BEHIND", "INLAND", "CAUSEWAY"):
        kept = {c for c in _cells(plain, segment) if not flat[c]}
        assert set(_cells(inter, segment)) == kept


def test_no_intertidal_land_builds_the_index_it_always_built() -> None:
    """The no-sea and no-wall paths: ``None`` or an all-zero raster changes nothing, bit for bit."""
    plain = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE)
    empty = D.build_segment_cell_index(
        _streets(), TRANSFORM, SHAPE, intertidal=np.zeros(SHAPE, bool)
    )
    sea_only = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=_sea())
    sea_empty = D.build_segment_cell_index(
        _streets(), TRANSFORM, SHAPE, sea=_sea(), intertidal=np.zeros(SHAPE, bool)
    )
    for a, b in ((plain, empty), (sea_only, sea_empty)):
        assert a[0] == b[0]
        assert np.array_equal(a[1], b[1]) and a[1].dtype == b[1].dtype
        assert np.array_equal(a[2], b[2]) and a[2].dtype == b[2].dtype


def test_an_intertidal_mask_on_another_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="intertidal"):
        D.build_segment_cell_index(
            _streets(), TRANSFORM, SHAPE, intertidal=np.zeros((20, 21), bool)
        )


# ============================================================================ the cache key
def test_an_index_built_before_the_raster_is_refused_until_the_build_removes_it(
    tmp_path: Path,
) -> None:
    _streets().to_parquet(tmp_path / "segments.parquet")
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    before = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["intertidal_sha256"]) == D.NO_INTERTIDAL_DIGEST
    stale = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()

    _write(tmp_path, D.INTERTIDAL_MASK_FILE, _mangroves().astype(np.uint8))
    with pytest.raises(D.StaleSegmentIndex, match="intertidal") as refused:
        D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert "built under sea" not in str(refused.value), "only the intertidal digest differs"
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == stale, "a read never rewrites"

    # The city build drops the index when it rewrites intertidal_mask.tif; the next read builds.
    (tmp_path / D.SEGMENT_CELLS_FILE).unlink()
    after = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert _cells(after, "CAUSEWAY").size == 0 < _cells(before, "CAUSEWAY").size
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["intertidal_sha256"]) == D.intertidal_mask_digest(_mangroves())
    written = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()
    again = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == written
    assert again[0] == after[0]

    # and an index built under intertidal land is refused once the raster says there is none
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, np.zeros(SHAPE, np.uint8))
    with pytest.raises(D.StaleSegmentIndex, match="intertidal"):
        D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)


def _write_old_index(root: Path) -> bytes:
    """An index as the writer before intertidal exclusion left it: no ``intertidal_sha256``."""
    index = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=_sea())
    np.savez_compressed(
        root / D.SEGMENT_CELLS_FILE,
        segment_ids=np.array(index[0], dtype=object),
        offsets=index[1],
        cells=index[2],
        segments_sha256=np.array(D.segment_ids_digest(index[0])),
        sea_sha256=np.array(D.sea_mask_digest(_sea())),
    )
    return (root / D.SEGMENT_CELLS_FILE).read_bytes()


def test_an_index_from_before_the_key_is_refused_by_intertidal_land(tmp_path: Path) -> None:
    _streets().to_parquet(tmp_path / "segments.parquet")
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    old = _write_old_index(tmp_path)
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, _mangroves().astype(np.uint8))
    with pytest.raises(D.StaleSegmentIndex, match="intertidal"):
        D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == old


def test_an_all_zero_raster_keeps_an_index_from_before_the_key(tmp_path: Path) -> None:
    """A city with no wall writes an all-zero raster; its index excludes the same cells either
    way, so it is used as it is rather than refused."""
    _streets().to_parquet(tmp_path / "segments.parquet")
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    old = _write_old_index(tmp_path)
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, np.zeros(SHAPE, np.uint8))
    got = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == old
    assert _cells(got, "CAUSEWAY").size > 0


def test_the_raster_reads_only_code_one_and_the_digest_ignores_an_empty_mask(
    tmp_path: Path,
) -> None:
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[0, 0], codes[0, 1] = 1, 255
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, codes)
    mask = D.city_intertidal_mask(tmp_path, SHAPE)
    assert mask is not None and mask[0, 0] and not mask[0, 1]
    assert int(mask.sum()) == 1
    assert D.city_intertidal_mask(tmp_path / "nowhere") is None
    with pytest.raises(ValueError):
        D.city_intertidal_mask(tmp_path, (20, 21))
    assert D.intertidal_mask_digest(None) == D.NO_INTERTIDAL_DIGEST
    assert D.intertidal_mask_digest(np.zeros(SHAPE, bool)) == D.NO_INTERTIDAL_DIGEST
    assert D.intertidal_mask_digest(mask) != D.NO_INTERTIDAL_DIGEST


# ============================================================================ hotspot windows
def _register_at(root: Path, name: str, row: int, col: int) -> None:
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(_x(col), _y(row))
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "hotspot_id": name,
            "name": name,
            "slug": name,
            "sourced": True,
            "source_url": f"https://example.invalid/{name}",
        },
    }
    (root / "hotspots.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8"
    )


def _rank(root: Path, depth: np.ndarray) -> dict:
    from varuna_products.hotspots import rank_hotspots
    from varuna_schemas.constants import IST

    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    times = tuple(t0 + timedelta(minutes=5 * k) for k in range(depth.shape[0]))
    ranked = rank_hotspots(depth, times, root, TRANSFORM, CRS, "TEST-RUN", attribution=False)
    assert len(ranked) == 1
    return ranked[0]


def test_a_junction_behind_the_mangroves_reads_its_street_not_the_tide(tmp_path: Path) -> None:
    _register_at(tmp_path, "behind-the-mangroves", 10, MANGROVE_COLS[1] + 1)
    depth = _high_water()
    depth[:, :, MANGROVE_COLS[1] :] = 0.10  # 10 cm on the streets
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    assert _rank(tmp_path, depth)["peak_depth_cm"] == pytest.approx(35.0), "the control"
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, _mangroves().astype(np.uint8))
    assert _rank(tmp_path, depth)["peak_depth_cm"] == pytest.approx(10.0)


def test_a_junction_whose_window_is_all_sea_and_mangrove_reads_dry(tmp_path: Path) -> None:
    _register_at(tmp_path, "in-the-mangroves", 10, 6)
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[:, SEA_COLS:12] = 1  # every cell of the window that is not sea is intertidal
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, codes)
    depth = np.full((3, *SHAPE), 0.5)
    assert _rank(tmp_path, depth)["peak_depth_cm"] == 0.0


def test_an_all_zero_raster_ranks_as_no_raster(tmp_path: Path) -> None:
    _register_at(tmp_path, "behind-the-mangroves", 10, MANGROVE_COLS[1] + 1)
    _write(tmp_path, D.SEA_MASK_FILE, _sea().astype(np.uint8))
    without = _rank(tmp_path, _high_water())
    _write(tmp_path, D.INTERTIDAL_MASK_FILE, np.zeros(SHAPE, np.uint8))
    assert _rank(tmp_path, _high_water()) == without
