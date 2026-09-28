"""The per-segment forecast table, built by array operations instead of a Python loop per row
(SPEC.md 11.8, 14; task P10.4).

:func:`varuna_products.depth.segment_forecast` is correct and slow. On the 08:40 Mumbai cycle it
spends 2.8 s taking a percentile one segment at a time and most of another 10 s building 766,656
row dictionaries, one per segment per step, which pandas then has to take apart again. Section
11.8 gives the whole products stage 2 s.

This module computes the same frame two ways faster, and **it is only worth having if the answer
is the same bits**. Every function here is tested against the path it replaces with
``np.array_equal`` and ``assert_frame_equal(check_exact=True)`` including dtypes and row order,
on synthetic fields and on the real Mumbai segment index. Since task E9 ``depth.segment_forecast``
is composed from these functions, and the tests hold both to a frozen copy of the row loop.

**Sampling by group.** A segment's depth is the 90th percentile of the cells in its 15 m buffer
(``depth.SEGMENT_PERCENTILE``). The segments do not all have the same number of cells, which is
why the original loops. But they only have about a hundred *distinct* cell counts, so segments
with equal counts are gathered into one ``(n_steps, n_segments, n_cells)`` block and the
percentile is taken over the last axis in a single call. The percentile of a row does not depend
on which other rows share the call, so the result is the loop's to the bit.

**Columns, not rows.** The frame is segment-major - every step of the first segment, then every
step of the next - so a ``(n_steps, n_segments)`` field becomes its column by ``.T.ravel()``,
ids and safe-until strings by repeating, and times by tiling. What makes this *equal* rather than
merely similar is dtype inference: ``DataFrame.from_records`` infers each column from Python
values. So each non-numeric column is inferred once from its distinct values in a short Series
and then indexed by position, which carries the inferred dtype (``str``, a tz-aware
``datetime64[us]``) to every row without asking pandas to infer it again 766,656 times.

**Safe-until by pattern.** Most of the city shares a handful of safe-until answers - on a dry
street every profile is ``null`` - so the JSON string is rendered once per distinct pattern of
first-risky steps and then looked up, rather than once per segment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from types import ModuleType

    import pandas as pd
    from numpy.typing import NDArray

__all__ = [
    "MAX_GATHER_ELEMENTS",
    "SegmentStatistics",
    "clear_segment_points_cache",
    "ensemble_statistics",
    "forecast_frame",
    "safe_until_blobs",
    "sample_segments",
    "segment_forecast_columnar",
    "segment_points_cached",
]

MAX_GATHER_ELEMENTS = 1 << 23
"""Most depth values one gather copies at once (8.4 M, about 64 MB in float64).

