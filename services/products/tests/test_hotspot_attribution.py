"""What the rail is allowed to say about responsible pipes (SPEC.md 7.2, 11.7; task P7.7).

The measurement itself lives in `services/flash/tests/test_attribution.py`, on a network small
enough to reason about. These tests are about the *contract* `rank_hotspots` writes into
`hotspots.json`, which is the thing three screens read:

* every entry carries a ranking **or** a reason, never neither and never both, so the console
  can always tell "no pipe is responsible" from "nobody asked";
* attribution is budgeted, and both gates say which one bit;
* and the drain graph it reads has to be this city's, not whatever graph happens to sit under
  the same name in the repository's ``city/`` root.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from varuna_products.hotspots import (
    ATTRIBUTION_MAX_HOTSPOTS,
    ATTRIBUTION_MIN_PEAK_CM,
    rank_hotspots,
)
from varuna_schemas.constants import IST

CRS = "EPSG:32643"
RES = 30.0
LEFT = 300_000.0
TOP = 2_110_000.0
TRANSFORM = (RES, 0.0, LEFT, 0.0, -RES, TOP)


def _lonlat(row: int, col: int) -> tuple[float, float]:
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return to_wgs.transform(LEFT + (col + 0.5) * RES, TOP - (row + 0.5) * RES)


def _register(tmp_path: Path, points: list[tuple[str, int, int]]) -> Path:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": list(_lonlat(row, col))},
            "properties": {"hotspot_id": name, "name": name, "slug": name, "sourced": False},
        }
        for name, row, col in points
    ]
    (tmp_path / "hotspots.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return tmp_path


def _times(n: int) -> tuple[datetime, ...]:
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    return tuple(t0 + timedelta(minutes=5 * k) for k in range(n))


def _depth_with_peaks(peaks_cm: dict[tuple[int, int], float], steps: int = 6) -> np.ndarray:
    """A depth cube whose only wet cells are the register points, at the depths asked for.

    The window a hotspot is read over is 45 m - two cells either way - and the statistic is the
    90th percentile of it, so a single wet cell in a 5x5 window would be averaged away. Every
    cell of each window is filled instead, which makes the peak the number written here.
    """
    depth = np.zeros((steps, 40, 40))
    for (row, col), cm in peaks_cm.items():
        depth[steps - 1, row - 2 : row + 3, col - 2 : col + 3] = cm / 100.0
    return depth


def test_attribution_off_is_recorded_rather_than_left_silent(tmp_path: Path) -> None:
    city = _register(tmp_path, [("Deep", 10, 10)])
    ranked = rank_hotspots(
        _depth_with_peaks({(10, 10): 40.0}),
        _times(6),
        city,
        TRANSFORM,
        CRS,
        "TEST-RUN",
        attribution=False,
    )
    assert [h["attribution"] for h in ranked] == [[]]
    assert ranked[0]["attribution_label"] == "Attribution was not run for this product."
    assert ranked[0]["attribution_status"] == "off"


def test_a_city_root_outside_the_repo_graph_is_named_not_attributed_against(
    tmp_path: Path,
) -> None:
    """`load_network` resolves a city *name* under the repository's own ``city/`` root.

    A products run pointed anywhere else - a fixture, an unpacked offline package - would
    otherwise attribute against whatever graph sits under that name, whose node indices mean
    nothing here. The label has to name both paths so the mismatch is fixable.
    """
    city = _register(tmp_path, [("Deep", 10, 10)])
    ranked = rank_hotspots(
        _depth_with_peaks({(10, 10): 40.0}), _times(6), city, TRANSFORM, CRS, "TEST-RUN"
    )
    assert ranked[0]["attribution"] == []
    label = ranked[0]["attribution_label"]
    assert label is not None
    assert "Attribution reads the drain graph from" in label
    assert str(city) in label


def test_the_two_budgets_each_say_which_one_bit(tmp_path: Path, monkeypatch) -> None:
    """One junction under the depth gate, and more than the count gate allows.

    Both are real limits (a `drain1d` run per candidate pipe costs 24-30 s across Mumbai's
    28-point register), so both have to be legible on the product rather than showing up as an
    empty list that reads like a hydraulic answer.
    """
    n = ATTRIBUTION_MAX_HOTSPOTS + 2
    # Descending depths, all above the gate, plus one that is under it.
    # Two columns of the 40 x 40 fixture grid, so thirteen 5x5 windows fit without touching.
    points = [(f"Wet{k}", 3 + 3 * k, 10) for k in range(n)]
    points.append(("Dry", 3, 30))
    peaks = {(3 + 3 * k, 10): 60.0 - k for k in range(n)}
    peaks[(3, 30)] = ATTRIBUTION_MIN_PEAK_CM - 1.0
    city = _register(tmp_path, points)

    asked: list[str] = []

    def _fake_attribute(network: Any, surface: Any, **kwargs: Any) -> Any:
        from varuna_flash.whatif import PipeAttributionResult

        asked.append(kwargs["target_label"])
        return PipeAttributionResult(
            target=kwargs["target_label"],
            depth_before_cm=kwargs["depth_before_cm"],
            rows=(),
            combined=None,
            reason="stub: measured elsewhere",
            n_candidates=0,
            n_nodes=0,
            method="stub",
            ms=0.0,
        )

    _install_fake_graph(monkeypatch, tmp_path, _fake_attribute)

    ranked = rank_hotspots(
        _depth_with_peaks(peaks, steps=6), _times(6), city, TRANSFORM, CRS, "TEST-RUN"
    )
    # The gates are applied to the *ranking*, so the deepest junctions are the ones attributed.
    assert asked == [h["name"] for h in ranked[:ATTRIBUTION_MAX_HOTSPOTS]]
    assert len(asked) == ATTRIBUTION_MAX_HOTSPOTS

    over_budget = ranked[ATTRIBUTION_MAX_HOTSPOTS]
    assert "only the worst" in (over_budget["attribution_label"] or "")
    dry = next(h for h in ranked if h["name"] == "Dry")
    assert "under the 5 cm the map draws as wet" in (dry["attribution_label"] or "")

    # The status says which empty list is which without parsing the label: the attributed ten
    # came back empty and are refusals; the two nobody looked at are not.
    assert {h["attribution_status"] for h in ranked[:ATTRIBUTION_MAX_HOTSPOTS]} == {"refused"}
    assert over_budget["attribution_status"] == "not_attempted"
    assert dry["attribution_status"] == "not_attempted"

    # The contract every consumer relies on: a ranking or a reason, never neither.
    for entry in ranked:
        assert bool(entry["attribution"]) != bool(entry["attribution_label"])


def _install_fake_graph(monkeypatch, root: Path, fake_attribute) -> None:
    """Point the attribution helper at a one-node graph rooted at ``root``.

    The helper deliberately refuses a city root that is not the repository's own, so a test that
    wants to exercise the budgets has to move the root rather than skip the check - moving it is
    the same thing the real cycle does.
    """
    import varuna_flash.whatif as whatif
    import varuna_schemas.paths as paths
    import varuna_twin.city as twin_city
    from varuna_twin.drain1d import BOUNDARY_FREE
    from varuna_twin.types import DrainNetwork

    monkeypatch.setattr(paths, "city_dir", lambda city: root)
    monkeypatch.setattr(whatif, "attribute_pipes", fake_attribute)

    network = DrainNetwork(
        node_ids=("N0",),
        z_ground=np.array([5.0]),
        z_invert=np.array([3.5]),
        storage_area=np.array([1.5]),
        inlet_length=np.array([0.6]),
        inlet_area=np.array([0.18]),
        kappa=np.array([0.25]),
        boundary=np.array([BOUNDARY_FREE], dtype=np.int8),
        flap_gate=np.array([False]),
        cell_row=np.array([0], dtype=np.int32),
        cell_col=np.array([0], dtype=np.int32),
        edge_ids=(),
        from_node=np.zeros(0, dtype=np.int32),
        to_node=np.zeros(0, dtype=np.int32),
        length=np.zeros(0),
        area=np.zeros(0),
        hydraulic_radius=np.zeros(0),
        diameter=np.zeros(0),
        edge_manning_n=np.zeros(0),
        q_full=np.zeros(0),
        beta=np.zeros(0),
    )
    monkeypatch.setattr(twin_city, "load_network", lambda city="mumbai": network)


def test_a_missing_drain_graph_is_named_rather_than_swallowed(tmp_path: Path, monkeypatch) -> None:
    import varuna_schemas.paths as paths
    import varuna_twin.city as twin_city

    city = _register(tmp_path, [("Deep", 10, 10)])
    monkeypatch.setattr(paths, "city_dir", lambda name: city)

    def _no_graph(city: str = "mumbai"):
        raise FileNotFoundError("No drain graph at city/nowhere/graph/nodes.parquet")

    monkeypatch.setattr(twin_city, "load_network", _no_graph)
    ranked = rank_hotspots(
        _depth_with_peaks({(10, 10): 40.0}), _times(6), city, TRANSFORM, CRS, "TEST-RUN"
    )
    label = ranked[0]["attribution_label"]
    assert label is not None
    assert "No inferred drain graph" in label
    assert "nodes.parquet" in label


@pytest.mark.parametrize("attribution", [True, False])
def test_every_entry_ends_with_exactly_one_of_a_ranking_and_a_reason(
    tmp_path: Path, attribution: bool
) -> None:
    city = _register(tmp_path, [("A", 8, 8), ("B", 20, 20)])
    ranked = rank_hotspots(
        _depth_with_peaks({(8, 8): 40.0, (20, 20): 12.0}),
        _times(6),
        city,
        TRANSFORM,
        CRS,
        "TEST-RUN",
        attribution=attribution,
    )
    assert len(ranked) == 2
    for entry in ranked:
        assert "_window" not in entry
        assert "_register_index" not in entry
        assert bool(entry["attribution"]) != bool(entry["attribution_label"])


def test_a_junction_takes_its_band_from_the_streets_around_it() -> None:
    """ADR-0076: the Twin's level at the junction, with its segments' member spread either side."""
    import pandas as pd
    from varuna_products.hotspots import attach_ensemble_band

    frame = pd.DataFrame(
        {
            "segment_id": ["A", "A", "B", "B", "C", "C"],
            "valid_ts": [0, 1, 0, 1, 0, 1],
            "depth_p10_cm": [8.0, 18.0, 4.0, 14.0, 0.0, 0.0],
            "depth_p50_cm": [10.0, 20.0, 10.0, 20.0, 0.0, 0.0],
            "depth_p90_cm": [14.0, 26.0, 12.0, 22.0, 0.0, 0.0],
        }
    )
    ranked = [
        {"depth_cm": [30.0, 40.0], "segment_ids": ["A", "B"]},
        {"depth_cm": [5.0, 5.0], "segment_ids": ["Z"]},
    ]
    attach_ensemble_band(ranked, frame)

    # Below: mean of (-2, -6) = -4 at both steps. Above: mean of (+4, +2) = +3 and (+6, +2) = +4.
    assert ranked[0]["depth_p10_cm"] == [26.0, 36.0]
    assert ranked[0]["depth_p90_cm"] == [33.0, 44.0]
    assert ranked[0]["band_segments"] == 2
    # No street of its own in the forecast: no band, and the count says so rather than a zero-width
    # band being invented.
    assert ranked[1]["band_segments"] == 0
    assert "depth_p10_cm" not in ranked[1]
