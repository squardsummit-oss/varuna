"""Running the STEPS members in worker processes (SPEC.md 11.1, P3.7).

SPEC.md 11.1 budgets the Sky stage at five seconds. It missed, and the STATUS BOARD has
carried the miss since 2026-09-09: 6.32 s warm at 20 members x 36 steps on the 120 x 120 Sky
domain, of which pySTEPS' nowcast is 5.7-6.0 s. Three tuning knobs had already been measured
without beating the default - ``num_workers`` at 2/4/6/8, the ``scipy`` and ``numpy`` FFT
backends, and ``domain="spectral"`` - so this module is not a fourth knob. It is the structural
answer the two measurements below point at.

**Measurement one: the nowcast is per-member, and almost nothing else.** Holding the domain,
the steps and the storm fixed and varying only the member count (2026-09-24, warm process, 12
python processes on an Intel i5-1155G7 with 4 physical and 8 logical cores)::

    members     1     2      5     10     20
    nowcast   331   643   1517   3413   6456   ms

A least-squares line through those points is 322 ms per member with an intercept of about 8 ms.
The cascade decomposition of the analysis, the noise filter and the AR coefficients are all
shared across members, and together they are lost in the noise of a single member. So nothing
is gained by making one member cheaper for the others' sake: the stage is twenty independent
three-hour integrations, and the only way it gets faster is running them at the same time.

**Measurement two: why threads could not do it.** ``cProfile`` over one 5-member 36-step
nowcast (2026-09-24; the profiler inflates the wall clock about 4x, so read the shares, not the
seconds)::

    2.740 s tottime   scipy.ndimage._nd_image.geometric_transform    40 % of the profile
    3.890 s cumtime   semilagrangian.extrapolate                     57 % of the profile
    2.447 s cumtime   steps.__update_state (AR, noise, recompose, mask)

The semi-Lagrangian advection is the stage, and it is ``scipy.ndimage`` C code that holds the
GIL. pySTEPS' own ``num_workers`` hands the member loop to dask's *thread* scheduler, so those
threads queue behind each other on exactly the hot call - which is why 2/4/6/8 workers measured
4.58/6.40/4.83/4.68 s against 4.52 s at one. Processes do not share a GIL; threads do.

**The split has to reproduce the cube, not merely a cube.** Rule 8 demands byte-identical
bakes, and the seven demo cycles are already baked, so a split that produced a *different*
valid 20-member ensemble would silently invalidate every committed run. pySTEPS seeds member
``j`` from a deterministic chain off the base seed - two ``RandomState`` draws per member, one
for the precipitation noise and one for the velocity perturbation
(``pysteps/nowcasts/steps.py``, the noise initialiser) - so :func:`member_seed` replays that
chain, and a group starting at member ``m`` is handed the seed pySTEPS would have held there.
Verified rather than argued: ``test_steps.py`` asserts that 8 members split 4 + 4 equal the
single call element for element, and that assertion is what pins this module to pySTEPS'
internals. If pySTEPS ever changes its seeding, that test goes red before a bake does.

**What it bought, measured end to end.** The whole Sky stage at 20 members x 36 steps on the
120 x 120 domain, same process, same inputs, three runs each (2026-09-24)::

    sequential   11.21   10.95   12.26  s   at 16 python processes
    pooled (4)    4.75    4.79    5.61  s   at 22-28 python processes

against SPEC.md 11.1's five seconds. Two of the three pooled runs are inside the budget and
the third is not, and the pooled runs were taken under *heavier* load than the sequential ones,
because the pool's own workers are four of the processes counted. So the honest statement is
that the stage now sits on the budget rather than at 2.2x over it - which is where the same
machine put it at 6.32 s warm when it was quiet, and at 10.9-12.3 s when it was not.

**The first cycle in a process does not wait for the pool.** Windows has no ``fork``, so every
worker imports numpy, scipy and pysteps from scratch: 16.0 s at 24 python processes, and 45.2 s
once inside a loaded pytest process against the 12-17 s the same cycle took sequentially.
Paying that *inside* a cycle would make the live path worse to make the bake path better, so
the pool is never waited for: :func:`run_groups` starts it, hands each worker an import to do,
and returns ``None`` so this cycle runs sequentially. The cycle after it finds the pool ready.
That is the right shape for both callers - ``make bake`` walks every cycle of a bundle in one
process (SPEC.md 11.11), so it pays one sequential cycle out of 49 and can pay none at all by
calling :func:`warm_pool` first, and a single cold "Compute live" is exactly as fast as it was
before this module existed, never slower.
"""

