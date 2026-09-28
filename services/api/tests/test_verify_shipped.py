"""A deployment serves the laptop's rain skill, and only for the laptop's runs (routers/verify.py).

The deployed API seeds `demo/runs`, which omit every run's rain cubes, so its scorer answers "not
scored". `uv run varuna verify` ships the full payload scored on the laptop with a record of the
runs it scored. These tests pin the rule: the copy is served when the live scorer has nothing to
score and this server holds exactly the recorded runs (by id and run.json) under the same scorer
version, and then it says so in `provenance.served_from`; any mismatch keeps the not-scored
answer, and a server that keeps rain products is never overridden.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from varuna_api.routers import verify
from varuna_verify import rain_event

EVENT = "MUM-2019-07-02"
RUNS = (
    ("MUM-20190702T0040Z-sky1.0-twin1.0-flash0.1-baked", "2019-07-02T06:10:00+05:30"),
    ("MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked", "2019-07-02T06:40:00+05:30"),
)


def _write_run(runs: Path, run_id: str, cycle_ts: str, **extra: Any) -> Path:
    run = runs / run_id
    run.mkdir(parents=True, exist_ok=True)
    body = {"run_id": run_id, "bundle": EVENT, "cycle_ts": cycle_ts, **extra}
    (run / "run.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return run


def _scored_payload() -> dict[str, Any]:
    """The parts of `event_rain_skill`'s answer these tests read, as the laptop scored them."""
    return {
        "event": EVENT,
        "available": True,
        "version": rain_event.RAIN_EVENT_VERSION,
        "label": "Reconstructed replay",
        "n_cycles": len(RUNS),
        "cycles": [{"run_id": run_id, "cycle_ts": ts} for run_id, ts in RUNS],
        "horizon": {"lead_min": 0, "status": "found", "threshold_mm_h": 20.0},
        "skipped_runs": [],
        "provenance": {"generator": "varuna_verify.rain_event"},
        "truth": {"t0": "2019-07-02T05:40:00+05:30", "t1": "2019-07-02T09:40:00+05:30"},
    }


@pytest.fixture
def deployed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """A deployment: the bundle has its truth, the runs are seeded without rain products."""
    bundles = tmp_path / "bundles"
    (bundles / EVENT / "truth" / "rain.zarr").mkdir(parents=True)
    runs = tmp_path / "data" / "runs"
    for run_id, ts in RUNS:
        _write_run(runs, run_id, ts)
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    shipped = tmp_path / "demo" / "verification"
    monkeypatch.setattr(verify, "shipped_dir", lambda: shipped)
    verify.clear_cache()
    yield {"runs": runs, "shipped": shipped}
    verify.clear_cache()


def _ship(paths: dict[str, Path]) -> Path:
    """Write the shipped copy exactly as `varuna verify` does, from the runs as they are now."""
    document = rain_event.shipped_rain_skill(_scored_payload(), runs_root=paths["runs"])
    return rain_event.write_shipped_rain_skill(document, paths["shipped"])


def _get(client: Any) -> dict[str, Any]:
    response = client.get("/v1/verification/rain-skill", params={"event": EVENT})
    assert response.status_code == 200
    return response.json()


def test_without_a_shipped_copy_the_deployment_is_not_scored(
    deployed: dict[str, Path], client: Any
) -> None:
    body = _get(client)
    assert body["available"] is False
    assert body["missing"] == "runs"
    assert "shipped_copy" not in body


def test_the_shipped_copy_is_served_when_the_runs_match(
    deployed: dict[str, Path], client: Any
) -> None:
    _ship(deployed)
    body = _get(client)
    assert body["available"] is True
    assert body["horizon"] == _scored_payload()["horizon"]
    assert "shipped" not in body, "the record is folded into provenance, not served beside it"

    served = body["provenance"]["served_from"]
    assert body["provenance"]["generator"] == "varuna_verify.rain_event"
    assert served["kind"] == "shipped_copy"
    assert served["file"] == f"demo/verification/{EVENT}.rain-skill.json"
    assert served["note"].startswith("Scored on the demo laptop from the baked runs' rain cubes")
    assert "same 2 runs" in served["note"]
    assert served["scorer_version"] == rain_event.RAIN_EVENT_VERSION
    assert [r["run_id"] for r in served["runs"]] == [run_id for run_id, _ in RUNS]
    assert [r["cycle_ts"] for r in served["runs"]] == [ts for _, ts in RUNS]


