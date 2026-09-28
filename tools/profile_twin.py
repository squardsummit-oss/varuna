"""Profile the coupled Twin run and its kernels, reproducibly (SPEC.md 11.3, 14; task P4.6).

Every performance claim about the Twin is supposed to trace to a measurement someone can run
again (rule 13). The measurements behind the P4.6 plan were scratch scripts; this is the same
instrument, committed. It changes no module: every timer is a monkeypatched wrapper that is put
back when the measurement ends, so what it times is the code as shipped.

Two modes::

    # a run of N five-minute steps, split by stage and by kernel, with a state snapshot
    uv run python tools/profile_twin.py --out <dir> run --forcing <rain.npy> --steps 12

    # best-of-N and median per-call cost of each kernel and of the Python around them
    uv run python tools/profile_twin.py --out <dir> micro --snapshot <dir>/snapshot.npz

``--forcing`` is a ``(steps, rows, cols)`` mm/h cube on the city grid (``.npy``), ``sky`` to
run Sky for the cycle and save the cube it produced, or ``uniform:<mm/h>`` for a synthetic
field. ``--synthetic-city`` swaps Mumbai for a tiny generated grid, so the tool itself can be
tested without ``city/`` on disk.

**Contention is part of the number.** This laptop shares its four cores with other work, and a
timing taken while three simulations run beside it says little about the code. Every report
records the Python process count and the CPU load before and after, and is labelled
``under contention`` whenever either says something else was running. ``micro`` reports the
best and the median of N interleaved repeats: the best approximates the uncontended cost, the
median is what a run under the same load would see, and neither is quoted without the other.

Nothing is written outside ``--out``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import numpy as np

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_CYCLE = "2019-07-02T08:40:00+05:30"
DEFAULT_BUNDLE = "MUM-2019-07-02"
LOAD_CONTENDED_PCT = 25.0
"""CPU load at or above which a measurement is labelled contended even with no other Python.

