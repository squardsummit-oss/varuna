"""City-in-a-box as a background job (SPEC.md 7.9, 12; task P9.5).

The wizard on stage is `run_city` in a thread, with its fifteen pipeline steps folded into the
six the screen shows and its real log lines forwarded as they are written. Nothing here is
scripted: SPEC.md 7.9's acceptance criterion is that every line in the log stream comes from
the pipeline, so this module installs a structlog processor that copies whatever the job's thread
logs into the job, stamped with the instant it was captured, rather than composing sentences of
its own.

**Why a thread and not a subprocess.** The pipeline publishes progress on the in-process bus that
the WebSocket already relays, so a thread reaches the browser with no extra plumbing. It holds the
GIL only in numpy and rasterio calls that release it, and the API is doing nothing else while a
judge watches a progress bar.

**One job at a time.** Two concurrent builds of the same city would write the same files from two
threads. A second request while one is running gets the running job back rather than an error -
on stage, a double-click on "Start" must not produce a failure dialog.

**Each of the six steps reports its own time.** The pipeline logs a ``city.step`` line with the
milliseconds of every one of its steps; the job adds them up per wizard step, so a row
reads "Done 1.4 s" with the pipeline's own number, and "Loaded from disk" when every step behind
it was read from the city folder rather than computed. The first forecast's stages (Sky, Twin,
Pulse, products) are timed as they run and then replaced by the run's own ``stage_ms``.

**The last build outlives the process.** Jobs live in memory, so an API restart used to erase the
log and every timing and leave the wizard with nothing to show but "built". When a job ends it now
writes ``city/<city>/onboard_last.json``: the last attempt and the last build that finished with a
forecast, kept apart so a failed rebuild never erases the run a good one made. The Chennai
console's default run is that record's first run (`varuna_api.runs_util`), never whichever run
sorts newest by name.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog
from varuna_schemas.constants import IST

log = structlog.get_logger("varuna.api.onboard")

__all__ = [
    "RECORD_FILE",
    "LogEntry",
    "OnboardState",
    "get_job",
    "latest_job",
    "onboard_run_id",
    "previous_build",
    "read_record",
    "record_for_job",
    "record_path",
    "served_job",
    "start_job",
]

MAX_LOG_LINES = 400
"""Log lines kept per job, and persisted with it. The screen shows them all; this bounds the
memory a long build costs and the size of the record (about 60 KB at the cap)."""

TAIL_LINES = 40
"""Lines in the legacy ``log_tail`` field, which older consoles read."""

STEP_OF: dict[str, str] = {
    "cache": "fetch_open_data",
    "dem": "fetch_open_data",
    "osm": "fetch_open_data",
    "landcover": "fetch_open_data",
    "hotspots": "fetch_open_data",
    "assets": "fetch_open_data",
    "sea": "condition_terrain",
    "condition": "condition_terrain",
    "roughness": "condition_terrain",
    "depressions": "condition_terrain",
    "segments": "infer_drains",
    "drains": "infer_drains",
    "units": "build_graph",
    "export": "build_graph",
    "report": "build_graph",
}
"""Which of the wizard's six steps each pipeline step belongs to (SPEC.md 7.9).

The wizard's list is the story - "fetch open data", "condition terrain", "infer drains" - and the
pipeline's is the work. Mapping them keeps the screen honest about *what* is happening without
making a judge read fifteen rows, and it means adding a pipeline step never silently drops off
the screen: an unmapped name's milliseconds are added to the step the wizard is already on
(`OnboardState.pipeline_step`), and a test fails until the name is mapped here.

**The sea mask belongs to "Condition terrain".** It marks which cells are sea before the DEM is
conditioned, so it is part of preparing the terrain rather than of fetching it. Before it was
mapped, its 13.0 s on Chennai were in the log and in no row: the rows added up to 147.5 s of a
160.5 s build.

**Road segments belong to "Infer drains".** They used to map to "Build graph", and since the
pipeline runs segments, then drains, then units, the fifth row ran, fell back to waiting while the
fourth ran, and ran again. The drain graph is inferred *along* the road segments (inlets every
40 m of road, SPEC.md 10.1 step 7), so they are that step's input, and the rows now advance in
the pipeline's own order."""

PIPELINE_ORDER: tuple[str, ...] = tuple(STEP_OF)
"""`varuna_city.pipeline.STEPS` by name, in order; the job reads the real list when it starts."""

ORDER: tuple[str, ...] = (
    "choose_area",
    "fetch_open_data",
    "condition_terrain",
    "infer_drains",
    "build_graph",
    "first_forecast",
)

FORECAST_STAGES: tuple[str, ...] = ("sky", "twin", "pulse", "flash", "products")
"""The first forecast's stages, in the order `run_cycle` runs them (SPEC.md 11.11)."""

DENY_EVENTS: frozenset[str] = frozenset(
    {"onboard.started", "onboard.failed", "onboard.done", "onboard.no_bundle"}
)
"""Lines the tap leaves out: the job's own bookkeeping, each of which already has a note."""

DROP_KEYS: frozenset[str] = frozenset(
    {"event", "level", "timestamp", "logger", "exception", "exc_info", "stack", "stack_info"}
)
"""Keys not rendered into a line. A traceback belongs in the server log, not the wizard."""

MAX_LINE_CHARS = 600
"""A captured line longer than this is cut, with an ellipsis. The pipeline's key-values are short;
the exceptions are a list or a dict rendered whole (a histogram, a list of missing paths), which
belong in the server log rather than across the width of the wizard."""

RECORD_FILE = "onboard_last.json"
"""The persisted last build, beside the city it built (`city/<city>/onboard_last.json`)."""

RECORD_SCHEMA = 1


def _now() -> datetime:
    return datetime.now(tz=IST)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class LogEntry:
    """One captured line: when it was captured, what it said, and at which level."""

    ts: str
    text: str
    level: str = "info"

    def to_dict(self) -> dict[str, str]:
        return {"ts": self.ts, "text": self.text, "level": self.level}


