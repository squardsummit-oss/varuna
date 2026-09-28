"""The shipped rain-skill copy (rain_event.shipped_rain_skill and its writer).

The scores are the scorer's own and are tested in test_rain_event.py. What is pinned here is the
copy: it records exactly the runs it scored, it is byte-identical on every write (rule 8), it is
gzipped past the size cap with no timestamp, one copy per event is left behind, and an unscored
event is never shipped.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from varuna_verify import rain_event

EVENT = "MUM-2019-07-02"
RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
CYCLE_TS = "2019-07-02T08:40:00+05:30"


def _payload() -> dict[str, Any]:
    return {
        "event": EVENT,
        "available": True,
        "version": rain_event.RAIN_EVENT_VERSION,
        "cycles": [{"run_id": RUN, "cycle_ts": CYCLE_TS}],
        "skipped_runs": [],
        "horizon": {"lead_min": 0, "first_failure": {"csi": np.float64(0.556)}},
        "n_cycles": np.int64(1),
        "truth": {"t0": "2019-07-02T05:40:00+05:30", "t1": "2019-07-02T09:40:00+05:30"},
    }


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    (runs / RUN).mkdir(parents=True)
    (runs / RUN / "run.json").write_text(
        json.dumps({"run_id": RUN, "bundle": EVENT, "cycle_ts": CYCLE_TS}), encoding="utf-8"
    )
    truth = tmp_path / "bundles" / EVENT / "truth" / "rain.zarr"
    (truth / "rain").mkdir(parents=True)
    (truth / "zarr.json").write_text("{}", encoding="utf-8")
    (truth / "rain" / "c0").write_bytes(b"\x00\x01")
    return runs, tmp_path / "bundles"


def test_the_record_names_the_runs_the_scorer_and_the_truth(roots: tuple[Path, Path]) -> None:
    runs, bundles = roots
    document = rain_event.shipped_rain_skill(_payload(), runs_root=runs, bundles_root=bundles)
    record = document["shipped"]
    assert record["runs"] == [
        {
            "run_id": RUN,
            "cycle_ts": CYCLE_TS,
            "run_json_sha256": rain_event.run_json_digest(runs / RUN),
            "scored": True,
        }
    ]
    assert record["scorer"] == "varuna_verify.rain_event"
    assert record["scorer_version"] == rain_event.RAIN_EVENT_VERSION
    assert record["truth"]["path"] == f"bundles/{EVENT}/truth/rain.zarr"
    assert len(record["truth"]["sha256"]) == 64
    assert {k: v for k, v in document.items() if k != "shipped"} == _payload()


def test_the_run_digest_ignores_layout_and_sees_values(tmp_path: Path) -> None:
    run = tmp_path / RUN
    run.mkdir()
    (run / "run.json").write_text('{"b": 1, "a": 2}', encoding="utf-8")
    first = rain_event.run_json_digest(run)
    (run / "run.json").write_bytes(b'{\r\n  "a": 2,\r\n  "b": 1\r\n}\r\n')
    assert rain_event.run_json_digest(run) == first
    (run / "run.json").write_text('{"a": 2, "b": 1, "created_at": "later"}', encoding="utf-8")
    assert rain_event.run_json_digest(run) != first


def test_writes_are_byte_identical_and_numpy_scalars_become_json(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    runs, bundles = roots
    document = rain_event.shipped_rain_skill(_payload(), runs_root=runs, bundles_root=bundles)
    folder = tmp_path / "shipped"
    path = rain_event.write_shipped_rain_skill(document, folder)
    first = path.read_bytes()
    assert rain_event.write_shipped_rain_skill(document, folder).read_bytes() == first
    assert b"\r" not in first and first.endswith(b"}\n")
    parsed = json.loads(first)
    assert parsed["n_cycles"] == 1 and parsed["horizon"]["first_failure"]["csi"] == 0.556
    assert list(parsed) == sorted(parsed), "keys are sorted"
    assert rain_event.read_shipped_text(path) == rain_event.shipped_text(document)


def test_past_the_cap_it_is_gzipped_alone_with_no_timestamp(
    roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, bundles = roots
    document = rain_event.shipped_rain_skill(_payload(), runs_root=runs, bundles_root=bundles)
    folder = tmp_path / "shipped"
    plain = rain_event.write_shipped_rain_skill(document, folder)
    assert plain.name == f"{EVENT}.rain-skill.json"

    monkeypatch.setattr(rain_event, "SHIPPED_GZIP_OVER_BYTES", 32)
    packed = rain_event.write_shipped_rain_skill(document, folder)
    assert packed.name == f"{EVENT}.rain-skill.json.gz"
    assert not plain.exists()
    data = packed.read_bytes()
    assert data[4:8] == b"\x00\x00\x00\x00", "gzip mtime is zero, so the bytes repeat"
    assert rain_event.write_shipped_rain_skill(document, folder).read_bytes() == data
    assert gzip.decompress(data).decode("utf-8") == rain_event.shipped_text(document)
    assert rain_event.read_shipped_text(packed) == rain_event.shipped_text(document)


def test_an_unscored_event_or_a_missing_run_is_never_shipped(roots: tuple[Path, Path]) -> None:
    runs, bundles = roots
    with pytest.raises(ValueError, match="not scored"):
        rain_event.shipped_rain_skill(
            {"event": EVENT, "available": False}, runs_root=runs, bundles_root=bundles
        )
    (runs / RUN / "run.json").unlink()
    with pytest.raises(FileNotFoundError):
        rain_event.shipped_rain_skill(_payload(), runs_root=runs, bundles_root=bundles)


def test_a_score_that_is_not_finite_is_refused() -> None:
    with pytest.raises(ValueError):
        rain_event.shipped_text({"event": EVENT, "csi": float("nan")})
