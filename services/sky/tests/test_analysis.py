"""The observed-rain analysis series a hot-started Twin spins up on (storm-realism plan, R3).

What is held here, and why each matters:

* **no look-ahead** - a step never reads a radar frame, or uses a gauge reading, from after the
  instant it ends at. A catch-up that peeked forward would forecast with hindsight.
* **ladder independence** - the step for an instant is byte-identical wherever the series
  starts, so ``make bake`` at every 5 minutes and at ``--every 30`` agree (rule 8, and the
  critique of the plan).
* **dry in, dry out**.
* **cadence** - 10-minute radar frames become 5-minute steps by holding each frame for two
  steps, labelled with the instant each step ends at, which is how the Twin applies a cube.
* **the truth field is never opened** - it is the verification's answer and live mode has none.

The last test measures the demo bundle when it is on disk, as information rather than an
assertion: the analysis-to-truth mass ratio is a published number, not a tuned one.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer
from varuna_schemas.constants import IST
from varuna_sky.analysis import (
    MAX_FRAME_AGE_MIN,
    ArrayFrameSource,
    analysis_rain,
    analysis_series,
)
from varuna_sky.products import AoiGrid, aoi_mean_weights
from varuna_sky.qc import CLUTTER_FRAMES
from varuna_sky.types import RadarGrid

T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
RADAR_MIN = 10
N_PX = 40
RES_M = 500.0
LEFT, TOP = 280_000.0, 2_140_000.0
CRS = "EPSG:32643"
STATIONS = 12


def at(hhmm: str) -> datetime:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime(2019, 7, 2, hours, minutes, tzinfo=IST)


def sky_grid() -> RadarGrid:
    return RadarGrid(
        crs=CRS, res_m=RES_M, n_px=N_PX, transform=(RES_M, 0.0, LEFT, 0.0, -RES_M, TOP)
    )


def aoi_grid(sky: RadarGrid) -> AoiGrid:
    """A 1.2 x 1.5 km window inside the domain, as Mumbai's AOI sits inside its radar domain."""
    return AoiGrid(
        crs=CRS,
        res_m=30.0,
        width=40,
        height=50,
        transform=(30.0, 0.0, sky.left + 5_000.0, 0.0, -30.0, sky.top - 5_000.0),
    )


def storm(n_frames: int, *, seed: int = 2019) -> tuple[np.ndarray, np.ndarray]:
    """Two moving, pulsing cells on a light background, rendered the way the bundles are.

    Returns the dBZ frames (Marshall-Palmer inverse, 1 dBZ speckle, floored to 5 dBZ classes,
    ``nan`` below the dry floor) and the rain field they were rendered from.
    """
    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[0:N_PX, 0:N_PX].astype(np.float64)
    fields = []
    for i in range(n_frames):
        pulse = 0.55 + 0.45 * np.sin(i / 2.5)
        east = 60.0 * pulse * np.exp(-((rows - 14) ** 2 + (cols - (4 + 1.5 * i)) ** 2) / 32.0)
        west = 30.0 * np.exp(-((rows - 27) ** 2 + (cols - (34 - 1.0 * i)) ** 2) / 18.0)
        fields.append(1.5 + east + west)
    rain = np.stack(fields)
    dbz = 10.0 * np.log10(200.0 * rain**1.6) + rng.normal(0.0, 1.0, rain.shape)
    dbz = np.floor(dbz / 5.0) * 5.0
    return np.where(rain >= 0.1, dbz, np.nan), rain