def _blank_steps() -> dict[str, dict[str, Any]]:
    steps: dict[str, dict[str, Any]] = {
        name: {
            "status": "waiting",
            "ms": 0.0,
            "detail": None,
            "loaded_from_disk": False,
            "progress": 0.0,
        }
        for name in ORDER
    }
    steps["first_forecast"]["stages"] = {
        stage: {"status": "waiting", "ms": None} for stage in FORECAST_STAGES
    }
    return steps


@dataclass
class OnboardState:
    """One onboarding run, readable while it is still going."""

    job_id: str
    city: str
    design_storm: str
    from_cache_only: bool
    status: str = "queued"
    step: str = "choose_area"
    progress: float = 0.0
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    lines: list[LogEntry] = field(default_factory=list)
    lines_total: int = 0
    first_run_id: str | None = None
    error: str | None = None
    steps_done: int = 0
    steps_total: int = 14
    failed_step: str | None = None
    """The pipeline step that broke first, once one has. Freezes `step` and `progress` there."""
    steps: dict[str, dict[str, Any]] = field(default_factory=_blank_steps)
    """Per wizard step: status, milliseconds, one line of detail, and whether it only loaded."""
    pipeline_steps: tuple[str, ...] = PIPELINE_ORDER
    forecast: dict[str, Any] | None = None
    """What the first forecast produced, read from its run directory once it has landed."""

    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _clock: dict[str, float] = field(default_factory=dict, repr=False)
    _seen: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or _now()
        return max((end - self.started_at).total_seconds(), 0.0)

    @property
    def texts(self) -> list[str]:
        """The lines' text alone, oldest first."""
        with self.lock:
            return [entry.text for entry in self.lines]

    # ---- the log ---------------------------------------------------------------------------
    def note(self, line: str, level: str = "info") -> None:
        if len(line) > MAX_LINE_CHARS:
            line = line[: MAX_LINE_CHARS - 3].rstrip() + "..."
        entry = LogEntry(ts=_stamp(), text=line, level=level)
        with self.lock:
            self.lines.append(entry)
            self.lines_total += 1
            if len(self.lines) > MAX_LOG_LINES:
                del self.lines[: len(self.lines) - MAX_LOG_LINES]

    # ---- the six steps ---------------------------------------------------------------------
    def members(self, wizard_step: str) -> list[str]:
        """The pipeline steps behind one wizard step, in the pipeline's order."""
        return [name for name in self.pipeline_steps if STEP_OF.get(name) == wizard_step]

    def begin(self, wizard_step: str) -> None:
        """Mark a wizard step running and start its clock. The job's current step follows it."""
        with self.lock:
            record = self.steps[wizard_step]
            if record["status"] in {"done", "failed"}:
                return
            record["status"] = "running"
            self._clock.setdefault(wizard_step, perf_counter())
            if self.failed_step is None:
                self.step = wizard_step

    def finish(
        self,
        wizard_step: str,
        *,
        ms: float | None = None,
        detail: str | None = None,
        status: str = "done",
    ) -> None:
        """Close a step the job times itself (choosing the area, the first forecast)."""
        with self.lock:
            record = self.steps[wizard_step]
            started = self._clock.pop(wizard_step, None)
            if ms is None and started is not None:
                ms = (perf_counter() - started) * 1000.0
            if ms is not None:
                record["ms"] = round(float(ms), 1)
            if detail is not None:
                record["detail"] = detail
            record["status"] = status
            record["progress"] = 1.0 if status == "done" else record["progress"]

    def pipeline_step(self, name: str, status: str, ms: float) -> None:
        """Fold one of the pipeline's ``city.step`` lines into its wizard step.

        A name `STEP_OF` does not map is added to the step the wizard is on, so its time lands in
        a row rather than nowhere. It never completes or advances that row - only the mapped steps
        behind a row decide when it is done - but it does count towards "loaded from disk", since
        a row that computed anything was not only read from the city folder.
        """
        mapped = STEP_OF.get(name)
        with self.lock:
            wizard_step = mapped or self.step
            record = self.steps.get(wizard_step)
            if record is None:
                return
            record["ms"] = round(float(record["ms"]) + float(ms or 0.0), 1)
            seen = self._seen.setdefault(wizard_step, {})
            seen[name] = status
            members = self.members(wizard_step)
            record["progress"] = round(
                sum(1 for member in members if member in seen) / max(len(members), 1), 3
            )
            complete = bool(members) and all(member in seen for member in members)
            if complete:
                record["status"] = "done"
                self._clock.pop(wizard_step, None)
                # `cache` only checks the tile manifest; it is never "loaded". A step counts as
                # loaded from disk when everything else behind it was read from the city folder.
                built = [s for n, s in seen.items() if n != "cache"]
                record["loaded_from_disk"] = bool(built) and all(s == "cached" for s in built)
            elif record["status"] == "waiting":
                record["status"] = "running"
                self._clock.setdefault(wizard_step, perf_counter())
        if complete and mapped is not None:
            following = self._next_wizard_step(name)
            if following is not None and following != wizard_step:
                self.begin(following)

    def _next_wizard_step(self, pipeline_name: str) -> str | None:
        try:
            index = self.pipeline_steps.index(pipeline_name)
        except ValueError:
            return None
        for name in self.pipeline_steps[index + 1 :]:
            mapped = STEP_OF.get(name)
            if mapped is not None:
                return mapped
        return None

    def fail_step(self, wizard_step: str) -> None:
        with self.lock:
            record = self.steps[wizard_step]
            started = self._clock.pop(wizard_step, None)
            if started is not None and not record["ms"]:
                record["ms"] = round((perf_counter() - started) * 1000.0, 1)
            record["status"] = "failed"

    # ---- the first forecast's stages ---------------------------------------------------------
    def stage_started(self, stage: str) -> None:
        with self.lock:
            stages = self.steps["first_forecast"]["stages"]
            stages.setdefault(stage, {"status": "waiting", "ms": None})
            stages[stage]["status"] = "running"
            self._clock[f"stage:{stage}"] = perf_counter()

    def stage_reset(self, stage: str) -> None:
        """Put a stage opened too early back to waiting (products, when Flash runs after Pulse)."""
        with self.lock:
            stages = self.steps["first_forecast"]["stages"]
            if self._clock.pop(f"stage:{stage}", None) is not None and stage in stages:
                stages[stage] = {"status": "waiting", "ms": None}

    def stage_finished(self, stage: str, ms: float | None = None, *, failed: bool = False) -> None:
        with self.lock:
            stages = self.steps["first_forecast"]["stages"]
            started = self._clock.pop(f"stage:{stage}", None)
            if ms is None and started is not None:
                ms = (perf_counter() - started) * 1000.0
            stages.setdefault(stage, {"status": "waiting", "ms": None})
            stages[stage]["status"] = "failed" if failed else "done"
            stages[stage]["ms"] = round(float(ms), 1) if ms is not None else None
            done = sum(1 for s in stages.values() if s["status"] == "done")
            self.steps["first_forecast"]["progress"] = round(done / max(len(stages), 1), 3)
            if not failed and self.failed_step is None:
                self.progress = max(self.progress, 0.85 + 0.15 * done / max(len(stages), 1))

    def close_open_stages(self) -> None:
        """A stage still running when the cycle returned ends there (products, usually)."""
        with self.lock:
            open_stages = [key.split(":", 1)[1] for key in self._clock if key.startswith("stage:")]
        for stage in open_stages:
            self.stage_finished(stage)

    def apply_stage_ms(self, stage_ms: dict[str, Any]) -> None:
        """Replace the live stage timings with the run's own, which are the numbers of record."""
        with self.lock:
            record = self.steps["first_forecast"]
            stages = record["stages"]
            kept: dict[str, float] = {}
            for stage in FORECAST_STAGES:
                if stage not in stage_ms:
                    continue
                value = float(stage_ms[stage])
                kept[stage] = round(value, 1)
                entry = stages.setdefault(stage, {"status": "waiting", "ms": None})
                entry["ms"] = round(value, 1)
                # Flash is 0 ms when it did not run (a design storm has no ensemble to spread).
                entry["status"] = "skipped" if stage == "flash" and value <= 0 else "done"
            # The run's own numbers, as `run.json` holds them, for a reader that wants the row.
            record["stage_ms"] = kept

    # ---- reading it --------------------------------------------------------------------------
    def steps_snapshot(self) -> dict[str, dict[str, Any]]:
        """The six steps as the wizard reads them. A running step reports its time so far."""
        with self.lock:
            out: dict[str, dict[str, Any]] = {}
            for name, record in self.steps.items():
                row = {
                    k: (dict(v) if isinstance(v, dict) else v)
                    for k, v in record.items()
                    if k != "stages"
                }
                started = self._clock.get(name)
                if record["status"] == "running" and started is not None:
                    so_far = (perf_counter() - started) * 1000.0
                    row["ms"] = round(max(float(record["ms"] or 0.0), so_far), 1)
                if "stages" in record:
                    stages = {}
                    for stage, entry in record["stages"].items():
                        item = dict(entry)
                        clock = self._clock.get(f"stage:{stage}")
                        if entry["status"] == "running" and clock is not None:
                            item["ms"] = round((perf_counter() - clock) * 1000.0, 1)
                        stages[stage] = item
                    row["stages"] = stages
                out[name] = row
            return out

    def log_entries(self) -> list[dict[str, str]]:
        with self.lock:
            return [entry.to_dict() for entry in self.lines]

    def to_dict(self) -> dict[str, Any]:
        entries = self.log_entries()
        return {
            "job_id": self.job_id,
            "city": self.city,
            "design_storm": self.design_storm,
            "status": self.status,
            "step": self.step,
            "progress": round(self.progress, 3),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_s": round(self.elapsed_s, 1),
            "log_tail": [entry["text"] for entry in entries[-TAIL_LINES:]],
            "log": entries,
            "log_total": self.lines_total,
            "steps": self.steps_snapshot(),
            "forecast": self.forecast,
            "first_run_id": self.first_run_id,
            "error": self.error,
            "failed_step": self.failed_step,
        }

    def record(self) -> dict[str, Any]:
        """What is persisted when the job ends: the finished state and every line it kept.

        The lines are stored as ``lines`` (``[{ts, text, level}]``, at most `MAX_LOG_LINES`), each
        stamped when the tap captured it, so a reloaded wizard shows the build's own log with its
        own times rather than a reconstruction. `served_job` turns a record back into the shape a
        live job answers with.
        """
        payload = self.to_dict()
        payload.pop("log_tail", None)
        payload["lines"] = payload.pop("log")
        payload["recorded_at"] = _stamp()
        return payload


