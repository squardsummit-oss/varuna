"""What a segment forecast is allowed to claim about its own spread (SPEC.md 11.7, 11.8).

The single-member case has to stay exactly as it was - a deterministic Twin run says p10 = p50 =
p90 and nothing else - and the member case has to produce a band that was *measured* across
members rather than widened by construction (rule 6).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from varuna_products.depth import PROFILE_TOLERANCE, segment_forecast
from varuna_schemas.constants import IST

N_STEPS = 4
N_SEG = 3


def _times(n: int = N_STEPS) -> tuple[datetime, ...]:
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    return tuple(t0 + timedelta(minutes=5 * k) for k in range(n))


def _index() -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    """Three segments, one cell each, so the sampled depth is the cell's depth."""
    return (
        ("seg-a", "seg-b", "seg-c"),
        np.array([0, 1, 2, 3], dtype=np.int64),
        np.array([0, 1, 2], dtype=np.int64),
    )


def _twin(depth_cm: np.ndarray) -> np.ndarray:
    """A ``(n_steps, 1, n_segments)`` raster whose cells are the given depths, in metres."""
    return (depth_cm / 100.0).reshape(depth_cm.shape[0], 1, depth_cm.shape[1])


def _ramp() -> np.ndarray:
    """Depth rising past every profile threshold, so safe-until has something to find."""
    return np.array(
        [
            [0.0, 0.0, 0.0],
            [20.0, 10.0, 0.0],
            [40.0, 20.0, 0.0],
            [70.0, 25.0, 0.0],
        ]
    )


def test_without_members_the_quantiles_are_one_number() -> None:
    """A deterministic run has no spread, and the columns say so rather than implying one."""
    frame, depth_cm = segment_forecast(_twin(_ramp()), _times(), _index(), "TEST-RUN")

    assert (frame.depth_p10_cm == frame.depth_p50_cm).all()
    assert (frame.depth_p90_cm == frame.depth_p50_cm).all()
    assert set(frame.p_gt_30.unique()) <= {0.0, 1.0}
    assert depth_cm.shape == (N_STEPS, N_SEG)


def test_two_members_give_a_band_and_a_fractional_exceedance() -> None:
    """The band and the probabilities come from the members disagreeing, not from a widening."""
    twin = _ramp()
    # Two members that straddle 30 cm on segment A at the third step: the Twin says 40 cm, the
    # members deviate by -15 and +15 cm, so exactly half of them clear 45 cm there.
    members = np.stack([twin - 15.0, twin + 15.0]).astype(np.float32)

    frame, _ = segment_forecast(
        _twin(twin), _times(), _index(), "TEST-RUN", member_depth_cm=members
    )

    band = frame.depth_p90_cm - frame.depth_p10_cm
    assert (band > 0).any(), "two disagreeing members must open a band somewhere"
    assert (frame.depth_p10_cm <= frame.depth_p50_cm).all()
    assert (frame.depth_p50_cm <= frame.depth_p90_cm).all()

    fractional = frame.p_gt_45[(frame.p_gt_45 > 0.0) & (frame.p_gt_45 < 1.0)]
    assert not fractional.empty, "one member above and one below a threshold is a probability"
    assert set(fractional.unique()) == {0.5}


def test_the_member_mean_is_the_twin_level() -> None:
    """ADR-0025: Flash supplies the spread, never the level.

    A lopsided pair - one member 40 cm high, the other 20 cm low - must not drag the level with
    it, because only each member's deviation from the member *mean* is carried across. The depths
    here are well clear of zero so nothing is clipped and the identity is exact.
    """
    twin = np.full((N_STEPS, N_SEG), 50.0)
    members = np.stack([twin + 40.0, twin - 20.0]).astype(np.float32)

    frame, _ = segment_forecast(
        _twin(twin), _times(), _index(), "TEST-RUN", member_depth_cm=members
    )

    # Two members put the median on the mean, and the mean of the re-centred stack is the Twin.
    assert np.allclose(frame.depth_p50_cm.to_numpy(), 50.0, atol=1e-3)
    assert np.allclose(frame.depth_p10_cm.to_numpy(), 50.0 - 24.0, atol=1e-3)  # 10th of +/-30
    assert np.allclose(frame.depth_p90_cm.to_numpy(), 50.0 + 24.0, atol=1e-3)


