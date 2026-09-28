"""The depth a route reads off a run, per segment and per five-minute step (task P8.1).

**Read from ``segments_wet.json``, not from the parquet.** ``segment_forecast.parquet`` is the
product of record - 766,656 rows, every segment at every step - and re-reading 19 MB of it per
request would spend the whole 300 ms route budget on IO. The cycle also writes the compact form
this loads: only the segments the run ever wetted (1,498 of 21,296 on the 2 July storm), which is
the same information for routing purposes because a segment that never reaches 5 cm never changes
anybody's route.

**What the probabilities are worth (corrected 2026-09-19, task D-01).** This docstring used to
say "a baked run is deterministic - ``ensemble_n`` is 1", and the router computed
``P(h > threshold)`` as a hard 1 or 0 from the median depth. That stopped being true at the
2026-09-13 re-bake, which put 20 members through the emulator and wrote each wet segment's
``p_gt`` series at 15/30/45/60 cm into ``segments_wet.json`` (``write_wet_segments``). Every
probability the router printed was therefore 1.0 or 0.0 over a run that knew better, and
``risk_tolerance`` had nothing to bite on - it was a placebo.

So :meth:`SegmentDepths.exceedance` reads ``p_gt`` when the run carries it and falls back to the
median-depth step function only when it does not (a one-member run, or a bake older than that
change). Which of the two answered is on the response as a note, not buried: a probability that
is really a threshold comparison must not be read as an ensemble's opinion (SPEC.md 6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import structlog
from varuna_schemas.paths import run_dir, runs_dir

log = structlog.get_logger("varuna.route.forecast")

__all__ = ["SegmentDepths", "latest_run_dir", "load_depths", "run_dir_at"]

STEP_MIN = 5
"""Forecast step, minutes. Matches the cycle's own step (SPEC.md 10.3)."""

_STEP_S = STEP_MIN * 60.0
"""The same step in seconds, which is the unit the search counts elapsed time in."""

DRY_CM = 5.0
"""Below this a segment is dry enough that no profile slows for it (SPEC.md 6.2 `--depth-dry`)."""