_JOBS: dict[str, OnboardState] = {}
_LATEST: dict[str, str] = {}
_GUARD = threading.Lock()


class _Tap:
    """A structlog processor that copies this thread's log lines into its job.

    Keyed on the thread, so a build running beside ordinary API traffic captures its own lines and
    nothing else. It returns the event dict untouched, so the normal console output is unaffected.

    Every event the job's thread logs is copied - ``city.*``, but also ``osm.*``, ``landcover.*``,
    ``drains.*`` and the cycle's own ``cycle.*`` and ``products.*`` lines - minus the handful in
    `DENY_EVENTS` that duplicate a note. It used to forward only ``city.*``, which left the first
    forecast, nine tenths of the job, with three lines.
    """

    def __init__(self) -> None:
        self.by_thread: dict[int, OnboardState] = {}

    def attach(self, state: OnboardState) -> None:
        self.by_thread[threading.get_ident()] = state

    def detach(self) -> None:
        self.by_thread.pop(threading.get_ident(), None)

    def __call__(self, logger: Any, name: str, event: dict[str, Any]) -> dict[str, Any]:
        state = self.by_thread.get(threading.get_ident())
        if state is None:
            return event
        message = str(event.get("event", ""))
        if not message or message in DENY_EVENTS:
            return event
        level = str(event.get("level") or name or "info").lower()
        if level == "debug":
            return event
        # The pipeline logs structured key-values; rendering them back to one line keeps the
        # stream readable without inventing wording.
        extras = " ".join(
            f"{k}={v}" for k, v in event.items() if k not in DROP_KEYS and v is not None
        )
        state.note(f"{message} {extras}".strip(), level=_level(level))
        if not message.startswith("city."):
            return event
        # A step whose declared output is missing comes back "failed" with no `city.step_failed`
        # line before it (`varuna_city.pipeline._run_step`); it is the same failure.
        failed_here = message == "city.step_failed" or (
            message == "city.step" and str(event.get("status") or "") == "failed"
        )
        if event.get("step") and state.failed_step is None:
            mapped = STEP_OF.get(str(event["step"]))
            if mapped and failed_here:
                state.step = mapped
        if failed_here and state.failed_step is None:
            # Stop the screen on the step that actually broke, having just mapped `step` to it.
            #
            # `run_city` does not stop at a failed step: it records the failure and runs the
            # remaining ones, each of which fails for want of an output the first one never
            # wrote. Without this freeze the bar walks on to 83 % and the wizard names the *last*
            # step it saw, so a judge watches the build die at "Build graph" when what is missing
            # is the open data at step one. The deployed wizard reported exactly that.
            state.failed_step = str(event.get("step") or "")
            # An unmapped step fails the row it was folded into, the one the wizard is on.
            mapped = STEP_OF.get(state.failed_step) or state.step
            if mapped in state.steps:
                state.fail_step(mapped)
            return event
        if state.failed_step is not None:
            return event
        if message == "city.step":
            name_ = str(event.get("step") or "")
            state.pipeline_step(name_, str(event.get("status") or ""), float(event.get("ms") or 0))
            state.steps_done += 1
            # The build is five sixths of the job; the design-storm cycle is the last sixth.
            state.progress = min(0.83, state.steps_done / max(state.steps_total, 1) * 0.83)
        return event


