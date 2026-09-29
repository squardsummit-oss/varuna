"""Seeding a deployment with the demo runs that ship in the repo.

The console is useless without a run: no depth raster, no wet streets, no hotspot rail, just the
honest empty state telling the operator to bake one. On the demo laptop `make bake` does that.
On a hosted deployment nothing can - a cycle takes about three minutes of CPU and the container
would spend its first quarter hour serving 404s.

So a small set of baked cycles is committed under ``demo/runs`` and copied onto the volume at
start-up. They are the same artifacts `make bake` writes, minus ``segment_forecast.parquet``:
that file is the product of record and `services/verify` wants it, but it is 19 MB per cycle and
the console never reads it - the compact ``segments_wet.json`` beside it is what the map draws
(`varuna_products.depth`).

**Re-seeding, and why it needs a marker.** A first version of this only seeded into an *empty*
run directory, which is right for protecting a locally baked cycle and wrong for everything
else: the moment the shipped set gained a product - the pump plan, as it happened - the volume
already held the older copies and would never take the new ones. `/v1/pumps` stayed 404 on a
deployment whose image contained the plan.

So each seeded run carries a ``.seeded`` marker holding a fingerprint of the demo set. A run
with no marker was baked on this machine and is never touched. A run whose marker matches is
already current. A run whose marker differs, or is missing from the volume entirely, is
replaced. The fingerprint is content-derived, so adding a file to the demo set is enough to make
the next boot pick it up; nothing has to be remembered to bump.

**A run baked here is never touched, and that is decided by what it carries.** A bake writes
``segment_forecast.parquet``, the product of record the shipped set deliberately omits, so a run
holding one was computed on this machine whatever else it lacks. That test used to be "carries
every file the shipped copy does", and it failed silently: a shipped run's ``alerts/`` folder is
named by the alerts *its* cycle raised, a fresh bake of the same cycle raises different ones, and
the fresh run was judged an old seeded copy, deleted, and replaced with the shipped one. On
2026-09-24 and again on 2026-09-26 that erased a complete re-bake of all seven demo cycles within
seconds of a test suite starting the API. ``LOCAL_PRODUCT`` is the rule now.

This is a copy, not a fallback path in the reader: once seeded the runs are ordinary runs on the
volume, a freshly baked cycle sits beside them, and nothing downstream has to know where they
came from.

**An onboarding record ships the same way, and never replaces one.** ``demo/onboard/<city>.json``
is a city-in-a-box build recorded on the demo laptop (`varuna_api.onboard`): its log lines, its
per-step times and the first forecast it made. The deployment cannot run the wizard (it is
switched off there, ADR-0038), so without the record its wizard has nothing to show but "built".
It is copied to ``city/<city>/onboard_last.json`` only when that file is absent and the city is
built on the volume, and it carries ``seeded: true`` so the wizard says it is a build recorded
elsewhere rather than presenting another machine's log as this one's. A build on this machine
writes its own record, which is then never overwritten.

**So does the first forecast that record names.** ``demo/onboard/runs/<run_id>`` is the run the
recorded build made, shipped like the demo runs (no ``segment_forecast.parquet``, no rain cubes)
and seeded by the same rules, so Pravesh on a deployment draws the forecast the record describes
instead of saying the run "is not on this API". It lives beside the record rather than in
``demo/runs``, which is the replay's seven cycles and is audited as exactly those. It is seeded
only into a city built on the volume, for the record's reason: a forecast for streets that are not
there describes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog
from varuna_schemas.paths import city_dir, repo_root, runs_dir

log = structlog.get_logger("varuna.api.seed")

__all__ = [
    "LOCAL_PRODUCT",
    "MARKER",
    "demo_onboard_dir",
    "demo_onboard_runs_dir",
    "demo_runs_dir",
    "run_fingerprint",
    "seed_demo_runs",
    "seed_onboard_records",
]

MARKER = ".seeded"
"""File written inside a seeded run, holding the fingerprint of the copy it came from."""

LOCAL_PRODUCT = "segment_forecast.parquet"
"""What only a bake on this machine writes: the shipped set omits it (19 MB a cycle), so a run
directory holding it is this deployment's own work and is never replaced or removed."""


