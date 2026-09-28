"""The SurfaceStepper is run_surface with its setup hoisted, and nothing else (task P4.6).

The stepper exists only to stop the coupled run paying run_surface's per-call setup 2,160 times.
That is worth doing only if no number moves, so equivalence here is **bitwise**, not a tolerance:
every depth, flux and ledger volume after every call is compared with `np.array_equal` / `==`
against run_surface on the same inputs, on a grid with rain that changes between steps,
buildings, a sea boundary under a rising tide, inlets asking for more than a cell holds and
surcharge pushed back up.

Then the other half of the bargain: hoisting the checks out of the call must not hoist them out
of existence. A NaN in the inlet raster raises (the capture limiter would otherwise read it as
"take everything" and publish plausible water), a corrupted grid raises within 100 sub-steps,
and the audit does fire every 100 sub-steps across calls, which run_surface's per-call counter
never did inside the coupled run.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from test_runner import T0, _make_network, _make_rain_cube, _make_terrain
from varuna_twin import runner, swe2d
from varuna_twin.runner import run_twin
from varuna_twin.swe2d import (
    MassBalanceError,
    SurfaceStepper,
    prepare_terrain,
    run_surface,
)
from varuna_twin.types import SurfaceState, TerrainGrid, TideSeries, TwinInputs

RES_M = 30.0
SHAPE = (24, 20)
SYNC_S = 5.0
SYNCS_PER_STEP = 12
LEDGER = (
    "volume_rain_m3",
    "volume_surcharge_m3",
    "volume_inlet_m3",
    "volume_tide_in_m3",
    "volume_tide_out_m3",
    "volume_created_m3",
)


def _coastal_terrain(seed: int = 2019) -> tuple[TerrainGrid, np.ndarray]:
    """Ground falling toward a sea strip on the east edge, with buildings scattered inland."""
    n_rows, n_cols = SHAPE
    rng = np.random.default_rng(seed)
    z = np.repeat((0.05 * (n_cols - 1 - np.arange(n_cols)) + 0.2)[None, :], n_rows, axis=0)
    z = z + rng.uniform(-0.03, 0.03, size=SHAPE)
    blocked = np.zeros(SHAPE, dtype=bool)
    blocked.flat[rng.choice(z.size, size=z.size // 10, replace=False)] = True
    sea = np.zeros(SHAPE, dtype=bool)
    sea[:, -1] = True
    blocked[sea] = False
    # A sea floor 1.5 m down and a quay 1.0 m below mean sea level beside it, so the rising stage
    # stands up to 2.0 m over the quay - on its cell and across its face with the sea. More than
    # 1.8 m is the depth at which a 30 m cell needs a second CFL sub-step inside a 5 s sync. The
    # quay is what makes it so: the sea's own depth no longer sets the step (`swe2d.CflScope`),
    # only land and the flow over the shoreline faces do.
    z[sea] = -1.5
    z[:, -2] = -1.0
    terrain = TerrainGrid(
        z=z,
        manning_n=rng.uniform(0.015, 0.05, size=SHAPE),
        blocked=blocked,
        imperviousness=np.full(SHAPE, 0.8),
        cn=np.full(SHAPE, 95.0),
        res_m=RES_M,
        crs="EPSG:32643",
        transform=(RES_M, 0.0, 0.0, 0.0, -RES_M, 0.0),
    )
    return terrain, sea


def _forcing(n_syncs: int, seed: int = 7) -> list[dict[str, object]]:
    """Per-sync rain, inlet, surcharge and stage, changing as the coupled run's would."""
    rng = np.random.default_rng(seed)
    out = []
    rain = np.zeros(SHAPE)
    for k in range(n_syncs):
        if k % SYNCS_PER_STEP == 0:
            rain = rng.uniform(0.0, 80.0, size=SHAPE) / 3.6e6  # mm/h -> m/s
        inlet = np.zeros(SHAPE)
        surcharge = np.zeros(SHAPE)
        cells = rng.choice(inlet.size, size=12, replace=False)
        # Some inlets ask for far more than a cell can hold, so the limiter is exercised.
        inlet.flat[cells[:8]] = rng.uniform(0.0, 5e-3, size=8)
        surcharge.flat[cells[8:]] = rng.uniform(0.0, 5e-4, size=4)
        out.append(
            {
                "rain": rain,
                "inlet": inlet,
                "surcharge": surcharge,
                "stage": 0.1 + 0.9 * k / max(n_syncs - 1, 1),
            }
        )
    return out


