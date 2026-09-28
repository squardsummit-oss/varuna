"""The Twin resumes from a state and returns its final one (task P4.2).

What is held here:

* **Resume identity.** Twelve steps in one run are array-equal to six steps plus a six-step
  resume from the first run's ``final_state`` - depth, fluxes, drain heads and flows, surcharge,
  and the hydrology accumulators - with a free outfall and with a tide rising over the outfall.
* **The cold path is unchanged.** A run with no ``initial_state`` matches, bit for bit, the
  output this module recorded from the runner before the change (on the machine that recorded
  it), and matches a run handed an explicit cold state everywhere.
* **A checkpoint from another city refuses to load**, naming what differs.
* **Mass balance keeps the run's own inflow as its denominator.** A resumed run with a large
  carried storage and a small injected error still breaches; the ``max(in, stored_start)``
  denominator the plan proposed would have hidden it, and the test shows that it would.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import timedelta
from typing import Any

import llvmlite.binding
import numpy as np
import pytest
from test_runner import T0, _make_network, _make_rain_cube, _make_terrain
from varuna_twin import drain1d, runner, swe2d
from varuna_twin.hydrology import DEPRESSION_STORAGE_MM
from varuna_twin.runner import MASS_BALANCE_MIN_RESIDUAL_M3, run_twin
from varuna_twin.types import (
    TideSeries,
    TwinFingerprint,
    TwinInputs,
    TwinResult,
    TwinState,
)

SHAPE = (15, 15)
PROFILE_12 = np.array([10.0, 25.0, 40.0, 60.0, 60.0, 45.0, 30.0, 50.0, 70.0, 40.0, 20.0, 10.0])


def _uniform_cube(profile: np.ndarray, shape: tuple[int, int] = SHAPE) -> np.ndarray:
    return np.broadcast_to(profile[:, None, None], (len(profile), *shape)).astype(np.float64)


def _rising_tide() -> TideSeries:
    """From below the outfall invert (0.62 m) to above its ground (2.12 m) within the hour."""
    return TideSeries(
        times=(T0, T0 + timedelta(minutes=60)),
        stage_m=np.array([0.3, 2.6], dtype=np.float64),
        source="illustrative",
    )


def _inputs(rain: np.ndarray, *, tidal: bool = False, **kwargs: Any) -> TwinInputs:
    terrain = kwargs.pop("terrain", None) or _make_terrain(shape=SHAPE)
    network = kwargs.pop("network", None) or _make_network(
        terrain, n_nodes=10, has_tidal_outfall=tidal
    )
    return TwinInputs(
        terrain=terrain,
        network=network,
        rain_mm_h=rain,
        t0=kwargs.pop("t0", T0),
        tide=_rising_tide() if tidal else None,
        **kwargs,
    )


def _assert_state_equal(a: TwinState, b: TwinState) -> None:
    for name in (
        "h",
        "qx",
        "qy",
        "drain_head",
        "drain_flow",
        "sink_filled_m3",
        "depression_remaining_mm",
        "cumulative_rain_mm",
    ):
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name), err_msg=name)
    assert a.valid_ts == b.valid_ts
    assert a.fingerprint == b.fingerprint


# ============================================================================ resume identity
@pytest.mark.parametrize("tidal", [False, True], ids=["free-outfall", "rising-tide"])
def test_six_plus_a_six_step_resume_is_twelve_steps(tidal: bool) -> None:
    rain = _uniform_cube(PROFILE_12)
    whole = run_twin(_inputs(rain, tidal=tidal))
    first = run_twin(_inputs(rain[:6], tidal=tidal))
    second = run_twin(
        _inputs(
            rain[6:],
            tidal=tidal,
            t0=T0 + timedelta(minutes=30),
            initial_state=first.final_state,
        )
    )

    # The storm must actually leave water behind, or identity is trivially true.
    assert float(first.final_state.h.max()) > 0.01
    assert float(np.abs(first.final_state.qx).max()) > 0.0
    assert float(np.abs(first.final_state.drain_flow).max()) > 0.0

    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        joined = np.concatenate([getattr(first, name), getattr(second, name)])
        np.testing.assert_array_equal(getattr(whole, name), joined, err_msg=name)
    assert whole.times == first.times + second.times
    _assert_state_equal(whole.final_state, second.final_state)

    # The two halves close the whole run's books: carried storage enters as start storage.
    assert second.mass_balance.volume_stored_start_m3 == pytest.approx(
        first.mass_balance.volume_stored_m3, rel=1e-12
    )
    assert second.mass_balance.ok, second.notes


def test_final_state_describes_the_last_output_and_is_read_only() -> None:
    result = run_twin(_inputs(_uniform_cube(PROFILE_12[:4])))
    state = result.final_state

    assert state.valid_ts == result.times[-1] == T0 + timedelta(minutes=20)
    np.testing.assert_array_equal(state.h, result.depth_m[-1])
    np.testing.assert_array_equal(state.drain_head, result.head_m[-1])
    np.testing.assert_array_equal(state.drain_flow, result.edge_flow[-1])
    assert state.sink_filled_m3.shape == (0,), "no pumps are wired; the slot exists and is empty"
    for name in ("h", "qx", "qy", "drain_head", "drain_flow", "cumulative_rain_mm"):
        array = getattr(state, name)
        assert array.dtype == np.float64
        assert not array.flags.writeable, f"final_state.{name} must be read-only"
    # Rain was remembered, so the SCS curve continues rather than restarting.
    assert float(state.cumulative_rain_mm.min()) > 0.0
    assert float(state.depression_remaining_mm.max()) < DEPRESSION_STORAGE_MM


def test_resuming_does_not_mutate_the_checkpoint() -> None:
    first = run_twin(_inputs(_uniform_cube(PROFILE_12[:6])))
    checkpoint = first.final_state
    # A caller-built state with writable arrays: the runner must copy, not adopt, them.
    writable = dataclasses.replace(
        checkpoint,
        h=np.array(checkpoint.h),
        qx=np.array(checkpoint.qx),
        qy=np.array(checkpoint.qy),
        drain_head=np.array(checkpoint.drain_head),
        drain_flow=np.array(checkpoint.drain_flow),
        cumulative_rain_mm=np.array(checkpoint.cumulative_rain_mm),
    )
    before = {name: np.array(getattr(writable, name)) for name in ("h", "qx", "drain_head")}

    run_twin(
        _inputs(
            _uniform_cube(PROFILE_12[6:]), t0=T0 + timedelta(minutes=30), initial_state=writable
        )
    )

    for name, array in before.items():
        np.testing.assert_array_equal(getattr(writable, name), array, err_msg=name)


# ============================================================================ cold path unchanged
def _cold_state(inputs: TwinInputs) -> TwinState:
    """The state the runner builds for itself when it is given none."""
    shape = inputs.terrain.shape
    return TwinState(
        valid_ts=inputs.t0,
        h=np.zeros(shape),
        qx=np.zeros(shape),
        qy=np.zeros(shape),
        drain_head=np.asarray(inputs.network.z_invert, dtype=np.float64).copy(),
        drain_flow=np.zeros(inputs.network.n_edges),
        sink_filled_m3=np.zeros(0),
        depression_remaining_mm=np.full(shape, DEPRESSION_STORAGE_MM),
        cumulative_rain_mm=np.zeros(shape),
        fingerprint=TwinFingerprint.of(inputs.terrain, inputs.network),
    )


@pytest.mark.parametrize("tidal", [False, True], ids=["free-outfall", "rising-tide"])
def test_no_initial_state_is_the_same_run_as_an_explicit_cold_state(tidal: bool) -> None:
    inputs = _inputs(_uniform_cube(PROFILE_12[:4]), tidal=tidal)
    default = run_twin(inputs)
    explicit = run_twin(dataclasses.replace(inputs, initial_state=_cold_state(inputs)))

    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        np.testing.assert_array_equal(getattr(default, name), getattr(explicit, name), name)
    assert default.mass_balance == explicit.mass_balance
    assert default.mass_balance.volume_stored_start_m3 == 0.0
    _assert_state_equal(default.final_state, explicit.final_state)


def _digest(array: np.ndarray) -> str:
    data = np.ascontiguousarray(array)
    return hashlib.sha256(
        str(data.dtype).encode() + str(data.shape).encode() + data.tobytes()
    ).hexdigest()


GOLDEN_CPU = "tigerlake"
"""The Numba host CPU the digests below were recorded on.

