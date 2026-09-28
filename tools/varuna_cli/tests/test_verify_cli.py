"""``varuna verify``: the scores ``/v1/verification`` serves, written as the landing page's copy.

The scoring itself is ``varuna_verify``'s and has its own tests. Here the event is a tiny fixture:
the pin scorer is replaced by hand-made :class:`EventScores` so the real ``sweep`` composes the
threshold rows the API serves, and the rain payload is the real ``score_cycles`` over a 4 x 4 Sky
grid, so the headline is cut from the shape the API returns and not from a guess at it. What is
pinned is the command's contract: what it writes, that it writes the same bytes twice, that
``--check`` fails on a stale copy, that it refuses to overwrite the landing copy with another
event or with nothing, and that every field the landing page and the chip read is still there.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import typer
from typer.testing import CliRunner
from varuna_cli import verify as verify_cli
from varuna_cli.main import build_app
from varuna_schemas.constants import IST
from varuna_sky.types import RadarGrid
from varuna_verify import event as event_mod
from varuna_verify import rain_event
from varuna_verify.event import EventScores
from varuna_verify.rain_event import CycleForecast, score_cycles
from varuna_verify.rain_skill import TruthCube

EVENT = "MUM-2019-07-02"
WINDOW = (datetime(2019, 7, 2, 6, 15, tzinfo=IST), datetime(2019, 7, 2, 9, 40, tzinfo=IST))
REPO_COPY = Path(__file__).resolve().parents[3] / verify_cli.COMMITTED_COPY

# ------------------------------------------------------------------ a tiny event: two pins


def _scores(threshold_cm: float, *, runs: bool = True) -> EventScores:
    """Two pins in the window: one hit 20 minutes early, one miss, one false alarm at 5 cm."""
    scores = EventScores(
        event=EVENT,
        run_ids=["MUM-20190702T0040Z-fixture-baked", "MUM-20190702T0110Z-fixture-baked"]
        if runs
        else [],
        window=WINDOW if runs else None,
        threshold_cm=threshold_cm,
        n_pins_total=2,
        n_pins_in_window=2 if runs else 0,
    )
    if not runs:
        scores.notes = ["No baked runs for this event. Run `make bake` and score again."]
        return scores
    scores.hits, scores.misses = 1, 1
    scores.false_alarms = 1 if threshold_cm < 10 else 0
    scores.lead_minutes = [20.0]
    scores.matched = [
        {
            "pin_id": "FIX-01",
            "name": "Hindmata junction, Dadar East",
            "kind": "log",
            "pin_ts": "2019-07-02T08:40:00+05:30",
            "forecast_ts": "2019-07-02T08:20:00+05:30",
            "lead_min": 20.0,
            "n_segments": 2,
            "source_url": "https://example.org/hindmata",
        }
    ]
    scores.missed = [
        {
            "pin_id": "FIX-02",
            "name": "Milan subway, Santacruz",
            "kind": "news",
            "pin_ts": "2019-07-02T09:10:00+05:30",
            "n_segments_near": 3,
            "deepest_nearby_cm": 8.2,
            "reason": "No street within 250 m was forecast over the threshold",
            "source_url": "https://example.org/milan",
        }
    ]
    scores.unavailable = {"depth_mae_cm": "No pin states a depth."}
    scores.notes = [f"Scored at {threshold_cm:.0f} cm."]
    return scores


# ------------------------------------------------------------------ and its rain, on a 4 x 4 grid

GRID = RadarGrid(
    crs="EPSG:32643", res_m=500.0, n_px=4, transform=(500.0, 0, 0.0, 0, -500.0, 2000.0)
)
CYCLE = datetime(2019, 7, 2, 6, 0, tzinfo=IST)


def _field(*cells: tuple[int, int], wet: float = 30.0) -> np.ndarray:
    out = np.full((4, 4), 1.0)
    for row, col in cells:
        out[row, col] = wet
    return out


def _rain_payload() -> dict[str, Any]:
    """``event_rain_skill``'s shape around a real ``score_cycles`` result."""
    truth = TruthCube(
        rain_mm_h=np.stack([_field(), _field((0, 0), (0, 1), (1, 0)), _field((0, 0))]).astype(
            np.float32
        ),
        times=tuple(CYCLE + timedelta(minutes=5 * k) for k in range(3)),
        grid=GRID,
    )
    mean = np.stack([_field((0, 0), (0, 1), (3, 3), wet=50.0), _field()])
    prob = (mean > 20).astype(np.float64)
    cycle = CycleForecast(
        run_id="MUM-20190702T0030Z-fixture-baked",
        cycle_ts=CYCLE,
        times=tuple(CYCLE + timedelta(minutes=5 * (k + 1)) for k in range(2)),
        grid=GRID,
        mean=mean,
        p50=mean.copy(),
        probs={10.0: prob, 20.0: prob, 40.0: (mean > 40).astype(np.float64)},
        persistence=_field((0, 0)),
        coverage=np.ones((4, 4), dtype=bool),
        aoi_bounds=(0.0, 1000.0, 1000.0, 2000.0),
        n_members=20,
        member_cube=True,
        products_source="rain/quantiles.zarr",
        persistence_facts={"matches_cycle": True},
    )
    return {
        "event": EVENT,
        "available": True,
        "version": rain_event.RAIN_EVENT_VERSION,
        "label": "Reconstructed replay",
        "truth": {
            "source": "truth/rain.zarr",
            "note": "The storm designer's field.",
            "t0": truth.times[0].isoformat(),
            "t1": truth.times[-1].isoformat(),
            "n_frames": 3,
        },
        "headline_threshold_mm_h": rain_event.HEADLINE_THRESHOLD_MM_H,
        "csi_floor": rain_event.CSI_FLOOR,
        **score_cycles([cycle], truth),
        "runs_without_member_cube": [],
        "skipped_runs": [],
        "unavailable": {},
        "notes": [],
    }


