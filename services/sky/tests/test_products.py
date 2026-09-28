"""The rain-cube products: the statistics, the two grids, and the Zarr stores (P3.5).

What is actually being defended here:

* the reduction is over the **member** axis and nothing else, so ``p10 <= p50 <= p90``
  everywhere and an exceedance probability is exactly the fraction of members above the
  threshold;
* the resample lands the storm where the storm is. A bilinear operator reproduces a linear
  field exactly, so the ramp test below is an equality, not a tolerance; and a blob put on a
  known Sky pixel has to come out at the matching metric coordinate on the AOI grid, which is
  what a transposed or vertically flipped resample would fail;
* the AOI-mean weight field is not an approximation: its hyetographs equal the mean of the
  fully resampled cube to floating-point round-off;
* two writes of the same products are byte-identical (rule 8, and SPEC.md P5.9 leans on it).

Everything runs on a 24 x 24 Sky grid with a 40 x 30 AOI window, so the file finishes in a
couple of seconds; the production 120 x 120 grid and 323 x 522 AOI are the integrator's.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import zarr
from varuna_schemas.constants import IST
from varuna_sky.products import (
    EXCEEDANCE_MM_H,
    HYETOGRAPH_VARIABLE,
    PRODUCT_DTYPE,
    RAIN_CUBE,
    RAIN_QUANTILES,
    AoiGrid,
    aoi_hyetographs,
    aoi_mean_weights,
    exceedance,
    load_aoi_grid,
    quantiles,
    read_attrs,
    read_rain_cube,
    read_sky_products,
    resample_to_aoi,
    sky_products,
    write_quantiles,
    write_rain_cube,
    write_rain_products,
)
from varuna_sky.types import RadarGrid, RainEnsemble, ZRParams

N_PX = 24
"""A 12 km Sky domain at 500 m: big enough to hold an AOI window with room on every side."""

SKY_LEFT = 300_000.0
SKY_TOP = 2_140_000.0
"""Plausible UTM 43N coordinates for the Mumbai domain, so the arithmetic runs at the
magnitude it will run at in production rather than near zero."""

AOI_LEFT = 303_265.0
AOI_TOP = 2_135_605.0
AOI_RES = 30.0
AOI_WIDTH = 40
AOI_HEIGHT = 30
"""A 1.2 km x 0.9 km AOI window, deliberately not aligned to the 500 m Sky grid and not
square, so a row/column mix-up cannot pass unnoticed."""

MP = ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0)
"""Marshall-Palmer, the relation the replay bundles were rendered with (storm.py MP_A/MP_B)."""

T0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
"""The replay's opening cycle (SPEC.md 15)."""


# ============================================================================ fixtures
def sky_grid(n_px: int = N_PX, res_m: float = 500.0) -> RadarGrid:
    return RadarGrid(
        crs="EPSG:32643",
        res_m=res_m,
        n_px=n_px,
        transform=(res_m, 0.0, SKY_LEFT, 0.0, -res_m, SKY_TOP),
    )


def aoi_grid(
    left: float = AOI_LEFT,
    top: float = AOI_TOP,
    width: int = AOI_WIDTH,
    height: int = AOI_HEIGHT,
    res_m: float = AOI_RES,
    crs: str = "EPSG:32643",
) -> AoiGrid:
    return AoiGrid(
        crs=crs,
        res_m=res_m,
        width=width,
        height=height,
        transform=(res_m, 0.0, left, 0.0, -res_m, top),
    )


def sky_coords(grid: RadarGrid) -> tuple[np.ndarray, np.ndarray]:
    """``(X, Y)`` cell-centre coordinates of the Sky grid, shaped like one frame."""
    x = grid.left + (np.arange(grid.n_px) + 0.5) * grid.res_m
    y = grid.top - (np.arange(grid.n_px) + 0.5) * grid.res_m
    return np.meshgrid(x, y)


def ensemble_from(
    cube: np.ndarray,
    grid: RadarGrid | None = None,
    *,
    step_min: int = 5,
    source: str = "fallback_steps",
) -> RainEnsemble:
    grid = grid or sky_grid()
    times = tuple(T0 + timedelta(minutes=step_min * (k + 1)) for k in range(cube.shape[1]))
    return RainEnsemble(
        rain_mm_h=cube,
        times=times,
        grid=grid,
        source=source,  # type: ignore[arg-type]
        seed=2019,
        zr=MP,
    )


