"""``make bake``: pre-compute a bundle's cycles into ``data/runs/``, oldest first (SPEC.md 4.3).

**The target did not exist.** Until 2026-09-13 ``uv run varuna bake`` printed "not implemented
until Phase 5" and exited 2, while P5.6 was ticked on it: ``varuna_cycle`` had no command line,
so the root task runner kept the phase-gate placeholder, and every run under ``demo/runs`` had
been made by calling :func:`~varuna_cycle.twin_cycle.run_cycle` by hand. Section 4.3 asks each
target to work from a clean clone; this one did not work anywhere.

**What a bake is.** Every instant on the bundle's cycle ladder that Sky can forecast from
(:func:`~varuna_cycle.sky_cycle.cycle_window` - the first needs three radar frames behind it),
run in chronological order and written atomically by the run registry. ``every`` strides the
ladder and ``start``/``end`` bound it, so the seven cycles shipped in ``demo/runs`` - every 30
minutes from 06:10 to 09:10 IST - are a command rather than a script.

**Resumable, because it is long.** A reconstructed cycle is about four minutes of CPU on the
demo laptop (Sky, a coupled Twin run, Flash, products, Pulse), so the 45 forecastable cycles of
``MUM-2019-07-02`` are more than three hours. A cycle whose run already exists is skipped unless
``overwrite`` is set - and "already exists" means the run id *this* bake would write. A
single-member ``flash0.0`` run left from before the emulator was fitted is a different run, and
it does not stop the twenty-member ``flash0.1`` run from being made.

**What it does not do: carry Pulse's posterior forward.** Section 11.11 says a bake carries the
posterior from cycle to cycle. It does not, because nothing does: ``run_cycle`` calls Pulse with
no prior, so each cycle re-assimilates every observation up to its own instant from the city's
prior. Later cycles see more observations, which is how blockage can still rise across a bake,
but that is a re-analysis per cycle rather than a filter. Handing the previous posterior in
without also restricting Pulse to the observations since the last cycle would count each
observation again every cycle and shrink the spread for nothing, so the gap is recorded against
P7.3 and printed at the end of every bake rather than half-wired here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import structlog
from varuna_schemas.constants import IST
from varuna_schemas.models.run import build_run_id
from varuna_schemas.paths import repo_root, runs_dir

from varuna_cycle.twin_cycle import (
    FLASH_ABSENT_VERSION,
    FLASH_MODEL_PATHS,
    FLASH_VERSION,
    SKY_VERSION,
    TWIN_VERSION,
    _is_design_storm,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from varuna_cycle.twin_cycle import CycleResult

log = structlog.get_logger("varuna.cycle.bake")

__all__ = [
    "PULSE_NOTE",
    "BakePlan",
    "BakeReport",
    "bake_cycles",
    "describe_event",
    "describe_plan",
    "describe_result",
    "expected_flash_version",
    "ladder",
    "parse_instant",
    "plan_bundle",
    "run_id_for",
]

PULSE_NOTE = (
    "Pulse re-assimilated every observation up to each cycle from the city's prior. Section 11.11 "
    "carries the posterior from one cycle to the next, and that is not wired yet (SPEC.md P7.3)."
)

BakeEvent = Callable[[str, int, int, datetime, Any], None]
"""``(kind, index, total, cycle_ts, detail)``; kind is started, skipped, baked or failed."""

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True, slots=True)
class BakePlan:
    """The cycles a bake will consider, oldest first, and the window they were chosen from."""

    bundle: str
    city: str
    period: timedelta
    """The bundle's cycle cadence - the ladder every instant sits on."""
    first: datetime
    """The earliest cycle the bundle can forecast from."""
    last: datetime
    """The latest cycle inside the bundle's window."""
    instants: tuple[datetime, ...]


@dataclass(slots=True)
class BakeReport:
    """What a bake did with each cycle it was given."""

    baked: list[CycleResult] = field(default_factory=list)
    skipped: list[tuple[datetime, str]] = field(default_factory=list)
    """Cycles whose run already existed, with that run's id."""
    failed: list[tuple[datetime, str]] = field(default_factory=list)
    """Cycles that raised, with the error as ``Type: message``."""

    @property
    def ok(self) -> bool:
        return not self.failed


def _ist(when: datetime) -> str:
    return f"{when.astimezone(IST):%H:%M}"