def gauges_for(rain: np.ndarray, times: list[datetime], *, seed: int = 7) -> pd.DataFrame:
    """Five-minute gauge readings at twelve stations, sampled from the rain with 10 % noise."""
    rng = np.random.default_rng(seed)
    grid = sky_grid()
    to_lonlat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    picks = [(8 + 7 * (k % 4), 6 + 9 * (k // 4)) for k in range(STATIONS)]
    rows = []
    n_readings = (len(times) - 1) * 2 + 1
    for step in range(n_readings):
        ts = times[0] + timedelta(minutes=5 * step)
        frame = min(round((step * 5 - 2.5) / RADAR_MIN), len(times) - 1)
        for k, (row, col) in enumerate(picks):
            lon, lat = to_lonlat.transform(*grid.xy(row, col))
            rate = float(rain[max(frame, 0), row, col]) * float(rng.uniform(0.9, 1.1))
            rows.append(
                {
                    "ts": ts,
                    "station_id": f"TEST-{k:02d}",
                    "name": f"Test gauge {k}",
                    "lat": round(float(lat), 7),
                    "lon": round(float(lon), 7),
                    "mm_5min": round(rate / 12.0, 3),
                    "synthetic": True,
                }
            )
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST)
    return frame


def radar_times(n_frames: int) -> list[datetime]:
    return [T0 + timedelta(minutes=RADAR_MIN * i) for i in range(n_frames)]


@pytest.fixture(scope="module")
def replay() -> tuple[ArrayFrameSource, pd.DataFrame]:
    """Three hours of frames, 05:40 to 08:40, and the gauges that go with them."""
    times = radar_times(19)
    dbz, rain = storm(len(times))
    return ArrayFrameSource(dbz=dbz, times=tuple(times), grid=sky_grid()), gauges_for(rain, times)


class CappedSource:
    """A frame source that fails the test the moment a frame after ``limit`` is asked for."""

    def __init__(self, inner: ArrayFrameSource, limit: datetime) -> None:
        self.inner = inner
        self.limit = limit
        self.read_times: list[datetime] = []

    @property
    def times(self) -> tuple[datetime, ...]:
        return self.inner.times

    @property
    def grid(self) -> RadarGrid:
        return self.inner.grid

    def read(self, indices):  # type: ignore[no-untyped-def]
        for index in indices:
            ts = self.inner.times[index]
            assert ts <= self.limit, (
                f"read the {ts:%H:%M} frame for a series ending {self.limit:%H:%M}"
            )
            self.read_times.append(ts)
        return self.inner.read(indices)


# ============================================================================ dry
@pytest.mark.parametrize("gauges", ["zero", "none"])
def test_a_dry_radar_gives_a_dry_cube(gauges: str) -> None:
    times = radar_times(8)
    source = ArrayFrameSource(
        dbz=np.full((len(times), N_PX, N_PX), np.nan), times=tuple(times), grid=sky_grid()
    )
    if gauges == "zero":
        table = gauges_for(np.zeros((len(times), N_PX, N_PX)), times)
    else:
        table = pd.DataFrame(columns=["ts", "station_id", "lat", "lon", "mm_5min"])

    series = analysis_series(source, table, T0, at("06:50"))

    assert series.n_steps == 14
    assert np.all(series.rain_mm_h == 0.0)
    assert np.all(series.on_aoi(aoi_grid(source.grid)) == 0.0)
    assert {step.merge_method for step in series.steps} == {"none"}


# ============================================================================ cadence
def test_each_ten_minute_frame_is_held_for_two_five_minute_steps(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    series = analysis_series(source, gauges, T0, at("08:40"))

    assert series.n_steps == 36
    assert series.times == tuple(T0 + timedelta(minutes=5 * (k + 1)) for k in range(36))
    for step in series.steps:
        minutes = (step.valid_ts - T0).total_seconds() / 60.0
        assert step.frame_ts == T0 + timedelta(minutes=RADAR_MIN * (minutes // RADAR_MIN))
        assert step.frame_ts <= step.valid_ts
    # 05:45 holds the 05:40 frame alone; after that, steps labelled f and f+5 share frame f
    assert series.steps[0].frame_ts == T0
    for k in range(1, 35, 2):
        assert series.steps[k].frame_ts == series.steps[k + 1].frame_ts
        assert series.rain_mm_h[k].tobytes() == series.rain_mm_h[k + 1].tobytes()
    assert series.on_aoi(aoi_grid(source.grid)).shape == (36, 50, 40)
    assert series.rain_mm_h.max() > 10.0, "the fixture storm should carry real rain"


def test_the_series_accumulates_the_way_the_twin_integrates(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    aoi = aoi_grid(source.grid)
    series = analysis_series(source, gauges, T0, at("07:40"))
    cube = series.on_aoi(aoi)

    by_weights = series.aoi_mean_mm_h(aoi)
    np.testing.assert_allclose(by_weights, cube.mean(axis=(1, 2)), rtol=1e-12)
    assert series.aoi_accumulation_mm(aoi) == pytest.approx(
        float(cube.mean(axis=(1, 2)).sum()) / 12.0
    )


# ============================================================================ no look-ahead
@pytest.mark.parametrize("t_to", ["05:45", "06:05", "06:40", "07:15", "08:40"])
def test_a_frame_after_the_series_end_is_never_read(replay, t_to: str) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    capped = CappedSource(source, at(t_to))

    series = analysis_series(capped, gauges, T0, at(t_to))

    assert capped.read_times, "the series read no frames at all"
    assert max(capped.read_times) <= at(t_to)
    assert series.times[-1] == at(t_to)


def test_a_step_is_the_same_whether_the_series_ends_at_it_or_later(replay) -> None:  # type: ignore[no-untyped-def]
    """With the capped source this closes the argument per step, not just per series.

    A series ending at 06:45 cannot have read anything after 06:45 (previous test); if its
    06:40 and 06:45 steps equal the ones in a series running on to 08:40, the longer series
    did not use later frames for them either.
    """
    source, gauges = replay
    short = analysis_series(CappedSource(source, at("06:45")), gauges, T0, at("06:45"))
    long = analysis_series(source, gauges, T0, at("08:40"))

    n = short.n_steps
    assert short.rain_mm_h.tobytes() == long.rain_mm_h[:n].tobytes()
    assert [s.to_dict() for s in short.steps] == [s.to_dict() for s in long.steps[:n]]


def test_a_gauge_reading_after_the_step_changes_nothing(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    end = at("07:10")
    poison = gauges[gauges["ts"] > end].copy()
    poison["mm_5min"] = 50.0  # 600 mm/h at every station, only after the series ends
    tampered = pd.concat([gauges[gauges["ts"] <= end], poison], ignore_index=True)

    clean = analysis_series(source, gauges, T0, end)
    dirty = analysis_series(source, tampered, T0, end)

    assert clean.rain_mm_h.tobytes() == dirty.rain_mm_h.tobytes()


# ============================================================================ ladder independence
def test_the_step_for_an_instant_does_not_depend_on_where_the_series_starts(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    aoi = aoi_grid(source.grid)
    series = {
        start: analysis_series(source, gauges, at(start), at("06:45"))
        for start in ("05:40", "06:10", "06:35")
    }
    cubes = {start: s.on_aoi(aoi) for start, s in series.items()}

    for instant in ("06:40", "06:45"):
        steps = {start: s.times.index(at(instant)) for start, s in series.items()}
        reference = series["06:35"]
        k_ref = steps["06:35"]
        for start in ("05:40", "06:10"):
            k = steps[start]
            assert series[start].rain_mm_h[k].tobytes() == reference.rain_mm_h[k_ref].tobytes()
            assert cubes[start][k].tobytes() == cubes["06:35"][k_ref].tobytes()
            assert series[start].steps[k].to_dict() == reference.steps[k_ref].to_dict()
    # the gauges took part: a merge that never ran would make this test vacuous
    assert any(step.zr_source == "adaptive" for step in series["05:40"].steps)
    assert all(step.merge_method == "mfb+idw" for step in series["05:40"].steps)


# ============================================================================ QC history, gaps, spans
def test_clutter_qc_waits_for_six_frames_of_history(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    series = analysis_series(source, gauges, T0, at("07:00"))

    for step in series.steps:
        assert step.clutter_applied == (step.n_frames >= CLUTTER_FRAMES)
    assert not series.steps[0].clutter_applied
    assert series.steps[-1].clutter_applied
    assert series.steps[-1].n_frames == 7, "an hour of 10-minute frames, inclusive"
    assert any("Clutter QC skipped on 5 of 9 frames" in note for note in series.notes)


def test_a_radar_gap_longer_than_one_interval_is_refused(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    """With 06:10 missing, 06:00 may bridge one step (10 min old) and not a second (15 min)."""
    keep = [i for i, ts in enumerate(source.times) if ts != at("06:10")]
    gapped = ArrayFrameSource(
        dbz=source.dbz[keep], times=tuple(source.times[i] for i in keep), grid=source.grid
    )
    assert MAX_FRAME_AGE_MIN == 10.0
    bridged = analysis_series(gapped, gauges, T0, at("06:10"))
    assert bridged.steps[-1].frame_ts == at("06:00")
    with pytest.raises(ValueError, match="gap"):
        analysis_series(gapped, gauges, T0, at("06:15"))


def test_spans_are_whole_steps_after_the_first_frame(replay) -> None:  # type: ignore[no-untyped-def]
    source, gauges = replay
    with pytest.raises(ValueError, match="whole number"):
        analysis_series(source, gauges, T0, T0 + timedelta(minutes=7))
    with pytest.raises(ValueError, match="No radar frame"):
        analysis_series(source, gauges, T0 - timedelta(minutes=10), T0)
    with pytest.raises(ValueError, match="time zone"):
        analysis_series(source, gauges, T0.replace(tzinfo=None), at("06:00"))


# ============================================================================ from a bundle on disk
def test_analysis_rain_reads_the_bundle_and_never_opens_the_truth_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zarr
    from varuna_replay.bundle import BundleLayout, write_cube, write_gauges
    from varuna_replay.domain import StormDomain

    times = radar_times(7)
    dbz, rain = storm(len(times))
    gauges = gauges_for(rain, times)
    layout = BundleLayout(root=tmp_path / "MUM-TEST-ANALYSIS")
    domain = StormDomain(crs=32643, res_m=RES_M, n_px=N_PX, left=LEFT, top=TOP)
    common = {"domain": domain, "t0": T0}
    write_cube(
        layout.radar,
        dbz.astype(np.float32),
        variable="dbz",
        times_min=[RADAR_MIN * i for i in range(len(times))],
        step_min=RADAR_MIN,
        units="dBZ",
        **common,
    )
    write_cube(
        layout.truth,
        np.repeat(rain, 2, axis=0)[: 2 * len(times) - 1].astype(np.float32),
        variable="rain",
        times_min=[5 * i for i in range(2 * len(times) - 1)],
        step_min=5,
        units="mm/h",
        **common,
    )
    write_gauges(layout.gauges, gauges.to_dict("records"))

    opened: list[str] = []

    def guard(name: str):  # type: ignore[no-untyped-def]
        real = getattr(zarr, name)

        def wrapper(store=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(store))
            if "truth" in str(store):
                raise AssertionError(f"the analysis opened the truth field at {store}")
            return real(store, *args, **kwargs)

        return wrapper

    for name in ("open", "open_group", "open_array"):
        monkeypatch.setattr(zarr, name, guard(name))

    aoi = aoi_grid(sky_grid())
    result = analysis_rain(layout.root, T0, at("06:40"), aoi)

    assert any("frames.zarr" in path for path in opened), "the radar cube was not read via zarr"
    assert result.rain_mm_h.shape == (12, 50, 40)
    assert result.bundle == "MUM-TEST-ANALYSIS"
    in_memory = analysis_series(
        ArrayFrameSource(dbz=dbz.astype(np.float32), times=tuple(times), grid=sky_grid()),
        gauges,
        T0,
        at("06:40"),
    )
    assert result.series.rain_mm_h.tobytes() == in_memory.rain_mm_h.tobytes()


# ============================================================================ the demo bundle
def _demo_available() -> bool:
    from varuna_schemas.paths import bundle_dir, city_dir

    return (bundle_dir("MUM-2019-07-02") / "radar" / "frames.zarr").exists() and (
        city_dir("mumbai") / "pipeline.json"
    ).is_file()


@pytest.mark.skipif(not _demo_available(), reason="needs `make bundle` and `make city CITY=mumbai`")
def test_demo_bundle_shape_and_mass_ratio_against_truth(capsys: pytest.CaptureFixture[str]) -> None:
    """05:40-08:40 is (36, 522, 323) on the city grid; the mass ratio is printed, not asserted.

    The truth field is read by *this test*, to measure the module against it, never by the
    module.
    """
    import zarr
    from varuna_schemas.paths import bundle_dir

    result = analysis_rain("MUM-2019-07-02", at("05:40"), at("08:40"))
    assert result.rain_mm_h.shape == (36, 522, 323)
    assert np.isfinite(result.rain_mm_h).all()

    group = zarr.open_group(str(bundle_dir("MUM-2019-07-02") / "truth" / "rain.zarr"), mode="r")
    attrs = dict(group.attrs)
    truth_grid = RadarGrid(
        crs=str(attrs["crs"]),
        res_m=float(attrs["res_m"]),
        n_px=int(attrs["n_px"]),
        transform=tuple(float(v) for v in attrs["transform"]),  # type: ignore[arg-type]
    )
    minutes = np.asarray(group["time_min"][:], dtype=np.float64)
    inside = (minutes >= 0.0) & (minutes <= 180.0)
    rates = np.tensordot(
        np.asarray(group["rain"][:], dtype=np.float64)[inside],
        aoi_mean_weights(truth_grid, result.aoi),
        axes=((1, 2), (0, 1)),
    )
    truth_mm = float(np.sum((rates[:-1] + rates[1:]) / 2.0) * 5.0 / 60.0)
    analysis_mm = result.aoi_accumulation_mm()
    with capsys.disabled():
        print(
            f"\n[R3 information] MUM-2019-07-02 05:40-08:40 AOI mean: analysis {analysis_mm:.1f} mm, "
            f"truth (trapezoid) {truth_mm:.1f} mm, ratio {analysis_mm / truth_mm:.3f}; "
            f"series {result.series.elapsed_ms} ms for {result.series.n_steps} steps "
            f"({os.cpu_count()} CPUs)"
        )
    assert truth_mm > 0.0
