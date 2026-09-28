"""The one replay clock the API process owns, and the bundle listing behind the cards.

The console, the replay page and the WebSocket all read the same clock: the mode banner says
"Replay 30x", the time bar scrubs it, and ``replay.clock`` events keep every open tab in step.
So the process holds exactly one :class:`~varuna_replay.clock.ReplayClock`, created lazily
from ``VARUNA_BUNDLE`` and replaced when the operator picks another bundle.

Nothing here invents a bundle. :func:`bundle_summaries` reports what is under ``bundles/``,
says which members are missing (a folder can hold a manifest and nothing else while
``make bundle`` is still to run) and how many of its cycles are baked under ``data/runs``.
"""

from __future__ import annotations

import asyncio
import threading

import structlog
from varuna_cycle.bus import Bus
from varuna_cycle.registry import RunRegistry
from varuna_replay.bundle import (
    BundleLayout,
    BundleNotFoundError,
    list_bundle_ids,
    load_manifest,
)
from varuna_replay.clock import DEFAULT_SPEED, REPLAY_SPEEDS, ReplayClock
from varuna_replay.validate import REQUIRED_MEMBERS
from varuna_schemas.models import ReplayBundleSummary
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.settings import Settings

log = structlog.get_logger("varuna.api.replay")


def bundle_hint(bundle_id: str) -> str:
    """What to run when a bundle is not on disk (SPEC.md 6.8: errors say the fix)."""
    known = list_bundle_ids()
    known_text = f" Bundles on disk: {', '.join(known)}." if known else ""
    return f"Run make bundle BUNDLE={bundle_id}.{known_text}"


def missing_members(layout: BundleLayout, label: str) -> list[str]:
    """Members the bundle's label requires that are not on disk, in contract order."""
    required = REQUIRED_MEMBERS.get(label, REQUIRED_MEMBERS["Reconstructed replay"])
    members = layout.members()
    return [name for name in required if not members[name].exists()]


def summarize_bundle(
    manifest: BundleManifest, layout: BundleLayout, registry: RunRegistry
) -> ReplayBundleSummary:
    """One card on the replay page: what the bundle is, whether it is built, whether it is baked.

    The card also carries the manifest's honesty text verbatim - the calibration basis, the
    design-storm basis, the tide source and the cited sources - because those are the labels
    ``docs/SIMPLIFICATIONS.md`` promises the operator can read on ``/replay`` (rules 6 and 7).
    """
    absent = missing_members(layout, manifest.label)
    total = manifest.n_cycles
    baked_cycles = len({meta.cycle_ts for meta in registry.list(bundle=manifest.id, limit=10_000)})
    return ReplayBundleSummary(
        id=manifest.id,
        city=manifest.city,
        label=manifest.label,
        t0=manifest.t0,
        t1=manifest.t1,
        seed=manifest.seed,
        built=not absent,
        missing_members=absent,
        baked=baked_cycles >= total,
        baked_cycles=min(baked_cycles, total),
        total_cycles=total,
        sources_n=len(manifest.sources),
        sources=list(manifest.sources),
        ground_truth_n=manifest.ground_truth_n,
        synthetic_notes=list(manifest.synthetic_notes),
        calibration_basis=manifest.calibration_basis,
        design_storm_basis=manifest.design_storm.basis if manifest.design_storm else None,
        tide_source=manifest.tide_source,
        description=manifest.description,
    )


def bundle_summaries(registry: RunRegistry) -> list[ReplayBundleSummary]:
    """Every folder under ``bundles/`` that carries a manifest, sorted by id."""
    summaries: list[ReplayBundleSummary] = []
    for bundle_id in list_bundle_ids():
        layout = BundleLayout.for_bundle(bundle_id)
        try:
            manifest = load_manifest(layout.root)
        except (BundleNotFoundError, ValueError) as exc:
            log.warning("replay.manifest_unreadable", bundle=bundle_id, error=str(exc)[:200])
            continue
        summaries.append(summarize_bundle(manifest, layout, registry))
    return summaries


def _speed_from(settings: Settings) -> float:
    """``VARUNA_REPLAY_SPEED`` when the time bar offers it, else the demo's 30x."""
    speed = float(settings.varuna_replay_speed)
    if speed in REPLAY_SPEEDS:
        return speed
    log.warning("replay.speed_not_offered", configured=speed, using=DEFAULT_SPEED)
    return DEFAULT_SPEED


def _warm_streams(clock: ReplayClock) -> None:
    """Read a bundle's schedule ahead of the first Play; a failure is the clock's to report."""
    try:
        counts = clock.streams.counts()
    except Exception as exc:  # the clock reports missing members itself; never fail the request
        log.warning("replay.warm_failed", bundle=clock.bundle_id, error=repr(exc)[:200])
        return
    log.info("replay.warmed", bundle=clock.bundle_id, counts=counts)


class ReplayController:
    """Owns the process's clock: opens one, swaps it for another bundle, closes it on shutdown."""

    def __init__(self) -> None:
        self._clock: ReplayClock | None = None
        self._lock = threading.Lock()
        self._warm: asyncio.Task[None] | None = None

    @property
    def clock(self) -> ReplayClock | None:
        """The open clock, or ``None`` when no bundle has been opened yet."""
        return self._clock

    async def open(
        self,
        bundle_id: str,
        *,
        bus: Bus,
        registry: RunRegistry,
        settings: Settings,
    ) -> ReplayClock:
        """The clock for ``bundle_id``, creating it on first use.

        Raises :class:`~varuna_replay.bundle.BundleNotFoundError` when the folder or its
        manifest is missing; the router turns that into a 404 that names the make target.
        """
        with self._lock:
            current = self._clock
            if current is not None and current.bundle_id == bundle_id:
                return current
        clock = ReplayClock.for_bundle(
            bundle_id,
            bus=bus,
            registry=registry,
            speed=_speed_from(settings),
            mode="baked",
        )
        with self._lock:
            previous, self._clock = self._clock, clock
        if previous is not None:
            await previous.stop()
        # Reading the schedule of the demo bundle costs about a second and a half (a Zarr time
        # axis and a 400k-row Parquet column); do it off the loop so the first Play does not
        # wait for it.
        if self._warm is not None:
            self._warm.cancel()
        self._warm = asyncio.create_task(
            asyncio.to_thread(_warm_streams, clock), name=f"replay-warm-{clock.bundle_id}"
        )
        log.info(
            "replay.clock_opened",
            bundle=clock.bundle_id,
            t0=clock.t0.isoformat(),
            t1=clock.t1.isoformat(),
            speed=clock.speed,
            replaced=previous.bundle_id if previous else None,
        )
        return clock

    async def aclose(self) -> None:
        """Stop the clock's background task. Called from the API lifespan on shutdown."""
        warm, self._warm = self._warm, None
        if warm is not None:
            warm.cancel()
        with self._lock:
            clock, self._clock = self._clock, None
        if clock is not None:
            await clock.stop()
            log.info("replay.clock_closed", bundle=clock.bundle_id)


__all__ = [
    "ReplayController",
    "bundle_hint",
    "bundle_summaries",
    "missing_members",
    "summarize_bundle",
]
