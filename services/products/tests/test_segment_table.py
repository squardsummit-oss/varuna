"""The column-wise segment table is the row-built one, to the bit (SPEC.md 11.8, rule 8; P10.4).

A faster products writer is only acceptable if a bake cannot tell it apart from the slow one, so
nothing here checks "close": arrays are compared with ``np.array_equal`` and frames with
``assert_frame_equal(check_exact=True)``, dtypes and row order included. The reference is always
the row loop ``depth.segment_forecast`` shipped before E9 wired this module into it, frozen
verbatim below as ``_row_loop_forecast``; both this module and the wired ``depth.segment_forecast``
are held to it.
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from varuna_products import depth as D
from varuna_products import segment_table as S
from varuna_schemas.constants import IST
from varuna_schemas.paths import city_dir, runs_dir

SEED = 2019
N_STEPS = 36
BAKED_0840 = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"


def _times(n: int = N_STEPS) -> tuple[datetime, ...]:
    t0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
    return tuple(t0 + timedelta(minutes=5 * (k + 1)) for k in range(n))


def _index(rng: np.random.Generator, n_seg: int, n_cells: int):
    """A CSR index with empty segments, many repeated cell counts and a few unique ones."""
    lengths = rng.choice([0, 1, 2, 3, 5, 8, 13, 21], size=n_seg)
    lengths[:5] = [0, 34, 55, 0, 89]  # empties and counts no other segment has
    offsets = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    cells = rng.integers(0, n_cells, size=int(offsets[-1]), dtype=np.int64)
    ids = tuple(f"S{k:05d}-000" for k in range(n_seg))
    return ids, offsets, cells


def _field(rng: np.random.Generator, shape: tuple[int, ...], dtype) -> np.ndarray:
    """Depth in metres: mostly dry, a wet tail past every profile threshold, ties included."""
    wet = rng.gamma(0.6, 0.25, size=shape)
    wet[rng.random(shape) < 0.45] = 0.0
    wet[rng.random(shape) < 0.05] = 0.3  # repeated values exercise the percentile's tie path
    return wet.astype(dtype)


def _loop_sample(depth_m: np.ndarray, index) -> np.ndarray:
    """The sampling loop from ``depth.segment_forecast``, verbatim."""
    segment_ids, offsets, cells = index
    n_steps = depth_m.shape[0]
    n_seg = len(segment_ids)
    flat = depth_m.reshape(n_steps, -1)
    depth_cm = np.zeros((n_steps, n_seg), dtype=np.float64)
    for k in range(n_seg):
        lo, hi = int(offsets[k]), int(offsets[k + 1])
        if hi <= lo:
            continue
        picked = flat[:, cells[lo:hi]]
        depth_cm[:, k] = np.percentile(picked, D.SEGMENT_PERCENTILE, axis=1) * 100.0
    return depth_cm


def _row_loop_forecast(depth_m: np.ndarray, times, index, run_id: str, member_depth_cm=None):
    """``depth.segment_forecast`` as it was before E9 wired this module in, verbatim bar the log.

    Frozen here because ``depth.segment_forecast`` now *is* the column-wise path, so comparing
    the two in the library would compare a function with itself. This is the reference every
    equivalence test below is held to: one Python row per segment per step, built by
    ``DataFrame.from_records``."""
    import json

    segment_ids, _, _ = index
    n_steps = depth_m.shape[0]
    depth_cm = _loop_sample(depth_m, index)
    thresholds = sorted(set(D.EXCEEDANCE_CM) | set(D.PROFILE_THRESHOLD_CM.values()))
    if member_depth_cm is None:
        p10 = p50 = p90 = depth_cm
        prob = {t: (depth_cm > t).astype(np.float64) for t in thresholds}
    else:
        level = D._member_levels(depth_cm, member_depth_cm)
        p10, p50, p90 = np.percentile(level, (10.0, 50.0, 90.0), axis=0)
        prob = {t: (level > t).mean(axis=0, dtype=np.float64) for t in thresholds}

    rows: list[dict[str, object]] = []
    for k, seg_id in enumerate(segment_ids):
        safe_until: dict[str, object] = {}
        for profile, threshold in D.PROFILE_THRESHOLD_CM.items():
            risky = np.flatnonzero(prob[threshold][:, k] > D.PROFILE_TOLERANCE[profile])
            safe_until[profile] = times[int(risky[0])].isoformat() if risky.size else None
        blob = json.dumps(safe_until)
        for step in range(n_steps):
            rows.append(
                {
                    "run_id": run_id,
                    "segment_id": seg_id,
                    "valid_ts": times[step],
                    "depth_p10_cm": float(p10[step, k]),
                    "depth_p50_cm": float(p50[step, k]),
                    "depth_p90_cm": float(p90[step, k]),
                    "p_gt_15": float(prob[15.0][step, k]),
                    "p_gt_30": float(prob[30.0][step, k]),
                    "p_gt_45": float(prob[45.0][step, k]),
                    "p_gt_60": float(prob[60.0][step, k]),
                    "safe_until": blob,
                }
            )
    return pd.DataFrame.from_records(rows), depth_cm


def _members(rng: np.random.Generator, depth_cm: np.ndarray, n: int, steps: int) -> np.ndarray:
    """A seeded member stack straddling the Twin level, so probabilities land strictly in (0, 1)."""
    noise = rng.normal(0.0, 12.0, size=(n, steps, depth_cm.shape[1]))
    return np.maximum(depth_cm[None, :steps] + noise, 0.0).astype(np.float32)


# ============================================================================ sampling
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_sampling_is_bitwise_the_shipped_path(dtype) -> None:
    """Grouping segments by cell count gives exactly the loop's depth, in float32 and float64."""
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), dtype)

    _, shipped = _row_loop_forecast(field, _times(), index, "TEST-RUN")
    grouped = S.sample_segments(field, index)

    assert grouped.dtype == np.float64
    assert np.array_equal(grouped, shipped)
    assert (grouped[:, [0, 3]] == 0.0).all(), "a segment with no cells stays dry, as in the loop"
    assert np.unique(np.diff(index[1])).size > 5, "the fixture must exercise several groups"


