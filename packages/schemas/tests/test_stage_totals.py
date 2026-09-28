"""Stage totals count each cycle stage once (P5.5).

``run.json`` ``stage_ms`` carries the Twin's wall clock as ``twin`` and, as provenance, the
time spent inside it (``twin_total_ms``, ``twin_surface_ms``, ``twin_drain_ms``,
``twin_coupling_ms``, ``twin_hydrology_ms``). Summing every key reported 189,704 ms for the
09:10 IST baked cycle whose stages took 76,851 ms. These tests pin the one rule that decides
what a total counts: :func:`varuna_schemas.models.top_level_stage_ms`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from varuna_schemas.constants import CYCLE_STAGES
from varuna_schemas.models import (
    CycleLogEntry,
    CycleStatus,
    RunMeta,
    stage_total_ms,
    top_level_stage_ms,
)
from varuna_schemas.samples import sample

REPO = Path(__file__).resolve().parents[3]
DEMO_RUNS = sorted((REPO / "demo" / "runs").glob("*/run.json"))

# `stage_ms` of demo/runs/MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked (09:10 IST) as
# committed on 2026-09-14, copied so the arithmetic below survives a re-bake of the demo runs.
BAKED_0910_STAGE_MS: dict[str, int] = {
    "sky": 5978,
    "twin": 58462,
    "twin_hydrology_ms": 612,
    "twin_surface_ms": 18502,
    "twin_drain_ms": 30215,
    "twin_coupling_ms": 5242,
    "twin_total_ms": 58282,
    "flash": 226,
    "products": 6619,
    "pulse": 5566,
}


def _meta_with(stage_ms: dict[str, int]) -> RunMeta:
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    return meta.model_copy(update={"stage_ms": stage_ms})


def test_total_ms_ignores_twin_substages() -> None:
    assert sum(BAKED_0910_STAGE_MS.values()) == 189_704  # what the deployed status reported
    assert stage_total_ms(BAKED_0910_STAGE_MS) == 5978 + 58462 + 226 + 6619 + 5566 == 76_851
    assert _meta_with(BAKED_0910_STAGE_MS).total_ms == 76_851
    assert list(top_level_stage_ms(BAKED_0910_STAGE_MS)) == [
        "sky",
        "twin",
        "flash",
        "pulse",
        "products",
    ]


def test_unknown_sub_keys_do_not_inflate_the_total() -> None:
    noisy = {
        **BAKED_0910_STAGE_MS,
        "twin_nest_ms": 40_000,
        "flash_members_ms": 900,
        "total": 80_000,
        "total_ms": 80_000,
    }
    assert stage_total_ms(noisy) == 76_851
    assert _meta_with(noisy).total_ms == 76_851


def test_every_declared_stage_counts_once() -> None:
    every = {stage: 10 for stage in CYCLE_STAGES}
    assert stage_total_ms(every) == 10 * len(CYCLE_STAGES)
    assert stage_total_ms({}) == 0


def test_cycle_status_elapsed_matches_run_total() -> None:
    meta = _meta_with(BAKED_0910_STAGE_MS)
    status = CycleStatus(run_id=meta.run_id, stage_ms=dict(meta.stage_ms))
    log_row = CycleLogEntry(
        cycle_ts=meta.cycle_ts,
        run_id=meta.run_id,
        mode=meta.mode,
        stage_ms=dict(meta.stage_ms),
        mass_balance_err=meta.mass_balance_err,
    )
    assert status.elapsed_ms == log_row.total_ms == meta.total_ms == 76_851
    dumped = status.model_dump(mode="json")
    assert dumped["elapsed_ms"] == 76_851
    assert dumped["stage_ms"] == BAKED_0910_STAGE_MS  # sub-timings kept as provenance
    # over_budget already filtered to budgeted stages; the sub-keys must stay out of it too.
    # Flash's 226 ms is inside its 300 ms budget; the other four are over theirs.
    assert status.over_budget == ["sky", "twin", "products", "pulse"]


def test_stale_serialised_total_is_recomputed_on_load() -> None:
    payload = _meta_with(BAKED_0910_STAGE_MS).model_dump(mode="json")
    payload["total_ms"] = 189_704  # what every committed run.json carries until a re-bake
    again = RunMeta.model_validate_json(json.dumps(payload))
    assert again.total_ms == 76_851
    assert again.model_dump(mode="json")["total_ms"] == 76_851


@pytest.mark.skipif(not DEMO_RUNS, reason="no demo/runs/*/run.json in this checkout")
@pytest.mark.parametrize("run_json", DEMO_RUNS, ids=lambda p: p.parent.name)
def test_committed_demo_runs_total_their_top_level_stages(run_json: Path) -> None:
    raw = json.loads(run_json.read_text(encoding="utf-8"))
    # Derived by a different rule than the one under test: a stage key has no underscore
    # and is not a total. Agreement on real files is the point.
    expected = sum(ms for key, ms in raw["stage_ms"].items() if "_" not in key and key != "total")
    meta = RunMeta.model_validate_json(run_json.read_text(encoding="utf-8"))
    assert meta.total_ms == expected
    assert meta.total_ms <= sum(raw["stage_ms"].values())