def _level(level: str) -> str:
    if level in {"warning", "warn"}:
        return "warning"
    if level in {"error", "critical", "exception", "fatal"}:
        return "error"
    return "info"


_TAP = _Tap()


def install_tap() -> None:
    """Add the log tap to structlog's processor chain, once."""
    import structlog as sl

    config = sl.get_config()
    processors = list(config["processors"])
    if any(isinstance(p, _Tap) for p in processors):
        return
    # Before the renderer, which is always last and consumes the event dict.
    processors.insert(max(len(processors) - 1, 0), _TAP)
    sl.configure(processors=processors)


# ---- the first forecast's stages, seen while they run ----------------------------------------
_STAGE_OWNER: dict[int, OnboardState] = {}
"""The job each onboarding thread is timing stages for. Any other thread passes straight through."""

_STAGES_WRAPPED = False
_WRAP_GUARD = threading.Lock()


def _stage_wrap(owner: Any, name: str, stage: str, *, then: str | None = None) -> None:
    """Wrap ``owner.name`` so that, on an onboarding thread, it reports ``stage`` as it runs.

    ``then`` is the stage that starts when this one ends: products has no function of its own to
    wrap, so it opens where Pulse closes and ends when the cycle returns. Flash runs between the
    two when the cycle has an ensemble to spread, so Flash's own wrapper puts products back to
    waiting on entry and opens it again on exit; a design storm with no ensemble never calls
    Flash, and products then runs from Pulse's end as before.

    Deliberately not `functools.wraps`: that copies the wrapped function's ``__dict__``, and the
    console's Compute live wrappers (`varuna_api.routers.cycle`) mark theirs with an attribute and
    skip anything already carrying it. Copying the marker would switch the budget bar off.
    """
    original: Callable[..., Any] = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        state = _STAGE_OWNER.get(threading.get_ident())
        if state is None:
            return original(*args, **kwargs)
        if then is not None:
            state.stage_reset(then)
        state.stage_started(stage)
        mark = perf_counter()
        try:
            result = original(*args, **kwargs)
        except BaseException:
            state.stage_finished(stage, (perf_counter() - mark) * 1000.0, failed=True)
            raise
        state.stage_finished(stage, (perf_counter() - mark) * 1000.0)
        if then is not None:
            state.stage_started(then)
        return result

    wrapped.__name__ = getattr(original, "__name__", name)
    wrapped.__qualname__ = getattr(original, "__qualname__", name)
    wrapped.__doc__ = getattr(original, "__doc__", None)
    setattr(owner, name, wrapped)


def _install_stage_wrappers() -> None:
    """Once per process: time Sky, the Twin and Pulse on onboarding threads."""
    global _STAGES_WRAPPED
    with _WRAP_GUARD:
        if _STAGES_WRAPPED:
            return
        import varuna_pulse.cycle as pulse_cycle
        import varuna_twin.runner as twin_runner
        from varuna_cycle import twin_cycle

        _stage_wrap(twin_cycle, "_sky_rain_on_city", "sky")
        # `run_cycle` imports `run_twin` and `run_pulse` from their modules at call time, so
        # wrapping the module attributes is what it picks up.
        _stage_wrap(twin_runner, "run_twin", "twin")
        _stage_wrap(pulse_cycle, "run_pulse", "pulse", then="products")
        _stage_wrap(twin_cycle, "_flash_members", "flash", then="products")
        _STAGES_WRAPPED = True


MIN_FREE_BYTES = 160 * 1024**2
"""Free space demanded before fetching tiles, sized on the two cities this project ships.

Measured, not guessed: Chennai's three tiles are 138.6 MB and Mumbai's are 143.1 MB (two
Copernicus GLO-30 degrees plus one 3-degree ESA WorldCover each, and the WorldCover tile is
~120 MB of that on its own). 160 MB covers either with room to spare.

The city folder is not counted because a rebuild overwrites it in place rather than adding to
it, and the tiles are dropped again straight after a fetch - see `_drop_fetched_tiles`. The
Railway volume is 500 MB with 311 MB already resident, so a floor much above this would refuse
a download that in fact fits.
"""


