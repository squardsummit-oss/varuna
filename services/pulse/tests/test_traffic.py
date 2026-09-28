"""Does the traffic detector's third filter actually run? (SPEC.md 11.6; task P7.1)

11.6 asks for three filters over a slowdown: it has to be deep against the segment's own
baseline, it has to persist, and it has to have no other explanation - "not explained by
network-wide congestion (neighbouring dry segments within 500 m have z > -1)". The first two are
exercised here because they decide what becomes an observation at all; the third is exercised
through the map ``run_pulse`` builds for it, because the filter was implemented and then never
handed the neighbours it needs, so it had never once fired on a real cycle.

The speeds are written the way the bundle writes them - ISO strings with an offset, one row per
segment per snapshot - so the frames below are the shape the detector meets in the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString
from varuna_pulse import traffic
from varuna_pulse.cycle import _neighbour_map
from varuna_pulse.traffic import detect_anomalies
from varuna_schemas.paths import bundles_dir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
CYCLE_TS = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
SNAPSHOTS = [CYCLE_TS - timedelta(minutes=10), CYCLE_TS - timedelta(minutes=5), CYCLE_TS]

BASELINE_KMH = 40.0
"""An arterial's weekday-hour speed. The z-score is against this, not against a city average."""

FLOODED_KMH = 3.0
"""Under 5 km/h, which SPEC.md 11.6 reads as at least 20 cm of water."""

# UTM 43N (EPSG:32643), the CRS the Mumbai city build uses, so the 500 m radius is metres.
CRS = "EPSG:32643"
CLUSTER_X = 270_000.0
CLUSTER_Y = 2_101_800.0


def _speeds(rows: list[tuple[datetime, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts": ts.isoformat(),
                "segment_id": segment_id,
                "kmh": kmh,
                "baseline_kmh": BASELINE_KMH,
                "synthetic": True,
            }
            for ts, segment_id, kmh in rows
        ]
    )


def _write_segments(city_root: Path, places: dict[str, tuple[float, float]]) -> None:
    """A minimal ``segments.parquet``: one 20 m stub of road per segment, at a given point."""
    city_root.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame(
        {"segment_id": list(places)},
        geometry=[LineString([(x - 10.0, y), (x + 10.0, y)]) for x, y in places.values()],
        crs=CRS,
    )
    frame.to_parquet(city_root / "segments.parquet")


def test_two_slow_snapshots_become_an_observation() -> None:
    """A street below the threshold on both snapshots is evidence of water."""
    speeds = _speeds(
        [
            (SNAPSHOTS[0], "S0-001", BASELINE_KMH),
            (SNAPSHOTS[1], "S0-001", FLOODED_KMH),
            (SNAPSHOTS[2], "S0-001", FLOODED_KMH),
        ]
    )

    observations = detect_anomalies(speeds, at=CYCLE_TS, raining=True)

    assert [o.segment_id for o in observations] == ["S0-001"]
    assert observations[0].z < -2.5
    assert observations[0].n_consecutive == 2
    # The depth the speed supports, with the uncertainty 11.6 attaches to it.
    assert (observations[0].depth_cm, observations[0].depth_sd_cm) == (20.0, 8.0)


def test_one_slow_snapshot_is_not_an_observation() -> None:
    """One dip in an otherwise normal window is a bus at a stop, not a flooded street.

    The persistence filter is what says so: the dip itself is deep (sigma is estimated from the
    snapshot before the window, where this street was moving normally, so the last reading is
    well past the threshold) but only the last of the two scored snapshots is below it.
    """
    speeds = _speeds(
        [
            (SNAPSHOTS[0], "S0-001", BASELINE_KMH),
            (SNAPSHOTS[1], "S0-001", BASELINE_KMH),
            (SNAPSHOTS[2], "S0-001", FLOODED_KMH),
        ]
    )

    assert detect_anomalies(speeds, at=CYCLE_TS, raining=True) == []