@pytest.fixture
def fixture_event(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point the command's two scorers at the tiny event; returns what each call was asked."""
    asked: dict[str, Any] = {"sweep": [], "rain": []}

    def fake_score_event(event: str, threshold_cm: float = 30.0) -> EventScores:
        asked["sweep"].append((event, threshold_cm))
        return _scores(threshold_cm)

    def fake_rain(event: str = EVENT, **_: Any) -> dict[str, Any]:
        asked["rain"].append(event)
        return _rain_payload()

    monkeypatch.setattr(event_mod, "score_event", fake_score_event)
    monkeypatch.setattr(rain_event, "event_rain_skill", fake_rain)
    return asked


def _app() -> typer.Typer:
    return build_app(typer.Typer())


def _run(*args: str) -> Any:
    return CliRunner().invoke(_app(), ["verify", *args])


# ------------------------------------------------------------------ the command


def test_it_writes_the_served_sweep_plus_the_rain_headline(
    fixture_event: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "verification.json"
    result = _run("--event", EVENT, "--out", str(out))
    assert result.exit_code == 0, result.output

    body = json.loads(out.read_bytes())
    served = event_mod.sweep(EVENT)
    assert {k: v for k, v in body.items() if k != "rain_skill"} == json.loads(json.dumps(served))
    assert [t for _, t in fixture_event["sweep"]] == [5.0, 15.0, 30.0] * 2
    assert fixture_event["rain"] == [EVENT]

    # Every field the landing page, the chip and the end-to-end test read (proof.tsx,
    # lib/api/verification.ts headlineFromBody, tests/e2e/landing.spec.ts).
    assert body["event"] == EVENT
    assert body["headline_threshold_cm"] == 15.0
    assert body["by_threshold"]["15"]["scores"]["csi"] == pytest.approx(0.5)
    assert set(body["scores"]) >= {"csi", "pod", "far", "median_lead_min"}
    assert body["scores"]["median_lead_min"] == 20.0
    assert body["ground_truth"]["n_in_window"] == 2

    assert "CSI 0.5, POD 0.5" in result.output
    assert "Rain skill over 1 cycles" in result.output


def test_the_rain_headline_is_cut_from_the_served_payload(
    fixture_event: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "verification.json"
    assert _run("--out", str(out)).exit_code == 0
    rain = json.loads(out.read_bytes())["rain_skill"]
    payload = _rain_payload()

    assert rain["available"] is True
    assert rain["horizon"] == payload["horizon"]
    assert rain["n_cycles"] == 1
    assert rain["run_ids"] == ["MUM-20190702T0030Z-fixture-baked"]
    assert set(rain["csi_by_lead"]) == {"20", "40"}
    # The hand count, as test_rain_event.py makes it: at +5 min the AOI mean has 2 hits and 1
    # miss (CSI 2/3); persistence 1 hit and 2 misses (CSI 1/3).
    first = rain["csi_by_lead"]["20"][0]
    assert first == {
        "lead_min": 5,
        "n_cycles": 1,
        "mean": pytest.approx(2 / 3, abs=1e-4),
        "p50": pytest.approx(2 / 3, abs=1e-4),
        "persistence": pytest.approx(1 / 3, abs=1e-4),
    }
    assert [row["lead_min"] for row in rain["csi_by_lead"]["40"]] == [5, 10]
    assert "by_scope" not in rain and "cycles" not in rain, "the full payload stays on the API"


def test_an_unscored_rain_event_keeps_its_reason() -> None:
    headline = verify_cli.rain_headline(
        {
            "event": EVENT,
            "available": False,
            "reason": "No run keeps rain products.",
            "missing": "runs",
            "command": "make bake BUNDLE=MUM-2019-07-02",
            "version": "2",
        }
    )
    assert headline == {
        "available": False,
        "reason": "No run keeps rain products.",
        "missing": "runs",
        "command": "make bake BUNDLE=MUM-2019-07-02",
        "version": "2",
    }


def test_two_runs_write_the_same_bytes_and_check_passes(
    fixture_event: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "verification.json"
    assert _run("--out", str(out)).exit_code == 0
    first = out.read_bytes()
    assert _run("--out", str(out)).exit_code == 0
    assert out.read_bytes() == first
    assert b"\r" not in first and first.endswith(b"}\n") and not first.endswith(b"\n\n")

    checked = _run("--out", str(out), "--check")
    assert checked.exit_code == 0, checked.output
    assert out.read_bytes() == first, "--check writes nothing"


def test_check_fails_on_a_stale_or_missing_copy(
    fixture_event: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "verification.json"
    missing = _run("--out", str(out), "--check")
    assert missing.exit_code == 1
    assert not out.exists()

    assert _run("--out", str(out)).exit_code == 0
    stale = json.loads(out.read_bytes())
    stale["run_ids"] = stale["run_ids"][:1]
    out.write_bytes(verify_cli.render(stale).encode("utf-8"))
    result = _run("--out", str(out), "--check")
    assert result.exit_code == 1
    assert "is stale" in result.output
    assert json.loads(out.read_bytes())["run_ids"] == stale["run_ids"], "--check never rewrites"


FIXTURE_RUN = "MUM-20190702T0030Z-fixture-baked"


@pytest.fixture
def fixture_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture rain payload's one run, on disk, so the shipped copy can record its run.json."""
    runs = tmp_path / "data" / "runs"
    (runs / FIXTURE_RUN).mkdir(parents=True)
    (runs / FIXTURE_RUN / "run.json").write_text(
        json.dumps({"bundle": EVENT, "cycle_ts": CYCLE.isoformat(), "run_id": FIXTURE_RUN}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path / "bundles"))
    return runs


def test_the_default_target_is_the_landing_copy(
    fixture_event: dict[str, Any],
    fixture_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    result = _run()
    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / verify_cli.COMMITTED_COPY).read_bytes())["event"] == EVENT
    assert (tmp_path / verify_cli.SHIPPED_RAIN_DIR / f"{EVENT}.rain-skill.json").is_file()


# ------------------------------------------------------------------ the shipped rain copy


def test_the_default_target_also_ships_the_full_rain_payload(
    fixture_event: dict[str, Any],
    fixture_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    assert _run().exit_code == 0
    shipped = tmp_path / verify_cli.SHIPPED_RAIN_DIR / f"{EVENT}.rain-skill.json"
    document = json.loads(shipped.read_bytes())

    # The payload the API serves, whole, not the landing headline.
    payload = json.loads(json.dumps(_rain_payload()))
    assert {k: v for k, v in document.items() if k != "shipped"} == payload
    record = document["shipped"]
    assert record["format"] == rain_event.SHIPPED_FORMAT
    assert record["scorer_version"] == rain_event.RAIN_EVENT_VERSION
    assert record["written_by"] == f"uv run varuna verify --event {EVENT}"
    assert record["runs"] == [
        {
            "run_id": FIXTURE_RUN,
            "cycle_ts": CYCLE.isoformat(),
            "run_json_sha256": rain_event.run_json_digest(fixture_runs / FIXTURE_RUN),
            "scored": True,
        }
    ]
    assert record["truth"]["path"] == f"bundles/{EVENT}/truth/rain.zarr"

    # Deterministic: a second write gives the same bytes, and --check passes over both files.
    first = shipped.read_bytes()
    assert b"\r" not in first and first.endswith(b"}\n")
    assert _run().exit_code == 0
    assert shipped.read_bytes() == first
    checked = _run("--check")
    assert checked.exit_code == 0, checked.output
    assert "shipped rain skill" in checked.output


def test_a_large_rain_payload_ships_gzipped_and_alone(
    fixture_event: dict[str, Any],
    fixture_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gzip

    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    folder = tmp_path / verify_cli.SHIPPED_RAIN_DIR
    assert _run().exit_code == 0
    assert (folder / f"{EVENT}.rain-skill.json").is_file()

    monkeypatch.setattr(rain_event, "SHIPPED_GZIP_OVER_BYTES", 100)
    assert _run().exit_code == 0
    packed = folder / f"{EVENT}.rain-skill.json.gz"
    assert packed.is_file()
    assert not (folder / f"{EVENT}.rain-skill.json").exists(), "one copy per event, never two"
    first = packed.read_bytes()
    assert _run().exit_code == 0
    assert packed.read_bytes() == first, "gzip carries no timestamp, so the bytes repeat"
    assert json.loads(gzip.decompress(first))["shipped"]["runs"][0]["run_id"] == FIXTURE_RUN
    assert _run("--check").exit_code == 0


def test_check_fails_on_a_stale_or_missing_rain_copy(
    fixture_event: dict[str, Any],
    fixture_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    assert _run().exit_code == 0
    shipped = tmp_path / verify_cli.SHIPPED_RAIN_DIR / f"{EVENT}.rain-skill.json"

    # A re-bake under the same id changes run.json, so the recorded digest goes stale.
    meta = fixture_runs / FIXTURE_RUN / "run.json"
    meta.write_text(meta.read_text(encoding="utf-8")[:-1] + ', "rebaked": true}', "utf-8")
    stale = _run("--check")
    assert stale.exit_code == 1
    assert "is stale" in stale.output and "rain" in stale.output

    shipped.unlink()
    missing = _run("--check")
    assert missing.exit_code == 1
    assert "does not exist" in missing.output


def test_out_writes_no_rain_copy_unless_asked(
    fixture_event: dict[str, Any], fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "elsewhere" / "verification.json"
    assert _run("--out", str(out)).exit_code == 0
    assert not list(tmp_path.rglob("*.rain-skill.json*"))

    folder = tmp_path / "rain"
    assert _run("--out", str(out), "--rain-dir", str(folder)).exit_code == 0
    assert (folder / f"{EVENT}.rain-skill.json").is_file()


def test_a_scored_run_without_run_json_is_refused_not_shipped(
    fixture_event: dict[str, Any],
    fixture_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    (fixture_runs / FIXTURE_RUN / "run.json").unlink()
    result = _run()
    assert result.exit_code == 1
    assert "cannot ship" in result.output
    assert not (tmp_path / verify_cli.COMMITTED_COPY).exists(), "nothing written by half"


def test_another_event_never_overwrites_the_landing_copy(
    fixture_event: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify_cli, "_repo_root", lambda: tmp_path)
    result = _run("--event", "CHN-IDF-25yr")
    assert result.exit_code == 2
    assert "--out" in result.output
    assert not (tmp_path / verify_cli.COMMITTED_COPY).exists()
    assert fixture_event["sweep"] == [], "refused before scoring anything"


def test_an_event_with_no_runs_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        event_mod, "score_event", lambda event, threshold_cm=30.0: _scores(threshold_cm, runs=False)
    )
    monkeypatch.setattr(rain_event, "event_rain_skill", lambda event, **_: {"available": False})
    out = tmp_path / "verification.json"
    result = _run("--out", str(out))
    assert result.exit_code == 1
    assert "make bake" in result.output
    assert not out.exists()


def test_an_event_with_no_ground_truth_names_the_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_pins(event: str, threshold_cm: float = 30.0) -> EventScores:
        raise FileNotFoundError(f"No ground truth for {event}. Run `make bundle BUNDLE={event}`.")

    monkeypatch.setattr(event_mod, "score_event", no_pins)
    result = _run("--event", "MUM-2020-08-05", "--out", str(tmp_path / "v.json"))
    assert result.exit_code == 2
    assert "make bundle" in result.output


# ------------------------------------------------------------------ the renderer


def test_render_is_prettier_layout() -> None:
    """The layout ``prettier --check`` accepts (checked against Prettier 3 on this structure)."""
    body = {
        "b": [1, 2],
        "a": {"x": "y"},
        "rows": [{"v": 2, "k": 1}, {"k": 3, "v": None}],
        "pairs": [[5, 60], [65, 120]],
        "one": [[1], [2]],
        "e": [],
        "o": {},
        "n": [1.2e-05, 12.0, -0.0, 1e20],
    }
    assert verify_cli.render(body) == (
        "{\n"
        '  "a": {\n'
        '    "x": "y"\n'
        "  },\n"
        '  "b": [1, 2],\n'
        '  "e": [],\n'
        '  "n": [1.2e-5, 12.0, -0.0, 1e20],\n'
        '  "o": {},\n'
        '  "one": [[1], [2]],\n'
        '  "pairs": [\n'
        "    [5, 60],\n"
        "    [65, 120]\n"
        "  ],\n"
        '  "rows": [\n'
        '    { "k": 1, "v": 2 },\n'
        '    { "k": 3, "v": null }\n'
        "  ]\n"
        "}\n"
    )


def test_render_round_trips_and_breaks_what_does_not_fit() -> None:
    body = {
        "notes": ["A sentence long enough that two of them cannot share a line of a hundred."] * 2,
        "leads": list(range(5, 185, 5)),
        "rows": [{"source_url": "https://example.org/" + "x" * 90, "pin_id": "FIX-01"}],
        "value": np.float64(0.4712),
    }
    text = verify_cli.render(body)
    assert json.loads(text) == json.loads(json.dumps(body, default=float))
    lines = text.splitlines()
    assert all(len(line) <= verify_cli.PRINT_WIDTH for line in lines if "https://" not in line)
    start = lines.index('  "leads": [')
    lead_lines = lines[start + 1 : lines.index("  ],", start)]
    assert len(lead_lines) == 2, "36 leads fill two lines rather than taking 36"
    assert lead_lines[0].startswith("    5, 10, 15,")
    assert '    "A sentence long' in text, "a list of long strings breaks one per line"
    assert '"value": 0.4712' in text


def test_render_refuses_what_json_cannot_hold() -> None:
    with pytest.raises(ValueError, match="null"):
        verify_cli.render({"csi": float("nan")})
    with pytest.raises(TypeError):
        verify_cli.render({1: "a"})


def test_the_committed_copy_is_this_commands_output() -> None:
    """``apps/command/public/verification.json`` is written by ``varuna verify`` and nothing else.

    Its bytes are what :func:`render` makes of its own content, so a hand edit, a paste from the
    API or a CRLF checkout fails here; and it carries every field its readers need.
    """
    if not REPO_COPY.is_file():
        pytest.skip(f"{REPO_COPY} is not in this checkout")
    text = REPO_COPY.read_bytes().decode("utf-8")
    body = json.loads(text)
    assert verify_cli.render(body) == text, (
        "re-run `uv run varuna verify --event MUM-2019-07-02` rather than editing the file"
    )
    assert body["event"] == verify_cli.DEFAULT_EVENT
    assert body["run_ids"], "the committed copy scores at least one run"
    headline = body["by_threshold"][f"{int(body['headline_threshold_cm'])}"]["scores"]
    assert headline["csi"] == body["scores"]["csi"]
    assert "n_in_window" in body["ground_truth"]
    assert "available" in body["rain_skill"]
