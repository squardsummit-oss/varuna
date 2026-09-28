"""``run_twin(..., on_step=...)``: a progress hook that changes no number.

The what-if lab's tide scenario runs one full-city Twin (about a minute) and fills a bar per
output step (motion M31). The hook exists for that bar and must not be able to move a depth:
it is handed two integers and never the solver's state, and the run is compared bitwise with
and without it here - on a tidal network, because the tide boundary is the path the lab uses it
for.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from test_runner import T0, _make_network, _make_rain_cube, _make_terrain
from varuna_twin.runner import run_twin
from varuna_twin.types import TideSeries, TwinInputs


def _inputs() -> TwinInputs:
    terrain = _make_terrain(shape=(12, 12))
    network = _make_network(terrain, n_nodes=6, has_tidal_outfall=True)
    tide = TideSeries(
        times=(T0, T0 + timedelta(minutes=30)),
        stage_m=np.array([1.0, 3.5]),
        source="synthetic test tide",
    )
    return TwinInputs(
        terrain=terrain,
        network=network,
        rain_mm_h=_make_rain_cube(n_steps=4, shape=(12, 12), peak_mm_h=50.0),
        t0=T0,
        step_min=5,
        tide=tide,
    )


def test_the_hook_is_called_once_per_output_step_in_order() -> None:
    calls: list[tuple[int, int]] = []
    run_twin(_inputs(), on_step=lambda k, n: calls.append((k, n)))
    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_the_hook_changes_no_number() -> None:
    plain = run_twin(_inputs())
    hooked = run_twin(_inputs(), on_step=lambda k, n: None)
    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        assert np.array_equal(getattr(plain, name), getattr(hooked, name)), name
    assert plain.mass_balance == hooked.mass_balance


def test_an_exception_in_the_hook_stops_the_run() -> None:
    """How a caller cancels: the hook raises, and nothing after it runs."""

    class Stop(Exception):
        pass

    seen: list[int] = []

    def stop_at_two(k: int, n: int) -> None:
        seen.append(k)
        if k == 2:
            raise Stop

    with pytest.raises(Stop):
        run_twin(_inputs(), on_step=stop_at_two)
    assert seen == [1, 2]


def test_the_sea_ledger_is_filled_and_changes_no_number() -> None:
    """``sea_ledger``: the boundary volumes the lab's tide lever reports, and nothing else.

    The rain and sea terms it carries are the ones :class:`MassBalance` sums into
    ``volume_in_m3``, so they are checked against that sum rather than against a constant.
    """
    plain = run_twin(_inputs())
    ledger: dict[str, float] = {}
    filled = run_twin(_inputs(), sea_ledger=ledger)
    assert set(ledger) == {
        "rain_in_m3",
        "tide_in_m3",
        "tide_out_m3",
        "outfall_m3",
        # The city ledger (runner docstring): the sea is the city's boundary, so what it passed
        # to land cells and pipes is one term, formed from the clamp's tallies and its storage.
        "sea_to_land_m3",
        "sea_stored_start_m3",
        "sea_stored_end_m3",
    }
    assert np.array_equal(plain.depth_m, filled.depth_m)
    assert plain.mass_balance == filled.mass_balance
    sea_to_land = (
        ledger["tide_in_m3"]
        - ledger["tide_out_m3"]
        - (ledger["sea_stored_end_m3"] - ledger["sea_stored_start_m3"])
    )
    assert ledger["sea_to_land_m3"] == pytest.approx(sea_to_land, rel=1e-12)
    volume_in = (
        ledger["rain_in_m3"] + max(ledger["sea_to_land_m3"], 0.0) + max(-ledger["outfall_m3"], 0.0)
    )
    assert volume_in == pytest.approx(filled.mass_balance.volume_in_m3, rel=1e-12)
    assert ledger["rain_in_m3"] > 0.0