def _drop_fetched_tiles(state: OnboardState, tiles: list[Path]) -> None:
    """Delete the tiles this job downloaded, once the city they built is on disk.

    Only ever the files `_warm_cache` fetched in this run. A warm cache fetches nothing and so
    drops nothing, which is what protects the demo laptop: `tools/prefetch_city_cache.py` put
    those tiles there deliberately and SPEC.md 7.9 needs them for the offline rehearsal.

    This mirrors the Railway entrypoint's `drop_download_cache` and for the same reason - the
    500 MB volume already holds 311 MB, so 139 MB of tiles is affordable during a build and dead
    weight after one. The cost is honest: onboarding the same city again re-downloads.
    """
    freed = 0
    for tile in tiles:
        try:
            if tile.is_file():
                freed += tile.stat().st_size
                tile.unlink()
        except OSError as error:  # a tile we cannot remove is wasted space, not a failed build
            log.warning("onboard.tile_not_dropped", path=str(tile), error=str(error))
    if freed:
        state.note(
            f"Released {freed / 1e6:.0f} MB of downloaded tiles; the built city is what persists."
        )


def _warm_cache(state: OnboardState) -> list[Path]:
    """Put this city's open-data tiles on disk before the build asks for them.

    The pipeline never downloads. Its first step verifies `city/cache/` against the manifest and
    raises if a tile is missing (`varuna_city.cache`: "Phase 1 never downloads"). That is right on
    the demo laptop, where `tools/prefetch_city_cache.py` has already run and SPEC.md 7.9 wants
    the wizard to work with the venue's network off.

    It was wrong everywhere else, and that is why city-in-a-box did not work on the deployed site.
    The Railway entrypoint *deletes* the tiles after the first build - `drop_download_cache`, on
    purpose, because the 500 MB volume cannot hold both the 339 MB cache and the city it produces -
    so the wizard demanded a cache the deployment had removed by design. Every run failed at the
    `cache` step, and, before the freeze above, reported it as "Build graph" at 83 %.

    Three outcomes, each named in the log:

    * cache already warm -> return without touching the network (the stage path)
    * cache cold, downloading refused -> raise, naming the command that fills it
    * cache cold, downloading allowed -> fetch this city's tiles only, then build

    Returns the tiles it downloaded, so the caller can release them once the build has consumed
    them. Empty when the cache was already warm, which is what keeps a laptop's prefetched tiles
    untouched.
    """
    import shutil
    import subprocess
    import sys

    from varuna_city.cache import cache_root, verify_cache
    from varuna_city.config import load_city_config
    from varuna_schemas.paths import repo_root

    rows = verify_cache(load_city_config(state.city), strict=False)
    missing = [row for row in rows if not row.ok]
    if not missing:
        state.note(f"Open data already cached: {len(rows)} tiles present. Nothing to download.")
        return []

    names = ", ".join(row.key for row in missing)
    offline = state.from_cache_only or os.environ.get("VARUNA_OFFLINE") == "1"
    if offline:
        msg = (
            f"{len(missing)} of {len(rows)} open-data tiles are not cached ({names}), and this "
            f"job was asked not to download. Fill the cache with "
            f"`uv run python tools/prefetch_city_cache.py --city {state.city}`, or start the job "
            f"with from_cache_only=false to let it fetch them."
        )
        raise RuntimeError(msg)

    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free < MIN_FREE_BYTES:
        msg = (
            f"Not enough room to cache {state.city}'s open data: {free / 1e6:.0f} MB free at "
            f"{root}, and the tiles plus the city they build need about "
            f"{MIN_FREE_BYTES / 1e6:.0f} MB. Free space on the volume and start the job again."
        )
        raise RuntimeError(msg)

    script = repo_root() / "tools" / "prefetch_city_cache.py"
    if not script.is_file():
        msg = f"The prefetch tool is not in this image ({script}), so the tiles cannot be fetched."
        raise RuntimeError(msg)

    state.note(
        f"{len(missing)} tiles are not cached ({names}). Fetching them once from Copernicus and "
        f"ESA; {free / 1e6:.0f} MB free."
    )
    # Streamed rather than captured: SPEC.md 7.9's acceptance criterion is that every line the
    # wizard shows comes from the pipeline, and a download that takes a minute should say so while
    # it happens rather than in one block when it is over.
    # Fixed argv, no shell, script path derived from the repo root - not user input.
    process = subprocess.Popen(
        [sys.executable, str(script), "--city", state.city],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(repo_root()),
    )
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            state.note(line.rstrip())
    if process.wait() != 0:
        msg = (
            f"Fetching {state.city}'s open data failed (prefetch exited {process.returncode}). "
            f"The log above is the downloader's own output."
        )
        raise RuntimeError(msg)

    still_missing = [row.key for row in verify_cache(load_city_config(state.city)) if not row.ok]
    if still_missing:
        msg = f"Tiles are still missing after the fetch: {', '.join(still_missing)}."
        raise RuntimeError(msg)
    state.note(f"Open data cached. Building {state.city}.")
    return [Path(row.path) for row in missing]


def _num(value: Any) -> str:
    """12345 -> "12,345": the pipeline's own count, grouped for reading."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _area_detail(state: OnboardState) -> str | None:
    """One line on the area chosen: the config's AOI and the grid it becomes."""
    from varuna_city.config import city_grid, load_city_config

    try:
        config = load_city_config(state.city)
        grid = city_grid(config)
    except Exception as error:  # a detail line is not worth failing the build over
        log.warning("onboard.area_unreadable", city=state.city, error=str(error))
        return None
    return (
        f"{config.aoi_id}, {grid.width} x {grid.height} cells at {grid.res:g} m, EPSG:{config.crs}"
    )