def test_chunking_cannot_change_a_value() -> None:
    """Splitting a group into chunks, down to one segment each, gives the same bits."""
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), np.float64)

    whole = S.sample_segments(field, index)
    assert np.array_equal(S.sample_segments(field, index, max_gather=1), whole)
    assert np.array_equal(S.sample_segments(field, index, max_gather=5_000), whole)


def test_an_index_shorter_than_its_segments_is_refused() -> None:
    rng = np.random.default_rng(SEED)
    ids, offsets, cells = _index(rng, 20, 100)
    with pytest.raises(ValueError, match="offsets"):
        S.sample_segments(np.zeros((2, 10, 10)), (ids, offsets[:-2], cells))


def test_sampling_on_the_real_mumbai_index_is_the_loop() -> None:
    """The real index has about a hundred distinct cell counts and a 189-cell longest segment.

    The field is seeded rather than a Twin run - no baked run keeps its depth cube - but it is
    the index's structure the grouping depends on, and that is the real one."""
    root = city_dir("mumbai")
    if not (root / "segment_cells.npz").is_file():
        pytest.skip("needs city/mumbai/segment_cells.npz: run `make city CITY=mumbai`")
    # Read the file itself rather than through `segment_cell_index`: that call rebuilds an index
    # whose ids differ from `segments.parquet`, and a test must never rewrite `city/`.
    with np.load(root / "segment_cells.npz", allow_pickle=True) as data:
        index = (tuple(data["segment_ids"].tolist()), data["offsets"], data["cells"])
    rng = np.random.default_rng(SEED)
    n_cells = int(index[2].max()) + 1
    field = _field(rng, (N_STEPS, 1, n_cells), np.float64)

    assert np.unique(np.diff(index[1])).size > 50
    assert np.array_equal(S.sample_segments(field, index), _loop_sample(field, index))


