"""VARUNA-Sky: radar frames and gauge readings to a 20-member, 3-hour rain ensemble.

The six stages of SPEC.md 11.1, each a pure function over the structures in
:mod:`varuna_sky.types`, wired together by :func:`varuna_sky.pipeline.run_sky`:

1. :func:`~varuna_sky.qc.run_qc` - coverage, clutter and the attenuation flag;
2. :func:`~varuna_sky.zr.run_zr` - the adaptive ``Z = a R^b`` relation, or Marshall-Palmer;
3. :func:`~varuna_sky.merge.merge_gauges` - mean-field bias then inverse-distance residuals;
4. :func:`~varuna_sky.motion.optical_flow` - pySTEPS Lucas-Kanade over the recent frames;
5. :func:`~varuna_sky.steps.nowcast` - the STEPS ensemble, or the SPEC.md 17 fallback;
6. :func:`~varuna_sky.products.sky_products` - quantiles, exceedances and AOI hyetographs.

Importing a stage does not import the others' heavy dependencies: only the names below are
re-exported eagerly, and each stage module is small. ``run_sky`` is what the cycle calls.

Stage 5 has one piece of machinery beside it: :mod:`varuna_sky.ensemble_pool` runs the STEPS
members in worker processes, because the nowcast measures as 322 ms per member with almost no
shared cost and 57 % of that is GIL-holding C code (P3.7). It produces the same cube as a
single call, element for element. A batch caller that knows it is about to run many cycles -
``make bake`` - can pay the workers' spawn up front with
:func:`~varuna_sky.ensemble_pool.warm_pool`; a live cycle should not, and never waits for it.
"""

from varuna_sky.analysis import AnalysisRain, AnalysisSeries, analysis_rain, analysis_series
from varuna_sky.merge import merge_gauges
from varuna_sky.motion import optical_flow, rain_from_dbz
from varuna_sky.pipeline import STAGES, run_sky, sky_notes
from varuna_sky.products import (
    AoiGrid,
    load_aoi_grid,
    read_sky_products,
    resample_to_aoi,
    sky_products,
    write_rain_products,
)
from varuna_sky.qc import run_qc
from varuna_sky.steps import nowcast
from varuna_sky.types import (
    GaugePair,
    MergeResult,
    MotionField,
    QCResult,
    RadarFrames,
    RadarGrid,
    RainEnsemble,
    SkyInputs,
    SkyProducts,
    SkyResult,
    ZRParams,
)
from varuna_sky.zr import fit_zr, gauge_pairs, marshall_palmer, run_zr

__version__ = "0.1.0"

__all__ = [
    "STAGES",
    "AnalysisRain",
    "AnalysisSeries",
    "AoiGrid",
    "GaugePair",
    "MergeResult",
    "MotionField",
    "QCResult",
    "RadarFrames",
    "RadarGrid",
    "RainEnsemble",
    "SkyInputs",
    "SkyProducts",
    "SkyResult",
    "ZRParams",
    "__version__",
    "analysis_rain",
    "analysis_series",
    "fit_zr",
    "gauge_pairs",
    "load_aoi_grid",
    "marshall_palmer",
    "merge_gauges",
    "nowcast",
    "optical_flow",
    "rain_from_dbz",
    "read_sky_products",
    "resample_to_aoi",
    "run_qc",
    "run_sky",
    "run_zr",
    "sky_notes",
    "sky_products",
    "write_rain_products",
]