def _stat_details(stats: dict[str, Any]) -> dict[str, str]:
    """One line per wizard step from the numbers the pipeline itself recorded (`pipeline.json`)."""

    def get(step: str, key: str) -> Any:
        return (stats.get(step) or {}).get(key)

    details: dict[str, str] = {}
    osm_roads, osm_buildings = get("osm", "roads"), get("osm", "buildings")
    if osm_roads is not None and osm_buildings is not None:
        details["fetch_open_data"] = (
            f"{_num(osm_roads)} roads, {_num(osm_buildings)} buildings from OSM"
        )
    depressions, spurious = get("depressions", "depressions"), get("condition", "pits_spurious")
    if depressions is not None:
        details["condition_terrain"] = f"{_num(depressions)} depressions kept" + (
            f", {_num(spurious)} spurious pits breached" if spurious is not None else ""
        )
    edges, length = get("drains", "edges"), get("drains", "pipe_length_km")
    if edges is not None:
        details["infer_drains"] = f"{_num(edges)} inferred pipes" + (
            f", {float(length):,.0f} km" if length is not None else ""
        )
    segments, units = get("segments", "segments"), get("units", "units")
    if segments is not None:
        details["build_graph"] = f"{_num(segments)} road segments" + (
            f", {_num(units)} surface units" if units is not None else ""
        )
    return details


def _run(state: OnboardState) -> None:
    from varuna_city.pipeline import STEPS, run_city

    _TAP.attach(state)
    state.status = "running"
    state.steps_total = len(STEPS)
    state.pipeline_steps = tuple(step.name for step in STEPS)
    try:
        state.begin("choose_area")
        state.finish("choose_area", detail=_area_detail(state))

        state.begin("fetch_open_data")
        state.note(f"Building {state.city} from {'cache' if state.from_cache_only else 'source'}.")
        mark = perf_counter()
        fetched = _warm_cache(state)
        with state.lock:
            fetch = state.steps["fetch_open_data"]
            fetch["ms"] = round(float(fetch["ms"]) + (perf_counter() - mark) * 1000.0, 1)
        result = run_city(state.city)
        for wizard_step, line in _stat_details(result.stats).items():
            with state.lock:
                state.steps[wizard_step]["detail"] = line
        failed = [s for s in result.steps if s.status not in {"ok", "cached"}]
        if failed:
            names = ", ".join(s.name for s in failed)
            msg = f"The {state.city} build failed at: {names}."
            raise RuntimeError(msg)
        # Only after the build succeeded: a failed one is retried from what is already on disk,
        # and deleting the tiles first would make the retry re-download them.
        _drop_fetched_tiles(state, fetched)

        state.begin("first_forecast")
        state.step = "first_forecast"
        state.progress = 0.85
        state.note(f"City built. Running the first cycle on {state.design_storm}.")
        state.first_run_id = _first_forecast(state)
        detail: str | None = None
        if state.forecast:
            detail = (
                f"{_num(state.forecast.get('wet_streets'))} of "
                f"{_num(state.forecast.get('streets_total'))} streets wet"
            )
        elif state.first_run_id is None:
            # `_first_forecast` returns None only when the bundle is absent: the city is built,
            # there was nothing to forecast with, and that is not a failure of the forecast.
            detail = f"{state.design_storm} is not built on this machine"
        state.finish(
            "first_forecast",
            detail=detail,
            status="done" if state.first_run_id else "skipped",
        )
        state.progress = 1.0
        state.status = "finished"
    except Exception as error:  # a build failure is a job outcome, not an API crash
        state.status = "failed"
        state.error = str(error)
        with state.lock:
            running = [name for name, rec in state.steps.items() if rec["status"] == "running"]
        for name in running:
            state.fail_step(name)
        state.note(f"Failed: {error}", level="error")
        log.warning("onboard.failed", job=state.job_id, city=state.city, error=str(error))
    finally:
        state.finished_at = _now()
        _TAP.detach()
        _persist(state)
        log.info(
            "onboard.done",
            job=state.job_id,
            city=state.city,
            status=state.status,
            elapsed_s=round(state.elapsed_s, 1),
            run_id=state.first_run_id,
        )


def _peak_cycle_ts(bundle: str) -> Any:
    """The cycle to issue the first forecast from: the storm's **median** frame.

    Two wrong answers were tried first, and both are instructive about what a nowcast is.

    **The bundle's first cycle** forecasts nothing. `CHN-IDF-25yr` builds from 40 dBZ at 05:40 to
    65 dBZ at 06:50; a nowcast issued at 06:00 can only extrapolate the 40 dBZ in front of it, so
    the AOI got 0.7 mm/h and not one street was wet. Sky was right and the answer was useless.

    **The peak frame** forecasts a catastrophe that is not in the bundle. A Chicago hyetograph is a
    single sharp spike with no advection: every frame is the same cell at a different intensity,
    so STEPS has no motion to extrapolate and simply persists whatever instant it was handed. Issue
    at the 65 dBZ peak and it holds 447 mm/h for three hours - the design storm specifies 150 mm
    of rain and the forecast delivered **609 mm**, a metre of water on the median street. Nothing
    was broken; the nowcast cannot know a spike is about to fall off, and a spike with no motion is
    a pathological thing to hand one.

    **The median frame** is the honest issue time. It is the intensity the storm actually sustains,
    so persisting it neither invents the peak nor misses the event. It is also what the design
    storm is *for*: a stated depth over a stated duration to drive the Twin, rather than a moving
    system to test a nowcast against.

    Returns None when the radar cannot be read, and the caller falls back to the default cycle.
    """
    from datetime import timedelta

    import numpy as np
    import zarr
    from varuna_replay.bundle import bundle_dir, load_manifest

    try:
        manifest = load_manifest(bundle)
        store = zarr.open(str(bundle_dir(bundle) / "radar" / "frames.zarr"), mode="r")
        dbz = np.asarray(store["dbz"])
        minutes = np.asarray(store["time_min"])
        per_frame = [
            float(np.nanmax(dbz[i])) if np.isfinite(dbz[i]).any() else float("-inf")
            for i in range(dbz.shape[0])
        ]
        usable = [v for v in per_frame if np.isfinite(v)]
        if not usable:
            return None
        median = float(np.median(usable))
        chosen = int(np.argmin([abs(v - median) for v in per_frame]))
        return manifest.t0 + timedelta(minutes=float(minutes[chosen]))
    except Exception as error:  # a bundle without readable radar still gets a default cycle
        log.warning("onboard.peak_cycle_failed", bundle=bundle, error=str(error))
        return None