def test_a_slowdown_whose_neighbours_are_equally_slow_is_rejected(tmp_path: Path) -> None:
    """Three streets within 500 m all stopped is an area, not three floods.

    The neighbour map comes from ``run_pulse``'s own builder rather than from a literal, because
    the defect this test is here for was the map never being built: the filter was correct and
    the call site passed it nothing. ``S0-100``, five kilometres away with no neighbour in the
    feed, is the control - a local anomaly has to survive the filter or the filter is a mute.
    """
    clustered = {
        "S0-001": (CLUSTER_X, CLUSTER_Y),
        "S0-002": (CLUSTER_X + 150.0, CLUSTER_Y),
        "S0-003": (CLUSTER_X + 300.0, CLUSTER_Y),
    }
    places = {**clustered, "S0-100": (CLUSTER_X + 5_000.0, CLUSTER_Y)}
    _write_segments(tmp_path, places)
    speeds = _speeds(
        [
            (ts, segment_id, BASELINE_KMH if ts == SNAPSHOTS[0] else FLOODED_KMH)
            for ts in SNAPSHOTS
            for segment_id in places
        ]
    )

    untested = detect_anomalies(speeds, at=CYCLE_TS, raining=True)
    assert sorted(o.segment_id for o in untested) == sorted(places)

    neighbours = _neighbour_map(tmp_path, [o.segment_id for o in untested])
    assert neighbours["S0-001"] == ["S0-002", "S0-003"]
    assert neighbours["S0-100"] == []

    kept = detect_anomalies(speeds, at=CYCLE_TS, raining=True, neighbours=neighbours)
    assert [o.segment_id for o in kept] == ["S0-100"]


def test_sigma_comes_from_the_history_not_from_the_two_scored_snapshots() -> None:
    """How erratic the street normally is decides how deep its dip has to be.

    11.6 scores against the segment's own sigma. The feed carries no sigma, and taking it from
    the two snapshots being scored makes it ``|dv|/sqrt(2)`` - two streets equally slow at
    3 km/h then get the same z whatever their normal behaviour was, and on the 2 July feed
    42.4 % of rows fell through to the floor because a segment slow twice has no scatter at all.

    ``S0-ERRATIC`` swings between 20 and 60 km/h all hour, so 3 km/h is 1.77 of its standard
    deviations and not evidence; ``S0-STEADY`` holds 36-44, so the same 3 km/h is 8.86 of its
    own. Under a sigma taken from inside the window both would score -4.6 and both would be
    observations.
    """
    # Twelve snapshots ending ten minutes back, so the two scored below sit outside them.
    history = [CYCLE_TS - timedelta(minutes=5 * (n + 2)) for n in reversed(range(12))]
    swings = {"S0-ERRATIC": (20.0, 60.0), "S0-STEADY": (36.0, 44.0)}
    rows = [
        (ts, segment_id, swing[n % 2])
        for n, ts in enumerate(history)
        for segment_id, swing in swings.items()
    ]
    rows += [
        (ts, segment_id, FLOODED_KMH)
        for ts in (CYCLE_TS - timedelta(minutes=5), CYCLE_TS)
        for segment_id in swings
    ]

    observations = detect_anomalies(_speeds(rows), at=CYCLE_TS, raining=True)

    assert [o.segment_id for o in observations] == ["S0-STEADY"]
    assert observations[0].z < -7.0


# ---- the vectorised detector against the loop it replaced (P7.1, PU5) ---------------------------
#
# The detector was rewritten from a per-segment loop to array operations because it was most of
# Pulse's stage time. The rewrite is only allowed if nothing it returns changed, so the loop is kept
# here as the reference and both run on the same feeds. Equality is exact: the dataclasses compare
# their floats bit for bit.