def demo_runs_dir() -> Path:
    """Where the committed demo runs live."""
    return repo_root() / "demo" / "runs"


def demo_onboard_dir() -> Path:
    """Where committed onboarding records live: beside the demo runs, ``demo/onboard``."""
    return demo_runs_dir().parent / "onboard"


def demo_onboard_runs_dir() -> Path:
    """Where an onboarded city's shipped first forecast lives: ``demo/onboard/runs``."""
    return demo_onboard_dir() / "runs"


def _city_built(city: str) -> bool:
    """The API's own test for a built city: its segment table is on disk."""
    return (city_dir(city) / "segments.parquet").is_file()


def _run_city(run: Path) -> str | None:
    """The ``city`` a shipped run's ``run.json`` names, or None when it names none or is unreadable."""
    try:
        payload = json.loads((run / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    city = payload.get("city") if isinstance(payload, dict) else None
    return city if isinstance(city, str) and city else None


def _shipped_runs() -> list[Path]:
    """Every run to seed: the replay's ``demo/runs``, then each onboarded city's first forecast.

    An onboarding run is included only when its city is built here (see the module docstring); the
    replay's runs always are.
    """
    runs: list[Path] = []
    replay = demo_runs_dir()
    if replay.is_dir():
        runs.extend(
            run for run in sorted(replay.iterdir()) if run.is_dir() and (run / "run.json").is_file()
        )
    onboarded = demo_onboard_runs_dir()
    if onboarded.is_dir():
        for run in sorted(onboarded.iterdir()):
            if not run.is_dir() or not (run / "run.json").is_file():
                continue
            city = _run_city(run)
            try:
                built = city is not None and _city_built(city)
            except ValueError:
                built = False
            if not built:
                log.info("api.onboard_run_city_not_built", run=run.name, city=city)
                continue
            runs.append(run)
    return runs


def _seeded_record(payload: dict[str, Any]) -> dict[str, Any]:
    """The shipped record, marked as seeded at the top and on each build it holds."""
    out = dict(payload)
    out["seeded"] = True
    for key in ("last_finished", "last_attempt"):
        build = out.get(key)
        if isinstance(build, dict):
            out[key] = {**build, "seeded": True}
    return out


def seed_onboard_records() -> int:
    """Copy each ``demo/onboard/<city>.json`` to ``city/<city>/onboard_last.json`` where absent.

    Returns how many were written. Skipped, each with a log line: a file that is not a record for
    the city it is named after, a city that is not built on this volume (a record of a build
    that is not here would describe nothing), and - always - a city that already has a record,
    because that one was written by a build on this machine or seeded before.
    """
    from varuna_api.onboard import RECORD_FILE

    source = demo_onboard_dir()
    if not source.is_dir():
        return 0
    written = 0
    for shipped in sorted(source.glob("*.json")):
        city = shipped.stem
        try:
            folder = city_dir(city)
            payload = json.loads(shipped.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("api.onboard_record_unreadable", path=str(shipped), error=str(error))
            continue
        if not isinstance(payload, dict) or str(payload.get("city", "")).lower() != city.lower():
            log.warning("api.onboard_record_mismatch", path=str(shipped), city=city)
            continue
        target = folder / RECORD_FILE
        if target.exists():
            continue
        if not _city_built(city):
            log.info("api.onboard_record_city_not_built", city=city)
            continue
        tmp = target.with_name(f".{target.name}.seed.tmp")
        try:
            tmp.write_text(
                json.dumps(_seeded_record(payload), indent=1) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(tmp, target)
        except OSError as error:
            log.warning("api.onboard_record_not_seeded", city=city, error=str(error))
            tmp.unlink(missing_ok=True)
            continue
        written += 1
    if written:
        log.info("api.seeded_onboard_records", written=written, source=str(source))
    return written


def run_fingerprint(run: Path) -> str:
    """A short content fingerprint of one demo run: every file's relative path and size.

    Sizes rather than bytes, because the point is to notice that the shipped set has *changed*,
    and re-hashing 2.7 MB of PNGs on every boot to learn that would be an odd way to spend a
    container's first second. A product gained, lost or regenerated all move a size.
    """
    digest = hashlib.sha256()
    for path in sorted(run.rglob("*")):
        if path.is_file() and path.name != MARKER:
            digest.update(path.relative_to(run).as_posix().encode("utf-8"))
            digest.update(str(path.stat().st_size).encode("utf-8"))
    return digest.hexdigest()[:16]


def _missing_from(source: Path, destination: Path) -> bool:
    """True when source holds a file destination does not."""
    return any(
        item.is_file()
        and item.name != MARKER
        and not (destination / item.relative_to(source)).exists()
        for item in source.rglob("*")
    )


def seed_demo_runs() -> int:
    """Copy the committed demo runs onto the volume. Returns how many were written.

    A run the deployment baked for itself is left alone even when a demo run of the same id
    exists, because that run is the deployment's own work and the shipped set is only a fallback
    for a volume that has none. It is recognised by being complete: a baked run carries every
    file the shipped one does and more.
    """
    # First, and whether or not any runs ship: the record is independent of them. It never breaks
    # the run seeding - a record is history, the runs are what the console needs.
    try:
        seed_onboard_records()
    except Exception as error:
        log.warning("api.onboard_records_not_seeded", error=str(error))

    source = demo_runs_dir()
    runs = _shipped_runs()
    if not runs:
        return 0

    target = runs_dir()
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped_local = 0
    current = 0
    shipped: set[str] = set()
    for run in runs:
        shipped.add(run.name)

        destination = target / run.name
        fingerprint = run_fingerprint(run)
        marker = destination / MARKER

        if destination.is_dir():
            if (destination / LOCAL_PRODUCT).is_file() and not marker.is_file():
                # Baked here. Left alone even when the shipped copy holds a file this one does
                # not - alert documents are named per cycle and never match across two bakes.
                skipped_local += 1
                continue
            if marker.is_file():
                if marker.read_text(encoding="utf-8").strip() == fingerprint:
                    current += 1
                    continue
            elif not _missing_from(run, destination):
                # Unknown provenance, and complete. Either this deployment baked the cycle
                # itself, or an earlier version of this function seeded it before markers
                # existed; either way it already has everything the shipped set holds, so
                # leave it alone. A locally baked run is a superset - it carries
                # `segment_forecast.parquet`, which the shipped set deliberately omits - so it
                # can never fall into the branch below.
                skipped_local += 1
                continue
            else:
                # Unmarked *and* missing something the shipped set has: an old seeded copy from
                # before the markers, which is exactly what needs replacing.
                log.info("api.reseed_unmarked", run=run.name)
            shutil.rmtree(destination)

        shutil.copytree(run, destination)
        marker.write_text(fingerprint + "\n", encoding="utf-8")
        copied += 1

    # **A seeded run the shipped set no longer carries is taken back.** Seeding used to only
    # add, which was harmless while the set only ever gained products. Re-baking the demo cycles
    # with the ensemble changed every run id (flash0.0 to flash0.1), and the volume would have
    # kept both runs of each cycle: the picker listing every cycle twice, the single-member run
    # one click from the ensemble that replaced it. Only a run carrying the marker goes - one
    # without it is this deployment's own work, and is never in the shipped set by nature.
    removed = 0
    for existing in sorted(target.iterdir()):
        if existing.name in shipped or not (existing / MARKER).is_file():
            continue
        if (existing / LOCAL_PRODUCT).is_file():
            continue
        shutil.rmtree(existing)
        removed += 1

    log.info(
        "api.seeded_demo_runs",
        written=copied,
        already_current=current,
        left_alone=skipped_local,
        removed=removed,
        source=str(source),
        target=str(target),
    )
    return copied