They were taken from ``run_twin`` at commit 1bc503a, before task P4.2 touched the runner, and
re-taken after it: every array, every time and every mass-balance float matched bit for bit.
Numba compiles the kernels for the host with ``fastmath``, so another CPU may round a reduction
differently without anything being wrong; there the test skips rather than fail, and the
explicit-cold-state test above still holds the cold path on every machine.

**Re-taken once more on 2026-09-24 (task P4.5/P4.6), and what moved is worth stating.** Two of
the four fixtures changed, in ``head_m`` and ``edge_flow`` only - ``depth_m`` and
``q_surcharge`` are bit-identical in all four, and so is ``error_fraction``. The size of it:
``plain``'s ``volume_out_m3`` went from 1.9655596751334399 to 1.9655596751334408 m3, **4 ULP**.

It is not the coupling fix. That was checked rather than assumed: neutralising the new
``blocked=`` argument reproduces the *new* numbers bit for bit, none of these fixtures has a
node on a building, and none of them surcharges at all, so the reorder hands the surface the
same zero it handed it before. What moved them is that `drain_kernel`'s first edge pass was
lifted into an `inline="always"` helper so the serial and parallel kernels cannot drift apart,
and `fastmath` reassociates the inlined form differently. `test_drain_kernel` still holds that
kernel to the NumPy specification at 1e-9, which bounds the difference from the other side."""

GOLDEN = {
    "plain": {
        "depth_m": "a60e139e62222f3c35f875617ac43cf3b5b69c576d228ea78008e5f59bb6311d",
        "head_m": "49734adf9d0d4dde00e916d6f2ea43960223dae08fd674063b7d40dd8ab8e145",
        "q_surcharge": "e9f54cab93d0e52ee636da663cadc49af0801c9e7fd652816084cd8eeaed51e2",
        "edge_flow": "2b28382e9d267b8c7f88b48604b60faa20bb80664ba724426fa0c8c8992168ac",
        "mb": (
            "0x1.c3df30ddabec2p+10",
            "0x1.f72eeb3b08d5dp+0",
            "0x1.c3616522dd296p+10",
            "0x1.465296db69174p-50",
        ),
    },
    "tidal": {
        "depth_m": "5cbecc309641f6f5182843adc28b7df3b8fa5f3e25b049fb68d9975d973ac068",
        "head_m": "6712cc784165f2033db145c90d18cd48cfd4c85953b554634406f011e2e601c1",
        "q_surcharge": "e9f54cab93d0e52ee636da663cadc49af0801c9e7fd652816084cd8eeaed51e2",
        "edge_flow": "7f2464421e7b862c7e8eb01be788d1a1a9fe4a28a3bd21a1f774463fd91849b8",
        "mb": (
            "0x1.c52cb843f1e0cp+10",
            "0x1.35862a5a74437p+3",
            "0x1.c2c1abef3cf7ap+10",
            "0x1.6989cd2fe267ep-50",
        ),
    },
    "dry": {
        "depth_m": "b8a5d8a86f6227ccffb68dfeea731b6332b4d2a455c9f2e38c8a8ed92640e07c",
        "head_m": "7724d17eaf6bd71c5cc020943e34ff17865d4ab189865cc56f18923099f7f6b3",
        "q_surcharge": "6666b08911a1f901e3f0bfc0fc547e450ba483eb1124d20ce1a3b16501efd5a2",
        "edge_flow": "24603eb2cff4ffd1fe0bf55742f73d8bdc78f38ec546de1a3836ed809ecfaee6",
        "mb": ("0x0.0p+0", "0x0.0p+0", "0x0.0p+0", "0x0.0p+0"),
    },
    "heavy6": {
        "depth_m": "9505585b2f4ec98ac7f173c554a84c56f02d103306e812f60b4a187ab5a3d14e",
        "head_m": "903bcd667d69bcf3f7f127a3a21f6292cf6e6d2d715e41d113643a61c2168a99",
        "q_surcharge": "b2248849e1d2a5cc5b5d90c7da1fbe93f33f4385e43d78d59520010b0c9d5e6e",
        "edge_flow": "9b008e1bb21cbfeaec75636561235dd730ced301cc1a95b42ad73af0f288a52a",
        "mb": (
            "0x1.15a382bdacc9ep+12",
            "0x1.2851e8cff8ea4p+4",
            "0x1.147b30d4dcce8p+12",
            "0x1.1faee7b4adbdap-47",
        ),
    },
}


def _golden_inputs(name: str) -> TwinInputs:
    """The ``test_runner`` fixtures the digests were recorded on, exactly as recorded."""
    terrain = _make_terrain(shape=SHAPE)
    if name == "plain":
        return TwinInputs(
            terrain=terrain,
            network=_make_network(terrain, n_nodes=10),
            rain_mm_h=_make_rain_cube(4, SHAPE, 50.0),
            t0=T0,
        )
    if name == "tidal":
        tide = TideSeries(
            times=(T0, T0 + timedelta(minutes=15), T0 + timedelta(minutes=30)),
            stage_m=np.array([0.5, 1.5, 2.0]),
            source="illustrative",
        )
        return TwinInputs(
            terrain=terrain,
            network=_make_network(terrain, n_nodes=10, has_tidal_outfall=True),
            rain_mm_h=_make_rain_cube(4, SHAPE, 50.0),
            t0=T0,
            tide=tide,
        )
    if name == "dry":
        small = _make_terrain(shape=(10, 10))
        return TwinInputs(
            terrain=small,
            network=_make_network(small, n_nodes=5),
            rain_mm_h=np.zeros((3, 10, 10)),
            t0=T0,
        )
    return TwinInputs(
        terrain=terrain,
        network=_make_network(terrain, n_nodes=10),
        rain_mm_h=_make_rain_cube(6, SHAPE, 120.0),
        t0=T0,
    )


@pytest.mark.skipif(
    llvmlite.binding.get_host_cpu_name() != GOLDEN_CPU,
    reason=f"golden digests were recorded on a {GOLDEN_CPU} host; Numba fastmath may round "
    "differently elsewhere, and the explicit-cold-state test still covers the cold path",
)
@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_cold_path_is_byte_identical_to_the_runner_before_hot_start(name: str) -> None:
    result: TwinResult = run_twin(_golden_inputs(name))
    expected = GOLDEN[name]
    for field in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        assert _digest(getattr(result, field)) == expected[field], field
    mb = result.mass_balance
    got = (
        mb.volume_in_m3.hex(),
        mb.volume_out_m3.hex(),
        mb.volume_stored_m3.hex(),
        mb.error_fraction.hex(),
    )
    assert got == expected["mb"]


# ============================================================================ fingerprint
@pytest.fixture(scope="module")
def checkpoint() -> TwinResult:
    return run_twin(
        _inputs(_uniform_cube(PROFILE_12[:6]), provenance={"bundle_manifest_sha256": "a1b2"})
    )


def _resume(state: TwinState, **kwargs: Any) -> TwinResult:
    kwargs.setdefault("t0", T0 + timedelta(minutes=30))
    kwargs.setdefault("provenance", {"bundle_manifest_sha256": "a1b2"})
    return run_twin(_inputs(_uniform_cube(PROFILE_12[6:8]), initial_state=state, **kwargs))


def test_a_matching_checkpoint_resumes(checkpoint: TwinResult) -> None:
    assert checkpoint.final_state.fingerprint.provenance == (("bundle_manifest_sha256", "a1b2"),)
    assert _resume(checkpoint.final_state).n_steps == 2


def test_moved_drain_inverts_refuse_to_resume(checkpoint: TwinResult) -> None:
    terrain = _make_terrain(shape=SHAPE)
    base = _make_network(terrain, n_nodes=10)
    regraded = dataclasses.replace(base, z_invert=base.z_invert - 0.01)
    with pytest.raises(ValueError, match="z_invert_sha256: the drain invert levels differ"):
        _resume(checkpoint.final_state, terrain=terrain, network=regraded)


def test_changed_building_cells_refuse_to_resume(checkpoint: TwinResult) -> None:
    terrain = _make_terrain(shape=SHAPE)
    blocked = terrain.blocked.copy()
    blocked[7, 7] = not blocked[7, 7]
    with pytest.raises(ValueError, match="blocked_sha256"):
        _resume(checkpoint.final_state, terrain=dataclasses.replace(terrain, blocked=blocked))


def test_a_different_grid_refuses_to_resume_and_names_every_difference(
    checkpoint: TwinResult,
) -> None:
    bigger = _make_terrain(shape=(16, 15))
    with pytest.raises(ValueError) as caught:
        _resume(checkpoint.final_state, terrain=bigger, network=_make_network(bigger, n_nodes=11))
    message = str(caught.value)
    for needle in ("shape: checkpoint (15, 15), this run (16, 15)", "n_nodes", "n_edges"):
        assert needle in message


def test_provenance_must_match_key_for_key(checkpoint: TwinResult) -> None:
    with pytest.raises(
        ValueError,
        match=r"provenance\['bundle_manifest_sha256'\]: checkpoint 'a1b2', this run 'ffff'",
    ):
        _resume(checkpoint.final_state, provenance={"bundle_manifest_sha256": "ffff"})
    with pytest.raises(ValueError, match=r"provenance\['twin_version'\]: checkpoint absent"):
        _resume(
            checkpoint.final_state,
            provenance={"bundle_manifest_sha256": "a1b2", "twin_version": "1.0"},
        )
    with pytest.raises(ValueError, match="this run absent"):
        _resume(checkpoint.final_state, provenance=None)


def test_a_checkpoint_from_another_instant_refuses_to_resume(checkpoint: TwinResult) -> None:
    with pytest.raises(ValueError, match="valid_ts: checkpoint 2019-07-02T07:10:00"):
        _resume(checkpoint.final_state, t0=T0 + timedelta(minutes=35))


def test_a_pump_volume_for_units_this_run_lacks_refuses_to_resume(checkpoint: TwinResult) -> None:
    stray = dataclasses.replace(checkpoint.final_state, sink_filled_m3=np.array([12.0]))
    with pytest.raises(ValueError, match="pump or tank"):
        _resume(stray)


# ============================================================================ mass balance
@pytest.fixture(scope="module")
def after_a_heavy_storm() -> TwinResult:
    """Six steps of a 120 mm/h storm: thousands of m3 left standing for a resume to carry."""
    return run_twin(_inputs(_make_rain_cube(6, SHAPE, 120.0)))


def _light_resume(state: TwinState, rain_mm_h: float) -> TwinResult:
    return run_twin(
        _inputs(
            _uniform_cube(np.full(6, rain_mm_h)),
            t0=T0 + timedelta(minutes=30),
            initial_state=state,
        )
    )


def test_resumed_run_with_carried_storage_closes_within_budget(
    after_a_heavy_storm: TwinResult,
) -> None:
    result = _light_resume(after_a_heavy_storm.final_state, 2.0)
    mb = result.mass_balance
    assert mb.volume_stored_start_m3 > 1000.0, "fixture: the resume must carry real storage"
    assert mb.volume_in_m3 > runner.MASS_BALANCE_MIN_VOLUME_M3
    assert mb.residual_limit_m3 is None
    assert mb.ok, result.notes
    assert mb.error_fraction < 1e-3
    # Without the start storage on the stored side, the same run would read as a large error.
    assert abs(mb.volume_stored_m3 - (mb.volume_in_m3 - mb.volume_out_m3)) > 1e-2 * mb.volume_in_m3


def test_a_small_error_still_breaches_under_a_large_carried_storage(
    after_a_heavy_storm: TwinResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject 0.5 % more rain onto the surface than the ledger counts: invented water.

    The surface stepper audits what it applied, so its own check passes; only the coupled audit
    can see the gap. Divided by the run's inflow it is 0.5 %, over budget. Divided by the
    carried storage - the ``max(in, stored_start)`` the plan first proposed - it would have
    been under budget, and this asserts that too, so the test cannot pass for the wrong reason.
    """
    original = swe2d.SurfaceStepper.set_rain

    def generous(self: swe2d.SurfaceStepper, r_eff_ms: np.ndarray) -> None:
        original(self, np.asarray(r_eff_ms) * 1.005)

    monkeypatch.setattr(swe2d.SurfaceStepper, "set_rain", generous)
    result = _light_resume(after_a_heavy_storm.final_state, 2.0)
    mb = result.mass_balance

    assert mb.residual_m3 > 0.0, "invented water is a positive residual"
    assert mb.error_fraction > 1e-3
    assert not mb.ok
    assert any(
        "exceeds the 0.1% budget" in note and "stored at start" in note for note in result.notes
    )
    rejected = abs(mb.residual_m3) / max(mb.volume_in_m3, mb.volume_stored_start_m3)
    assert rejected < 1e-3, "fixture: the carried storage must be large enough to mask the error"