def _wet_state(terrain: TerrainGrid) -> SurfaceState:
    rng = np.random.default_rng(11)
    h = rng.uniform(0.0, 0.3, size=SHAPE)
    h[terrain.blocked] = 0.0
    return SurfaceState(h=h, qx=np.zeros(SHAPE), qy=np.zeros(SHAPE))


def _copy(state: SurfaceState) -> SurfaceState:
    return SurfaceState(h=state.h.copy(), qx=state.qx.copy(), qy=state.qy.copy())


def test_stepper_is_bitwise_identical_to_run_surface_call_by_call() -> None:
    terrain, sea = _coastal_terrain()
    kernel = prepare_terrain(terrain)
    n_syncs = 10 * SYNCS_PER_STEP
    forcing = _forcing(n_syncs)
    reference = _wet_state(terrain)
    candidate = _copy(reference)
    stepper = SurfaceStepper(candidate, kernel, sea_mask=sea)

    ledger_reference = dict.fromkeys(LEDGER, 0.0)
    sub_steps = 0
    for k, f in enumerate(forcing):
        if k % SYNCS_PER_STEP == 0:
            stepper.set_rain(f["rain"])
        run = run_surface(
            reference,
            kernel,
            SYNC_S,
            r_eff_ms=f["rain"],
            q_inlet_ms=f["inlet"],
            q_surcharge_ms=f["surcharge"],
            sea_mask=sea,
            tide_stage_m=f["stage"],
            max_dt_s=SYNC_S,
        )
        step = stepper.advance(
            SYNC_S,
            q_inlet_ms=f["inlet"],
            q_surcharge_ms=f["surcharge"],
            tide_stage_m=f["stage"],
            max_dt_s=SYNC_S,
        )
        assert np.array_equal(candidate.h, reference.h), f"h differs after sync {k}"
        assert np.array_equal(candidate.qx, reference.qx), f"qx differs after sync {k}"
        assert np.array_equal(candidate.qy, reference.qy), f"qy differs after sync {k}"
        assert step.n_steps == run.n_steps
        for name in LEDGER:
            assert getattr(step, name) == getattr(run, name), f"{name} differs at sync {k}"
            ledger_reference[name] += getattr(run, name)
        sub_steps += run.n_steps

        if (k + 1) % SYNCS_PER_STEP == 0:  # an output step: the run-long ledger agrees too
            summary = stepper.summary()
            for name in LEDGER:
                assert getattr(summary, name) == ledger_reference[name], (name, k)
            assert summary.volume_stored_m3 == reference.volume_m3(kernel.cell_area_m2)

    assert stepper.n_steps == sub_steps
    # The fixture must actually exercise what it claims to.
    assert ledger_reference["volume_tide_in_m3"] > 0.0
    assert ledger_reference["volume_inlet_m3"] > 0.0
    assert ledger_reference["volume_surcharge_m3"] > 0.0
    assert sub_steps > n_syncs, "deep water should force more than one CFL sub-step per sync"
    result = stepper.finish()
    assert result.error_fraction < swe2d.MASS_BALANCE_TOLERANCE