def storm_cube(
    n_members: int = 8,
    n_steps: int = 4,
    grid: RadarGrid | None = None,
    seed: int = 2019,
) -> np.ndarray:
    """A drifting Gaussian cell with per-member intensity spread - a small, honest ensemble."""
    grid = grid or sky_grid()
    x, y = sky_coords(grid)
    rng = np.random.default_rng(seed)
    scales = 0.5 + rng.random(n_members)
    cube = np.zeros((n_members, n_steps, grid.n_px, grid.n_px), dtype=np.float64)
    for step in range(n_steps):
        cx = SKY_LEFT + 4_000.0 + 900.0 * step
        cy = SKY_TOP - 5_000.0 - 700.0 * step
        blob = 60.0 * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1_500.0**2))
        for member in range(n_members):
            cube[member, step] = blob * scales[member] + 2.0
    return cube


def digest(root: Path) -> str:
    """A hash of every file under ``root``, for the byte-identical determinism test."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


# ============================================================================ statistics
def test_quantiles_are_ordered_at_every_pixel_and_step() -> None:
    q10, q50, q90 = quantiles(storm_cube())
    assert q10.shape == (4, N_PX, N_PX)
    assert np.all(q10 <= q50) and np.all(q50 <= q90)
    assert np.any(q10 < q90), "an ensemble with spread must not collapse to one quantile"


def test_quantiles_are_taken_across_members_only() -> None:
    """Member ``m`` is a uniform field of value ``m``, so the answers are arithmetic."""
    grid = sky_grid()
    cube = np.broadcast_to(
        np.arange(20, dtype=np.float64)[:, None, None, None], (20, 3, grid.n_px, grid.n_px)
    )
    q10, q50, q90 = quantiles(cube)
    assert np.allclose(q10, 1.9) and np.allclose(q50, 9.5) and np.allclose(q90, 17.1)


def test_the_mean_conserves_the_ensemble_volume_where_the_median_does_not() -> None:
    """Why the Twin runs on the mean (SPEC.md 11.11), stated as arithmetic.

    Twenty members each carry the same convective cell in a different place - which is exactly
    what a STEPS ensemble is. Every member delivers the same volume of water, so the forecast
    volume is unambiguous; but at any one pixel most members are dry, so the pixelwise median is
    zero almost everywhere and the field it makes delivers a fraction of it. Feed that to a
    water balance and the city receives rain nobody forecast.
    """
    grid = sky_grid()
    n_members, n_steps = 20, 3
    assert grid.n_px >= n_members, "fixture needs a column per member so none of them overlap"
    cube = np.zeros((n_members, n_steps, grid.n_px, grid.n_px), dtype=np.float64)
    for m in range(n_members):
        # One column per member, so no pixel is wet in more than one of them - the extreme of
        # the disagreement a STEPS ensemble has about where a convective cell will be.
        cube[m, :, 4:7, m] = 60.0

    _q10, q50, _q90 = quantiles(cube)
    mean = cube.mean(axis=0)
    member_volume = cube.sum(axis=(1, 2, 3))

    assert np.allclose(member_volume, member_volume[0]), "fixture: every member carries the same"
    assert mean.sum() == pytest.approx(float(member_volume[0]))
    assert q50.sum() == pytest.approx(0.0), "the median of mostly-dry members is dry"


def test_products_publish_the_mean_beside_the_quantiles() -> None:
    """The Twin reads ``products.mean``; it must be the mean of the members, not a quantile."""
    cube = storm_cube()
    products = sky_products(ensemble_from(cube), aoi_grid())
    assert products.mean.shape == products.p50.shape
    assert np.allclose(products.mean, cube.mean(axis=0), atol=1e-6)


def test_exceedance_is_the_member_fraction_strictly_above_the_threshold() -> None:
    grid = sky_grid()
    cube = np.zeros((10, 1, grid.n_px, grid.n_px), dtype=np.float64)
    cube[:3, 0, 5, 6] = 25.0  # three members exceed 20 mm/h
    cube[3, 0, 5, 6] = 20.0  # exactly on the threshold: not an exceedance
    cube[:7, 0, 5, 7] = 45.0
    p20 = exceedance(cube, EXCEEDANCE_MM_H[0])
    p40 = exceedance(cube, EXCEEDANCE_MM_H[1])
    assert p20[0, 5, 6] == pytest.approx(0.3)
    assert p40[0, 5, 6] == 0.0
    assert p20[0, 5, 7] == pytest.approx(0.7)
    assert p40[0, 5, 7] == pytest.approx(0.7)
    assert p20[0, 0, 0] == 0.0


def test_a_dry_cube_gives_zero_rain_and_zero_probability_everywhere() -> None:
    grid = sky_grid()
    products = sky_products(ensemble_from(np.zeros((6, 4, grid.n_px, grid.n_px))), aoi_grid())
    for field in (products.p10, products.p50, products.p90):
        assert np.count_nonzero(field) == 0
    assert np.count_nonzero(products.p_gt_20) == 0
    assert np.count_nonzero(products.p_gt_40) == 0
    assert np.count_nonzero(products.aoi_hyetographs) == 0


def test_products_have_the_shapes_the_contract_declares() -> None:
    products = sky_products(ensemble_from(storm_cube(n_members=8, n_steps=4)), aoi_grid())
    assert products.p50.shape == (4, N_PX, N_PX)
    assert products.p_gt_20.shape == (4, N_PX, N_PX)
    assert products.aoi_hyetographs.shape == (8, 4), "hyetographs are per member, per step"
    assert products.n_steps == 4
    assert products.p50.dtype == np.dtype(PRODUCT_DTYPE)
    assert products.times[0] == T0 + timedelta(minutes=5)


def test_a_cube_with_a_hole_is_refused_rather_than_published_as_dry() -> None:
    cube = storm_cube(n_members=4, n_steps=2)
    cube[1, 0, 3, 3] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        sky_products(ensemble_from(cube), aoi_grid())


def test_a_time_axis_that_does_not_match_the_steps_is_refused() -> None:
    cube = storm_cube(n_members=4, n_steps=3)
    ensemble = ensemble_from(cube)
    broken = RainEnsemble(
        rain_mm_h=cube,
        times=ensemble.times[:2],
        grid=ensemble.grid,
        source=ensemble.source,
        seed=ensemble.seed,
        zr=ensemble.zr,
    )
    with pytest.raises(ValueError, match="valid times"):
        sky_products(broken, aoi_grid())


# ============================================================================ the AOI grid
def test_the_aoi_grid_reads_back_the_city_pipeline_record() -> None:
    """The numbers are Mumbai's own, from ``city/mumbai/pipeline.json`` (SPEC.md 3.3)."""
    record = {
        "crs": "EPSG:32643",
        "res_m": 30.0,
        "width": 323,
        "height": 522,
        "bounds": [269970.0, 2101560.0, 279660.0, 2117220.0],
        "transform": [30.0, 0.0, 269970.0, 0.0, -30.0, 2117220.0],
    }
    grid = AoiGrid.from_mapping(record)
    assert grid.shape == (522, 323)
    assert grid.bounds == (269970.0, 2101560.0, 279660.0, 2117220.0)
    assert grid.x_coords()[0] == 269985.0, "row 0 / column 0 is the north-west cell centre"
    assert grid.y_coords()[0] == 2117205.0
    assert grid.y_coords()[-1] < grid.y_coords()[0], "northings decrease with row"