from __future__ import annotations

import atexit
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.pool")

__all__ = [
    "WORKERS_ENV",
    "member_groups",
    "member_seed",
    "pool_is_warm",
    "run_groups",
    "shutdown_pool",
    "worker_count",
]

WORKERS_ENV = "VARUNA_SKY_WORKERS"
"""Override for the worker count. ``0`` or ``1`` forces the sequential path."""

MIN_MEMBERS_PER_WORKER = 2
"""Below this a worker is not worth its pickle round trip, so the group count is capped at
``n_members // 2``. At 20 members and 4 workers this never binds."""

MIN_SPLIT_MEMBERS = 8
"""Fewer members than this run in this process, whatever the worker count would be.

A four-member cycle is under a second and spawning four interpreters to serve it is a loss even
once the pool is warm. It also keeps the single-stage unit tests, which run at four and six
members, from starting a pool at all - they would otherwise churn one on every change of member
count, and the suite would spend more time on interpreters than on forecasts."""

WARM_TASKS_PER_WORKER = 2
"""Import tasks queued per worker when the pool starts.

The queue is shared, so submitting exactly one task per worker would in principle let a single
worker take two while another takes none. Each task is an import that takes seconds, so an idle
worker that has taken one will not come back for a second before the others have started; two
per worker is belt and braces, and the import is cached, so a duplicate costs nothing.
"""

_POOL: ProcessPoolExecutor | None = None
_POOL_WORKERS = 0
_WARMING: list[Any] = []
_LOCK = threading.Lock()


