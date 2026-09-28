"""What the city-in-a-box wizard reports, and when it is allowed to download (SPEC.md 7.9).

Two defects these cover, both found on the deployed site rather than locally:

1. **The progress bar lied.** `run_city` does not stop at a failed step - it records the failure
   and runs the remaining thirteen, each failing for want of an output the first never wrote - so
   a build that died on its *first* step reported the *last* one. The live wizard showed
   "Build graph" at 83 % when what was missing was the open data at step one.

2. **The cache the deployment deletes on purpose.** The pipeline never downloads; its first step
   verifies `city/cache/` and raises if a tile is absent. The Railway entrypoint deletes that
   cache after the first build (a 500 MB volume cannot hold both it and the city it makes), so
   the wizard demanded a cache the deployment had removed by design and failed every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from varuna_api.onboard import STEP_OF, OnboardState, _Tap, _warm_cache
from varuna_city.pipeline import STEPS

PIPELINE_STEPS: tuple[str, ...] = tuple(step.name for step in STEPS)
"""`varuna_city.pipeline.STEPS` by name, in order - the real list, never a copy of it."""


def a_job(*, from_cache_only: bool = True, city: str = "chennai") -> OnboardState:
    state = OnboardState(
        job_id="onboard-test",
        city=city,
        design_storm="CHN-IDF-25yr",
        from_cache_only=from_cache_only,
    )
    state.step = "fetch_open_data"
    return state


def drive(state: OnboardState, *, fail_at: str | None) -> None:
    """Replay what the pipeline logs for a build, optionally failing one step and carrying on."""
    tap = _Tap()
    tap.attach(state)
    try:
        for name in PIPELINE_STEPS:
            failed = fail_at is not None and PIPELINE_STEPS.index(name) >= PIPELINE_STEPS.index(
                fail_at
            )
            if failed:
                tap(None, "info", {"event": "city.step_failed", "step": name, "error": "boom"})
            tap(
                None,
                "info",
                {
                    "event": "city.step",
                    "step": name,
                    "status": "failed" if failed else "ok",
                    "ms": 1.0,
                },
            )
    finally:
        tap.detach()


class TestEveryPipelineStepHasARow:
    """The sea mask landed as a fifteenth step and its 13.0 s fell into no row (the rows added up
    to 147.5 s of a 160.5 s Chennai build), because these tests carried a copy of the step list."""

    def test_every_pipeline_step_is_mapped_in_the_pipelines_order(self) -> None:
        assert tuple(STEP_OF) == PIPELINE_STEPS

    def test_the_sea_mask_is_part_of_conditioning_the_terrain(self) -> None:
        assert STEP_OF["sea"] == "condition_terrain"

    def test_every_steps_milliseconds_land_in_some_row(self) -> None:
        state = a_job()
        drive(state, fail_at=None)
        rows = state.to_dict()["steps"]
        pipeline_ms = sum(rows[name]["ms"] for name in set(STEP_OF.values()))
        assert pipeline_ms == pytest.approx(1.0 * len(PIPELINE_STEPS))

    def test_an_unmapped_step_is_added_to_the_row_the_wizard_is_on(self) -> None:
        state = a_job()
        state.pipeline_steps = (*PIPELINE_STEPS[:6], "tomorrows_step", *PIPELINE_STEPS[6:])
        tap = _Tap()
        tap.attach(state)
        try:
            state.begin("fetch_open_data")
            for name in state.pipeline_steps:
                tap(None, "info", {"event": "city.step", "step": name, "status": "ok", "ms": 2.0})
        finally:
            tap.detach()
        rows = state.to_dict()["steps"]
        # Fetch finished at `assets` and handed over to "Condition terrain", which is where the
        # unmapped step's time goes; it neither completes nor reopens a row.
        condition = rows["condition_terrain"]
        assert condition["ms"] == pytest.approx(2.0 * (len(state.members("condition_terrain")) + 1))
        assert condition["status"] == "done"
        assert rows["fetch_open_data"]["ms"] == pytest.approx(
            2.0 * len(state.members("fetch_open_data"))
        )
        total = sum(rows[name]["ms"] for name in set(STEP_OF.values()))
        assert total == pytest.approx(2.0 * len(state.pipeline_steps))
        assert state.step == "build_graph"


class TestFailureIsReportedWhereItHappened:
    def test_a_failure_at_the_first_step_does_not_report_the_last(self) -> None:
        state = a_job()
        drive(state, fail_at="cache")
        assert state.failed_step == "cache"
        # The regression: 'build_graph' at 0.83, which is what the deployed wizard showed.
        assert state.step == "fetch_open_data"
        assert state.progress == 0.0

    def test_a_failure_midway_names_its_own_wizard_step(self) -> None:
        state = a_job()
        drive(state, fail_at="drains")
        assert state.failed_step == "drains"
        assert state.step == "infer_drains"
        # Ten steps passed before it, so the bar is part way rather than pinned at either end.
        assert 0.0 < state.progress < 0.83

    def test_a_clean_build_still_advances_to_the_end_of_the_build_share(self) -> None:
        state = a_job()
        drive(state, fail_at=None)
        assert state.failed_step is None
        assert state.step == "build_graph"
        # Five sixths; the design-storm cycle is the last sixth.
        assert state.progress == pytest.approx(0.83)


@dataclass(frozen=True)
class FakeCheck:
    """Stands in for `varuna_city.cache.TileCheck`; only `key` and `ok` are read."""

    key: str
    ok: bool


@pytest.fixture
def cache_rows(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Control what `_warm_cache` sees on disk without needing 139 MB of real tiles."""
    import varuna_city.cache as cache_module

    def set_rows(rows: list[FakeCheck]) -> None:
        monkeypatch.setattr(cache_module, "verify_cache", lambda *a, **k: rows)

    return set_rows