def test_load_aoi_grid_reads_the_grid_block_of_pipeline_json(tmp_path: Path) -> None:
    record = {
        "city": "mumbai",
        "grid": {
            "crs": "EPSG:32643",
            "res_m": 30.0,
            "width": 323,
            "height": 522,
            "transform": [30.0, 0.0, 269970.0, 0.0, -30.0, 2117220.0],
        },
    }
    (tmp_path / "pipeline.json").write_text(json.dumps(record), encoding="utf-8")
    assert load_aoi_grid(tmp_path) == AoiGrid.from_mapping(record["grid"])
    assert load_aoi_grid(tmp_path / "pipeline.json").width == 323


def test_an_unbuilt_city_says_which_command_builds_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make city"):
        load_aoi_grid(tmp_path / "chennai")


def test_a_rotated_or_inconsistent_grid_record_is_refused() -> None:
    with pytest.raises(ValueError, match="unrotated"):
        AoiGrid(crs="EPSG:32643", res_m=30.0, width=4, height=4, transform=(30, 1, 0, 0, -30, 0))
    with pytest.raises(ValueError, match="disagrees"):
        AoiGrid(crs="EPSG:32643", res_m=30.0, width=4, height=4, transform=(50, 0, 0, 0, -50, 0))


# ============================================================================ resampling
def test_a_uniform_field_resamples_to_the_same_constant() -> None:
    """The plainest conservation statement: bilinear weights sum to one everywhere."""
    grid = sky_grid()
    field = np.full(grid.shape, 7.25)
    out = resample_to_aoi(field, grid, aoi_grid())
    assert out.shape == (AOI_HEIGHT, AOI_WIDTH)
    assert np.allclose(out, 7.25, atol=1e-12)


