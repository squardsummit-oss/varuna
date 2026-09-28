"""The sea is not a street (wave B coastline).

The Twin holds the sea at the tide's level, so its depth there is metres of seawater. Read as a
street's depth it put up to 139 cm on Carter Road, Danda Seaface and the Mithi bridges; drawn
through the depth ramp it painted the bay "rescue vehicles only". These tests pin the three
places the products leave ``sea_mask.tif`` out: the segment index, the hotspot windows and the
depth PNGs. Every city is a temporary folder; nothing here reads or writes ``city/``.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from PIL import Image
from shapely.geometry import LineString
from varuna_products import depth as D

CRS = "EPSG:32643"
RES = 30.0
X0, Y0 = 270_000.0, 2_105_000.0
TRANSFORM = (RES, 0.0, X0, 0.0, -RES, Y0)
SHAPE = (20, 20)
SEA_COLS = 5


def _sea() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[:, :SEA_COLS] = True
    return mask


def _write_sea(root: Path, codes: np.ndarray) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        root / D.SEA_MASK_FILE,
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


def _x(col: float) -> float:
    return X0 + (col + 0.5) * RES


def _y(row: float) -> float:
    return Y0 - (row + 0.5) * RES


def _streets() -> gpd.GeoDataFrame:
    """A promenade along the shore, an inland street, and a pier standing on the sea."""
    return gpd.GeoDataFrame(
        {"segment_id": ["PROMENADE", "INLAND", "PIER"]},
        geometry=[
            # 5 m inside the first land column, so its 15 m buffer reaches 10 m onto the sea
            LineString([(X0 + SEA_COLS * RES + 5.0, _y(2)), (X0 + SEA_COLS * RES + 5.0, _y(17))]),
            LineString([(_x(12), _y(2)), (_x(12), _y(17))]),
            LineString([(_x(1), _y(8)), (_x(3), _y(8))]),
        ],
        crs=CRS,
    )


def _cells(index, segment: str) -> np.ndarray:
    ids, offsets, cells = index
    k = ids.index(segment)
    return cells[offsets[k] : offsets[k + 1]]


# ============================================================================ segment index
def test_sea_cells_are_left_out_of_every_segment() -> None:
    sea = _sea()
    plain = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE)
    coast = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=sea)
    flat_sea = sea.ravel()

    # the promenade's 15 m buffer reaches the sea; with the mask it keeps only its land cells
    assert flat_sea[_cells(plain, "PROMENADE")].any()
    assert _cells(coast, "PROMENADE").size > 0
    assert not flat_sea[_cells(coast, "PROMENADE")].any()
    assert set(_cells(coast, "PROMENADE")) == {
        c for c in _cells(plain, "PROMENADE") if not flat_sea[c]
    }
    # an inland street is untouched
    assert np.array_equal(_cells(coast, "INLAND"), _cells(plain, "INLAND"))
    # a pier on the sea keeps no cells, so it reads dry rather than reading the sea
    assert _cells(plain, "PIER").size > 0
    assert _cells(coast, "PIER").size == 0


def test_a_segment_with_no_cells_samples_dry() -> None:
    """A pier with no land cells reads 0 cm, never the sea's three metres."""
    from varuna_products.segment_table import sample_segments

    coast = D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=_sea())
    depth = np.full((2, *SHAPE), 0.1)
    depth[:, _sea()] = 3.0  # three metres of sea, 10 cm on the land
    depth_cm = sample_segments(depth, coast)
    ids = coast[0]
    assert np.all(depth_cm[:, ids.index("PIER")] == 0.0)
    assert np.allclose(depth_cm[:, ids.index("PROMENADE")], 10.0)
    plain = sample_segments(depth, D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE))
    assert np.all(plain[:, ids.index("PROMENADE")] > 100.0)


def test_a_sea_on_another_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="grid"):
        D.build_segment_cell_index(_streets(), TRANSFORM, SHAPE, sea=np.zeros((20, 21), bool))