# ============================================================================ frame
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_single_member_frame_equals_the_row_built_frame(dtype) -> None:
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), dtype)

    shipped, shipped_cm = _row_loop_forecast(field, _times(), index, "TEST-RUN")
    frame, depth_cm = S.segment_forecast_columnar(field, _times(), index, "TEST-RUN")
    wired, wired_cm = D.segment_forecast(field, _times(), index, "TEST-RUN")

    pd.testing.assert_frame_equal(frame, shipped, check_exact=True)
    assert np.array_equal(depth_cm, shipped_cm)
    pd.testing.assert_frame_equal(wired, shipped, check_exact=True)
    assert np.array_equal(wired_cm, shipped_cm)


@pytest.mark.parametrize("member_steps", [N_STEPS, 24])
def test_twenty_member_frame_equals_the_row_built_frame(member_steps: int) -> None:
    """With members, including a member horizon shorter than the Twin's."""
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), np.float64)
    members = _members(rng, S.sample_segments(field, index), 20, member_steps)

    shipped, shipped_cm = _row_loop_forecast(
        field, _times(), index, "TEST-RUN", member_depth_cm=members
    )
    frame, depth_cm = S.segment_forecast_columnar(
        field, _times(), index, "TEST-RUN", member_depth_cm=members
    )
    wired, wired_cm = D.segment_forecast(
        field, _times(), index, "TEST-RUN", member_depth_cm=members
    )

    pd.testing.assert_frame_equal(frame, shipped, check_exact=True)
    assert np.array_equal(depth_cm, shipped_cm)
    pd.testing.assert_frame_equal(wired, shipped, check_exact=True)
    assert np.array_equal(wired_cm, shipped_cm)
    fractional = shipped.p_gt_30[(shipped.p_gt_30 > 0.0) & (shipped.p_gt_30 < 1.0)]
    assert not fractional.empty, "the fixture must carry a real spread, or the test is vacuous"


def test_a_fifty_member_stack_is_still_the_row_built_frame() -> None:
    """P7.6's ensemble is fifty members, and nothing in the products may count on twenty.

    The stack stopped being "one emulator run per Sky rain member" when
    ``varuna_flash.ensemble`` put a blockage draw and a storage-coefficient draw on each member,
    so the member count is now a parameter of a different service. The row loop this module is
    held to never knew the count either; this is the test that says so out loud, and it would
    catch a twenty-shaped assumption reaching in through a percentile axis or a reshape.

    Fifty is also where the memory goes: a Mumbai stack of fifty is 153 MB and
    ``depth._member_levels`` copies it once. The fixture here is small on purpose - the cost is
    measured in that module's docstring, not paid in the suite.
    """
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), np.float64)
    members = _members(rng, S.sample_segments(field, index), 50, N_STEPS)

    shipped, shipped_cm = _row_loop_forecast(
        field, _times(), index, "TEST-RUN", member_depth_cm=members
    )
    wired, wired_cm = D.segment_forecast(
        field, _times(), index, "TEST-RUN", member_depth_cm=members
    )
    pd.testing.assert_frame_equal(wired, shipped, check_exact=True)
    assert np.array_equal(wired_cm, shipped_cm)

    statistics = S.ensemble_statistics(S.sample_segments(field, index), members)
    assert statistics.n_members == 50
    # Fifty members resolve a probability to 0.02, where twenty resolve it to 0.05. If the
    # exceedance were still being taken over twenty of them, no value would land off the coarser
    # grid - so this is the assertion that the extra thirty members reached the probability.
    fine = statistics.prob[30.0]
    off_the_twentieths = np.abs(fine * 20.0 - np.round(fine * 20.0)) > 1e-9
    assert off_the_twentieths.any(), "no probability needed fifty members to express"