def _reference_detect(
    speeds: pd.DataFrame,
    *,
    at: datetime,
    raining: bool = True,
    neighbours: dict[str, list[str]] | None = None,
    incident_segments: set[str] | None = None,
) -> list[traffic.TrafficObservation]:
    """The detector as it stood at 1bc503a, before vectorisation. Do not edit."""
    from datetime import UTC

    if not raining or speeds.empty:
        return []

    frame = speeds.copy()
    frame["ts"] = traffic._as_ts(frame["ts"])
    stamps = sorted(frame["ts"].unique())
    if not stamps:
        return []
    cutoff = np.datetime64(at.astimezone(UTC).replace(tzinfo=None))
    target = max((s for s in stamps if s <= cutoff), default=None)
    if target is None:
        return []
    prior = [s for s in stamps if s <= target]
    history = prior[-traffic.MIN_CONSECUTIVE :]
    if len(history) < traffic.MIN_CONSECUTIVE:
        return []

    sigma_history = prior[: -traffic.MIN_CONSECUTIVE][-traffic.SIGMA_WINDOW :]
    sigma_frame = frame[frame["ts"].isin(sigma_history)]
    sigma = (
        pd.Series(dtype=np.float64)
        if sigma_frame.empty
        else sigma_frame.groupby("segment_id")["kmh"].std()
    )
    window = frame[frame["ts"].isin(history)].copy()
    base = window["baseline_kmh"].to_numpy(dtype=np.float64)
    dev = window["kmh"].to_numpy(dtype=np.float64) - base
    sig = window["segment_id"].map(sigma).to_numpy(dtype=np.float64)
    est = np.isfinite(sig) & (sig > 1.0)
    window["z"] = dev / np.where(est, sig, np.maximum(base * 0.2, 3.0))

    per_segment = window.groupby("segment_id")
    incidents = incident_segments or set()
    slow = {str(k): float(g["z"].min()) for k, g in per_segment}
    out: list[traffic.TrafficObservation] = []
    for segment_id, group in per_segment:
        key = str(segment_id)
        ordered = group.sort_values("ts")
        if not bool((ordered["z"] < traffic.ANOMALY_Z).all()):
            continue
        if key in incidents:
            continue
        if neighbours is not None:
            nearby = [slow[n] for n in neighbours.get(key, []) if n in slow]
            if nearby and max(nearby) < traffic.NEIGHBOUR_Z:
                continue
        latest = ordered.iloc[-1]
        speed = float(latest["kmh"])
        depth, sd = traffic.depth_prior_cm(speed)
        if depth <= 0.0:
            continue
        out.append(
            traffic.TrafficObservation(
                segment_id=key,
                ts=at,
                speed_kmh=speed,
                baseline_kmh=float(latest["baseline_kmh"]),
                z=float(latest["z"]),
                depth_cm=depth,
                depth_sd_cm=sd,
                n_consecutive=len(ordered),
            )
        )
    return out


