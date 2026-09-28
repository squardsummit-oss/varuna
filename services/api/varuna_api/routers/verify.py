"""Verification scores for one event (SPEC.md 12, 7.10; task P9.7).

Every number comes from `varuna_verify`, computed from run artifacts and the bundle's curated
pins. Nothing here is typed in, and what cannot be computed is returned as an `unavailable` entry
with the reason rather than as a plausible figure (rule 6).

**Scored once per set of inputs.** The sweep reads every run's wet streets and routes against the
city graph: 9-10 s warm on the laptop and far longer on a shared host, and every top bar and the
landing page ask for it. The answer is kept until an input changes - the bundle's ground truth or
any run's `segments_wet.json` (added, removed or rewritten) - so a new bake is scored again and
a page load never recomputes a score nobody's inputs moved. Concurrent first requests wait for
the one computation rather than each starting their own.

**Rain skill by lead time** (`GET /v1/verification/rain-skill`) is kept the same way under its own
key - the bundle's truth, radar and gauges and every run's rain products - and its own lock, so
scoring the pins and scoring the rain never wait on each other. It is a separate path because it
is ~150 KB that only `/verify` reads, where the headline every top bar fetches is ~8 KB.

**A deployment serves the laptop's rain scores, and only for the laptop's runs.** A deployment
seeds `demo/runs`, which omit `rain/cube.zarr` and `rain/quantiles.zarr` (15 and 4.7 MB a cycle),
so its scorer finds nothing to score. `uv run varuna verify` writes the full answer, scored on the
machine that baked the runs, to `demo/verification/<event>.rain-skill.json[.gz]` with a record of
the runs it scored. When the live scorer has nothing to score, that copy is served - only if this
server holds exactly those runs, by id and `run.json`, under the same scorer version - with
`provenance.served_from` saying so. Anything else keeps the honest not-scored answer, with
`shipped_copy.refused` saying why the copy was not used.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query

from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.verify")

router = APIRouter(prefix="/v1", tags=["verification"])

_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_lock = threading.Lock()
_rain_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_rain_lock = threading.Lock()


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def inputs_fingerprint(event: str) -> tuple[Any, ...]:
    """What the sweep for ``event`` reads: its ground truth and every run's wet streets."""
    from varuna_schemas.paths import bundles_dir, runs_dir

    parts: list[Any] = [_stat(Path(bundles_dir()) / event / "ground_truth.geojson")]
    root = runs_dir()
    if root.is_dir():
        for run in sorted(root.iterdir()):
            wet = _stat(run / "segments_wet.json")
            if wet is not None:
                parts.append((run.name, *wet))
    return tuple(parts)


def scored_sweep(event: str) -> dict[str, Any]:
    """``varuna_verify.event.sweep(event)``, recomputed only when its inputs have changed."""
    from varuna_verify.event import sweep

    with _lock:
        key = inputs_fingerprint(event)
        hit = _cache.get(event)
        if hit is not None and hit[0] == key:
            return hit[1]
        result = sweep(event)
        _cache[event] = (key, result)
        log.info("verify.scored", bundle=event, runs=len(key) - 1)
        return result


def scored_rain_skill(event: str) -> dict[str, Any]:
    """``varuna_verify.rain_event.event_rain_skill(event)``, recomputed only when its inputs change."""
    from varuna_verify.rain_event import event_rain_skill, rain_event_inputs

    with _rain_lock:
        key = rain_event_inputs(event)
        hit = _rain_cache.get(event)
        if hit is not None and hit[0] == key:
            return hit[1]
        result = event_rain_skill(event)
        _rain_cache[event] = (key, result)
        log.info(
            "verify.rain_scored",
            bundle=event,
            available=result.get("available"),
            cycles=result.get("n_cycles"),
        )
        return result


SHIPPED_FALLBACK_MISSING = frozenset({"truth", "runs"})
"""The scorer's ``missing`` values a shipped copy may stand in for: this server has no truth
field or no run keeping rain products. Runs that keep rain products and fail to load are a fault
here, and are reported as one rather than covered by another machine's scores."""


def shipped_dir() -> Path:
    """Where the shipped rain-skill copies live: ``demo/verification`` beside the demo runs."""
    from varuna_schemas.paths import repo_root

    return repo_root() / "demo" / "verification"


def _held_runs(event: str) -> dict[str, str | None]:
    """Every run of ``event`` this server holds, by id, with its ``run.json`` digest (or None)."""
    from varuna_schemas.paths import runs_dir
    from varuna_verify.rain_event import run_json_digest

    root = runs_dir()
    held: dict[str, str | None] = {}
    if not root.is_dir():
        return held
    for run in sorted(root.iterdir()):
        meta = run / "run.json"
        if not run.is_dir() or not meta.is_file():
            continue
        try:
            bundle = json.loads(meta.read_text(encoding="utf-8")).get("bundle")
        except (OSError, ValueError):
            continue
        if bundle != event:
            continue
        try:
            held[run.name] = run_json_digest(run)
        except (OSError, ValueError):
            held[run.name] = None
    return held