def _minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def ladder(
    first: datetime,
    last: datetime,
    period: timedelta,
    *,
    every: timedelta | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[datetime]:
    """The cycle instants to bake, oldest first.

    Args:
        first, last: the bundle's forecastable window; both sit on the cycle ladder.
        period: the ladder's cadence.
        every: stride between baked cycles; a whole multiple of ``period``. Default ``period``.
        start, end: bound the bake. A start between two cycles moves up to the next one, since a
            cycle that happened before the asked-for start was not asked for. Both are clamped
            into the window, the way the replay clock clamps a seek rather than refusing it.

    Raises:
        ValueError: ``every`` is not a whole multiple of ``period``, or no cycle is left.
    """
    if period <= timedelta(0):
        msg = f"The cycle period must be positive; the bundle says {period}."
        raise ValueError(msg)
    stride = every if every is not None else period
    if stride <= timedelta(0) or stride % period != timedelta(0):
        msg = (
            f"--every must be a whole multiple of the bundle's {_minutes(period)} min cycle; "
            f"got {stride.total_seconds() / 60:g} min."
        )
        raise ValueError(msg)

    low = first if start is None else max(start, first)
    high = last if end is None else min(end, last)
    # Ceiling division on timedeltas, which are exact: `first` is on the ladder, so the opening
    # cycle is the first ladder instant at or after `low`.
    steps_up = -((first - low) // period)
    opening = first + steps_up * period
    if opening > high:
        msg = (
            f"No cycle to bake between {_ist(low)} and {_ist(high)} IST; the bundle forecasts "
            f"from {_ist(first)} to {_ist(last)} IST."
        )
        raise ValueError(msg)
    count = (high - opening) // stride
    return [opening + index * stride for index in range(count + 1)]


def parse_instant(text: str, *, on: datetime) -> datetime:
    """``06:10`` is IST on the day of ``on`` (the bundle's ``t0``); anything else is ISO 8601.

    An ISO instant has to carry its offset. A naive one would be read in whatever zone the
    machine is in, and a bake on a UTC runner would then compute the wrong five and a half hours.
    """
    value = text.strip()
    match = _CLOCK.match(value)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            msg = f"{text!r} is not a clock time; write it as HH:MM, for example 06:10."
            raise ValueError(msg)
        return on.astimezone(IST).replace(hour=hour, minute=minute, second=0, microsecond=0)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        msg = (
            f"{text!r} is neither HH:MM (IST on the bundle's day) nor an ISO 8601 instant such "
            "as 2019-07-02T06:10+05:30."
        )
        raise ValueError(msg) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"{text!r} has no UTC offset; write it as {value}+05:30 for IST."
        raise ValueError(msg)
    return parsed


def plan_bundle(
    bundle: str,
    *,
    every_min: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> BakePlan:
    """Resolve a bundle's forecastable window and choose the cycles to bake from it.

    Raises:
        BundleNotFoundError: the bundle or its radar cube is not built.
        ValueError: the options leave no cycle, or cannot be read.
    """
    from varuna_replay.bundle import BundleLayout, load_manifest

    from varuna_cycle.sky_cycle import _cycle_period, cycle_window

    layout = BundleLayout.for_bundle(bundle)
    manifest = load_manifest(layout.root)
    first, last = cycle_window(manifest, layout)
    period = _cycle_period(manifest)
    instants = ladder(
        first,
        last,
        period,
        every=timedelta(minutes=every_min) if every_min is not None else None,
        start=parse_instant(start, on=manifest.t0) if start else None,
        end=parse_instant(end, on=manifest.t0) if end else None,
    )
    return BakePlan(
        bundle=manifest.id,
        city=manifest.city,
        period=period,
        first=first,
        last=last,
        instants=tuple(instants),
    )


def expected_flash_version(bundle: str) -> str:
    """The Flash version a bake of ``bundle`` will stamp into its run ids.

    The same two conditions `run_cycle` applies: a design storm never runs the emulator, and a
    reconstruction runs it only when a fitted model is on disk.
    """
    if _is_design_storm(bundle):
        return FLASH_ABSENT_VERSION
    root = repo_root()
    fitted = any((root / path).is_file() for path in FLASH_MODEL_PATHS)
    return FLASH_VERSION if fitted else FLASH_ABSENT_VERSION


def run_id_for(city: str, cycle_ts: datetime, flash_version: str, mode: str = "baked") -> str:
    """The run id a cycle of this build writes, before it is computed."""
    return build_run_id(city, cycle_ts, SKY_VERSION, TWIN_VERSION, flash_version, mode)  # type: ignore[arg-type]


def _emit(on_event: BakeEvent | None, *event: Any) -> None:
    if on_event is not None:
        on_event(*event)


SKY_POOL_WARM_TIMEOUT_S = 180.0
"""Ceiling on the wait for Sky's workers to import; measured at 16-45 s under load."""


def _warm_sky_pool() -> None:
    """Start Sky's worker pool before the first cycle rather than during it.

    Sky splits its twenty members across worker processes and never waits for them inside a
    cycle, so the first cycle in a process runs sequentially while the workers import. A bake
    knows it is about to run every cycle, so it pays the spawn once up front. Not waiting is not
    an error - the first cycle is then sequential, and the cube is identical either way.
    """
    try:
        from varuna_sky.ensemble_pool import warm_pool
    except ImportError:  # pragma: no cover - a Sky without the pool runs sequentially anyway
        return
    started = perf_counter()
    ready = warm_pool(20, timeout=SKY_POOL_WARM_TIMEOUT_S)
    log.info("bake.sky_pool", ready=ready, ms=round((perf_counter() - started) * 1000.0))


def bake_cycles(
    plan: BakePlan,
    *,
    overwrite: bool = False,
    runner: Callable[..., CycleResult] | None = None,
    runs_root: Path | None = None,
    flash_version: str | None = None,
    on_event: BakeEvent | None = None,
) -> BakeReport:
    """Run every cycle of ``plan`` oldest first, skipping runs that exist unless ``overwrite``.

    A cycle that raises is recorded and the bake carries on: the next cycle does not depend on
    it (see the module docstring on Pulse), and three hours of work should not end at cycle 12.
    ``KeyboardInterrupt`` is not an ``Exception`` and still stops the bake.

    Args:
        runner: the cycle to run, :func:`~varuna_cycle.twin_cycle.run_cycle` by default.
        runs_root: where existing runs are looked for; ``runs_dir()`` by default.
        flash_version: the version to expect in run ids; worked out from the bundle by default.
    """
    if runner is None:
        from varuna_cycle.twin_cycle import run_cycle

        runner = run_cycle
        _warm_sky_pool()
    root = runs_root if runs_root is not None else runs_dir()
    version = flash_version or expected_flash_version(plan.bundle)
    ordered = sorted(plan.instants)
    total = len(ordered)
    report = BakeReport()

    for index, cycle_ts in enumerate(ordered, start=1):
        run_id = run_id_for(plan.city, cycle_ts, version)
        if not overwrite and (root / run_id / "run.json").is_file():
            report.skipped.append((cycle_ts, run_id))
            _emit(on_event, "skipped", index, total, cycle_ts, run_id)
            continue
        _emit(on_event, "started", index, total, cycle_ts, run_id)
        try:
            result = runner(
                plan.bundle, cycle_ts, city=plan.city, mode="baked", overwrite=overwrite
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            log.warning(
                "cycle.bake_failed",
                bundle=plan.bundle,
                cycle_ts=cycle_ts.isoformat(),
                error=message,
            )
            report.failed.append((cycle_ts, message))
            _emit(on_event, "failed", index, total, cycle_ts, message)
            continue
        report.baked.append(result)
        _emit(on_event, "baked", index, total, cycle_ts, result)

    log.info(
        "cycle.bake_done",
        bundle=plan.bundle,
        baked=len(report.baked),
        skipped=len(report.skipped),
        failed=len(report.failed),
    )
    return report


def describe_result(result: CycleResult) -> str:
    """One line for a finished cycle: its id, time, ensemble size, water balance and reach.

    The peak is the land's (``CycleResult.peak_depth_cm``): the whole grid's would be the sea
    the Twin holds at the tide, up to 2.58 m of bay at Mumbai's crest.
    """
    seconds = result.stage_ms.get("total", 0) / 1000.0
    return (
        f"{result.run_id}  {seconds:.1f} s  {result.ensemble_n} member(s)  "
        f"mass balance {result.mass_balance_err * 100:.3f} %  "
        f"land peak {result.peak_depth_cm:.1f} cm  "
        f"{result.wet_segments:,} wet segments"
    )


def describe_event(kind: str, index: int, total: int, cycle_ts: datetime, detail: Any) -> str:
    """One terminal line per bake event."""
    where = f"[{index}/{total}] {_ist(cycle_ts)} IST"
    if kind == "started":
        return f"{where}  baking {detail}"
    if kind == "skipped":
        return f"{where}  already baked: {detail}"
    if kind == "failed":
        return f"{where}  failed: {detail}"
    return f"{where}  {describe_result(detail)}"


def describe_plan(
    plan: BakePlan, *, runs_root: Path | None = None, flash_version: str | None = None
) -> list[str]:
    """What ``varuna cycle plan`` prints: the window, then each cycle and whether it exists."""
    root = runs_root if runs_root is not None else runs_dir()
    version = flash_version or expected_flash_version(plan.bundle)
    lines = [
        f"{plan.bundle} on {plan.city}: {len(plan.instants)} cycle(s). The bundle forecasts from "
        f"{_ist(plan.first)} to {_ist(plan.last)} IST on a {_minutes(plan.period)} min ladder."
    ]
    for cycle_ts in plan.instants:
        run_id = run_id_for(plan.city, cycle_ts, version)
        state = "already baked" if (root / run_id / "run.json").is_file() else "to bake"
        lines.append(f"  {_ist(cycle_ts)} IST  {run_id}  {state}")
    return lines