A group is split into chunks of segments below this, so peak memory does not grow with the size
of the largest group. The chunking cannot change a value: each segment's percentile is computed
from its own row whatever else is in the chunk."""


def _depth() -> ModuleType:
    """``varuna_products.depth``, imported when called rather than when this module loads.

    E9 makes ``depth.py`` call into this module. If both imported each other at the top, whichever
    loaded first would find the other half-initialised and fail, so the constants and the member
    re-centring are fetched here at call time instead of being copied (a copied constant is one
    that can drift from the one the product is documented by)."""
    from varuna_products import depth

    return depth


# ============================================================================ sampling
def sample_segments(
    depth_m: NDArray[np.floating],
    index: tuple[Sequence[str], NDArray[np.integer], NDArray[np.integer]],
    *,
    percentile: float | None = None,
    max_gather: int = MAX_GATHER_ELEMENTS,
) -> NDArray[np.float64]:
    """Depth per segment per step in cm, ``(n_steps, n_segments)``, from a Twin depth field.

    ``depth_m`` is ``(n_steps, n_rows, n_cols)`` in metres. ``index`` is the CSR segment-cell
    index exactly as :func:`varuna_products.depth.segment_cell_index` returns it: segment ``k``'s
    flat cell indices are ``cells[offsets[k]:offsets[k + 1]]``. A segment with no cells stays at
    0 cm, as it does in the loop. ``percentile`` defaults to ``depth.SEGMENT_PERCENTILE``.

    Bitwise equal to the per-segment loop in ``depth.segment_forecast`` for float32 and float64
    fields, including the loop's dtype path: the percentile is taken in the field's own dtype,
    scaled to cm there, and only then stored in the float64 result.

    Raises:
        ValueError: if ``offsets`` is shorter than ``n_segments + 1``, which would leave a
            segment's cells undefined (the loop fails on it too, with an ``IndexError``).
    """
    if percentile is None:
        percentile = _depth().SEGMENT_PERCENTILE
    segment_ids, offsets, cells = index
    n_steps = int(depth_m.shape[0])
    n_seg = len(segment_ids)
    offsets = np.asarray(offsets)
    if offsets.shape[0] < n_seg + 1:
        raise ValueError(
            f"offsets has {offsets.shape[0]} entries; a CSR index over {n_seg} segments needs "
            f"{n_seg + 1}."
        )
    flat = depth_m.reshape(n_steps, -1)
    depth_cm = np.zeros((n_steps, n_seg), dtype=np.float64)
    if n_seg == 0 or n_steps == 0:
        return depth_cm

    starts = offsets[:n_seg].astype(np.int64)
    lengths = offsets[1 : n_seg + 1].astype(np.int64) - starts
    # Segments sorted by cell count, so each distinct count is one contiguous run of positions.
    order = np.argsort(lengths, kind="stable")
    sorted_lengths = lengths[order]
    distinct, first = np.unique(sorted_lengths, return_index=True)
    bounds = [*first.tolist(), n_seg]

    for g, n_cells in enumerate(distinct.tolist()):
        if n_cells <= 0:  # the loop skips ``hi <= lo``; those segments keep their zero
            continue
        members = order[bounds[g] : bounds[g + 1]]
        per_chunk = max(1, int(max_gather) // max(1, n_steps * n_cells))
        ramp = np.arange(n_cells, dtype=np.int64)
        for lo in range(0, members.size, per_chunk):
            ks = members[lo : lo + per_chunk]
            picked = flat[:, cells[starts[ks][:, None] + ramp[None, :]]]
            depth_cm[:, ks] = np.percentile(picked, percentile, axis=2) * 100.0
    return depth_cm


# ============================================================================ statistics
@dataclass(frozen=True)
class SegmentStatistics:
    """The quantiles and exceedance fractions a segment forecast is built from.

    Every field is ``(n_steps, n_segments)``; ``prob`` is keyed by threshold in cm. With one
    member ``p10``, ``p50`` and ``p90`` are the same array and every probability is 0 or 1, which
    is what a deterministic run honestly has to say (see ``depth.py``'s module docstring)."""

    n_members: int
    p10: NDArray[np.floating]
    p50: NDArray[np.floating]
    p90: NDArray[np.floating]
    prob: dict[float, NDArray[np.float64]]


def ensemble_statistics(
    depth_cm: NDArray[np.floating],
    member_depth_cm: NDArray[np.floating] | None = None,
    thresholds: Sequence[float] | None = None,
) -> SegmentStatistics:
    """Quantiles and ``P(depth > threshold)`` per segment per step, as ``segment_forecast`` has them.

    ``thresholds`` defaults to every exceedance column plus every profile threshold, the same set
    ``depth.segment_forecast`` asks of the field. The member stack is re-centred on the Twin level
    by ``depth._member_levels`` itself rather than by a copy of it, so a change there cannot open
    a gap here."""
    depth = _depth()
    if thresholds is None:
        thresholds = sorted(set(depth.EXCEEDANCE_CM) | set(depth.PROFILE_THRESHOLD_CM.values()))
    if member_depth_cm is None:
        prob = {t: (depth_cm > t).astype(np.float64) for t in thresholds}
        return SegmentStatistics(1, depth_cm, depth_cm, depth_cm, prob)
    level = depth._member_levels(depth_cm, member_depth_cm)
    p10, p50, p90 = np.percentile(level, (10.0, 50.0, 90.0), axis=0)
    prob = {t: (level > t).mean(axis=0, dtype=np.float64) for t in thresholds}
    return SegmentStatistics(int(level.shape[0]), p10, p50, p90, prob)


def safe_until_blobs(
    prob: Mapping[float, NDArray[np.floating]],
    times: Sequence[datetime],
    profile_threshold_cm: Mapping[str, float] | None = None,
    profile_tolerance: Mapping[str, float] | None = None,
) -> list[str]:
    """One JSON safe-until string per segment, identical to the strings the row loop writes.

    For each profile the answer is the time of the first step where ``P(depth > threshold)``
    exceeds the profile's tolerance, or ``null`` if no step does (SPEC.md 11.8). Keys keep the
    order of ``profile_threshold_cm`` (default ``depth.PROFILE_THRESHOLD_CM``), because the string
    is the product and ``json.dumps`` writes keys in insertion order.
    """
    depth = _depth()
    thresholds = (
        depth.PROFILE_THRESHOLD_CM if profile_threshold_cm is None else profile_threshold_cm
    )
    tolerance = depth.PROFILE_TOLERANCE if profile_tolerance is None else profile_tolerance
    profiles = list(thresholds.items())
    if not profiles:
        return []
    shape = np.asarray(prob[profiles[0][1]]).shape
    n_seg = int(shape[1])
    if n_seg == 0:
        return []

    first = np.full((n_seg, len(profiles)), -1, dtype=np.int64)
    for j, (profile, threshold) in enumerate(profiles):
        risky = np.asarray(prob[threshold]) > tolerance[profile]
        hit = risky.any(axis=0)
        first[hit, j] = risky.argmax(axis=0)[hit]

    patterns, inverse = np.unique(first, axis=0, return_inverse=True)
    names = [profile for profile, _ in profiles]
    texts = [
        json.dumps(
            {
                name: (times[int(step)].isoformat() if step >= 0 else None)
                for name, step in zip(names, row.tolist(), strict=True)
            }
        )
        for row in patterns
    ]
    return [texts[i] for i in np.asarray(inverse).reshape(-1).tolist()]


# ============================================================================ frame
def _inferred(values: list[Any], positions: NDArray[np.intp]) -> Any:
    """``values`` inferred as a column once, then laid out at ``positions``.

    Inference in pandas depends on the set of values, not on how many times each appears, so a
    Series of the distinct values gets the dtype the full row-built column would - ``str`` for
    ids, a tz-aware ``datetime64[us]`` for times - and ``take`` spreads it without re-inferring."""
    import pandas as pd

    return pd.Series(values).array.take(positions)


def forecast_frame(
    run_id: str,
    segment_ids: Sequence[str],
    times: Sequence[datetime],
    statistics: SegmentStatistics,
    safe_until: Sequence[str],
) -> pd.DataFrame:
    """The ``segment_forecast.parquet`` table, column by column.

    Equal to ``DataFrame.from_records`` over the row dictionaries ``depth.segment_forecast``
    builds: the same columns in the same order, segment-major row order, the same dtypes, the
    same values to the bit. With no segments or no steps it is the same empty, column-less frame
    the row loop produces.

    Raises:
        ValueError: if ``times`` is shorter than the statistics' step axis, or ``safe_until``
            does not hold one string per segment.
    """
    import pandas as pd

    n_steps, n_seg = (int(v) for v in np.asarray(statistics.p50).shape)
    if n_steps == 0 or n_seg == 0:
        return pd.DataFrame.from_records([])
    if len(times) < n_steps:
        raise ValueError(f"times has {len(times)} entries for {n_steps} steps.")
    if len(safe_until) != n_seg:
        raise ValueError(f"safe_until has {len(safe_until)} strings for {n_seg} segments.")
    if len(segment_ids) != n_seg:
        raise ValueError(f"segment_ids has {len(segment_ids)} ids for {n_seg} segments.")

    n_rows = n_steps * n_seg
    segment_at = np.repeat(np.arange(n_seg, dtype=np.intp), n_steps)
    step_at = np.tile(np.arange(n_steps, dtype=np.intp), n_seg)

    def by_row(field: NDArray[np.floating]) -> NDArray[np.float64]:
        # (n_steps, n_segments) -> segment-major rows. float32 widens to float64 exactly, which
        # is what `float(value)` does in the row loop.
        return np.asarray(field).T.ravel().astype(np.float64, copy=False)

    prob = statistics.prob
    columns: dict[str, Any] = {
        "run_id": _inferred([run_id], np.zeros(n_rows, dtype=np.intp)),
        "segment_id": _inferred(list(segment_ids), segment_at),
        "valid_ts": _inferred(list(times[:n_steps]), step_at),
        "depth_p10_cm": by_row(statistics.p10),
        "depth_p50_cm": by_row(statistics.p50),
        "depth_p90_cm": by_row(statistics.p90),
        "p_gt_15": by_row(prob[15.0]),
        "p_gt_30": by_row(prob[30.0]),
        "p_gt_45": by_row(prob[45.0]),
        "p_gt_60": by_row(prob[60.0]),
        "safe_until": _inferred(list(safe_until), segment_at),
    }
    return pd.DataFrame(columns)


def segment_forecast_columnar(
    depth_m: NDArray[np.floating],
    times: tuple[datetime, ...],
    index: tuple[Sequence[str], NDArray[np.integer], NDArray[np.integer]],
    run_id: str,
    member_depth_cm: NDArray[np.floating] | None = None,
) -> tuple[pd.DataFrame, NDArray[np.float64]]:
    """``depth.segment_forecast`` without the loops: the same ``(frame, depth_cm)``, faster.

    It does not log; the ``products.segment_forecast`` line stays with its caller, which has the
    statistics it reports. Composed from :func:`sample_segments`, :func:`ensemble_statistics`,
    :func:`safe_until_blobs` and :func:`forecast_frame`. ``depth.segment_forecast`` composes the
    same four parts itself, because it logs their statistics; the tests hold both this wrapper and
    that function to the frozen row loop."""
    depth_cm = sample_segments(depth_m, index)
    statistics = ensemble_statistics(depth_cm, member_depth_cm)
    blobs = safe_until_blobs(statistics.prob, times)
    frame = forecast_frame(run_id, index[0], times, statistics, blobs)
    return frame, depth_cm


# ============================================================================ geometry
_POINTS_CACHE: dict[Path, tuple[int, int, dict[str, tuple[float, float]]]] = {}


def segment_points_cached(city_root: Path) -> dict[str, tuple[float, float]]:
    """``depth.segment_points`` memoised in-process on ``segments.parquet``'s mtime and size.

    The midpoints are pure geometry, but reading and reprojecting 21,296 lines costs about 1.25 s
    and the products stage paid it on every cycle. The memo is invalidated the moment the table
    is rewritten (a rebuilt city), and a missing table is never cached, so a city built after the
    first call is still read. A fresh dict is returned each time: a caller that edits its copy
    cannot change what the next cycle is given."""
    table = Path(city_root) / "segments.parquet"
    # `_read_segment_points` rather than `segment_points`: since E9 the public function *is* this
    # memo, so calling it here would recurse.
    try:
        stat = table.stat()
    except OSError:
        return _depth()._read_segment_points(city_root)
    key = table.resolve()
    stamp = (int(stat.st_mtime_ns), int(stat.st_size))
    hit = _POINTS_CACHE.get(key)
    if hit is not None and (hit[0], hit[1]) == stamp:
        return dict(hit[2])
    points = _depth()._read_segment_points(city_root)
    _POINTS_CACHE[key] = (*stamp, points)
    return dict(points)


def clear_segment_points_cache() -> None:
    """Forget every memoised midpoint table (tests, or a process that rebuilds a city in place)."""
    _POINTS_CACHE.clear()