def test_with_no_new_inflow_the_residual_is_held_to_an_absolute_limit(
    after_a_heavy_storm: TwinResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero rain on a wet city: nothing enters, water drains out, a ratio would divide by zero."""
    clean = _light_resume(after_a_heavy_storm.final_state, 0.0)
    mb = clean.mass_balance
    assert mb.volume_in_m3 <= runner.MASS_BALANCE_MIN_VOLUME_M3
    assert mb.volume_out_m3 > 1.0, "fixture: the drains must still be discharging"
    assert mb.residual_limit_m3 == MASS_BALANCE_MIN_RESIDUAL_M3
    assert mb.error_fraction == 0.0
    assert mb.ok, clean.notes

    # Now lose half of what leaves through the outfall from the ledger.
    original = drain1d.simulate

    def forgetful(*args: Any, **kwargs: Any) -> drain1d.DrainRunReport:
        report = original(*args, **kwargs)
        return dataclasses.replace(report, boundary_m3=report.boundary_m3 * 0.5)

    monkeypatch.setattr(runner.drain1d, "simulate", forgetful)
    leaky = _light_resume(after_a_heavy_storm.final_state, 0.0)
    assert not leaky.mass_balance.ok
    assert abs(leaky.mass_balance.residual_m3) > MASS_BALANCE_MIN_RESIDUAL_M3
    assert any("m3 limit that applies while inflow is below" in note for note in leaky.notes)