class _RunSurfaceEachSync:
    """The runner's surface call as it was before the stepper: one run_surface per sync."""

    def __init__(self, state, terrain, *, sea_mask=None, audit_every=None) -> None:
        self.state = state
        self.terrain = terrain
        # `coupling.advance_surface_with_capture` reads the stepper's grid and CFL scope for the
        # step at which it splits a sync; the runner hands this double the prepared kernel
        # terrain, and run_surface builds the same scope from the same sea mask on every call.
        self.kernel = terrain
        kernel = prepare_terrain(terrain)
        self.cfl_scope = swe2d.cfl_scope(swe2d._sea_index(sea_mask, kernel), kernel)
        self.sea_mask = sea_mask
        self.rain = None
        self.initial_m3 = state.volume_m3(terrain.cell_area_m2)
        self.totals = dict.fromkeys(LEDGER, 0.0)

    def set_rain(self, r_eff_ms) -> None:
        self.rain = r_eff_ms

    def advance(self, duration_s, *, q_inlet_ms, q_surcharge_ms, tide_stage_m, max_dt_s):
        run = swe2d.run_surface(
            self.state,
            self.terrain,
            duration_s,
            r_eff_ms=self.rain,
            q_inlet_ms=q_inlet_ms,
            q_surcharge_ms=q_surcharge_ms,
            sea_mask=self.sea_mask,
            tide_stage_m=tide_stage_m,
            max_dt_s=max_dt_s,
        )
        for name in LEDGER:
            self.totals[name] += getattr(run, name)
        return run

    def finish(self):
        """The run-long ledger, summed over the per-sync calls.

        The stepper keeps this itself; the pre-stepper implementation had to add it up, and it
        has to here too, because the runner's closing decomposition (task P4.5) reads it to say
        which side of the coupling a residual sits on. Returning ``None`` instead would make this
        double the only caller that cannot be audited - and it is precisely the one asserting the
        two implementations agree.
        """
        return swe2d.SurfaceRun(
            state=self.state,
            volume_initial_m3=self.initial_m3,
            volume_stored_m3=self.state.volume_m3(self.terrain.cell_area_m2),
            n_steps=0,
            dt_min_s=0.0,
            dt_max_s=0.0,
            elapsed_ms=0,
            notes=(),
            **self.totals,
        )


def _tidal_inputs() -> TwinInputs:
    terrain = _make_terrain(shape=(15, 15))
    network = _make_network(terrain, n_nodes=10, has_tidal_outfall=True)
    tide = TideSeries(
        times=(T0, T0 + timedelta(minutes=15), T0 + timedelta(minutes=30)),
        stage_m=np.array([0.5, 1.5, 2.0], dtype=np.float64),
        source="illustrative",
    )
    rain = _make_rain_cube(n_steps=6, shape=(15, 15), peak_mm_h=120.0)
    return TwinInputs(terrain=terrain, network=network, rain_mm_h=rain, t0=T0, tide=tide)