def test_safe_until_strings_are_the_loops_strings() -> None:
    rng = np.random.default_rng(SEED)
    index = _index(rng, 300, 40 * 40)
    field = _field(rng, (N_STEPS, 40, 40), np.float64)
    members = _members(rng, S.sample_segments(field, index), 20, N_STEPS)

    shipped, _ = _row_loop_forecast(field, _times(), index, "TEST-RUN", member_depth_cm=members)
    statistics = S.ensemble_statistics(S.sample_segments(field, index), members)
    blobs = S.safe_until_blobs(statistics.prob, _times())

    assert blobs == shipped.safe_until.tolist()[::N_STEPS]
    assert len(set(blobs)) > 3, "several distinct safe-until answers, or the dedupe is untested"


def test_zero_segments_give_the_same_empty_frame() -> None:
    index = ((), np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.int64))
    field = np.zeros((N_STEPS, 4, 4))
    shipped, shipped_cm = _row_loop_forecast(field, _times(), index, "TEST-RUN")
    frame, depth_cm = S.segment_forecast_columnar(field, _times(), index, "TEST-RUN")
    pd.testing.assert_frame_equal(frame, shipped, check_exact=True)
    assert np.array_equal(depth_cm, shipped_cm)


def test_fewer_times_than_steps_is_refused() -> None:
    rng = np.random.default_rng(SEED)
    index = _index(rng, 10, 100)
    field = _field(rng, (N_STEPS, 10, 10), np.float64)
    with pytest.raises(ValueError, match="times"):
        S.segment_forecast_columnar(field, _times(N_STEPS - 1), index, "TEST-RUN")


def test_the_baked_0840_product_is_rebuilt_exactly() -> None:
    """The 08:40 run's own quantiles and probabilities, laid out again, give back its parquet.

    This is the shipped 50-member product rather than a fixture: its safe-until strings are
    re-derived from its ``p_gt`` columns (every profile threshold is one of them) and the frame is
    compared after a parquet round trip, because the file is the product of record."""
    path = runs_dir() / BAKED_0840 / "segment_forecast.parquet"
    if not path.is_file():
        pytest.skip(f"needs a baked {BAKED_0840} under VARUNA_DATA_DIR/runs")
    stored = pd.read_parquet(path)
    ids = tuple(pd.unique(stored["segment_id"]).tolist())
    n_seg = len(ids)
    n_steps = len(stored) // n_seg

    def field(column: str) -> np.ndarray:
        return stored[column].to_numpy(dtype=np.float64).reshape(n_seg, n_steps).T

    statistics = S.SegmentStatistics(
        n_members=0,  # not stored in the parquet, and not used to build the frame
        p10=field("depth_p10_cm"),
        p50=field("depth_p50_cm"),
        p90=field("depth_p90_cm"),
        prob={t: field(f"p_gt_{t:g}") for t in D.EXCEEDANCE_CM},
    )
    times = tuple(stored["valid_ts"].iloc[:n_steps].dt.to_pydatetime())
    blobs = S.safe_until_blobs(statistics.prob, times)
    assert blobs == stored["safe_until"].tolist()[::n_steps]

    rebuilt = S.forecast_frame(str(stored["run_id"].iloc[0]), ids, times, statistics, blobs)
    buffer = io.BytesIO()
    rebuilt.to_parquet(buffer, index=False)
    buffer.seek(0)
    pd.testing.assert_frame_equal(pd.read_parquet(buffer), stored, check_exact=True)