def test_bilinear_reproduces_a_linear_field_exactly() -> None:
    """Bilinear interpolation is exact on a plane, so this is an equality test, not a
    tolerance one: it pins the georeference, the half-pixel offset and the axis directions
    all at once. A field tilted in both x and y catches a swapped axis."""
    grid = sky_grid()
    aoi = aoi_grid()
    x, y = sky_coords(grid)
    field = 0.004 * (x - SKY_LEFT) - 0.003 * (y - SKY_TOP) + 12.0

    out = resample_to_aoi(field, grid, aoi)
    ax, ay = np.meshgrid(aoi.x_coords(), aoi.y_coords())
    expected = 0.004 * (ax - SKY_LEFT) - 0.003 * (ay - SKY_TOP) + 12.0
    assert np.allclose(out, expected, rtol=0, atol=1e-8)


def test_the_aoi_mean_is_conserved_for_a_linear_field() -> None:
    """A plane's mean over the AOI window is its value at the window centre; bilinear keeps
    it, so the resample neither gains nor loses water over the city."""
    grid = sky_grid()
    aoi = aoi_grid()
    x, y = sky_coords(grid)
    field = 0.004 * (x - SKY_LEFT) - 0.003 * (y - SKY_TOP) + 12.0
    left, bottom, right, top = aoi.bounds
    centre = 0.004 * ((left + right) / 2 - SKY_LEFT) - 0.003 * ((bottom + top) / 2 - SKY_TOP) + 12.0
    assert resample_to_aoi(field, grid, aoi).mean() == pytest.approx(centre, abs=1e-8)


def test_the_storm_lands_on_the_matching_aoi_coordinate() -> None:
    """A blob centred on one known Sky pixel must peak at the AOI cell over that metre.

    Row 9, column 7 is off-centre in the AOI window by different amounts in x and in y, so a
    transposed resample or a flipped northing axis moves the peak and fails here.
    """
    grid = sky_grid()
    aoi = aoi_grid()
    row, col = 9, 7
    cx, cy = grid.xy(row, col)
    x, y = sky_coords(grid)
    field = 80.0 * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 800.0**2))

    out = resample_to_aoi(field, grid, aoi)
    peak_row, peak_col = np.unravel_index(int(np.argmax(out)), out.shape)
    peak_x = aoi.x_coords()[peak_col]
    peak_y = aoi.y_coords()[peak_row]
    assert abs(peak_x - cx) <= aoi.res_m, f"peak at easting {peak_x}, storm at {cx}"
    assert abs(peak_y - cy) <= aoi.res_m, f"peak at northing {peak_y}, storm at {cy}"
    # No AOI cell centre sits exactly on the Sky node, so the interpolated peak is a little
    # under the node value - and, bilinear being an average of its neighbours, never over it.
    assert out.max() <= field.max()
    assert out.max() == pytest.approx(field[row, col], rel=0.02)


def test_leading_axes_are_carried_through_the_resample() -> None:
    grid = sky_grid()
    cube = storm_cube(n_members=3, n_steps=2, grid=grid)
    out = resample_to_aoi(cube, grid, aoi_grid())
    assert out.shape == (3, 2, AOI_HEIGHT, AOI_WIDTH)
    assert np.allclose(out[1, 1], resample_to_aoi(cube[1, 1], grid, aoi_grid()))


