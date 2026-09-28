"""The cycle ladder, the inputs one cycle reads, and reading a baked run's rain back.

Nothing here runs VARUNA-Sky: these are the decisions ``varuna_cycle.sky_cycle`` makes around
it - which instant a scrub means, which three radar frames that instant sees, which gauge
readings are recent enough, and what a run directory can say about the rain it holds. The one
test that runs a real cycle end to end lives with the API routes that serve it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from varuna_cycle.registry import RunRegistry
from varuna_cycle.sky_cycle import (
    HISTORY_FRAMES,
    cycle_window,
    gauge_window,
    has_rain_products,
    radar_history,
    read_run_rain,
    snap_to_cycle,
)
from varuna_replay.bundle import RADAR_VARIABLE, BundleLayout, write_cube, write_gauges
from varuna_replay.domain import StormDomain, step_times_min
from varuna_schemas.constants import IST
from varuna_schemas.models import RunMeta
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.models.city import RadarDomain
from varuna_schemas.models.common import BBox
from varuna_schemas.samples import sample
from varuna_sky.products import AoiGrid, sky_products, write_rain_products
from varuna_sky.types import RadarGrid, RainEnsemble, ZRParams

T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
WINDOW_MIN = 60.0
RADAR_MIN = 10
N_PX = 16
RES_M = 500.0
LEFT, TOP = 280_000.0, 2_140_000.0
CRS = "EPSG:32643"


def _manifest(bundle_id: str = "MUM-TEST-LADDER") -> BundleManifest:
    return BundleManifest(
        id=bundle_id,
        city="mumbai",
        label="Design storm",
        t0=T0,
        t1=T0 + timedelta(minutes=WINDOW_MIN),
        cadences={"radar": RADAR_MIN, "truth": 5, "gauges": 15, "cycle": 5},
        radar_domain=RadarDomain(center_lon=72.86, center_lat=19.065, size_km=8.0, res_m=RES_M),
        aoi=BBox(min_lon=72.815, min_lat=18.995, max_lon=72.905, max_lat=19.135),
        seed=2019,
        description="A flat test field: the ladder does not care what the rain looks like.",
    )


@pytest.fixture
def layout(tmp_path: Path) -> BundleLayout:
    """A bundle with seven radar frames ten minutes apart and gauges every fifteen."""
    bundle = BundleLayout(root=tmp_path / "MUM-TEST-LADDER")
    domain = StormDomain(crs=32643, res_m=RES_M, n_px=N_PX, left=LEFT, top=TOP)
    times_min = step_times_min(0.0, WINDOW_MIN, RADAR_MIN)
    frames = np.stack(
        [np.full((N_PX, N_PX), 20.0 + index, dtype=np.float32) for index in range(times_min.size)]
    )
    write_cube(
        bundle.radar,
        frames,
        variable=RADAR_VARIABLE,
        times_min=times_min,
        domain=domain,
        t0=T0,
        step_min=RADAR_MIN,
        units="dBZ",
    )
    write_gauges(
        bundle.gauges,
        [
            {
                "ts": T0 + timedelta(minutes=minutes),
                "station_id": "TEST-00",
                "name": "Test gauge",
                "lat": 19.02,
                "lon": 72.86,
                "mm_5min": 1.5,
                "synthetic": True,
            }
            for minutes in range(0, int(WINDOW_MIN) + 1, 15)
        ],
    )
    return bundle


# ============================================================================ the ladder
@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("05:43", "05:40"),  # floored onto the five-minute ladder
        ("05:45", "05:45"),  # a ladder instant is itself
        ("04:00", "05:40"),  # before the window: clamped to t0
        ("23:00", "06:40"),  # after the window: clamped to t1
    ],
)
def test_a_scrub_is_floored_onto_the_cycle_ladder(asked: str, expected: str) -> None:
    manifest = _manifest()
    hour, minute = (int(part) for part in asked.split(":"))
    snapped = snap_to_cycle(manifest, datetime(2019, 7, 2, hour, minute, tzinfo=IST))
    assert snapped.strftime("%H:%M") == expected


def test_the_first_forecastable_cycle_is_the_one_with_three_frames_behind_it(
    layout: BundleLayout,
) -> None:
    """Optical flow needs three frames (SPEC.md 11.1 step 4); frames are ten minutes apart."""
    first, last = cycle_window(_manifest(), layout)
    assert first.strftime("%H:%M") == "06:00"
    assert last.strftime("%H:%M") == "06:40"


def test_a_bundle_with_too_few_frames_names_make_bundle(tmp_path: Path) -> None:
    bundle = BundleLayout(root=tmp_path / "MUM-TEST-SHORT")
    domain = StormDomain(crs=32643, res_m=RES_M, n_px=N_PX, left=LEFT, top=TOP)
    write_cube(
        bundle.radar,
        np.zeros((2, N_PX, N_PX), dtype=np.float32),
        variable=RADAR_VARIABLE,
        times_min=np.array([0.0, 10.0]),
        domain=domain,
        t0=T0,
        step_min=RADAR_MIN,
        units="dBZ",
    )
    with pytest.raises(ValueError, match="make bundle BUNDLE=MUM-TEST-SHORT"):
        cycle_window(_manifest("MUM-TEST-SHORT"), bundle)


# ============================================================================ cycle inputs
def test_the_history_is_the_three_newest_frames_at_or_before_the_cycle(
    layout: BundleLayout,
) -> None:
    frames = radar_history(layout, datetime(2019, 7, 2, 6, 5, tzinfo=IST))

    assert frames.n_frames == HISTORY_FRAMES
    assert [ts.strftime("%H:%M") for ts in frames.times] == ["05:40", "05:50", "06:00"]
    assert frames.latest_ts.strftime("%H:%M") == "06:00"
    # Newest last, as pySTEPS expects: the fixture's frames rise by 1 dBZ each.
    assert list(np.diff(frames.dbz[:, 0, 0])) == [1.0, 1.0]
    assert frames.grid == RadarGrid(
        crs=CRS, res_m=RES_M, n_px=N_PX, transform=(RES_M, 0.0, LEFT, 0.0, -RES_M, TOP)
    )


def test_a_cycle_without_three_frames_says_which_one_to_ask_for(layout: BundleLayout) -> None:
    with pytest.raises(ValueError, match=r"earliest cycle .* can forecast is 06:00 IST"):
        radar_history(layout, datetime(2019, 7, 2, 5, 45, tzinfo=IST))


def test_gauges_are_the_last_hour_and_carry_their_offset(layout: BundleLayout) -> None:
    window = gauge_window(layout, datetime(2019, 7, 2, 6, 40, tzinfo=IST))

    assert [ts.strftime("%H:%M") for ts in window["ts"]] == ["05:55", "06:10", "06:25", "06:40"]
    assert window["ts"].iloc[0].utcoffset() == timedelta(hours=5, minutes=30)
    assert set(window["station_id"]) == {"TEST-00"}


def test_a_shorter_window_takes_fewer_readings(layout: BundleLayout) -> None:
    window = gauge_window(layout, datetime(2019, 7, 2, 6, 40, tzinfo=IST), minutes=20.0)
    assert [ts.strftime("%H:%M") for ts in window["ts"]] == ["06:25", "06:40"]


# ============================================================================ reading a run
def _write_rain_run(registry: RunRegistry, meta: RunMeta) -> Path:
    grid = RadarGrid(
        crs=CRS, res_m=RES_M, n_px=N_PX, transform=(RES_M, 0.0, LEFT, 0.0, -RES_M, TOP)
    )
    aoi = AoiGrid(
        crs=CRS,
        res_m=30.0,
        width=16,
        height=16,
        transform=(30.0, 0.0, LEFT + 2_000.0, 0.0, -30.0, TOP - 2_000.0),
    )
    members = np.array([0.5, 1.0, 1.5, 2.0], dtype=np.float32)
    rain = members[:, None, None, None] * np.full((1, 3, N_PX, N_PX), 8.0, dtype=np.float32)
    ensemble = RainEnsemble(
        rain_mm_h=rain,
        times=tuple(meta.cycle_ts + timedelta(minutes=5 * (step + 1)) for step in range(3)),
        grid=grid,
        source="fallback_steps",
        seed=2019,
        zr=ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0),
    )
    products = sky_products(ensemble, aoi)
    return registry.write_run_dir(
        meta.run_id,
        lambda folder: write_rain_products(folder, ensemble, products, aoi),
        meta=meta,
    )


def test_a_run_reports_the_provenance_only_its_cube_records(tmp_path: Path) -> None:
    """The nowcaster, the seed and the Z-R relation live in rain/cube.zarr, not in run.json."""
    registry = RunRegistry(tmp_path / "runs")
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    run_dir = _write_rain_run(registry, meta)

    assert has_rain_products(run_dir)
    rain = read_run_rain(run_dir, meta)
    assert rain.run_id == meta.run_id
    assert rain.mode == "baked"
    assert rain.cycle_ts == meta.cycle_ts
    assert rain.city == "mumbai"
    assert rain.nowcaster == "fallback_steps"
    assert rain.seed == 2019
    assert (rain.zr_a, rain.zr_b, rain.zr_source) == (200.0, 1.6, "marshall_palmer")
    assert rain.zr_pairs is None  # the cube does not record it, so it is not invented
    assert rain.exceedance_mm_h == (20.0, 40.0)
    assert (rain.n_members, rain.n_steps, rain.step_min) == (4, 3, 5.0)
    assert rain.notes == tuple(meta.notes)


def test_the_band_is_the_quantiles_of_the_members_it_ships_with(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "runs")
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    rain = read_run_rain(_write_rain_run(registry, meta), meta)

    hyetographs = np.asarray(rain.products.aoi_hyetographs, dtype=np.float64)
    expected = np.quantile(hyetographs, [0.1, 0.5, 0.9], axis=0, method="linear")
    assert rain.aoi_band() == pytest.approx(expected)
    assert rain.aoi_band().shape == (3, 3)


def test_a_run_without_rain_products_names_make_bake(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "runs")
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    run_dir = registry.write_meta(meta)

    assert not has_rain_products(run_dir)
    with pytest.raises(FileNotFoundError, match="make bake BUNDLE=MUM-2019-07-02"):
        read_run_rain(run_dir, meta)
