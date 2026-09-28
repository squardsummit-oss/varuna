"""Rain-nowcast products as the console reads them
(``GET /v1/nowcast/rain`` and ``GET /v1/nowcast/rain/series``; SPEC.md 11.1 step 6, 12).

VARUNA-Sky reduces its 20-member, 3-hour ensemble to per-pixel quantiles, two exceedance
fields and one area-of-interest-mean hyetograph per member, and writes them to
``data/runs/<run_id>/rain/quantiles.zarr`` (SPEC.md 10.3). A browser cannot open a Zarr
store, so these are the two shapes that store is served in:

* :class:`RainNowcast` - every member's AOI-mean hyetograph and the spread band across the
  members, which is what the console's time bar draws under its track (SPEC.md 7.2);
* :class:`RainPointSeries` - the quantiles and exceedance probabilities of the one 500 m Sky
  pixel a named junction falls in, which is the fan chart at Hindmata (SPEC.md 7.2, P3.8).

Both carry the provenance of :class:`RainProduct`, because the run stamp above the map has to
say where the numbers came from: which run, baked or live, which nowcaster ran, and the
honesty labels the cycle earned (SPEC.md 0.6). Rain is always ``mm/h``; times are IST.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.models.common import (
    HttpUrlStr,
    Latitude,
    Longitude,
    Probability,
    Timestamp,
    VarunaModel,
)
from varuna_schemas.models.run import RunMode

NowcastSource = Literal["pysteps_steps", "fallback_steps"]
"""Which nowcaster produced the ensemble (mirrors ``varuna_sky.types.NowcastSource``).

Mirrored rather than imported: ``varuna_sky`` depends on ``varuna_schemas`` and not the other
way round. The fallback of SPEC.md 17 is named on the wire, never hidden."""

ZRSource = Literal["adaptive", "marshall_palmer"]
"""Whether ``(a, b)`` were fitted this cycle or fell back to Marshall-Palmer
(mirrors ``varuna_sky.types.ZRSource``)."""


class ZRRelation(VarunaModel):
    """The ``Z = a R^b`` relation the analysis came through (SPEC.md Appendix A)."""

    a: float = Field(gt=0, description="Prefactor; 200 for Marshall-Palmer, 100-400 when fitted.")
    b: float = Field(gt=0, description="Exponent; 1.6 for Marshall-Palmer, 1.1-1.8 when fitted.")
    source: ZRSource = Field(description="adaptive = fitted this cycle from gauge-radar pairs.")
    n_pairs: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Co-located gauge-radar pairs the fit had. Null on a baked run: rain/cube.zarr "
            "records the relation but not the pair count it was fitted from."
        ),
    )


class RainProduct(VarunaModel):
    """What every rain response says about where its numbers came from.

    ``run_id`` is null in exactly one case: the products were computed on demand from a bundle
    and never written to ``data/runs``, so there is no run to name. The response then reads
    ``mode: live``, which is what the run stamp shows (SPEC.md 7.2). Inventing a run id for
    an unpublished cycle would put an identifier on screen that resolves to nothing.
    """

    run_id: str | None = Field(
        description=(
            "Run the products were read from, or null when they were computed on demand and "
            "not published as a run."
        )
    )
    valid_ts: Timestamp = Field(
        description=(
            "Cycle time of the run (IST): the instant the forecast was made. Each step's own "
            "valid time is on the step."
        )
    )
    mode: RunMode = Field(
        description="baked = read from a run under data/runs; live = computed for this request."
    )
    bundle: str | None = Field(default=None, description="Replay bundle id, null when live.")
    city: str = Field(description="City slug the AOI belongs to, e.g. mumbai.")
    n_members: int = Field(ge=1, description="Ensemble members (20 in the prototype).")
    n_steps: int = Field(ge=1, description="Forecast steps (36 = 3 h at 5 min).")
    step_min: float = Field(gt=0, description="Minutes between forecast steps.")
    nowcaster: NowcastSource | None = Field(
        default=None,
        description=(
            "Nowcaster that produced the ensemble. Null when the run's rain/cube.zarr is not "
            "on disk to say, which is the only artifact that records it."
        ),
    )
    seed: int | None = Field(default=None, description="Seed the ensemble was drawn with.")
    zr: ZRRelation | None = Field(default=None, description="The Z-R relation of this cycle.")
    stage_ms: dict[str, int] = Field(
        default_factory=dict,
        description="Wall-clock per stage in ms, for the cycle budget bar (SPEC.md 7.2).",
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "The run's honesty labels, verbatim: which Z-R relation ran, what the gauge merge "
            "did, whether the NWP blend is off (SPEC.md 0.6). The console prints them as "
            "they arrive."
        ),
    )


class RainStep(VarunaModel):
    """One forecast step of a rain series: the ensemble spread in mm/h."""

    valid_ts: Timestamp = Field(description="When this step is valid (IST).")
    lead_min: int = Field(description="Minutes from the cycle time; 5 for the first step.")
    p10_mm_h: float = Field(ge=0, description="10th percentile across the ensemble members.")
    p50_mm_h: float = Field(ge=0, description="Median across the ensemble members.")
    p90_mm_h: float = Field(ge=0, description="90th percentile across the ensemble members.")

    @model_validator(mode="after")
    def _quantiles_ordered(self) -> RainStep:
        if not self.p10_mm_h <= self.p50_mm_h <= self.p90_mm_h:
            msg = "rain quantiles must satisfy p10 <= p50 <= p90"
            raise ValueError(msg)
        return self


class RainPointStep(RainStep):
    """One forecast step at a point, with the two exceedance probabilities the pixel carries."""

    p_gt_20: Probability = Field(description="P(R > 20 mm/h) across the members at this pixel.")
    p_gt_40: Probability = Field(description="P(R > 40 mm/h) across the members at this pixel.")

    @model_validator(mode="after")
    def _exceedance_ordered(self) -> RainPointStep:
        if self.p_gt_20 < self.p_gt_40:
            msg = "exceedance probabilities must be non-increasing with threshold"
            raise ValueError(msg)
        return self


class RainMemberSeries(VarunaModel):
    """One ensemble member's AOI-mean hyetograph, one value per forecast step."""

    member: int = Field(ge=0, description="Member index in the ensemble, 0-based.")
    mm_h: list[float] = Field(min_length=1, description="AOI-mean rain rate per step, in mm/h.")