class TestWhenItMayDownload:
    def test_a_warm_cache_downloads_nothing(self, cache_rows: Any) -> None:
        """The stage path: every tile present, so the wizard runs with the venue network off."""
        cache_rows([FakeCheck("dem/a.tif", True), FakeCheck("worldcover/b.tif", True)])
        state = a_job(from_cache_only=True)
        _warm_cache(state)
        assert "already cached" in state.texts[-1]

    def test_a_cold_cache_is_refused_when_the_job_may_not_download(self, cache_rows: Any) -> None:
        cache_rows([FakeCheck("dem/a.tif", False), FakeCheck("worldcover/b.tif", True)])
        state = a_job(from_cache_only=True)
        with pytest.raises(RuntimeError) as caught:
            _warm_cache(state)
        message = str(caught.value)
        # The message has to carry the fix, not just the complaint (SPEC.md 6.8).
        assert "prefetch_city_cache.py" in message
        assert "dem/a.tif" in message

    def test_offline_mode_refuses_even_when_the_job_asked_to_download(
        self, cache_rows: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`VARUNA_OFFLINE=1` is the authority, whatever the request body says (SPEC.md 4.4)."""
        monkeypatch.setenv("VARUNA_OFFLINE", "1")
        cache_rows([FakeCheck("dem/a.tif", False)])
        state = a_job(from_cache_only=False)
        with pytest.raises(RuntimeError, match="not cached"):
            _warm_cache(state)

    def test_a_full_disk_is_refused_before_the_download_starts(
        self, cache_rows: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A volume that fills mid-download leaves a half-written city; say so first instead."""
        import shutil

        import varuna_city.cache as cache_module

        cache_rows([FakeCheck("worldcover/b.tif", False)])
        monkeypatch.setattr(cache_module, "cache_root", lambda: tmp_path)
        monkeypatch.setattr(
            shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(0, 0, 50 * 1024**2)
        )
        # Pin the branch under test. `_warm_cache` refuses before it ever looks at the disk when
        # VARUNA_OFFLINE=1 - correctly, since that switch overrides every download - and CI's test
        # process has it set. Unpinned, this met the offline refusal in CI and failed on a message
        # it never asked about, while passing on a laptop where the variable is unset.
        monkeypatch.delenv("VARUNA_OFFLINE", raising=False)
        state = a_job(from_cache_only=False)
        with pytest.raises(RuntimeError, match="Not enough room"):
            _warm_cache(state)