def test_run_twin_is_bitwise_identical_to_the_run_surface_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every output of a coupled run with rain, buildings, a sea boundary and a tidal outfall."""
    inputs = _tidal_inputs()
    stepped = run_twin(inputs)
    with monkeypatch.context() as patch:
        patch.setattr(runner.swe2d, "SurfaceStepper", _RunSurfaceEachSync)
        legacy = run_twin(inputs)

    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        assert np.array_equal(getattr(stepped, name), getattr(legacy, name)), name
    for name in ("volume_in_m3", "volume_out_m3", "volume_stored_m3", "error_fraction"):
        assert getattr(stepped.mass_balance, name) == getattr(legacy.mass_balance, name), name
    assert stepped.times == legacy.times
    assert float(np.max(stepped.depth_m)) > 0.0


def test_a_nan_in_the_inlet_raster_raises_on_the_call_that_carries_it() -> None:
    terrain, sea = _coastal_terrain()
    state = _wet_state(terrain)
    stepper = SurfaceStepper(state, terrain, sea_mask=sea)
    forcing = _forcing(3)
    stepper.set_rain(forcing[0]["rain"])
    inlet = np.array(forcing[0]["inlet"])
    stepper.advance(SYNC_S, q_inlet_ms=inlet, q_surcharge_ms=None, tide_stage_m=0.2, max_dt_s=5.0)
    taken = stepper.n_steps

    # A single transient NaN, one call only: the per-call reduction has to see it, because the
    # capture limiter would turn it into "drain this cell" and the depth would stay finite.
    inlet[3, 4] = np.nan
    with pytest.raises(ValueError, match="q_inlet_ms contains non-finite"):
        stepper.advance(
            SYNC_S, q_inlet_ms=inlet, q_surcharge_ms=None, tide_stage_m=0.2, max_dt_s=5.0
        )
    assert stepper.n_steps == taken, "raised before any sub-step ran on the bad raster"
    assert taken <= swe2d.MASS_BALANCE_EVERY

    surcharge = np.zeros(SHAPE)
    surcharge[0, 0] = np.inf
    with pytest.raises(ValueError, match="q_surcharge_ms contains non-finite"):
        stepper.advance(SYNC_S, q_surcharge_ms=surcharge, tide_stage_m=0.2, max_dt_s=5.0)


def test_a_corrupted_grid_raises_within_one_hundred_sub_steps() -> None:
    terrain, sea = _coastal_terrain()
    state = _wet_state(terrain)
    stepper = SurfaceStepper(state, terrain, sea_mask=sea)
    forcing = _forcing(400)
    stepper.set_rain(forcing[0]["rain"])
    inland = ~terrain.blocked & ~sea

    def advance(k: int) -> None:
        f = forcing[k]
        stepper.advance(
            SYNC_S,
            q_inlet_ms=f["inlet"],
            q_surcharge_ms=f["surcharge"],
            tide_stage_m=f["stage"],
            max_dt_s=SYNC_S,
        )

    advance(0)
    corrupted_at = stepper.n_steps
    state.h[inland] += 0.05  # water from nowhere: about 5 % of what the grid holds
    with pytest.raises(MassBalanceError):
        for k in range(1, len(forcing)):
            advance(k)
    assert stepper.n_steps - corrupted_at <= swe2d.MASS_BALANCE_EVERY + 2


def test_a_nan_depth_fails_the_audit_instead_of_comparing_false() -> None:
    terrain, sea = _coastal_terrain()
    state = _wet_state(terrain)
    stepper = SurfaceStepper(state, terrain, sea_mask=sea, audit_every=1)
    stepper.set_rain(np.zeros(SHAPE))
    state.h[5, 5] = np.nan
    with pytest.raises(MassBalanceError):
        stepper.advance(SYNC_S, tide_stage_m=0.2, max_dt_s=SYNC_S)

    # The per-call helper run_surface keeps uses the same NaN-safe comparison.
    totals = dict.fromkeys(("rain", "surcharge", "inlet", "tide_in", "tide_out", "created"), 0.0)
    nan_state = SurfaceState(h=np.full(SHAPE, np.nan), qx=np.zeros(SHAPE), qy=np.zeros(SHAPE))
    with pytest.raises(MassBalanceError):
        swe2d._audit(nan_state, prepare_terrain(terrain), 1e6, totals, 100)


def test_the_audit_counts_sub_steps_across_calls() -> None:
    """run_surface's counter restarts every call; the stepper's does not."""
    terrain, sea = _coastal_terrain()
    state = _wet_state(terrain)
    stepper = SurfaceStepper(state, terrain, sea_mask=sea)
    forcing = _forcing(260)
    stepper.set_rain(forcing[0]["rain"])
    for f in forcing:
        stepper.advance(
            SYNC_S,
            q_inlet_ms=f["inlet"],
            q_surcharge_ms=f["surcharge"],
            tide_stage_m=f["stage"],
            max_dt_s=SYNC_S,
        )
    assert stepper.n_steps >= 2 * swe2d.MASS_BALANCE_EVERY
    assert stepper.audits == stepper.n_steps // swe2d.MASS_BALANCE_EVERY
    stepper.finish()
    assert stepper.audits == stepper.n_steps // swe2d.MASS_BALANCE_EVERY + 1


def test_advance_refuses_to_run_without_rain_or_without_a_stage() -> None:
    terrain, sea = _coastal_terrain()
    stepper = SurfaceStepper(_wet_state(terrain), terrain, sea_mask=sea)
    with pytest.raises(ValueError, match="set_rain"):
        stepper.advance(SYNC_S, tide_stage_m=0.2)
    stepper.set_rain(0.0)
    with pytest.raises(ValueError, match="tide_stage_m"):
        stepper.advance(SYNC_S)