class RainSeries(RainProduct):
    """A rain product that is a series over the forecast steps.

    ``steps`` is a ``Sequence`` rather than a ``list`` so that a subclass can narrow it to its
    own step type - :class:`RainPointSeries` carries exceedance probabilities the AOI band has
    no equivalent for. Validation, serialisation and the JSON schema are unchanged.
    """

    steps: Sequence[RainStep] = Field(
        default_factory=list, description="One entry per forecast step, in time order."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def peak_p50_mm_h(self) -> float:
        """Highest median rain rate over the horizon; 0 when the series is empty."""
        return max((step.p50_mm_h for step in self.steps), default=0.0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def peak_ts(self) -> Timestamp | None:
        """When :attr:`peak_p50_mm_h` occurs, or null when the series is empty."""
        if not self.steps:
            return None
        return max(self.steps, key=lambda step: step.p50_mm_h).valid_ts


class RainNowcast(RainSeries):
    """The cycle's rain averaged over the city: the time bar's spread band (SPEC.md 7.2).

    ``members`` is the AOI-mean hyetograph of each member - the ensemble's own disagreement,
    not a smoothed envelope - and ``steps`` is the quantile band across those members, taken
    with the same quantiles and interpolation as the per-pixel products so the band and the map
    cannot mean different things. Note what the band is *of*: the spread of the AOI mean, which
    is narrower than the spread over any one street.
    """

    members: list[RainMemberSeries] = Field(
        default_factory=list, description="One hyetograph per member, in member order."
    )


class RainPoint(VarunaModel):
    """The point a fan chart was sampled at, and the Sky pixel it fell in."""

    lon: Longitude
    lat: Latitude
    name: str = Field(description="What the point is called, e.g. Hindmata junction.")
    hotspot_id: str | None = Field(
        default=None, description="Register id when the point came from the city's hotspots."
    )
    source_url: HttpUrlStr | None = Field(
        default=None, description="Where the register's coordinate was verified against."
    )
    row: int = Field(ge=0, description="Row of the Sky pixel the point falls in.")
    col: int = Field(ge=0, description="Column of the Sky pixel the point falls in.")
    res_m: float = Field(gt=0, description="Sky pixel size in metres: a 500 m pixel, not a street.")


class RainPointSeries(RainSeries):
    """The rain fan chart at one point (SPEC.md 7.2, task P3.8).

    Sampled from the 500 m Sky grid, which is the grid the quantiles are published on: the
    30 m AOI resample is the Twin's forcing and is never written to disk (SPEC.md 10.3), so a
    point series describes the pixel the junction sits in, not the junction's own square metre.
    """

    point: RainPoint
    steps: Sequence[RainPointStep] = Field(
        default_factory=list, description="One entry per forecast step, in time order."
    )
    exceedance_mm_h: list[float] = Field(
        min_length=2,
        max_length=2,
        description="The two thresholds p_gt_20 and p_gt_40 report, in mm/h.",
    )


__all__ = [
    "NowcastSource",
    "RainMemberSeries",
    "RainNowcast",
    "RainPoint",
    "RainPointSeries",
    "RainPointStep",
    "RainProduct",
    "RainSeries",
    "RainStep",
    "ZRRelation",
    "ZRSource",
]