def test_a_negative_level_is_clipped_to_dry() -> None:
    """Re-centring can push a dry street below zero, and a negative depth is not a depth."""
    twin = np.zeros((N_STEPS, N_SEG))
    members = np.stack([np.full_like(twin, 5.0), np.zeros_like(twin)]).astype(np.float32)

    frame, _ = segment_forecast(
        _twin(twin), _times(), _index(), "TEST-RUN", member_depth_cm=members
    )

    assert (frame.depth_p10_cm >= 0.0).all()
    assert (frame.depth_p90_cm > 0.0).any(), "the wetter member still has to show"


def test_safe_until_reads_the_probability_not_the_level() -> None:
    """11.8's rule is the first step P(> threshold) passes the profile's tolerance.

    An ambulance accepts 0.2. A street the Twin puts at 55 cm never crosses its 60 cm threshold
    on the level alone, so the deterministic reading calls it safe for the whole horizon; with
    members that straddle 60 cm, half of them say otherwise and the ambulance turns back. That
    difference is the whole point of the member axis.
    """
    twin = np.full((N_STEPS, N_SEG), 55.0)
    members = np.stack([twin + 10.0, twin - 10.0]).astype(np.float32)
    args = (_twin(twin), _times(), _index(), "TEST-RUN")

    assert PROFILE_TOLERANCE["ambulance"] < 0.5 <= PROFILE_TOLERANCE["car"]
    deterministic = json.loads(segment_forecast(*args)[0].iloc[0].safe_until)
    assert deterministic["ambulance"] is None, "55 cm never crosses 60 cm on the level alone"

    frame, _ = segment_forecast(*args, member_depth_cm=members)
    safe = json.loads(frame.iloc[0].safe_until)
    assert safe["ambulance"] is not None, "P(> 60 cm) = 0.5 is over an ambulance's 0.2"
    assert safe["car"] is not None, "every member is over a car's 30 cm"


def _sequential_rasters(run_dir: Path, depth_m: np.ndarray, transform, stat: str) -> None:
    """``write_depth_rasters``' PNG and world-file loop as it was before E9, verbatim."""
    from PIL import Image
    from varuna_schemas.ramps import depth_array_to_rgba

    out = run_dir / "depth"
    out.mkdir(parents=True, exist_ok=True)
    res, _, left, _, _, top = transform
    for step in range(depth_m.shape[0]):
        rgba = depth_array_to_rgba(depth_m[step], alpha=255, dry_alpha=0)
        Image.fromarray(rgba, mode="RGBA").save(out / f"{stat}_{step:02d}.png", optimize=True)
        (out / f"{stat}_{step:02d}.pgw").write_text(
            f"{res}\n0.0\n0.0\n{-res}\n{left + res / 2.0}\n{top - res / 2.0}\n", encoding="utf-8"
        )


def _digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


TRANSFORM = (30.0, 0.0, 269970.0, 0.0, -30.0, 2117220.0)


@pytest.mark.parametrize("workers", [1, 3, 8])
def test_parallel_raster_encoding_writes_the_sequential_bytes(tmp_path, workers: int) -> None:
    """Rule 8: the thread pool changes when a PNG is encoded, never what is in it."""
    from varuna_products.depth import write_depth_rasters

    rng = np.random.default_rng(2019)
    depth_m = rng.gamma(0.6, 0.25, size=(12, 60, 40)).astype(np.float32)
    depth_m[rng.random(depth_m.shape) < 0.5] = 0.0  # a dry band, which the ramp makes transparent

    _sequential_rasters(tmp_path / "old", depth_m, TRANSFORM, "p50")
    written = write_depth_rasters(
        tmp_path / "new", depth_m, TRANSFORM, "EPSG:32643", stat="p50", workers=workers
    )

    new = _digests(tmp_path / "new")
    assert new.pop(str(Path("depth") / "bounds.json"))
    assert new == _digests(tmp_path / "old")
    assert [p.name for p in written] == [f"p50_{k:02d}.png" for k in range(12)]


