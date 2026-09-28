"""One live Sky cycle at a time (the deployed API's 2026-09-19 outage).

The deployed API logged two 20-member pySTEPS cycles starting in the same second and never logged
again: the container was killed and stayed down for three days while the platform reported it
healthy. These tests pin the guard that refuses the second cycle, and pin that a cycle which fails
still releases it - a guard that stayed held after an error would turn one bad request into a
permanent 503.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from varuna_api import rain

BUNDLE = "MUM-2019-07-02"
WHEN = datetime(2019, 7, 2, 1, 10, tzinfo=UTC)


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        replay=SimpleNamespace(clock=None),
        settings=SimpleNamespace(varuna_bundle=BUNDLE),
    )


def test_a_second_live_cycle_is_refused_while_one_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_cycle(bundle: str, when: datetime | None) -> object:
        started.set()
        release.wait(10)
        raise ValueError("stopped by the test")

    monkeypatch.setattr(rain, "run_bundle_cycle", slow_cycle)
    first_error: list[BaseException] = []

    def run_first() -> None:
        try:
            rain._computed(_state(), BUNDLE, WHEN)
        except BaseException as exc:  # the first cycle is made to fail on purpose
            first_error.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(10), "the first cycle never started"
        with pytest.raises(HTTPException) as refused:
            rain._computed(_state(), BUNDLE, WHEN)
        assert refused.value.status_code == 503
        assert refused.value.detail["code"] == "cycle_busy"
        assert "already running" in refused.value.detail["message"]
    finally:
        release.set()
        thread.join(10)

    # The first cycle failed, and the guard must still be free for the next request.
    assert first_error, "the first cycle should have surfaced its error"
    assert rain._LIVE_CYCLE.acquire(blocking=False), "a failed cycle left the guard held"
    rain._LIVE_CYCLE.release()


def test_cycles_one_after_another_are_both_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime | None] = []

    def quick_cycle(bundle: str, when: datetime | None) -> object:
        calls.append(when)
        raise ValueError("no bundle in this test")

    monkeypatch.setattr(rain, "run_bundle_cycle", quick_cycle)
    for _ in range(2):
        with pytest.raises(HTTPException) as info:
            rain._computed(_state(), BUNDLE, WHEN)
        # Refused for being uncomputable, not for being busy.
        assert info.value.detail["code"] == "cycle_not_computable"
    assert len(calls) == 2