@dataclass(frozen=True, slots=True)
class SegmentDepths:
    """One run's depth series for every segment it wetted."""

    run_id: str
    times: tuple[datetime, ...]
    """The instant each forecast step is valid at, as the cycle wrote them."""

    depth_cm: dict[str, list[float]]
    n_steps: int
    n_total: int
    """Segments in the city, wet or not - so the response can say 1,498 of 21,296."""

    ensemble_n: int

    rain_aoi_mm_h: tuple[float, ...] = ()
    """The run's AOI-mean rain per forecast step, straight off ``run.json``.

    Carried here so a route can say what this cycle's rain peaks at beside what the drain under
    the street was designed for (PRD 3.4). It is an **AOI mean**, not the rain over one junction,
    and every response that quotes it says so."""

    p_gt: dict[float, dict[str, list[float]]] = field(default_factory=dict)
    """``{threshold_cm: {segment_id: [P(h > threshold) per step]}}``, empty when the run has none.

    Exactly the ``p_gt`` block ``segments_wet.json`` carries: the four thresholds of the depth
    ramp, over the segments the run wetted. The cycle writes it only when some value lies
    strictly between 0 and 1, so its presence is itself the claim that this run measured a
    spread."""

    velocity_ms: dict[str, list[float]] = field(default_factory=dict)
    """``{segment_id: [depth-averaged flow speed, m/s, per step]}``, empty when the run has none.

    The pedestrian hazard rule (``h * v >= 0.5 m^2/s``, Appendix A) needs it, and **no run
    VARUNA bakes carries one** (measured 2026-09-22 on all seven demo cycles): the Twin's solver
    holds face fluxes ``qx``/``qy`` while it runs, but no product keeps them - ``segments_wet.json``
    has depth and ``p_gt``, ``segment_forecast.parquet`` has depth quantiles, exceedance and
    safe-until, and neither has a speed. So this is read from an optional ``velocity_ms`` block in
    ``segments_wet.json`` - the same ``{segment_id: [per step]}`` shape as ``depth_cm`` - and
    stays empty until the products stage writes one. Nothing here derives or assumes a speed."""

    @property
    def has_velocity(self) -> bool:
        """True when the run carries a flow speed the pedestrian hazard rule can use."""
        return bool(self.velocity_ms)

    def velocity_at(self, segment_id: str, step: int) -> float | None:
        """Flow speed in m/s on a segment at a step, or None where the run does not say."""
        series = self.velocity_ms.get(segment_id)
        if not series:
            return None
        return series[step] if step < len(series) else series[-1]

    @property
    def has_exceedance(self) -> bool:
        """True when the run carries per-member exceedance rather than a threshold comparison."""
        return bool(self.p_gt)

    @property
    def valid_ts(self) -> datetime:
        """When the forecast starts."""
        return self.times[0]

    def step_at(self, when: datetime) -> int:
        """Which forecast step covers an instant, clamped to the run's window.

        Clamped rather than refused: a route that runs past the end of the forecast is a real
        request ("leave at 09:30, the forecast ends at 09:40"), and holding the last step is the
        honest answer - the water at +180 min is the last thing this run knows.
        """
        delta = (when - self.times[0]).total_seconds() / 60.0
        return max(0, min(self.n_steps - 1, int(delta // STEP_MIN)))

    def depart_offset_s(self, when: datetime) -> float:
        """Seconds from the run's first forecast step to an instant, for :meth:`step_after`."""
        return (when - self.times[0]).total_seconds()

    def step_after(self, depart_offset_s: float, seconds: float) -> int:
        """The step reached ``seconds`` after a departure, as :meth:`step_at` would answer it.

        The search pops a node twenty thousand times a route and asked :meth:`step_at` each
        time, which built a `timedelta`, divided it and clamped it - datetime arithmetic to
        answer a question that is integer division on a float it already holds. A test pins the
        two against each other across the window and past both of its ends.
        """
        step = int((depart_offset_s + seconds) // _STEP_S)
        if step < 0:
            return 0
        last = self.n_steps - 1
        return step if step < last else last

    def depth_at(self, segment_id: str, step: int) -> float:
        """Depth in cm on a segment at a step; 0 for anything the run never wetted."""
        series = self.depth_cm.get(segment_id)
        if not series:
            return 0.0
        return series[step] if step < len(series) else series[-1]

    def exceedance(self, segment_id: str, threshold_cm: float, step: int) -> float:
        """``P(depth > threshold_cm)`` on a segment at a step.

        From the run's own ``p_gt`` where it has one - which is where ``risk_tolerance`` gets
        something to weigh - and otherwise from the median depth, which makes it 1 or 0 by
        construction. A segment the run never wetted is absent from both and cannot exceed
        anything, so it scores 0.
        """
        by_segment = self.p_gt.get(float(threshold_cm))
        if by_segment is not None:
            series = by_segment.get(segment_id)
            if series is not None:
                return series[step] if step < len(series) else series[-1]
            if segment_id not in self.depth_cm:
                return 0.0
        return 1.0 if self.depth_at(segment_id, step) > threshold_cm else 0.0

    def peak(self, segment_id: str) -> float:
        series = self.depth_cm.get(segment_id)
        return max(series) if series else 0.0

    def time_of(self, step: int) -> datetime:
        if step < len(self.times):
            return self.times[step]
        return self.times[-1] + timedelta(minutes=STEP_MIN * (step - len(self.times) + 1))


def latest_run_dir(city: str = "mumbai") -> Path:
    """The newest run **for a city** that carries a segment forecast.

    The city filter is load-bearing. Runs from every city share `data/runs/` and their ids sort
    chronologically, so once Chennai was onboarded its `CHN-` runs sorted above Mumbai's and the
    router began planning Mumbai trips against Chennai depths - a wrong answer that looked
    entirely normal.

    Raises:
        FileNotFoundError: nothing is baked. The message carries the command that fixes it.
    """
    from varuna_schemas.models.run import RunIdError, city_code

    try:
        prefix = f"{city_code(city)}-"
    except RunIdError:
        prefix = ""
    root = runs_dir()
    candidates = (
        [
            p
            for p in sorted(root.iterdir(), reverse=True)
            if p.is_dir()
            and (not prefix or p.name.startswith(prefix))
            and (p / "segments_wet.json").is_file()
        ]
        if root.is_dir()
        else []
    )
    if not candidates:
        msg = (
            "No baked run to route against. Press Play on the replay, or run "
            "`make bake BUNDLE=MUM-2019-07-02`."
        )
        raise FileNotFoundError(msg)
    return candidates[0]


def run_dir_at(at: datetime, city: str = "mumbai") -> Path:
    """The run a city had issued by an instant: the newest whose cycle is not after ``at``.

    **Why the reachability scrub needs this.** ``GET /v1/reachability`` is asked at the console's
    scrub time with no run id, and used to answer from :func:`latest_run_dir` - the 09:10 IST
    cycle, whose forecast starts at 09:15. Every scrub position before 09:15 clamped to that run's
    first step, so by construction the tab answered one question - "what can this facility reach
    in the 09:15 water of the 09:10 forecast" - for the whole morning while looking as if it
    updated on scrub, and at 06:40 it was showing water from two and a half hours later. The
    question a scrub to 07:40 asks is "what could KEM reach at 07:40, on the forecast the city had
    then", which is the newest cycle issued at or before 07:40.

    Ties between runs of one cycle break the way :func:`latest_run_dir` breaks them (the name
    that sorts last). An instant before the first cycle gets the earliest run, clamped to its first
    step as :meth:`SegmentDepths.step_at` always clamps; an instant after the last gets the newest.

    Raises:
        FileNotFoundError: nothing is baked for this city.
    """
    from varuna_schemas.models.run import RunIdError, city_code, parse_run_id

    try:
        prefix = f"{city_code(city)}-"
    except RunIdError:
        prefix = ""
    root = runs_dir()
    dated: list[tuple[datetime, str, Path]] = []
    if root.is_dir():
        for p in root.iterdir():
            if not (p.is_dir() and p.name.startswith(prefix)):
                continue
            if not (p / "segments_wet.json").is_file():
                continue
            try:
                cycle = parse_run_id(p.name).cycle_ts
            except RunIdError:
                continue
            dated.append((cycle, p.name, p))
    if not dated:
        return latest_run_dir(city)  # raises with the command that fixes it
    dated.sort()
    issued = [entry for entry in dated if entry[0] <= at]
    return issued[-1][2] if issued else dated[0][2]


@lru_cache(maxsize=4)
def _load(path_str: str, mtime_ns: int) -> SegmentDepths:
    del mtime_ns  # part of the cache key: a re-baked run invalidates itself.
    path = Path(path_str)
    wet = json.loads((path / "segments_wet.json").read_text(encoding="utf-8"))
    depth = {str(k): [float(v) for v in series] for k, series in wet.get("depth_cm", {}).items()}
    n_steps = max((len(v) for v in depth.values()), default=1)
    # `valid_ts` is the list of step times, one per forecast step, exactly as the cycle wrote it.
    stamps = wet.get("valid_ts") or []
    times = tuple(datetime.fromisoformat(str(t)) for t in stamps)
    if not times:
        msg = f"{path.name}/segments_wet.json carries no step times; it cannot be routed against."
        raise ValueError(msg)

    ensemble_n = 1
    rain: tuple[float, ...] = ()
    meta_path = path / "run.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ensemble_n = int(meta.get("ensemble_n", 1) or 1)
        rain = tuple(float(v) for v in (meta.get("rain_aoi_mm_h") or []))

    # `{"15": {segment_id: [...]}}` on the wire; keyed by float here so a profile's threshold
    # (15.0, 30.0, 45.0, 60.0 cm - the depth ramp) looks itself up without string formatting.
    p_gt = {
        float(threshold): {
            str(sid): [float(v) for v in series] for sid, series in by_segment.items()
        }
        for threshold, by_segment in (wet.get("p_gt") or {}).items()
    }

    # Optional, and absent from every run baked so far; see SegmentDepths.velocity_ms.
    velocity = {
        str(sid): [float(v) for v in series]
        for sid, series in (wet.get("velocity_ms") or {}).items()
    }

    depths = SegmentDepths(
        run_id=str(wet.get("run_id", path.name)),
        times=times,
        depth_cm=depth,
        n_steps=n_steps,
        n_total=int(wet.get("n_segments_total", len(depth))),
        ensemble_n=ensemble_n,
        rain_aoi_mm_h=rain,
        p_gt=p_gt,
        velocity_ms=velocity,
    )
    log.info(
        "route.depths_loaded",
        run_id=depths.run_id,
        wet=len(depth),
        total=depths.n_total,
        steps=n_steps,
        ensemble_n=ensemble_n,
        p_gt=sorted(p_gt),
        velocity=bool(velocity),
    )
    return depths


def load_depths(
    run_id: str | None = None, city: str = "mumbai", *, at: datetime | None = None
) -> SegmentDepths:
    """Load a run's segment depths, cached on the file's mtime.

    With no ``run_id``, ``at`` picks the run the city had issued by that instant
    (:func:`run_dir_at`); with neither, the newest run, as before.
    """
    if run_id:
        path = run_dir(run_id)
    elif at is not None:
        path = run_dir_at(at, city)
    else:
        path = latest_run_dir(city)
    wet = path / "segments_wet.json"
    if not wet.is_file():
        msg = f"Run {path.name} has no segment forecast; it cannot be routed against."
        raise FileNotFoundError(msg)
    return _load(str(path), wet.stat().st_mtime_ns)
