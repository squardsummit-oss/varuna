"""Does an observation carry what it did to the posterior? (SPEC.md 11.6; task P7.4)

11.6 asks for "observations.parquet with the beta change each caused" and 7.3's assimilation
timeline prints that change on every card. The product carried no blockage field at all, so the
card's ``betaBefore``/``betaAfter`` were never passed and every row read "No blockage change
recorded for this observation yet" under a panel that promised the blockage it moved.

What is pinned here is the payload rather than the filter: the three fields reach the file, the
disagreement list sits beside the observations, and it is ordered by residual so the worst miss
is the first row a reader sees.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from varuna_pulse.cycle import PulseResult, _place_of, write_observations

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path


def _result() -> PulseResult:
    """Two observations on two pipes, one of which the filter moved."""
    import numpy as np

    observations = [
        {
            "kind": "traffic",
            "segment_id": "S0-342",
            "edge_id": "MUM-E003642",
            "ts": "2019-07-02T08:40:00+05:30",
            "depth_cm": 20.0,
            "depth_sd_cm": 8.0,
            "beta_before": 0.15,
            "beta_after": 0.2264,
            "innovation_cm": 19.84,
            "modelled_depth_cm": 0.2,
        },
        {
            "kind": "report",
            "report_id": "R-7",
            "place": "Hindmata junction",
            "edge_id": "MUM-E001100",
            "ts": "2019-07-02T08:35:00+05:30",
            "depth_cm": 45.0,
            "depth_sd_cm": 12.0,
            "beta_before": 0.20,
            "beta_after": 0.20,
            "innovation_cm": -60.5,
            "modelled_depth_cm": 105.5,
        },
    ]
    disagreements = [
        {
            "kind": "report",
            "place": "Hindmata junction",
            "edge_id": "MUM-E001100",
            "ts": "2019-07-02T08:35:00+05:30",
            "observed_depth_cm": 45.0,
            "modelled_depth_cm": 105.5,
            "residual_cm": -60.5,
        },
        {
            "kind": "traffic",
            "place": "S0-342",
            "edge_id": "MUM-E003642",
            "ts": "2019-07-02T08:40:00+05:30",
            "observed_depth_cm": 20.0,
            "modelled_depth_cm": 0.2,
            "residual_cm": 19.84,
        },
    ]
    return PulseResult(
        beta_mean=np.array([0.2264, 0.20]),
        beta_sd=np.array([0.1, 0.15]),
        health={},
        n_traffic=1,
        n_reports=1,
        n_assimilated=2,
        n_edges_updated=1,
        observations=observations,
        disagreements=disagreements,
        notes=("Largest model-observation disagreement: 61 cm at Hindmata junction.",),
    )


def test_the_written_product_carries_the_blockage_each_observation_moved(tmp_path: Path) -> None:
    write_observations(tmp_path, _result(), "MUM-20190702T0310Z-test")
    payload = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))

    for record in payload["observations"]:
        assert {"beta_before", "beta_after", "innovation_cm"} <= set(record)
    # A pipe no observation reached keeps its prior exactly, and the pair says so rather than
    # being dropped: the card prints "0.20 -> 0.20", which is a real answer.
    assert payload["observations"][1]["beta_before"] == payload["observations"][1]["beta_after"]


def test_the_disagreement_list_rides_beside_the_observations_worst_first(tmp_path: Path) -> None:
    write_observations(tmp_path, _result(), "MUM-20190702T0310Z-test")
    payload = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))

    rows = payload["disagreements"]
    assert [abs(row["residual_cm"]) for row in rows] == sorted(
        (abs(row["residual_cm"]) for row in rows), reverse=True
    )
    first = rows[0]
    assert first["observed_depth_cm"] - first["modelled_depth_cm"] == first["residual_cm"]


def test_a_traffic_anomaly_falls_back_from_street_to_segment_id() -> None:
    report = {"place": "Hindmata junction", "segment_id": "S0-342", "edge_id": "MUM-E003642"}
    traffic = {"segment_id": "S0-342", "edge_id": "MUM-E003642"}

    assert _place_of(report, "Dr Ambedkar Road") == "Hindmata junction"
    assert _place_of(traffic, "Dr Ambedkar Road") == "Dr Ambedkar Road"
    assert _place_of(traffic, None) == "S0-342"
    assert _place_of({"edge_id": "MUM-E003642"}, None) == "MUM-E003642"