def test_an_aoi_cell_outside_the_domain_is_a_hole_not_a_dry_street() -> None:
    """Rule 6: no forecast means no number, not zero rain."""
    grid = sky_grid()
    # Push the window east so its last columns fall past the domain edge.
    aoi = aoi_grid(left=grid.bounds[2] - 600.0, width=40)
    out = resample_to_aoi(np.full(grid.shape, 5.0), grid, aoi)
    assert np.isfinite(out[:, :20]).all() and np.allclose(out[:, :20], 5.0)
    assert np.isnan(out[:, 20:]).all()


def test_a_crs_mismatch_is_refused_rather_than_silently_reprojected() -> None:
    with pytest.raises(ValueError, match="must share a CRS"):
        resample_to_aoi(np.zeros(sky_grid().shape), sky_grid(), aoi_grid(crs="EPSG:32644"))


def test_an_aoi_outside_the_domain_names_both_boxes() -> None:
    with pytest.raises(ValueError, match="lies outside the Sky domain"):
        resample_to_aoi(np.zeros(sky_grid().shape), sky_grid(), aoi_grid(left=500_000.0))


# ============================================================================ hyetographs
def test_the_mean_weights_are_a_mean() -> None:
    weights = aoi_mean_weights(sky_grid(), aoi_grid())
    assert weights.shape == (N_PX, N_PX)
    assert weights.sum() == pytest.approx(1.0, abs=1e-12)
    assert (weights >= 0.0).all()
    assert np.count_nonzero(weights) < N_PX * N_PX, "a 1.2 km window touches few 500 m pixels"


def test_hyetographs_equal_the_mean_of_the_fully_resampled_cube() -> None:
    """The weight field is an identity, not a shortcut: proving that is what lets the cycle
    skip materialising a 30 m ensemble."""
    grid = sky_grid()
    aoi = aoi_grid()
    cube = storm_cube(n_members=5, n_steps=3, grid=grid)
    fast = aoi_hyetographs(cube, grid, aoi)
    slow = np.array(
        [
            [resample_to_aoi(cube[m, t], grid, aoi).mean() for t in range(cube.shape[1])]
            for m in range(cube.shape[0])
        ]
    )
    assert fast.shape == slow.shape == (5, 3)
    assert np.allclose(fast, slow, rtol=1e-12, atol=1e-12)


def test_hyetographs_follow_each_member_and_the_storm() -> None:
    grid = sky_grid()
    cube = storm_cube(n_members=6, n_steps=4, grid=grid)
    hyetographs = aoi_hyetographs(cube, grid, aoi_grid())
    assert (hyetographs > 0.0).all(), "the storm crosses the AOI, so no member is dry"
    spread = hyetographs.max(axis=0) - hyetographs.min(axis=0)
    assert (spread > 0.0).all(), "the members disagree; the time bar draws that as a band"


def test_a_partly_covered_aoi_averages_only_what_the_domain_covers() -> None:
    grid = sky_grid()
    aoi = aoi_grid(left=grid.bounds[2] - 600.0, width=40)
    cube = np.full((2, 1, grid.n_px, grid.n_px), 4.0)
    assert np.allclose(aoi_hyetographs(cube, grid, aoi), 4.0)


# ============================================================================ the Zarr stores
def test_the_rain_cube_round_trips_with_its_provenance(tmp_path: Path) -> None:
    grid = sky_grid()
    cube = storm_cube(n_members=4, n_steps=3, grid=grid)
    ensemble = ensemble_from(cube, grid, source="pysteps_steps")

    path = write_rain_cube(tmp_path, ensemble)
    assert path == tmp_path / RAIN_CUBE

    back = read_rain_cube(path)
    assert back.shape == cube.shape
    assert np.array_equal(back, cube.astype(PRODUCT_DTYPE))

    attrs = read_attrs(path)
    assert attrs["crs"] == grid.crs and attrs["n_px"] == grid.n_px
    assert attrs["transform"] == list(grid.transform)
    assert attrs["nowcast_source"] == "pysteps_steps"
    assert attrs["seed"] == 2019
    assert (attrs["zr_a"], attrs["zr_b"]) == (MP.a, MP.b)
    assert attrs["units"] == "mm/h"
    assert attrs["step_min"] == 5.0
    assert attrs["t0"] == (T0 + timedelta(minutes=5)).isoformat()
    assert attrs["cycle_ts"] == T0.isoformat(), "the cycle is one step before the first lead"