# ============================================================================ geometry
def _segments_table(root: Path, shift_m: float = 0.0) -> None:
    import geopandas as gpd
    from shapely.geometry import LineString

    root.mkdir(parents=True, exist_ok=True)
    x0 = 272_000.0 + shift_m
    gpd.GeoDataFrame(
        {"segment_id": ["S1-000", "S2-000"]},
        geometry=[
            LineString([(x0, 2_104_000.0), (x0 + 120.0, 2_104_000.0)]),
            LineString([(x0, 2_104_500.0), (x0, 2_104_620.0), (x0 + 60.0, 2_104_700.0)]),
        ],
        crs="EPSG:32643",
    ).to_parquet(root / "segments.parquet")


def test_segment_points_are_memoised_until_the_table_changes(tmp_path, monkeypatch) -> None:
    root = tmp_path / "mumbai"
    _segments_table(root)
    S.clear_segment_points_cache()
    calls = {"n": 0}
    original = D._read_segment_points

    def counted(city_root):
        calls["n"] += 1
        return original(city_root)

    monkeypatch.setattr(D, "_read_segment_points", counted)

    first = S.segment_points_cached(root)
    assert first == original(root)
    first["S1-000"] = (0.0, 0.0)  # a caller's edit must not reach the next caller
    second = S.segment_points_cached(root)
    assert calls["n"] == 1
    assert second == original(root)

    # A rebuilt city: new geometry, and an mtime that is certainly different.
    _segments_table(root, shift_m=500.0)
    stat = (root / "segments.parquet").stat()
    os.utime(root / "segments.parquet", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    third = S.segment_points_cached(root)
    assert calls["n"] == 2
    assert third == original(root)
    assert third != second
    S.clear_segment_points_cache()


def test_the_public_segment_points_is_the_memo(tmp_path, monkeypatch) -> None:
    """Since E9 the products stage's own call, ``depth.segment_points``, reads the table once."""
    root = tmp_path / "mumbai"
    _segments_table(root)
    S.clear_segment_points_cache()
    calls = {"n": 0}
    original = D._read_segment_points

    def counted(city_root):
        calls["n"] += 1
        return original(city_root)

    monkeypatch.setattr(D, "_read_segment_points", counted)
    first = D.segment_points(root)
    second = D.segment_points(root)
    assert calls["n"] == 1
    assert first == second == original(root)
    assert first is not second, "each caller gets its own copy"
    S.clear_segment_points_cache()


def test_a_city_without_segments_is_not_memoised(tmp_path) -> None:
    S.clear_segment_points_cache()
    root = tmp_path / "chennai"
    assert S.segment_points_cached(root) == {}
    _segments_table(root)
    assert len(S.segment_points_cached(root)) == 2
    S.clear_segment_points_cache()


def test_the_time_bar_band_is_quantiles_of_the_mean_not_the_mean_of_quantiles() -> None:
    """SPEC.md 7.2: the p10-p90 of AOI-mean depth, across members, on the Twin's level."""
    import numpy as np
    from varuna_products.depth import aoi_depth_band

    twin = np.array([[10.0, 20.0], [30.0, 40.0]])  # 2 steps x 2 streets: means 15 and 35
    # Ten members whose street means differ by -4.5 ... +4.5 cm from their own mean at step 0,
    # and not at all at step 1.
    offsets = np.arange(10, dtype=np.float64) - 4.5
    members = np.zeros((10, 2, 2))
    members[:, 0, :] = offsets[:, None]
    band = aoi_depth_band(twin, members)
    assert band is not None
    assert band["p50"] == [15.0, 35.0]
    assert band["p10"][0] == pytest.approx(15.0 + np.percentile(offsets, 10), abs=1e-3)
    assert band["p90"][0] == pytest.approx(15.0 + np.percentile(offsets, 90), abs=1e-3)
    assert band["p10"][1] == band["p90"][1] == 35.0


def test_one_member_draws_no_band() -> None:
    import numpy as np
    from varuna_products.depth import aoi_depth_band

    twin = np.ones((3, 4))
    assert aoi_depth_band(twin, None) is None
    assert aoi_depth_band(twin, np.ones((1, 3, 4))) is None
