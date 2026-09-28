"""The intertidal zone the coast wall stands behind is persisted as ``intertidal_mask.tif``.

The Twin keeps mangroves, wetland and water behind the wall as land so the tide can walk onto
them; the products read the raster to keep that water out of every street and hotspot depth
(``varuna_products.depth``). These tests pin what the condition step computes and what the city
build writes. Every grid is synthetic and every city a temporary folder; nothing here reads or
writes ``city/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine
from varuna_city import pipeline as P
from varuna_city.condition import condition_dem, intertidal_zone, raise_coast_wall
from varuna_city.config import CityGrid

CRS = "EPSG:32643"
RES = 10.0
WALL = 2.72


def _mangrove_bay(size: int = 20) -> tuple[np.ndarray, np.ndarray, np.ndarray, Affine]:
    """Sea in cols 0-3 at -1 m; mangroves (class 95) at 0.5 m in rows 5-14, cols 4-5; built-up
    land at 1.0 m behind them in cols 6-8; built-up land at 3 m everywhere else."""
    dem = np.full((size, size), 3.0)
    classes = np.full((size, size), 50, dtype=np.int16)
    sea = np.zeros((size, size), dtype=bool)
    sea[:, :4] = True
    dem[sea] = -1.0
    classes[sea] = 80
    dem[5:15, 4:6] = 0.5
    classes[5:15, 4:6] = 95
    dem[5:15, 6:9] = 1.0
    return dem, classes, sea, Affine(RES, 0.0, 0.0, 0.0, -RES, size * RES)


def _mangroves(size: int = 20) -> np.ndarray:
    mask = np.zeros((size, size), dtype=bool)
    mask[5:15, 4:6] = True
    return mask


# ============================================================================ condition_dem
def test_condition_dem_returns_the_intertidal_land_the_wall_stands_behind() -> None:
    dem, classes, sea, transform = _mangrove_bay()
    kwargs = {"use_whitebox": False, "use_pyflwdir": False}
    coast = condition_dem(
        dem, transform, CRS, sea=sea, landcover=classes, coast_wall_m=WALL, **kwargs
    )
    assert coast.intertidal_mask is not None
    assert np.array_equal(coast.intertidal_mask, _mangroves())
    assert not (coast.intertidal_mask & sea).any(), "the sea is its own raster"
    assert int(coast.intertidal_mask.sum()) == coast.changes["intertidal_cells"] == 20

    # No wall, no zone: nothing to persist, and the rest of the output is what it always was.
    plain = condition_dem(dem, transform, CRS, **kwargs)
    assert plain.intertidal_mask is None
    ring = condition_dem(dem, transform, CRS, sea=sea, coast_wall_m=WALL, **kwargs)
    assert ring.intertidal_mask is None, "the shore-ring rule has no intertidal zone"
    no_wall = condition_dem(dem, transform, CRS, sea=sea, landcover=classes, **kwargs)
    assert no_wall.intertidal_mask is None


def test_a_zone_passed_in_builds_the_same_wall_as_one_computed_inside() -> None:
    """``condition_dem`` computes the zone once and hands it to the wall, so the raster the build
    writes and the zone the wall was raised behind are one array."""
    dem, classes, sea, _ = _mangrove_bay()
    zone = intertidal_zone(dem, sea, classes, wall_m=WALL)
    given, given_stats = raise_coast_wall(dem, sea, wall_m=WALL, landcover=classes, zone=zone)
    inside, inside_stats = raise_coast_wall(dem, sea, wall_m=WALL, landcover=classes)
    assert np.array_equal(given, inside)
    assert given_stats == inside_stats


# ============================================================================ the city build
def _ctx(root: Path, *, with_sea: bool) -> P.Ctx:
    dem, classes, sea, transform = _mangrove_bay()
    grid = CityGrid(crs=CRS, res=RES, width=dem.shape[1], height=dem.shape[0], transform=transform)
    config = SimpleNamespace(
        id="nowhere-city", building_burn_m=5.0, road_carve_m=0.15, depression_min_area_m2=900.0
    )
    ctx = P.Ctx(config=config, grid=grid, out_dir=root)  # type: ignore[arg-type]
    ctx.put("dem", dem)
    ctx.put("landcover", classes)
    if with_sea:
        ctx.put("sea_codes", sea.astype(np.uint8))
        ctx.put("sea_config", SimpleNamespace(coast_wall_m=WALL))
    return ctx


def _read(path: Path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as src:
        return src.read(1), {
            "crs": str(src.crs),
            "transform": src.transform,
            "dtype": src.dtypes[0],
        }


def test_the_condition_step_writes_the_mask_counts_it_and_drops_the_index(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, with_sea=True)
    (tmp_path / P.SEGMENT_INDEX_FILE).write_bytes(b"built before the intertidal raster")

    changes = P._step_condition(ctx)

    band, meta = _read(tmp_path / P.INTERTIDAL_MASK_FILE)
    assert meta["dtype"] == "uint8"
    assert meta["transform"] == ctx.grid.transform
    assert np.array_equal(band, _mangroves().astype(np.uint8))
    assert changes["intertidal_mask_cells"] == 20 == changes["intertidal_cells"]
    assert changes["intertidal_mask_file"] == "intertidal_mask.tif"
    written = json.loads((tmp_path / "condition.json").read_text(encoding="utf-8"))
    assert written["intertidal_mask_cells"] == 20
    # an index built before the raster could read mangroves as streets: the build removes it
    assert not (tmp_path / P.SEGMENT_INDEX_FILE).exists()
    # written beside the target and renamed over it; nothing partial is left behind
    assert not [p.name for p in tmp_path.iterdir() if "partial" in p.name]
    assert np.array_equal(ctx.get("intertidal_mask"), _mangroves())

    loaded = _ctx(tmp_path, with_sea=True)
    P._load_condition(loaded)
    assert np.array_equal(loaded.get("intertidal_mask"), _mangroves())


def test_the_mask_is_byte_identical_on_a_second_build(tmp_path: Path) -> None:
    """Rule 8: the same inputs write the same raster."""
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        P._step_condition(_ctx(tmp_path / name, with_sea=True))
    a = (tmp_path / "a" / P.INTERTIDAL_MASK_FILE).read_bytes()
    assert a == (tmp_path / "b" / P.INTERTIDAL_MASK_FILE).read_bytes()


def test_a_city_with_no_sea_writes_an_all_zero_mask_and_conditions_as_before(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path, with_sea=False)
    changes = P._step_condition(ctx)

    band, _ = _read(tmp_path / P.INTERTIDAL_MASK_FILE)
    assert band.shape == ctx.grid.shape
    assert not band.any()
    assert changes["intertidal_mask_cells"] == 0
    # the conditioned surface is condition_dem's without a sea, bit for bit
    dem, _, _, transform = _mangrove_bay()
    plain = condition_dem(dem, transform, CRS, seed=ctx.seed)
    assert np.array_equal(ctx.get("conditioned"), plain.dem)
    assert "coast_wall_m" not in changes


def test_a_failed_write_leaves_the_old_mask_and_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, with_sea=True)
    P._step_condition(ctx)
    before = (tmp_path / P.INTERTIDAL_MASK_FILE).read_bytes()

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        P._write_mask_atomic(
            np.ones(ctx.grid.shape, bool), ctx.grid, ctx.path("intertidal_mask.tif")
        )
    assert (tmp_path / P.INTERTIDAL_MASK_FILE).read_bytes() == before
    assert not [p.name for p in tmp_path.iterdir() if "partial" in p.name]


def test_a_city_built_before_the_raster_is_stale_at_the_condition_step(tmp_path: Path) -> None:
    """The raster is a declared output, so a city folder without one reconditions."""
    step = next(s for s in P.STEPS if s.name == "condition")
    assert P.INTERTIDAL_MASK_FILE in step.outputs
    ctx = _ctx(tmp_path, with_sea=True)
    for name in step.outputs:
        (tmp_path / name).write_bytes(b"x")
    assert step.fresh(ctx, config_mtime=0.0)
    (tmp_path / P.INTERTIDAL_MASK_FILE).unlink()
    assert not step.fresh(ctx, config_mtime=0.0)


def test_the_name_is_the_one_the_products_read() -> None:
    from varuna_products.depth import INTERTIDAL_MASK_FILE

    assert P.INTERTIDAL_MASK_FILE == INTERTIDAL_MASK_FILE


def test_the_report_names_the_intertidal_land_left_out_of_street_depths() -> None:
    from varuna_city.report import _coastline_section

    sea = {"cells": 100, "km2": 0.09, "open_sea": {"cells": 100}, "tidal_creek": {"cells": 0}}
    condition = {
        "coast_wall_m": 2.72,
        "shore_ring_cells": 2289,
        "coast_wall_cells_raised": 382,
        "intertidal_cells": 674,
        "intertidal_mask_cells": 674,
        "intertidal_mask_file": "intertidal_mask.tif",
    }
    text = "\n".join(_coastline_section(sea, None, condition))
    assert "Intertidal land left out of street and hotspot depths" in text
    assert "674 cells in intertidal_mask.tif" in text
    older = {k: v for k, v in condition.items() if not k.startswith("intertidal_mask")}
    assert "left out of street" not in "\n".join(_coastline_section(sea, None, older))