def _refusal(record: dict[str, Any], event: str, held: dict[str, str | None]) -> str | None:
    """Why the shipped copy does not describe the runs held here, or None when it does."""
    from varuna_verify.rain_event import RAIN_EVENT_VERSION, SHIPPED_FORMAT

    if record.get("format") != SHIPPED_FORMAT or record.get("event") != event:
        return f"It is not a {SHIPPED_FORMAT} copy for {event}."
    if record.get("scorer_version") != RAIN_EVENT_VERSION:
        return (
            f"It was scored by version {record.get('scorer_version')} of the rain scorer and "
            f"this server runs version {RAIN_EVENT_VERSION}."
        )
    shipped = {str(r.get("run_id")): r.get("run_json_sha256") for r in record.get("runs") or []}
    if not shipped:
        return "It records no runs."
    if set(shipped) != set(held):
        only_shipped = sorted(set(shipped) - set(held))
        only_held = sorted(set(held) - set(shipped))
        parts = []
        if only_shipped:
            parts.append(f"not here: {', '.join(only_shipped)}")
        if only_held:
            parts.append(f"here but not scored: {', '.join(only_held)}")
        return (
            f"It was scored on {_runs(len(shipped))} and this server holds {len(held)} "
            f"({'; '.join(parts)})."
        )
    changed = sorted(run for run, digest in shipped.items() if held.get(run) != digest)
    if changed:
        return (
            f"run.json differs from the one it scored under the same id for "
            f"{_runs(len(changed))}, so they are not the runs it scored: {', '.join(changed)}."
        )
    return None


def _runs(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


def shipped_rain_skill(event: str, live: dict[str, Any]) -> dict[str, Any]:
    """The shipped copy of ``event``'s rain skill when it describes the runs held here, else ``live``.

    Only when the live scorer had nothing to score (:data:`SHIPPED_FALLBACK_MISSING`): a server
    that keeps rain products always scores them itself. The copy is served only when this server
    holds exactly the runs it records - the same ids and the same ``run.json`` - and the rain
    scorer is the version that wrote it. It then carries ``provenance.served_from`` saying where
    it was scored and why it is served here. Otherwise the live, not-scored answer stands, with
    ``shipped_copy.refused`` saying why the copy was not used.
    """
    if live.get("available") or live.get("missing") not in SHIPPED_FALLBACK_MISSING:
        return live
    from varuna_verify.rain_event import SHIPPED_KEY, read_shipped_text, shipped_names

    folder = shipped_dir()
    path = next((folder / name for name in shipped_names(event) if (folder / name).is_file()), None)
    if path is None:
        return live
    shown = f"demo/verification/{path.name}"
    try:
        document = json.loads(read_shipped_text(path))
    except (OSError, ValueError, EOFError) as error:
        log.warning("verify.rain_shipped_unreadable", bundle=event, path=shown, error=str(error))
        return {
            **live,
            "shipped_copy": {"file": shown, "refused": f"It could not be read: {error}"},
        }
    record = document.get(SHIPPED_KEY) if isinstance(document, dict) else None
    if not isinstance(record, dict):
        refused: str | None = "It carries no shipped record."
    elif not document.get("available"):
        refused = "It is not a scored event."
    else:
        refused = _refusal(record, event, _held_runs(event))
    if refused is not None or not isinstance(record, dict):
        reason = refused or "It carries no shipped record."
        log.info("verify.rain_shipped_refused", bundle=event, path=shown, reason=reason)
        return {**live, "shipped_copy": {"file": shown, "refused": reason}}
    body = {key: value for key, value in document.items() if key != SHIPPED_KEY}
    n_runs = len(record.get("runs") or [])
    note = (
        "Scored on the demo laptop from the baked runs' rain cubes (rain/quantiles.zarr and "
        "rain/cube.zarr), which the copies of those runs on this server do not carry. Served "
        f"from {shown} because this server holds the same {n_runs} runs, matched by id and "
        "run.json."
    )
    body["provenance"] = {
        **(body.get("provenance") or {}),
        "served_from": {**record, "kind": "shipped_copy", "file": shown, "note": note},
    }
    log.info("verify.rain_shipped_served", bundle=event, path=shown, runs=n_runs)
    return body


def clear_cache() -> None:
    """Forget every kept score (tests)."""
    with _lock:
        _cache.clear()
    with _rain_lock:
        _rain_cache.clear()


@router.get("/verification", summary="Scores for an event against its sourced ground truth")
def verification(
    event: Annotated[str, Query(description="Bundle id, e.g. MUM-2019-07-02")] = "MUM-2019-07-02",
) -> dict[str, Any]:
    """Detection, timing and the threshold sweep, plus what could not be scored and why."""
    try:
        return scored_sweep(event)
    except FileNotFoundError as error:
        raise api_error(404, "no_ground_truth", str(error)) from error


@router.get(
    "/verification/rain-skill",
    summary="Rain skill by lead time against the bundle's reconstructed truth field",
)
def verification_rain_skill(
    event: Annotated[str, Query(description="Bundle id, e.g. MUM-2019-07-02")] = "MUM-2019-07-02",
) -> dict[str, Any]:
    """Rain CSI, POD and FAR at 10, 20 and 40 mm/h by lead, pooled over the event's baked cycles.

    Scored for the ensemble mean, the ensemble median and persistence (the analysis each nowcast
    started from, held), with the Brier score and reliability of the members' exceedance
    probability, the sample size behind every lead and the useful skill horizon it computes. An
    event whose bundle has no truth field or no baked rain answers ``available: false`` with the
    reason; an unknown event is a 404.
    """
    from varuna_schemas.paths import bundles_dir

    if not (Path(bundles_dir()) / event).is_dir():
        raise api_error(
            404,
            "no_bundle",
            f"No bundle {event}. Run make bundle BUNDLE={event}, then make bake BUNDLE={event}.",
        )
    return shipped_rain_skill(event, scored_rain_skill(event))