def test_an_index_from_another_coastline_is_refused_until_the_build_removes_it(
    tmp_path: Path,
) -> None:
    _streets().to_parquet(tmp_path / "segments.parquet")
    before = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["sea_sha256"]) == D.NO_SEA_DIGEST
    stale = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()

    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[:, :SEA_COLS] = 1
    _write_sea(tmp_path, codes)
    with pytest.raises(D.StaleSegmentIndex, match="sea"):
        D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == stale

    # The city build removes the index when it rewrites sea_mask.tif; the next read builds it.
    (tmp_path / D.SEGMENT_CELLS_FILE).unlink()
    after = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert _cells(after, "PIER").size == 0 < _cells(before, "PIER").size
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["sea_sha256"]) == D.sea_mask_digest(_sea())

    # and a matching index is reused as it is
    written = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()
    again = D.segment_cell_index(tmp_path, TRANSFORM, SHAPE, CRS)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == written
    assert again[0] == after[0]


def test_the_sea_raster_reads_open_sea_and_creek_but_not_no_data(tmp_path: Path) -> None:
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[0, 0], codes[0, 1], codes[0, 2] = 1, 2, 255
    _write_sea(tmp_path, codes)
    sea = D.city_sea_mask(tmp_path, SHAPE)
    assert sea is not None
    assert sea[0, 0] and sea[0, 1] and not sea[0, 2]
    assert int(sea.sum()) == 2
    assert D.city_sea_mask(tmp_path / "nowhere") is None
    with pytest.raises(ValueError):
        D.city_sea_mask(tmp_path, (20, 21))


# ============================================================================ depth PNGs
def test_the_sea_is_drawn_transparent_and_the_land_is_drawn_as_before(tmp_path: Path) -> None:
    depth = np.full((2, *SHAPE), 0.4, dtype=np.float32)  # 40 cm everywhere, sea included
    D.write_depth_rasters(tmp_path / "plain", depth, TRANSFORM, CRS, stat="p50", workers=1)
    D.write_depth_rasters(
        tmp_path / "coast", depth, TRANSFORM, CRS, stat="p50", workers=1, sea_mask=_sea()
    )
    for step in range(2):
        name = Path("depth") / f"p50_{step:02d}.png"
        plain = np.asarray(Image.open(tmp_path / "plain" / name).convert("RGBA"))
        coast = np.asarray(Image.open(tmp_path / "coast" / name).convert("RGBA"))
        assert (plain[..., 3][_sea()] > 0).all()
        assert (coast[..., 3][_sea()] == 0).all()
        assert np.array_equal(coast[~_sea()], plain[~_sea()])


def test_a_sea_mask_on_another_grid_is_refused_by_the_raster_writer(tmp_path: Path) -> None:
    depth = np.zeros((1, *SHAPE), dtype=np.float32)
    with pytest.raises(ValueError, match="sea_mask"):
        D.write_depth_rasters(
            tmp_path, depth, TRANSFORM, CRS, sea_mask=np.zeros((20, 21), dtype=bool)
        )


# ============================================================================ hotspot windows
def _register_at(root: Path, name: str, row: int, col: int) -> None:
    import json

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


def _peak(root: Path, depth: np.ndarray) -> float:
    from datetime import datetime, timedelta

    from varuna_products.hotspots import rank_hotspots
    from varuna_schemas.constants import IST

    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    times = tuple(t0 + timedelta(minutes=5 * k) for k in range(depth.shape[0]))
    ranked = rank_hotspots(depth, times, root, TRANSFORM, CRS, "TEST-RUN")
    assert len(ranked) == 1
    return float(ranked[0]["peak_depth_cm"])


def _coast_depth() -> np.ndarray:
    depth = np.full((3, *SHAPE), 0.2, dtype=np.float64)  # 20 cm on the land
    depth[:, _sea()] = 3.0  # three metres of sea
    return depth


def test_a_junction_by_the_shore_reads_its_street_not_the_sea(tmp_path: Path) -> None:
    _register_at(tmp_path, "shore-junction", 10, SEA_COLS + 1)
    without = _peak(tmp_path, _coast_depth())
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[_sea()] = 1
    _write_sea(tmp_path, codes)
    assert without == pytest.approx(300.0)
    assert _peak(tmp_path, _coast_depth()) == pytest.approx(20.0)