def _storm_summary(bundle: str, run: dict[str, Any]) -> dict[str, Any] | None:
    """The design storm as the finish card states it: total, duration, peak rate, and whence.

    Read from the bundle's manifest when it is on this machine (`design_storm.total_depth_mm`,
    its duration and the hyetograph's largest block). The deployed API ships no bundles, so there
    the same three numbers are taken from the AOI-mean rain the run itself recorded, and the
    record says so: that is the rain the Twin was actually forced with, over the run's window.
    """
    from varuna_schemas.paths import bundle_dir

    try:
        manifest = json.loads((bundle_dir(bundle) / "manifest.json").read_text(encoding="utf-8"))
        storm = manifest.get("design_storm") or {}
        hyetograph = [float(v) for v in storm.get("hyetograph_mm_h") or []]
        total = storm.get("total_depth_mm")
        duration = storm.get("duration_min")
        if total is not None and duration is not None:
            return {
                "id": bundle,
                "total_mm": round(float(total), 1),
                "duration_min": int(duration),
                "peak_mm_h": round(max(hyetograph), 1) if hyetograph else None,
                "source": "manifest",
            }
    except (OSError, ValueError, TypeError):
        pass
    rain = [float(v) for v in run.get("rain_aoi_mm_h") or [] if v is not None]
    step_min = float(run.get("step_min") or 5)
    if not rain:
        return None
    return {
        "id": bundle,
        "total_mm": round(sum(rain) * step_min / 60.0, 1),
        "duration_min": int(len(rain) * step_min),
        "peak_mm_h": round(max(rain), 1),
        "source": "run",
    }