def _old_wet_segments(depth_cm, segment_ids, times, run_id, p_gt) -> str:
    """``write_wet_segments``' JSON as it was built before E9, verbatim, for a given ``p_gt``."""
    from varuna_products.depth import EXCEEDANCE_CM, WET_THRESHOLD_CM, _probability

    n_steps = depth_cm.shape[0]
    wet = np.flatnonzero(depth_cm.max(axis=0) >= WET_THRESHOLD_CM)
    series = {str(segment_ids[k]): [round(float(v), 1) for v in depth_cm[:, k]] for k in wet}
    product = {
        "run_id": run_id,
        "valid_ts": [t.isoformat() for t in times],
        "min_depth_cm": WET_THRESHOLD_CM,
        "n_segments_total": len(segment_ids),
        "n_segments_wet": len(series),
        "depth_cm": series,
    }
    fields = {t: np.asarray(p_gt[t])[:, wet] for t in EXCEEDANCE_CM}
    assert all(f.shape == (n_steps, wet.size) for f in fields.values())
    if sum(int(((f > 0.0) & (f < 1.0)).any(axis=0).sum()) for f in fields.values()):
        product["p_gt"] = {
            f"{t:g}": {
                str(segment_ids[k]): [_probability(v) for v in field[:, j]]
                for j, k in enumerate(wet)
            }
            for t, field in fields.items()
        }
    return json.dumps(product, separators=(",", ":"))


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_wet_segments_json_is_the_row_built_bytes(tmp_path, dtype) -> None:
    """The per-column conversion writes the file the per-value loop wrote, to the byte."""
    from varuna_products.depth import write_wet_segments

    rng = np.random.default_rng(2019)
    n_steps, n_seg = 8, 400
    depth_cm = (rng.gamma(0.7, 18.0, size=(n_steps, n_seg)) * (rng.random(n_seg) < 0.6)).astype(
        dtype
    )
    depth_cm[:, 7] = 5.0  # exactly on the wet edge, which is inclusive
    depth_cm[3, 7] = -0.0  # prints "-0.0", so it must not share a result with 0.0
    ids = tuple(f"S{k:05d}-000" for k in range(n_seg))
    # Probabilities in twentieths, as a 20-member stack gives, with certain 0s and 1s among them;
    # one threshold as integers, and a NaN and a -0.0, each of which prints differently.
    p_gt = {t: rng.integers(0, 21, size=(n_steps, n_seg)) / 20.0 for t in (15.0, 30.0, 45.0)}
    p_gt[60.0] = (rng.random((n_steps, n_seg)) < 0.5).astype(np.int64)
    p_gt[15.0][2, 7] = np.nan
    p_gt[30.0][5, 7] = -0.0

    product = write_wet_segments(tmp_path, depth_cm, ids, _times(n_steps), "TEST-RUN", p_gt=p_gt)

    written = (tmp_path / "segments_wet.json").read_text(encoding="utf-8")
    assert "p_gt" in product, "the fixture must carry a spread, or the p_gt half is untested"
    assert written == _old_wet_segments(depth_cm, ids, _times(n_steps), "TEST-RUN", p_gt)


def test_a_member_stack_for_another_city_is_refused() -> None:
    """Positional arrays with the wrong segment axis would give every street someone else's band."""
    twin = _ramp()
    with pytest.raises(ValueError, match="segment axis"):
        segment_forecast(
            _twin(twin),
            _times(),
            _index(),
            "TEST-RUN",
            member_depth_cm=np.zeros((2, N_STEPS, N_SEG + 1), dtype=np.float32),
        )