def test_a_junction_whose_window_is_all_sea_reads_dry(tmp_path: Path) -> None:
    _register_at(tmp_path, "offshore", 10, 1)
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[:, :12] = 1
    _write_sea(tmp_path, codes)
    depth = np.full((3, *SHAPE), 3.0)
    assert _peak(tmp_path, depth) == 0.0


# ============================================================================ attribution
def _chain(cells: list[tuple[int, int]]):
    """A drain chain through ``cells`` in order, ending at a free outfall."""
    from varuna_twin.types import DrainNetwork

    n = len(cells)
    rows = np.array([r for r, _ in cells], dtype=np.int32)
    cols = np.array([c for _, c in cells], dtype=np.int32)
    boundary = np.zeros(n, dtype=np.int8)
    boundary[-1] = 2
    area = np.pi * 0.3**2
    return DrainNetwork(
        node_ids=tuple(f"N{i}" for i in range(n)),
        z_ground=np.full(n, 2.0),
        z_invert=np.full(n, 0.5),
        storage_area=np.full(n, 1.0),
        inlet_length=np.full(n, 0.6),
        inlet_area=np.full(n, 0.04),
        kappa=np.full(n, 0.25),
        boundary=boundary,
        flap_gate=np.zeros(n, dtype=bool),
        cell_row=rows,
        cell_col=cols,
        edge_ids=tuple(f"E{i}" for i in range(n - 1)),
        from_node=np.arange(n - 1, dtype=np.int32),
        to_node=np.arange(1, n, dtype=np.int32),
        length=np.full(n - 1, 40.0),
        area=np.full(n - 1, area),
        hydraulic_radius=np.full(n - 1, 0.15),
        diameter=np.full(n - 1, 0.6),
        edge_manning_n=np.full(n - 1, 0.013),
        q_full=np.full(n - 1, 0.3),
        beta=np.full(n - 1, 0.15),
    )


def _frozen_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> np.ndarray:
    """The street depth attribution freezes onto every node, captured at ``attribute_pipes``."""
    from datetime import datetime, timedelta

    import varuna_flash.whatif as whatif
    from varuna_products.hotspots import rank_hotspots
    from varuna_schemas.constants import IST

    captured: dict[str, np.ndarray] = {}

    def spy(network, surface_depth_m, **kwargs):
        captured["surface"] = np.array(surface_depth_m)
        return whatif.PipeAttributionResult(
            target=str(kwargs.get("target_label", "")),
            depth_before_cm=float(kwargs.get("depth_before_cm", 0.0)),
            rows=(),
            combined=None,
            reason="captured by the test",
            n_candidates=0,
            n_nodes=int(network.n_nodes),
            method="spy",
            ms=0.0,
        )

    monkeypatch.setattr(whatif, "attribute_pipes", spy)
    # `rank_hotspots` attributes only against the city folder `city_dir` names, so the temporary
    # city is made that folder.
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path.parent))
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    depth = _coast_depth()
    times = tuple(t0 + timedelta(minutes=5 * k) for k in range(depth.shape[0]))
    # The junction and one street node on land, then the outfall spine's last node on the sea.
    network = _chain([(10, SEA_COLS + 1), (10, SEA_COLS), (10, SEA_COLS - 1)])
    rank_hotspots(depth, times, tmp_path, TRANSFORM, CRS, "TEST-RUN", network=network)
    assert "surface" in captured, "attribution never ran for the shore junction"
    return captured["surface"]


def test_attribution_freezes_a_node_on_the_sea_dry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Twin keeps a sea node out of the exchange; attribution must not hand its inlet the
    sea's three metres to capture, or cleaning a pipe would be measured against seawater."""
    _register_at(tmp_path, "shore-junction", 10, SEA_COLS + 1)
    without = _frozen_surface(tmp_path, monkeypatch)
    assert np.allclose(without[:, 2], 3.0), "the control should read the sea at the sea node"

    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[_sea()] = 1
    _write_sea(tmp_path, codes)
    with_sea = _frozen_surface(tmp_path, monkeypatch)
    assert np.all(with_sea[:, 2] == 0.0)
    # the land nodes read their street, exactly as before
    assert np.array_equal(with_sea[:, :2], without[:, :2])
    assert np.allclose(with_sea[:, :2], 0.2)