def _forecast_summary(run_id: str, bundle: str) -> dict[str, Any] | None:
    """The finish card's numbers, every one read from the run directory the cycle wrote."""
    from varuna_schemas.paths import run_dir

    try:
        folder = run_dir(run_id)
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("onboard.summary_unreadable", run_id=run_id, error=str(error))
        return None
    wet: dict[str, Any] = {}
    try:
        wet = json.loads((folder / "segments_wet.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        wet = {}
    depths = wet.get("depth_cm") or {}
    peaks = [max(series) for series in depths.values() if series]
    stage_ms = {
        key: value
        for key, value in (run.get("stage_ms") or {}).items()
        if key in FORECAST_STAGES and isinstance(value, int | float)
    }
    return {
        "run_id": run_id,
        "cycle_ts": run.get("cycle_ts"),
        "n_steps": run.get("n_steps"),
        "step_min": run.get("step_min"),
        "streets_total": wet.get("n_segments_total"),
        "wet_streets": wet.get("n_segments_wet", len(peaks) if depths else None),
        "wet_threshold_cm": wet.get("min_depth_cm"),
        "median_peak_cm": round(statistics.median(peaks), 1) if peaks else None,
        "max_peak_cm": round(max(peaks), 1) if peaks else None,
        "stage_ms": stage_ms,
        "forecast_ms": run.get("total_ms") or sum(stage_ms.values()) or None,
        "mass_balance_err": run.get("mass_balance_err"),
        "storm": _storm_summary(bundle, run),
    }


def _first_forecast(state: OnboardState) -> str | None:
    """Run one cycle of the design storm on the newly built city.

    Returns the run id, or None with a note when the bundle is not there. A city built without a
    first forecast is still a city; refusing to report the build because the storm is missing
    would hide the thing that did work (SPEC.md 6.8).
    """
    from varuna_cycle.twin_cycle import run_cycle

    cycle_ts = _peak_cycle_ts(state.design_storm)
    if cycle_ts is not None:
        state.note(
            f"Forecasting from {cycle_ts:%H:%M} IST, the design storm's median frame - the "
            f"intensity it sustains rather than its instantaneous peak."
        )
    _install_stage_wrappers()
    ident = threading.get_ident()
    _STAGE_OWNER[ident] = state
    try:
        result = run_cycle(
            bundle=state.design_storm,
            cycle_ts=cycle_ts,
            city=state.city,
            mode="baked",
            overwrite=True,
        )
    except FileNotFoundError as error:
        state.note(
            f"No first forecast: the {state.design_storm} bundle is not built "
            f"(`make bundle BUNDLE={state.design_storm}`). The city itself is ready.",
            level="warning",
        )
        log.warning("onboard.no_bundle", city=state.city, error=str(error))
        return None
    finally:
        _STAGE_OWNER.pop(ident, None)
        state.close_open_stages()
    state.forecast = _forecast_summary(result.run_id, state.design_storm)
    if state.forecast:
        state.apply_stage_ms(state.forecast["stage_ms"])
    # The deepest street, not the deepest cell: a coastal cell below the sea's datum can hold
    # metres of water on land, which is real but is not a street a reader can picture. The
    # finish card quotes the same figure.
    street_peak = (state.forecast or {}).get("max_peak_cm")
    state.note(
        f"First forecast published: {result.run_id}, deepest street "
        f"{street_peak if street_peak is not None else result.peak_depth_cm} cm on "
        f"{result.wet_segments} wet segments."
    )
    return result.run_id


# ---- the persisted last build ------------------------------------------------------------------
def record_path(city: str) -> Path:
    """``city/<city>/onboard_last.json``. Raises ValueError for a name that is not one segment."""
    from varuna_schemas.paths import city_dir

    return Path(city_dir(city)) / RECORD_FILE


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Temp file beside the target, then a rename: a reader sees the old record or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _persist(state: OnboardState) -> None:
    """Write the job's record atomically, keeping the last good build beside the last attempt.

    ``last_attempt`` is always this job. ``last_finished`` is this job only when it finished with
    a forecast; otherwise the one already on disk is carried over untouched. A failed rebuild
    must never erase the run a good one made: an onboarded city's console opens that run by
    default (`varuna_api.runs_util.latest_run_for`), so losing it would put the console back on
    whichever run sorts newest by name.
    """
    try:
        path = record_path(state.city)
        existing = read_record(state.city) or {}
        record = state.record()
        good = record["status"] == "finished" and bool(record.get("first_run_id"))
        payload = {
            "schema": RECORD_SCHEMA,
            "city": state.city,
            "last_attempt": record,
            "last_finished": record if good else existing.get("last_finished"),
        }
        _write_atomic(path, payload)
    except (OSError, ValueError, TypeError) as error:
        # A record we cannot write is lost history, not a failed build.
        log.warning("onboard.record_not_written", city=state.city, error=str(error))


_RECORD_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
"""Parsed records by path, with the mtime and size they were read at. `latest_run_for` asks for
the onboarded run on every request that defaults a run, and the record is up to ~120 KB of JSON."""


def read_record(city: str) -> dict[str, Any] | None:
    """The persisted record for a city, or None when there is none or it cannot be read."""
    try:
        path = record_path(city)
        info = path.stat()
    except (OSError, ValueError):
        return None
    key = str(path)
    cached = _RECORD_CACHE.get(key)
    if cached is not None and cached[0] == info.st_mtime_ns and cached[1] == info.st_size:
        return cached[2]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    _RECORD_CACHE[key] = (info.st_mtime_ns, info.st_size, payload)
    return payload


def _run_exists(run_id: Any) -> bool:
    from varuna_schemas.paths import run_dir

    if not isinstance(run_id, str) or not run_id:
        return False
    try:
        return (run_dir(run_id) / "run.json").is_file()
    except ValueError:
        return False


def _served(record: dict[str, Any] | None, *, seeded: bool = False) -> dict[str, Any] | None:
    """A record as the API serves it: whether its first run is still here, and where it came from.

    ``seeded`` is true when the record was shipped with the deployment (`varuna_api.seed`) rather
    than written by a build on this machine; the wizard says so rather than presenting another
    machine's log as this one's.
    """
    if not isinstance(record, dict):
        return None
    out = dict(record)
    out["first_run_exists"] = _run_exists(out.get("first_run_id"))
    out["seeded"] = bool(out.get("seeded") or seeded)
    return out


def served_job(record: dict[str, Any], *, built: bool | None = None) -> dict[str, Any]:
    """A persisted record in the shape a live job answers with (``log`` and ``log_tail``).

    ``from_record`` is true, so a reader can tell a build it is watching from one it is reading.
    """
    out = dict(record)
    lines = [entry for entry in out.pop("lines", None) or [] if isinstance(entry, dict)]
    out["log"] = lines
    out["log_tail"] = [str(entry.get("text", "")) for entry in lines[-TAIL_LINES:]]
    out.setdefault("log_total", len(lines))
    out["from_record"] = True
    if built is not None:
        out["built"] = built
    return out


def previous_build(city: str) -> dict[str, Any]:
    """``previous`` (the last good build, else the last attempt) and ``last_attempt`` when it
    differs, each with whether its first run is still on this API. Both None with no record."""
    payload = read_record(city) or {}
    seeded = bool(payload.get("seeded"))
    finished = payload.get("last_finished")
    attempt = payload.get("last_attempt")
    previous = finished if isinstance(finished, dict) else attempt
    later = None
    if (
        isinstance(attempt, dict)
        and isinstance(previous, dict)
        and attempt.get("job_id") != previous.get("job_id")
    ):
        later = {
            key: attempt.get(key)
            for key in (
                "job_id",
                "status",
                "started_at",
                "finished_at",
                "elapsed_s",
                "error",
                "failed_step",
            )
        }
    return {"previous": _served(previous, seeded=seeded), "last_attempt": later}


def _city_of_job(job_id: str) -> str | None:
    """``onboard-chennai-1a2b3c4d`` -> ``chennai``; None for an id this module never minted."""
    if not job_id.startswith("onboard-"):
        return None
    city, _, suffix = job_id[len("onboard-") :].rpartition("-")
    if not city or not suffix:
        return None
    return city


def record_for_job(job_id: str) -> dict[str, Any] | None:
    """The persisted record of a job that is no longer in memory, found by its id.

    Only a record whose ``job_id`` matches is returned: the city in the id picks the file, and
    the file has to name the same job, so an old id never answers with a newer build.
    """
    city = _city_of_job(job_id)
    if city is None:
        return None
    payload = read_record(city) or {}
    seeded = bool(payload.get("seeded"))
    for key in ("last_attempt", "last_finished"):
        record = payload.get(key)
        if isinstance(record, dict) and record.get("job_id") == job_id:
            return _served(record, seeded=seeded)
    return None


def onboard_run_id(city: str) -> str | None:
    """The first run of the city's last good onboarding build, when that run is on this API."""
    payload = read_record(city) or {}
    finished = payload.get("last_finished")
    if not isinstance(finished, dict):
        return None
    run_id = finished.get("first_run_id")
    return run_id if _run_exists(run_id) else None


def start_job(city: str, design_storm: str, from_cache_only: bool) -> OnboardState:
    """Start a build, or hand back the one already running for this city."""
    with _GUARD:
        running = _JOBS.get(_LATEST.get(city, ""))
        if running is not None and running.status in {"queued", "running"}:
            return running

        state = OnboardState(
            job_id=f"onboard-{city}-{uuid.uuid4().hex[:8]}",
            city=city,
            design_storm=design_storm,
            from_cache_only=from_cache_only,
        )
        _JOBS[state.job_id] = state
        _LATEST[city] = state.job_id

    install_tap()
    thread = threading.Thread(target=_run, args=(state,), name=f"onboard-{city}", daemon=True)
    thread.start()
    log.info("onboard.started", job=state.job_id, city=city, storm=design_storm)
    # A moment for the thread to mark itself running, so the first poll is not "queued" forever
    # if the caller polls immediately.
    time.sleep(0.05)
    return state


def get_job(job_id: str) -> OnboardState | None:
    return _JOBS.get(job_id)


def latest_job(city: str) -> OnboardState | None:
    return _JOBS.get(_LATEST.get(city, ""))