def _messy_feed(seed: int) -> tuple[pd.DataFrame, list[datetime], dict[str, list[str]], set[str]]:
    """A feed with what the clean one lacks: shuffled rows, gaps, NaNs, erratic and steady streets.

    Half the segments collapse to a slow speed for a stretch - some for one snapshot only, some
    above the 15 km/h the depth prior ignores - a few rows are missing so a segment is seen once
    in its window, and a few speeds are NaN.
    """
    rng = np.random.default_rng(seed)
    stamps = [CYCLE_TS - timedelta(minutes=5 * n) for n in reversed(range(24))]
    ids = [f"S{seed}-{n:03d}" for n in range(240)]
    rows = []
    for k, segment in enumerate(ids):
        base = float(rng.uniform(15.0, 50.0))
        spread = float(rng.choice([0.5, 2.0, 6.0, 15.0]))
        onset = int(rng.integers(10, 26)) if k % 2 == 0 else 99
        length = int(rng.integers(1, 6))
        for n, ts in enumerate(stamps):
            if rng.random() < 0.04:
                continue  # a gap in the probe data
            kmh = max(base + float(rng.normal(0.0, spread)), 0.5)
            if onset <= n < onset + length:
                kmh = float(rng.choice([2.0, 4.5, 9.0, 14.0, 16.0]))
            if rng.random() < 0.01:
                kmh = float("nan")
            rows.append(
                {
                    "ts": ts.isoformat(),
                    "segment_id": segment,
                    "kmh": kmh,
                    "baseline_kmh": base,
                    "synthetic": True,
                }
            )
    frame = pd.DataFrame(rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    neighbours = {
        s: sorted(str(x) for x in rng.choice(ids, size=int(rng.integers(0, 6)), replace=False))
        for s in ids
    }
    incidents = {str(x) for x in rng.choice(ids, size=12, replace=False)}
    return frame, stamps, neighbours, incidents


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_vectorised_detector_returns_what_the_loop_returned(seed: int) -> None:
    speeds, stamps, neighbours, incidents = _messy_feed(seed)
    checked = 0
    moments = [stamps[0], stamps[1], stamps[5], stamps[13], stamps[-1], stamps[-1] + timedelta(1)]
    for at in moments:
        for kwargs in (
            {},
            {"neighbours": neighbours},
            {"neighbours": neighbours, "incident_segments": incidents},
        ):
            expected = _reference_detect(speeds, at=at, **kwargs)
            assert traffic.detect_anomalies(speeds, at=at, **kwargs) == expected
            checked += len(expected)
    assert checked > 0  # the feed has to exercise the filters, not agree on nothing


def test_a_window_scores_each_snapshot_as_at_would() -> None:
    """``(start, end]`` is every snapshot in it, each scored as ``at=`` scores it, stamped with it.

    This is the form a carried-forward posterior needs: the observations since the previous cycle
    and not one more, so a flooded street is assimilated once per cycle rather than again from the
    start of the storm every time.
    """
    speeds, stamps, neighbours, _ = _messy_feed(4)
    start, end = stamps[15], stamps[20]

    window = traffic.detect_anomalies(speeds, start=start, end=end, neighbours=neighbours)

    expected = [
        (o.segment_id, stamp, o.speed_kmh, o.z, o.n_consecutive)
        for stamp in stamps[16:21]
        for o in traffic.detect_anomalies(speeds, at=stamp, neighbours=neighbours)
    ]
    assert expected
    assert [(o.segment_id, o.ts, o.speed_kmh, o.z, o.n_consecutive) for o in window] == expected
    assert all(start < o.ts <= end for o in window)
    assert all(o.ts.utcoffset() == end.utcoffset() for o in window)

    # Consecutive windows partition the storm: nothing counted twice, nothing dropped.
    halves = traffic.detect_anomalies(speeds, end=start) + traffic.detect_anomalies(
        speeds, start=start, end=end
    )
    whole = traffic.detect_anomalies(speeds, end=end)
    assert [(o.segment_id, o.ts) for o in halves] == [(o.segment_id, o.ts) for o in whole]


def test_one_snapshot_or_a_window_but_not_both() -> None:
    speeds, stamps, _, _ = _messy_feed(5)
    with pytest.raises(ValueError, match="either at="):
        traffic.detect_anomalies(speeds)
    with pytest.raises(ValueError, match="either at="):
        traffic.detect_anomalies(speeds, at=stamps[-1], end=stamps[-1])
    with pytest.raises(ValueError, match="only applies with end="):
        traffic.detect_anomalies(speeds, at=stamps[-1], start=stamps[0])
    assert traffic.detect_anomalies(speeds, start=stamps[-1], end=stamps[-1]) == []
    assert traffic.detect_anomalies(speeds, at=stamps[-1], raining=False) == []


DEMO_FEED = bundles_dir() / "MUM-2019-07-02" / "traffic" / "speeds.parquet"
DEMO_CYCLES = ["06:10", "06:40", "07:10", "07:40", "08:10", "08:40", "09:10"]


@pytest.mark.skipif(not DEMO_FEED.is_file(), reason="needs `make bundle BUNDLE=MUM-2019-07-02`")
def test_the_demo_feed_gives_the_same_anomalies_at_every_demo_cycle() -> None:
    """The seven cycles the demo ships, 06:10 to 09:10 IST: the same anomalies, to the bit."""
    speeds = pd.read_parquet(DEMO_FEED)
    total = 0
    for hhmm in DEMO_CYCLES:
        at = datetime.fromisoformat(f"2019-07-02T{hhmm}+05:30")
        expected = _reference_detect(speeds, at=at)
        assert traffic.detect_anomalies(speeds, at=at) == expected, hhmm
        total += len(expected)
    assert total > 0