def worker_count(n_members: int) -> int:
    """How many worker processes to split ``n_members`` across; 1 means run in this process.

    The default is four, not ``os.cpu_count()``. This machine reports 8 logical cores over 4
    physical ones, and the hot call is FFT- and memory-bandwidth-bound rather than
    latency-bound, so the second thread of a core buys little and costs cache; four is also
    what the measurement in this module's docstring was taken at. ``VARUNA_SKY_WORKERS``
    overrides it, and a bad value is logged and ignored rather than crashing a cycle.
    """
    members = int(n_members)
    raw = os.environ.get(WORKERS_ENV)
    requested: int | None = None
    if raw is not None:
        try:
            requested = int(raw)
        except ValueError:
            log.warning("sky.pool.bad_workers_env", value=raw, using="default")
        else:
            if requested <= 1:
                return 1
    if members < MIN_SPLIT_MEMBERS:
        return 1
    if requested is None:
        cores = os.cpu_count() or 1
        requested = min(4, max(1, cores // 2))
    return max(1, min(requested, members // MIN_MEMBERS_PER_WORKER))


def member_seed(base_seed: int, offset: int) -> int:
    """The seed pySTEPS would hold when it reached member ``offset`` of one call.

    ``pysteps.nowcasts.steps`` builds its per-member random states in a chain: for each member
    it makes a ``RandomState`` for the precipitation noise, draws the next seed from it, makes
    a ``RandomState`` for the velocity perturbation, and draws the next seed from that. Handing
    a group this seed makes its first member pySTEPS' member ``offset``, so a split ensemble is
    the *same* ensemble - which is the whole point, because the demo cycles are already baked.
    """
    seed = int(base_seed)
    for _ in range(int(offset)):
        state = np.random.RandomState(seed)
        seed = int(state.randint(0, high=int(1e9)))
        state = np.random.RandomState(seed)
        seed = int(state.randint(0, high=int(1e9)))
    return seed


def member_groups(n_members: int, n_workers: int) -> tuple[tuple[int, int], ...]:
    """``(offset, size)`` per group, splitting ``n_members`` as evenly as the count allows.

    The remainder is spread over the leading groups rather than piled onto the last one: the
    stage ends when its slowest group ends, so 20 members over 3 workers is 7 + 7 + 6 and not
    6 + 6 + 8.
    """
    total = int(n_members)
    groups = max(1, min(int(n_workers), total))
    base, extra = divmod(total, groups)
    out: list[tuple[int, int]] = []
    offset = 0
    for index in range(groups):
        size = base + (1 if index < extra else 0)
        if size <= 0:
            continue
        out.append((offset, size))
        offset += size
    return tuple(out)


def _warm_worker() -> bool:
    """Import in a worker what a real group would import, so the next cycle finds it loaded."""
    from pysteps.nowcasts import get_method

    import varuna_sky.steps  # noqa: F401  - imported for its side effect in this process

    get_method("steps")
    return True


def _ready_pool(n_workers: int) -> ProcessPoolExecutor | None:
    """The warm pool, or ``None`` after starting one that is not warm yet.

    Never blocks on the spawn. The pool is kept for the life of the process because the cost
    this module cannot remove is the workers' import of numpy, scipy and pysteps, which on
    Windows is paid per spawn; a bake walks every cycle of a bundle in one process, so paying
    it once rather than once per cycle is the difference between the pool helping and hurting.
    """
    global _POOL, _POOL_WORKERS, _WARMING
    with _LOCK:
        # A pool that is bigger than this cycle needs is reused, never rebuilt: the spare
        # workers simply take no task. Rebuilding on every change of member count would spawn
        # four interpreters to save a second, which is how a cache becomes a cost.
        if _POOL is None or _POOL_WORKERS < n_workers:
            if _POOL is not None:
                _POOL.shutdown(wait=False, cancel_futures=True)
            _POOL = ProcessPoolExecutor(max_workers=n_workers)
            _POOL_WORKERS = n_workers
            _WARMING = [
                _POOL.submit(_warm_worker) for _ in range(n_workers * WARM_TASKS_PER_WORKER)
            ]
            log.info("sky.pool.warming", workers=n_workers, this_cycle="sequential")
            return None
        if _WARMING:
            if not all(future.done() for future in _WARMING):
                log.info("sky.pool.still_warming", workers=n_workers, this_cycle="sequential")
                return None
            _WARMING = []
        return _POOL


def shutdown_pool() -> None:
    """Drop the pool. Registered with :mod:`atexit`, and called by the tests between runs."""
    global _POOL, _POOL_WORKERS, _WARMING
    with _LOCK:
        if _POOL is not None:
            _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None
        _POOL_WORKERS = 0
        _WARMING = []


def pool_is_warm(n_workers: int) -> bool:
    """Whether a pool of this size exists and has finished importing. For tests and logging."""
    with _LOCK:
        return (
            _POOL is not None
            and _POOL_WORKERS >= n_workers
            and all(future.done() for future in _WARMING)
        )


def warm_pool(n_members: int, timeout: float | None = None) -> bool:
    """Start the pool and *wait* for its workers to finish importing. Returns whether they did.

    The one caller that should use this is a batch one: ``make bake`` knows before it starts
    that it is about to run every cycle of a bundle, so it can pay the spawn once up front
    instead of losing its first cycle to the sequential path. A live cycle must not call it -
    that is exactly the wait :func:`run_groups` exists to avoid.

    ``timeout`` is a ceiling on the wait, not a promise about it; a ``False`` return means the
    workers are still importing and the next cycle will run sequentially, which is not an error.
    """
    workers = worker_count(int(n_members))
    if workers <= 1:
        return False
    _ready_pool(workers)
    with _LOCK:
        warming = list(_WARMING)
    for future in warming:
        try:
            future.result(timeout=timeout)
        except Exception as exc:
            log.warning("sky.pool.warm_failed", error=str(exc))
            return False
    return pool_is_warm(workers)


atexit.register(shutdown_pool)


def _group_worker(payload: dict[str, Any]) -> NDArray[np.floating]:
    """Run one group of members in a worker process. Must be importable by name (spawn)."""
    from varuna_sky.steps import steps_members

    return steps_members(**payload)


def run_groups(
    payloads: tuple[dict[str, Any], ...],
    n_workers: int,
) -> tuple[NDArray[np.floating], ...] | None:
    """Run each payload in a worker process, in group order; ``None`` if the pool is unusable.

    Returning ``None`` rather than raising is deliberate. A pool can fail to start for reasons
    that have nothing to do with the forecast - a sandbox that forbids process creation, an
    already daemonic parent, a machine out of handles - and none of them are a reason to fail a
    cycle when the sequential path produces exactly the same cube, only slower. The caller logs
    the fallback, so the slowdown is visible rather than mysterious. A pool that exists but has
    not finished importing returns ``None`` for the same reason: this cycle must not wait for
    it (see the module docstring).
    """
    try:
        pool = _ready_pool(n_workers)
        if pool is None:
            return None
        futures = [pool.submit(_group_worker, payload) for payload in payloads]
        return tuple(future.result() for future in futures)
    except Exception as exc:  # any pool failure falls back to the sequential path
        log.warning("sky.pool.failed", error=str(exc), fallback="sequential")
        shutdown_pool()
        return None
