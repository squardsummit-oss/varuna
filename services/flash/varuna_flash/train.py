"""Fitting Flash-lite to the Twin (`make train`; SPEC.md 11.7, task P7.5).

Reads the Twin runs under ``data/train/``, reduces each depth field to per-segment depth with the
same sampler the products use, fits the emulator, and writes both the model and the skill it was
measured at to ``docs/verification/flash_lite.json`` - which is what `/verify` reads and what the
"Reduced-order emulator calibrated to VARUNA-Twin" badge on the what-if drawer is claiming.

The per-segment reduction matters: the emulator is fitted against exactly the quantity it will be
asked to predict, sampled the same way (90th percentile over a 15 m buffer), so its error is the
error of the thing on screen rather than of some intermediate field.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import structlog

from varuna_flash.model import fit, save

log = structlog.get_logger("varuna.flash.train")

__all__ = ["train_from_runs"]


def _segment_depths(
    depth_m: np.ndarray, index: tuple[tuple[str, ...], np.ndarray, np.ndarray]
) -> np.ndarray:
    """Twin depth field to per-segment depth in cm, the products' own sampler."""
    from varuna_products.depth import SEGMENT_PERCENTILE

    segment_ids, offsets, cells = index
    n_steps = depth_m.shape[0]
    flat = depth_m.reshape(n_steps, -1)
    out = np.zeros((n_steps, len(segment_ids)))
    for k in range(len(segment_ids)):
        lo, hi = int(offsets[k]), int(offsets[k + 1])
        if hi <= lo:
            continue
        out[:, k] = np.percentile(flat[:, cells[lo:hi]], SEGMENT_PERCENTILE, axis=1) * 100.0
    return out


def _segment_rain(
    rain_mm_h: np.ndarray, index: tuple[tuple[str, ...], np.ndarray, np.ndarray]
) -> np.ndarray:
    """Rain field to per-segment rain in mm/h, averaged over the segment's own cells.

    The mean rather than the 90th percentile the depth uses: depth is about the deepest part of
    the road, and rain is about how much water the whole catchment received.
    """
    segment_ids, offsets, cells = index
    n_steps = rain_mm_h.shape[0]
    flat = rain_mm_h.reshape(n_steps, -1)
    out = np.zeros((n_steps, len(segment_ids)))
    for k in range(len(segment_ids)):
        lo, hi = int(offsets[k]), int(offsets[k + 1])
        if hi <= lo:
            continue
        out[:, k] = flat[:, cells[lo:hi]].mean(axis=1)
    return out


def train_from_runs(
    train_dir: Path,
    city: str = "mumbai",
    *,
    out_model: Path | None = None,
    out_report: Path | None = None,
    holdout: int = 2,
) -> dict[str, object]:
    """Fit the emulator from every ``.npz`` in ``train_dir`` and write the model and report."""
    from varuna_products.depth import segment_cell_index
    from varuna_schemas.paths import city_dir
    from varuna_twin.city import load_terrain

    # `train-*`, not `*`: the fitted model is written into this directory too, and globbing it
    # back in as training data fails with a confusing KeyError three runs later.
    files = sorted(train_dir.glob("train-*.npz"))
    if len(files) <= holdout:
        msg = f"need more than {holdout} training runs in {train_dir}; found {len(files)}"
        raise ValueError(msg)

    terrain = load_terrain(city)
    index = segment_cell_index(city_dir(city), terrain.transform, terrain.shape, terrain.crs)
    segment_ids = index[0]

    training: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    beta_ref: np.ndarray | None = None
    # The report's note describes the corpus it was fitted to, so the description is measured
    # from the runs as they are read rather than written as a literal - a re-fit on a different
    # corpus must not publish the old one's storms next to the new one's skill (rule 6).
    peak_rain_mm_h: list[float] = []
    beta_draws: set[bytes] = set()
    for path in files:
        blob = np.load(path)
        depth_cm = _segment_depths(blob["depth_m"].astype(np.float64), index)
        # Each segment is driven by the rain over its **own** catchment, not by the AOI mean.
        # Driving on the mean forces one fitted gain per segment to carry every storm's spatial
        # pattern, and it cannot: a segment is wet when the convective cell is over it and dry
        # when the cell is elsewhere, and the mean is the same in both cases. Measured, that
        # collapsed CSI at 30 cm to 0.02 - the emulator got the city's total roughly right and
        # its map completely wrong.
        rain = _segment_rain(blob["rain_mm_h"].astype(np.float64), index)
        beta = blob["beta"].astype(np.float64)
        training.append((depth_cm, rain, np.zeros(len(segment_ids))))
        peak_rain_mm_h.append(float(rain.max()))
        beta_draws.add(beta.tobytes())
        if beta_ref is None:
            beta_ref = np.zeros(len(segment_ids))
        log.info(
            "flash.training_run",
            run=path.stem,
            steps=depth_cm.shape[0],
            peak_cm=round(float(depth_cm.max()), 1),
            peak_rain_mm_h=round(float(rain.max()), 1),
            mean_beta=round(float(beta.mean()), 3),
        )

    # Sort by how wet each run got, so the holdout is the heaviest storms - the honest direction
    # to hold out in. A model that only saw drizzle should be judged on a downpour.
    training.sort(key=lambda run: float(run[0].max()))

    model = fit(training, segment_ids, beta_ref=beta_ref, holdout=holdout)

    model_path = out_model or (train_dir / "flash_lite.npz")
    save(model, model_path)

    n_runs = model.n_training_runs + holdout
    n_draws = len(beta_draws)
    rain_span = (
        f"{min(peak_rain_mm_h):.0f}-{max(peak_rain_mm_h):.0f}"
        if round(min(peak_rain_mm_h)) != round(max(peak_rain_mm_h))
        else f"{peak_rain_mm_h[0]:.0f}"
    )
    report = {
        "model": str(model_path),
        "n_segments": model.n_segments,
        "fitted_segments": model.fitted_segments,
        "n_training_runs": model.n_training_runs,
        "n_holdout_runs": holdout,
        "rmse_cm": round(model.rmse_cm, 3),
        "csi_30cm": round(model.csi_30cm, 4),
        "note": (
            f"Reduced-order emulator calibrated to VARUNA-Twin. Corpus of {n_runs} Twin runs "
            f"peaking at {rain_span} mm/h over the segments across {n_draws} blockage "
            f"draw{'s' if n_draws != 1 else ''}, {model.n_training_runs} fitted and {holdout} "
            f"held out; SPEC.md 11.7 asks for 200 runs and this is {n_runs}, so the held-out "
            "skill below is the number to trust, not the structure."
        ),
    }
    report_path = out_report or Path("docs/verification/flash_lite.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log.info("flash.train_report", **{k: v for k, v in report.items() if k != "note"})
    return report