def test_a_gzipped_copy_is_served_the_same_way(
    deployed: dict[str, Path], client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rain_event, "SHIPPED_GZIP_OVER_BYTES", 64)
    written = _ship(deployed)
    assert written.name.endswith(".rain-skill.json.gz")
    body = _get(client)
    assert body["available"] is True
    assert body["provenance"]["served_from"]["file"].endswith(".rain-skill.json.gz")


def test_a_run_line_ending_change_still_matches(deployed: dict[str, Path], client: Any) -> None:
    """The digest is of the parsed run.json: a checkout's CRLF or re-indent is the same run."""
    _ship(deployed)
    meta = deployed["runs"] / RUNS[0][0] / "run.json"
    meta.write_bytes(json.dumps(json.loads(meta.read_bytes())).encode("utf-8") + b"\r\n")
    assert _get(client)["available"] is True


def test_refused_when_this_server_holds_another_run(deployed: dict[str, Path], client: Any) -> None:
    _ship(deployed)
    extra = "MUM-20190702T0140Z-sky1.0-twin1.0-flash0.1-baked"
    _write_run(deployed["runs"], extra, "2019-07-02T07:10:00+05:30")
    body = _get(client)
    assert body["available"] is False
    assert body["missing"] == "runs"
    refused = body["shipped_copy"]["refused"]
    assert (
        refused
        == f"It was scored on 2 runs and this server holds 3 (here but not scored: {extra})."
    )


def test_refused_when_a_scored_run_is_missing(deployed: dict[str, Path], client: Any) -> None:
    _ship(deployed)
    gone = deployed["runs"] / RUNS[1][0]
    (gone / "run.json").unlink()
    gone.rmdir()
    body = _get(client)
    assert body["available"] is False
    assert body["shipped_copy"]["refused"] == (
        f"It was scored on 2 runs and this server holds 1 (not here: {RUNS[1][0]})."
    )


def test_refused_when_a_run_was_rebaked_under_the_same_id(
    deployed: dict[str, Path], client: Any
) -> None:
    _ship(deployed)
    _write_run(deployed["runs"], *RUNS[0], created_at="2026-09-29T10:00:00+05:30")
    body = _get(client)
    assert body["available"] is False
    assert body["shipped_copy"]["refused"].startswith(
        "run.json differs from the one it scored under the same id for 1 run"
    )


def test_refused_when_the_scorer_version_moved(
    deployed: dict[str, Path], client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ship(deployed)
    monkeypatch.setattr(rain_event, "RAIN_EVENT_VERSION", "999")
    body = _get(client)
    assert body["available"] is False
    assert "version" in body["shipped_copy"]["refused"]


def test_a_server_that_keeps_rain_products_scores_them_itself(
    deployed: dict[str, Path], client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live path is unchanged: a shipped copy never replaces a score this server computed."""
    _ship(deployed)
    live = {"event": EVENT, "available": True, "horizon": {"lead_min": 45}, "provenance": {}}
    monkeypatch.setattr(rain_event, "event_rain_skill", lambda event: live)
    body = _get(client)
    assert body == live
    assert "served_from" not in body["provenance"]


def test_runs_that_fail_to_load_are_reported_not_covered(deployed: dict[str, Path]) -> None:
    """A fault in this server's own rain products is not hidden behind another machine's scores."""
    _ship(deployed)
    live = {"event": EVENT, "available": False, "missing": "loadable_runs", "reason": "broken"}
    assert verify.shipped_rain_skill(EVENT, live) is live


def test_an_unreadable_copy_keeps_the_not_scored_answer(
    deployed: dict[str, Path], client: Any
) -> None:
    deployed["shipped"].mkdir(parents=True)
    (deployed["shipped"] / f"{EVENT}.rain-skill.json").write_text("{not json", encoding="utf-8")
    body = _get(client)
    assert body["available"] is False
    assert "could not be read" in body["shipped_copy"]["refused"]