def test_the_cube_is_chunked_one_member_step_slice_at_a_time(tmp_path: Path) -> None:
    """The declared access pattern, asserted: a chunk spanning members or steps would make
    reading one frame decompress the whole ensemble. Sharding groups those chunks into one
    file per member, which must not change either the chunking or what comes back."""
    grid = sky_grid()
    cube = storm_cube(n_members=4, n_steps=3, grid=grid)
    write_rain_cube(tmp_path, ensemble_from(cube, grid))
    group = zarr.open_group(str(tmp_path / RAIN_CUBE), mode="r")
    assert group["rain"].chunks == (1, 1, grid.n_px, grid.n_px)
    assert group["rain"].shards == (1, 3, grid.n_px, grid.n_px)
    assert np.array_equal(np.asarray(group["rain"][2, 1]), cube[2, 1].astype(PRODUCT_DTYPE))
    assert np.array_equal(np.asarray(group["time_min"][:]), [0.0, 5.0, 10.0])
    assert np.asarray(group["x"][:])[0] == SKY_LEFT + 250.0
    assert np.asarray(group["y"][:])[0] == SKY_TOP - 250.0


def test_the_products_round_trip_through_quantiles_zarr(tmp_path: Path) -> None:
    grid = sky_grid()
    aoi = aoi_grid()
    products = sky_products(ensemble_from(storm_cube(n_members=5, n_steps=3, grid=grid), grid), aoi)

    path = write_quantiles(tmp_path, products, aoi)
    assert path == tmp_path / RAIN_QUANTILES
    back = read_sky_products(path)

    for name in ("p10", "p50", "p90", "p_gt_20", "p_gt_40", "aoi_hyetographs"):
        assert np.array_equal(getattr(back, name), getattr(products, name)), name
    assert back.times == products.times
    assert back.grid == products.grid

    attrs = read_attrs(path)
    assert attrs["quantiles"] == [0.10, 0.50, 0.90]
    assert attrs["exceedance_mm_h"] == [20.0, 40.0]
    assert attrs["aoi"]["width"] == AOI_WIDTH and attrs["aoi"]["height"] == AOI_HEIGHT
    group = zarr.open_group(str(path), mode="r")
    assert group["p50"].chunks == (1, grid.n_px, grid.n_px)
    assert group["p50"].attrs["units"] == "mm/h"
    assert group["p_gt_20"].attrs["units"] == "probability"
    assert group[HYETOGRAPH_VARIABLE].shape == (5, 3)


def test_two_writes_of_the_same_products_are_byte_identical(tmp_path: Path) -> None:
    """Rule 8, and the foundation of the idempotent bake (SPEC.md P5.9)."""
    grid = sky_grid()
    aoi = aoi_grid()
    ensemble = ensemble_from(storm_cube(n_members=4, n_steps=3, grid=grid), grid)
    products = sky_products(ensemble, aoi)
    for name in ("a", "b"):
        write_rain_products(tmp_path / name, ensemble, products, aoi)
    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_a_rewrite_after_a_change_is_not_identical(tmp_path: Path) -> None:
    """The determinism test above would pass on an empty store; this is its control."""
    grid = sky_grid()
    aoi = aoi_grid()
    cube = storm_cube(n_members=4, n_steps=3, grid=grid)
    write_rain_products(
        tmp_path / "a", ensemble_from(cube, grid), sky_products(ensemble_from(cube, grid), aoi), aoi
    )
    wetter = cube * 1.1
    write_rain_products(
        tmp_path / "b",
        ensemble_from(wetter, grid),
        sky_products(ensemble_from(wetter, grid), aoi),
        aoi,
    )
    assert digest(tmp_path / "a") != digest(tmp_path / "b")


def test_unevenly_spaced_steps_are_refused(tmp_path: Path) -> None:
    """``time_min`` is written as an offset axis, so an uneven axis would misplace a frame."""
    grid = sky_grid()
    cube = storm_cube(n_members=2, n_steps=3, grid=grid)
    broken = RainEnsemble(
        rain_mm_h=cube,
        times=(T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=17)),
        grid=grid,
        source="fallback_steps",
        seed=2019,
        zr=MP,
    )
    with pytest.raises(ValueError, match="evenly spaced"):
        write_rain_cube(tmp_path, broken)