A judgement, not a spec number: an idle Windows laptop sits in single digits, and a quarter of
four cores is one core's worth of somebody else's work."""


# ============================================================================ machine state
def machine_state() -> dict[str, Any]:
    """Python process count, CPU load and core counts, as the operating system reports them."""
    state: dict[str, Any] = {
        "taken_at": datetime.now(IST).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "physical_cores": None,
        "cpu_load_pct": None,
        "python_processes": _python_process_count(),
    }
    try:
        import numba

        state["numba_threads"] = int(numba.get_num_threads())
        state["numba_threads_max"] = int(getattr(numba.config, "NUMBA_NUM_THREADS"))  # noqa: B009
    except Exception:  # pragma: no cover - numba is a Twin dependency
        state["numba_threads"] = None
    if sys.platform == "win32":
        state.update(_windows_cpu())
    elif hasattr(os, "getloadavg"):
        cpus = os.cpu_count() or 1
        state["cpu_load_pct"] = round(100.0 * os.getloadavg()[0] / cpus, 1)
    return state


def _python_process_count() -> int | None:
    """How many Python interpreters are alive, this one included."""
    try:
        if sys.platform == "win32":
            listing = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout
            return sum(1 for line in listing.splitlines() if "python" in line.lower())
        proc = Path("/proc")
        if proc.is_dir():
            count = 0
            for comm in proc.glob("[0-9]*/comm"):
                try:
                    if "python" in comm.read_text().lower():
                        count += 1
                except OSError:
                    continue
            return count
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _windows_cpu() -> dict[str, Any]:
    """Load and core counts from Win32_Processor, in one PowerShell call."""
    script = (
        "$p = Get-CimInstance Win32_Processor; "
        "'{0};{1};{2}' -f ($p | Measure-Object LoadPercentage -Average).Average, "
        "($p | Measure-Object NumberOfCores -Sum).Sum, "
        "($p | Measure-Object NumberOfLogicalProcessors -Sum).Sum"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
        load, cores, logical = out.split(";")
        return {
            "cpu_load_pct": float(load) if load else None,
            "physical_cores": int(cores) if cores else None,
            "logical_cpus": int(logical) if logical else os.cpu_count(),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def contention_label(*states: dict[str, Any]) -> str:
    """``under contention`` if any snapshot saw another Python process or a busy CPU."""
    for state in states:
        others = state.get("python_processes")
        if others is not None and others > 1:
            return "under contention"
        load = state.get("cpu_load_pct")
        if load is not None and load >= LOAD_CONTENDED_PCT:
            return "under contention"
    return "idle"


# ============================================================================ timing helpers
def best_and_median(fn: Callable[[], object], repeats: int) -> dict[str, float]:
    """Best and median wall time of ``repeats`` calls, in milliseconds."""
    samples = []
    for _ in range(max(repeats, 1)):
        started = time.perf_counter()
        fn()
        samples.append(1000.0 * (time.perf_counter() - started))
    return {
        "best_ms": round(min(samples), 4),
        "median_ms": round(statistics.median(samples), 4),
        "n": len(samples),
    }


@dataclass
class Hooks:
    """Monkeypatched timers: seconds and call counts per key, restored on exit."""

    seconds: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    _restore: list[tuple[object, str, object]] = field(default_factory=list)

    def wrap(
        self,
        owner: object,
        name: str,
        key: str,
        after: Callable[[tuple, dict, object], None] | None = None,
        before: Callable[[tuple, dict], None] | None = None,
    ) -> None:
        # A missing target raises rather than being skipped: a renamed kernel would otherwise
        # report 0 s for its stage, which reads as a measurement and is not one.
        original = getattr(owner, name)
        seconds, calls = self.seconds, self.calls

        def timed(*args: Any, **kwargs: Any) -> object:
            if before is not None:
                before(args, kwargs)
            started = time.perf_counter()
            result = original(*args, **kwargs)
            seconds[key] = seconds.get(key, 0.0) + time.perf_counter() - started
            calls[key] = calls.get(key, 0) + 1
            if after is not None:
                after(args, kwargs, result)
            return result

        self._restore.append((owner, name, original))
        setattr(owner, name, timed)

    def replace(self, owner: object, name: str, stand_in: object) -> None:
        """Swap an attribute for ``stand_in`` until the hooks are restored."""
        self._restore.append((owner, name, getattr(owner, name)))
        setattr(owner, name, stand_in)

    def restore(self) -> None:
        for owner, name, original in reversed(self._restore):
            setattr(owner, name, original)
        self._restore.clear()


@contextmanager
def hooked() -> Iterator[Hooks]:
    hooks = Hooks()
    try:
        yield hooks
    finally:
        hooks.restore()


# ============================================================================ inputs
def synthetic_city(n: int = 15, n_nodes: int = 10, seed: int = 2019):
    """A small sloping grid with buildings, a drain trunk and a tidal outfall (rule 8: seeded)."""
    from varuna_twin.drain1d import BOUNDARY_TIDAL
    from varuna_twin.types import DrainNetwork, TerrainGrid

    res = 30.0
    shape = (n, n)
    z = np.repeat(((n - 1 - np.arange(n)) * 0.002 * res + 2.0)[:, None], n, axis=1)
    blocked = np.zeros(shape, dtype=bool)
    rng = np.random.default_rng(seed)
    blocked.flat[rng.choice(n * n, size=n * n // 10, replace=False)] = True
    blocked[[0, -1], :] = False
    blocked[:, [0, -1]] = False
    terrain = TerrainGrid(
        z=z.astype(np.float64),
        manning_n=np.full(shape, 0.03),
        blocked=blocked,
        imperviousness=np.full(shape, 0.7),
        cn=np.full(shape, 95.0),
        res_m=res,
        crs="EPSG:32643",
        transform=(res, 0.0, 0.0, 0.0, -res, 0.0),
    )
    rows = np.linspace(2, n - 3, n_nodes).astype(np.int32)
    cols = np.linspace(2, n - 3, n_nodes).astype(np.int32)
    z_ground = z[rows, cols].astype(np.float64)
    diameter, n_pipe, slope = 0.6, 0.013, 0.003
    area = np.pi * (diameter / 2) ** 2
    r_h = diameter / 4
    q_full = (1.0 / n_pipe) * area * r_h ** (2.0 / 3.0) * np.sqrt(slope)
    boundary = np.zeros(n_nodes, dtype=np.int8)
    boundary[-1] = BOUNDARY_TIDAL
    n_edges = n_nodes - 1
    network = DrainNetwork(
        node_ids=tuple(f"N{i:04d}" for i in range(n_nodes)),
        z_ground=z_ground,
        z_invert=z_ground - 1.5,
        storage_area=np.full(n_nodes, 1.0),
        inlet_length=np.full(n_nodes, 0.6),
        inlet_area=np.full(n_nodes, 0.04),
        kappa=np.full(n_nodes, 0.25),
        boundary=boundary,
        flap_gate=np.zeros(n_nodes, dtype=bool),
        cell_row=rows,
        cell_col=cols,
        edge_ids=tuple(f"E{i:04d}" for i in range(n_edges)),
        from_node=np.arange(n_edges, dtype=np.int32),
        to_node=np.arange(1, n_nodes, dtype=np.int32),
        length=np.full(n_edges, 40.0),
        area=np.full(n_edges, area),
        hydraulic_radius=np.full(n_edges, r_h),
        diameter=np.full(n_edges, diameter),
        edge_manning_n=np.full(n_edges, n_pipe),
        q_full=np.full(n_edges, q_full),
        beta=np.full(n_edges, 0.15),
    )
    return terrain, network


def synthetic_tide(t0: datetime, steps: int):
    """A rising illustrative sea over the run, so the boundary cells actually move."""
    from varuna_twin.types import TideSeries

    return TideSeries(
        times=(t0, t0 + timedelta(minutes=5 * max(steps, 1))),
        stage_m=np.array([0.5, 2.0], dtype=np.float64),
        source="illustrative",
    )


def load_forcing(
    spec: str, *, shape: tuple[int, int], steps: int, bundle: str, cycle: datetime, city: str
) -> tuple[np.ndarray, str]:
    """The rain cube a run is forced with, and a description of where it came from."""
    if spec.startswith("uniform:"):
        rate = float(spec.split(":", 1)[1])
        return np.full((steps, *shape), rate, dtype=np.float64), f"uniform {rate} mm/h (synthetic)"
    if spec == "sky":
        from varuna_cycle.twin_cycle import _sky_rain_on_city

        cube, _cycle = _sky_rain_on_city(bundle, cycle, city, steps)
        return np.asarray(cube, dtype=np.float64), f"Sky ensemble mean, {bundle} {cycle}"
    cube = np.load(spec)
    return np.asarray(cube, dtype=np.float64)[:steps], f"file {spec}"


# ============================================================================ run mode
SNAPSHOT_KEYS = (
    "h",
    "qx",
    "qy",
    "head",
    "flow",
    "r_eff",
    "q_inlet_cell",
    "q_surch_cell",
    "q_inlet_node",
    "q_surch_node",
    "tide",
)


def profile_run(inputs, *, snapshot_sync: int | None = None) -> tuple[dict, dict]:
    """Run ``run_twin`` with every kernel and wrapper timed; snapshot the state at one sync.

    The snapshot is taken immediately before sync ``snapshot_sync``'s exchange and holds what
    that sync's surface and drain calls then consumed, so ``micro`` can replay one real sync.
    """
    from varuna_twin import coupling_kernel, drain1d, drain_kernel, runner, swe2d

    snap: dict[str, np.ndarray] = {}
    live: dict[str, Any] = {}
    dts: list[float] = []

    def keep_surface(_a: tuple, _k: dict, result: object) -> None:
        live.setdefault("surface", result)

    def keep_rain(_a: tuple, _k: dict, result: object) -> None:
        live["r_eff"] = result

    def keep_tide(_a: tuple, _k: dict, result: object) -> None:
        live["tide"] = result

    def keep_drain(args: tuple, _k: dict, _r: object) -> None:
        live.setdefault("drain", args[1])

    def before_exchange(_a: tuple, kwargs: dict) -> None:
        n = hooks.calls.get("compute_exchange", 0)
        if snapshot_sync is None or n != snapshot_sync or snap:
            return
        surface = live["surface"]
        snap["h"] = np.array(kwargs["surface_h"], copy=True)
        snap["qx"] = surface.qx.copy()
        snap["qy"] = surface.qy.copy()
        snap["head"] = np.array(kwargs["drain_head"], copy=True)
        drain = live.get("drain")
        snap["flow"] = (
            drain.flow.copy()
            if drain is not None
            else np.zeros(inputs.network.n_edges, dtype=np.float64)
        )
        snap["r_eff"] = np.array(live["r_eff"], dtype=np.float64, copy=True)
        tide = live.get("tide")
        snap["tide"] = np.array(np.nan if tide is None else float(tide))

    def after_exchange(_a: tuple, _k: dict, result: Any) -> None:
        if "h" in snap and "q_inlet_cell" not in snap:
            snap["q_inlet_cell"] = result.q_inlet_cell.copy()
            snap["q_surch_cell"] = result.q_surcharge_cell.copy()
            snap["q_inlet_node"] = result.q_inlet_node.copy()
            snap["q_surch_node"] = result.q_surcharge_node.copy()

    before = machine_state()
    with hooked() as hooks:
        hooks.wrap(swe2d, "dry_state", "py_dry_state", after=keep_surface)
        hooks.wrap(swe2d, "_update_flux", "k_flux")
        hooks.wrap(swe2d, "_update_depth", "k_depth")
        hooks.wrap(swe2d, "cfl_dt", "py_cfl", after=lambda _a, _k, r: dts.append(cast(float, r)))
        hooks.wrap(swe2d, "_apply_tide", "py_tide")
        hooks.wrap(swe2d, "_audit", "py_audit")
        hooks.wrap(swe2d, "_check_ledger", "py_audit")
        hooks.wrap(swe2d, "_notes", "py_notes")
        hooks.wrap(swe2d, "run_surface", "surface_call")
        stepper = getattr(swe2d, "SurfaceStepper", None)
        if stepper is not None:
            hooks.wrap(stepper, "advance", "surface_call")
        hooks.wrap(drain_kernel, "step_kernel", "k_drain")
        hooks.wrap(coupling_kernel, "exchange_kernel", "k_exchange")
        hooks.wrap(drain1d, "simulate", "drain_call", after=keep_drain)
        hooks.wrap(
            runner,
            "compute_exchange",
            "compute_exchange",
            before=before_exchange,
            after=after_exchange,
        )
        hooks.wrap(runner, "effective_rain", "effective_rain", after=keep_rain)
        hooks.wrap(runner, "_tide_at", "py_tide_at", after=keep_tide)
        started = time.perf_counter()
        twin = runner.run_twin(inputs)
        wall_s = time.perf_counter() - started
        seconds, calls = dict(hooks.seconds), dict(hooks.calls)
    after = machine_state()

    d = np.asarray(dts, dtype=np.float64)
    surface_call = seconds.get("surface_call", 0.0)
    kernels = seconds.get("k_flux", 0.0) + seconds.get("k_depth", 0.0)
    n_surface_calls = calls.get("surface_call", 0)
    report = {
        "mode": "run",
        "contention": contention_label(before, after),
        "machine_before": before,
        "machine_after": after,
        "grid": list(inputs.terrain.shape),
        "n_nodes": int(inputs.network.n_nodes),
        "n_edges": int(inputs.network.n_edges),
        "n_steps": int(np.asarray(inputs.rain_mm_h).shape[0]),
        "run_twin_wall_s": round(wall_s, 3),
        "twin_stage_ms": twin.stage_ms,
        "mass_balance_error": float(twin.mass_balance.error_fraction),
        "peak_depth_m": float(np.max(twin.depth_m)),
        "timers_s": {k: round(v, 4) for k, v in sorted(seconds.items())},
        "calls": dict(sorted(calls.items())),
        "split_s": {
            "surface_kernels": round(kernels, 3),
            "surface_python": round(surface_call - kernels, 3),
            "drain_kernel": round(seconds.get("k_drain", 0.0), 3),
            "drain_python": round(seconds.get("drain_call", 0.0) - seconds.get("k_drain", 0.0), 3),
            "coupling_kernel": round(seconds.get("k_exchange", 0.0), 3),
            "coupling_python": round(
                seconds.get("compute_exchange", 0.0) - seconds.get("k_exchange", 0.0), 3
            ),
            "hydrology": round(seconds.get("effective_rain", 0.0), 3),
        },
        "surface_python_ms_per_call": (
            round(1000.0 * (surface_call - kernels) / n_surface_calls, 4)
            if n_surface_calls
            else None
        ),
        "cfl_substeps": int(d.size),
        "cfl_dt_s": (
            {
                "min": float(d.min()),
                "median": float(np.median(d)),
                "p10": float(np.percentile(d, 10)),
                "share_below_sync": float((d < 4.999).mean()),
            }
            if d.size
            else None
        ),
        "snapshot_sync": snapshot_sync if snap else None,
    }
    return report, snap


# ============================================================================ micro mode
def _no_kernel(*_args: object) -> None:
    """Stands in for a compiled kernel when only the Python around it is being timed."""


def micro(
    terrain,
    network,
    snap: dict[str, np.ndarray],
    *,
    threads: list[int],
    repeats: int,
    rounds: int,
    python_repeats: int | None = None,
) -> dict:
    """Per-call best and median of each kernel and of the surface wrapper around them.

    The surface call is timed over exactly one CFL sub-step - the snapshot's own ``dt`` - so
    ``call - (flux + depth)`` is the Python and NumPy cost that sub-step paid for being inside
    a call, which is the quantity the SurfaceStepper exists to remove.
    """
    import numba
    from varuna_twin import coupling, drain1d, runner, swe2d
    from varuna_twin.types import DrainState, SurfaceState

    kt = swe2d.prepare_terrain(terrain)
    solver = drain1d.prepare(network)
    sea = runner._build_sea_mask(terrain, network)
    tide = float(snap["tide"])
    tide_stage = None if not np.isfinite(tide) or sea is None else tide
    h0, qx0, qy0 = snap["h"], snap["qx"], snap["qy"]
    r_eff = np.ascontiguousarray(snap["r_eff"], dtype=np.float64)
    q_in = np.ascontiguousarray(snap["q_inlet_cell"], dtype=np.float64)
    q_su = np.ascontiguousarray(snap["q_surch_cell"], dtype=np.float64)
    dt = swe2d.cfl_dt(h0, kt.res_m, 5.0)

    qx, qy, h = qx0.copy(), qy0.copy(), h0.copy()
    work_scale = np.ones(kt.shape)
    rows = [np.zeros(kt.shape[0]) for _ in range(4)]

    def flux() -> None:
        qx[:] = qx0
        qy[:] = qy0
        swe2d._update_flux(
            h0,
            kt.z,
            kt.manning_n,
            kt.blocked,
            qx,
            qy,
            kt.res_m,
            dt,
            swe2d.GRAVITY,
            swe2d.DRY_DEPTH_M,
            swe2d.FRICTION_DEPTH_EXPONENT,
        )

    def depth() -> None:
        h[:] = h0
        swe2d._update_depth(
            h,
            qx,
            qy,
            r_eff,
            q_in,
            q_su,
            kt.blocked,
            work_scale,
            rows[0],
            rows[1],
            rows[2],
            rows[3],
            kt.res_m,
            dt,
        )

    def flux_and_depth() -> None:
        flux()
        depth()

    state = SurfaceState(h=h0.copy(), qx=qx0.copy(), qy=qy0.copy())

    def reset() -> None:
        state.h[:] = h0
        state.qx[:] = qx0
        state.qy[:] = qy0

    def surface_call() -> None:
        reset()
        run_surface_once()

    def run_surface_once() -> None:
        swe2d.run_surface(
            state,
            kt,
            dt,
            r_eff_ms=r_eff,
            q_inlet_ms=q_in,
            q_surcharge_ms=q_su,
            sea_mask=sea,
            tide_stage_m=tide_stage,
            max_dt_s=dt,
        )

    stepper_call: Callable[[], None] | None = None
    stepper_once: Callable[[], None] | None = None
    if hasattr(swe2d, "SurfaceStepper"):
        # The run-long audit is off here only because every repeat rewinds the state, which
        # a ledger rightly reads as water appearing from nowhere; its cost is one volume sum
        # per MASS_BALANCE_EVERY sub-steps, amortised to microseconds per call.
        stepper = swe2d.SurfaceStepper(state, kt, sea_mask=sea, audit_every=0)
        stepper.set_rain(r_eff)

        def stepper_once() -> None:
            stepper.advance(
                dt, q_inlet_ms=q_in, q_surcharge_ms=q_su, tide_stage_m=tide_stage, max_dt_s=dt
            )

        def stepper_call() -> None:
            reset()
            stepper_once()

    drain_state = DrainState(head=snap["head"].copy(), flow=snap["flow"].copy())
    sinks, sink_state = drain1d.no_sinks()

    def drain_sync() -> None:
        drain_state.head[:] = snap["head"]
        drain_state.flow[:] = snap["flow"]
        drain1d.simulate(
            solver,
            drain_state,
            duration_s=5.0,
            dt_s=1.0,
            q_inlet=snap["q_inlet_node"],
            q_surcharge=snap["q_surch_node"],
            tide_stage_m=tide_stage,
            sinks=sinks,
            sink_state=sink_state,
        )

    buffers = coupling.ExchangeBuffers.allocate(network.n_nodes, kt.z.shape)

    def exchange() -> None:
        coupling.compute_exchange(
            surface_h=h0,
            surface_z=kt.z,
            drain_head=snap["head"],
            network=network,
            solver=solver,
            cell_area_m2=kt.cell_area_m2,
            sync_s=5.0,
            out=buffers,
        )

    before = machine_state()
    for fn in (flux, depth, surface_call, drain_sync, exchange):
        fn()  # compile and warm caches outside the timed repeats
    if stepper_call is not None:
        stepper_call()

    default_threads = int(numba.get_num_threads())
    max_threads = int(getattr(numba.config, "NUMBA_NUM_THREADS"))  # noqa: B009
    py_repeats = repeats if python_repeats is None else python_repeats
    result: dict[str, Any] = {
        "mode": "micro",
        "grid": list(kt.shape),
        "n_nodes": int(network.n_nodes),
        "n_edges": int(network.n_edges),
        "substep_dt_s": dt,
        "repeats": repeats,
        "python_repeats": py_repeats,
        "rounds": rounds,
        "threads_default": default_threads,
        "rounds_data": [],
    }
    try:
        for rnd in range(rounds):
            entry: dict[str, Any] = {"kernels_by_threads": {}}
            for nt in threads:
                if nt > max_threads:
                    continue
                numba.set_num_threads(nt)
                entry["kernels_by_threads"][str(nt)] = {
                    "flux": best_and_median(flux, repeats),
                    "depth": best_and_median(depth, repeats),
                }
            numba.set_num_threads(default_threads)
            kernels = best_and_median(flux_and_depth, repeats)
            call = best_and_median(surface_call, repeats)
            entry["surface_kernels_one_substep"] = kernels
            entry["run_surface_one_substep"] = call
            entry["run_surface_overhead_ms"] = {
                "best": round(call["best_ms"] - kernels["best_ms"], 4),
                "median": round(call["median_ms"] - kernels["median_ms"], 4),
            }
            if stepper_call is not None:
                step = best_and_median(stepper_call, repeats)
                entry["stepper_advance_one_substep"] = step
                entry["stepper_overhead_ms"] = {
                    "best": round(step["best_ms"] - kernels["best_ms"], 4),
                    "median": round(step["median_ms"] - kernels["median_ms"], 4),
                }
            # The subtraction above is two noisy numbers under contention and can even come out
            # negative. This is the same call with both kernels replaced by no-ops, so what is
            # left is the Python and NumPy the sub-step pays for, measured directly. No rewind
            # here: with the kernels stubbed the only write is the sea clamp, which re-imposes
            # the same level every time, so the state is already the snapshot's. The rewind is
            # harness cost, not solver cost, and is reported on its own.
            # These calls take well under a millisecond, so 25 samples of them are mostly a sample
            # of the scheduler on a shared machine: they get their own, larger repeat count.
            entry["harness_reset_ms"] = best_and_median(reset, py_repeats)
            with hooked() as stubs:
                stubs.replace(swe2d, "_update_flux", _no_kernel)
                stubs.replace(swe2d, "_update_depth", _no_kernel)
                reset()
                entry["run_surface_python_only"] = best_and_median(run_surface_once, py_repeats)
                if stepper_once is not None:
                    entry["stepper_python_only"] = best_and_median(stepper_once, py_repeats)
                reset()
            entry["drain_sync_5_steps"] = best_and_median(drain_sync, repeats)
            entry["exchange_call"] = best_and_median(exchange, repeats)
            result["rounds_data"].append(entry)
            print(f"round {rnd}: {json.dumps(entry)}", flush=True)
    finally:
        numba.set_num_threads(default_threads)
    after = machine_state()
    result["machine_before"] = before
    result["machine_after"] = after
    result["contention"] = contention_label(before, after)
    return result


# ============================================================================ CLI
def _parse_threads(text: str) -> list[int]:
    return [int(t) for t in text.split(",") if t.strip()]


def _load_city(args: argparse.Namespace):
    if args.synthetic_city:
        return synthetic_city()
    from varuna_twin.city import load_network, load_terrain

    return load_terrain(args.city), load_network(args.city)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True, type=Path, help="directory for every output")
    parser.add_argument("--city", default="mumbai")
    parser.add_argument("--synthetic-city", action="store_true", help="a tiny generated grid")
    parser.add_argument("--quiet-logs", action="store_true", help="structlog at WARNING")
    sub = parser.add_subparsers(dest="mode", required=True)

    run_p = sub.add_parser("run", help="profile a coupled run")
    run_p.add_argument("--forcing", required=True, help="rain.npy | sky | uniform:<mm/h>")
    run_p.add_argument("--steps", type=int, default=12)
    run_p.add_argument("--bundle", default=DEFAULT_BUNDLE)
    run_p.add_argument("--cycle", default=DEFAULT_CYCLE)
    run_p.add_argument("--snapshot-sync", type=int, default=700)

    micro_p = sub.add_parser("micro", help="best-of-N per-call kernel and wrapper cost")
    micro_p.add_argument("--snapshot", required=True, type=Path)
    micro_p.add_argument("--threads", type=_parse_threads, default=[1, 2, 4, 8])
    micro_p.add_argument("--repeats", type=int, default=25)
    micro_p.add_argument("--rounds", type=int, default=2)
    micro_p.add_argument(
        "--python-repeats",
        type=int,
        default=400,
        help="samples for the sub-millisecond kernels-stubbed calls",
    )

    args = parser.parse_args(argv)
    if args.quiet_logs:
        import logging

        import structlog

        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    terrain, network = _load_city(args)

    if args.mode == "run":
        from varuna_twin.types import TwinInputs

        cycle = datetime.fromisoformat(args.cycle)
        cube, source = load_forcing(
            args.forcing,
            shape=terrain.shape,
            steps=args.steps,
            bundle=args.bundle,
            cycle=cycle,
            city=args.city,
        )
        if args.forcing == "sky":
            np.save(out / "rain.npy", cube)
        if args.synthetic_city:
            tide = synthetic_tide(cycle, cube.shape[0])
        else:
            from varuna_twin.city import load_tide

            tide = load_tide(args.bundle, city=args.city)
        inputs = TwinInputs(
            terrain=terrain, network=network, rain_mm_h=cube, t0=cycle, step_min=5, tide=tide
        )
        report, snap = profile_run(inputs, snapshot_sync=args.snapshot_sync)
        report["forcing"] = source
        report["command"] = sys.argv if argv is None else ["profile_twin.py", *argv]
        if snap:
            np.savez(out / "snapshot.npz", **snap)
        (out / "profile_run.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps(report, indent=2, default=str))
        return 0

    with np.load(args.snapshot) as data:
        snap = {k: np.asarray(data[k]) for k in data.files}
    report = micro(
        terrain,
        network,
        snap,
        threads=args.threads,
        repeats=args.repeats,
        rounds=args.rounds,
        python_repeats=args.python_repeats,
    )
    report["snapshot"] = str(args.snapshot)
    report["command"] = sys.argv if argv is None else ["profile_twin.py", *argv]
    (out / "profile_micro.json").write_text(json.dumps(report, indent=2, default=str))
    print(
        json.dumps({k: v for k, v in report.items() if k != "rounds_data"}, indent=2, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
