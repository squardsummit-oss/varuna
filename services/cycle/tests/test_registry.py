from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from varuna_cycle.registry import RUN_JSON, RunRegistry, summarize
from varuna_schemas.models import RunMeta, build_run_id
from varuna_schemas.samples import sample


def _meta(**overrides: object) -> RunMeta:
    base = sample("RunMeta")
    assert isinstance(base, RunMeta)
    return base.model_copy(update=overrides)


def _shifted(base: RunMeta, minutes: int, mode: str = "baked", **overrides: object) -> RunMeta:
    ts = base.cycle_ts + timedelta(minutes=minutes)
    parts = base.parts
    run_id = build_run_id(
        base.city,
        ts,
        parts.sky_version,
        parts.twin_version,
        parts.flash_version,
        mode,  # type: ignore[arg-type]
    )
    return base.model_copy(
        update={"run_id": run_id, "cycle_ts": ts, "created_at": ts, "mode": mode, **overrides}
    )


def test_empty_when_runs_dir_is_missing(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    assert reg.list() == []
    assert reg.latest() is None
    assert reg.get("MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked") is None
    assert reg.run_list().count == 0


def test_write_and_read_back(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    meta = _meta()

    def writer(folder: Path) -> None:
        (folder / "hotspots.json").write_text("[]", encoding="utf-8")

    path = reg.write_run_dir(meta.run_id, writer, meta=meta)
    assert path == tmp_path / "runs" / meta.run_id
    assert (path / RUN_JSON).is_file()
    assert (path / "hotspots.json").is_file()
    assert not [p for p in (tmp_path / "runs").iterdir() if p.name.startswith(".")]
    got = reg.get(meta.run_id)
    assert got is not None
    assert got.run_id == meta.run_id
    assert got.total_ms == meta.total_ms
    assert reg.latest() == got


def test_list_is_newest_first_with_filters_and_limit(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    base = _meta()
    older = _shifted(base, -10)
    newer = _shifted(base, +5, mode="live", bundle=None)
    chennai = _shifted(base, +10, city="chennai", bundle="CHN-IDF-25yr")
    chennai = chennai.model_copy(update={"run_id": chennai.run_id.replace("MUM-", "CHN-")})
    for meta in (base, older, newer, chennai):
        reg.write_meta(meta)

    ids = [m.run_id for m in reg.list()]
    assert ids == [chennai.run_id, newer.run_id, base.run_id, older.run_id]
    assert [m.run_id for m in reg.list(city="mumbai")] == [newer.run_id, base.run_id, older.run_id]
    assert [m.run_id for m in reg.list(bundle=base.bundle)] == [base.run_id, older.run_id]
    assert [m.run_id for m in reg.list(limit=2)] == [chennai.run_id, newer.run_id]
    assert reg.latest(city="mumbai", bundle=base.bundle) is not None
    assert reg.latest(city="mumbai", bundle=base.bundle).run_id == base.run_id  # type: ignore[union-attr]
    listing = reg.run_list(city="mumbai")
    assert listing.count == 3 and listing.latest_run_id == newer.run_id
    assert summarize(base).total_ms == base.total_ms


def test_invalid_and_foreign_folders_are_skipped(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "not-a-run").mkdir()
    (runs / "broken").mkdir()
    (runs / "broken" / RUN_JSON).write_text("{not json", encoding="utf-8")
    (runs / ".tmp-leftover").mkdir()
    (runs / "stray.txt").write_text("x", encoding="utf-8")
    mismatch = runs / "MUM-20190702T1200Z-sky1.0-twin1.0-flash0.3-baked"
    mismatch.mkdir()
    (mismatch / RUN_JSON).write_text(_meta().model_dump_json(), encoding="utf-8")
    reg = RunRegistry(runs)
    assert reg.list() == []
    assert reg.get("../etc") is None


def test_write_refuses_duplicate_unless_overwrite(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    meta = _meta()
    reg.write_meta(meta)
    with pytest.raises(FileExistsError):
        reg.write_meta(meta)
    updated = meta.model_copy(update={"notes": ["rebaked"]})
    reg.write_meta(updated, overwrite=True)
    got = reg.get(meta.run_id)
    assert got is not None and got.notes == ["rebaked"]


def test_failed_writer_leaves_no_trace(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    meta = _meta()

    def boom(_folder: Path) -> None:
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        reg.write_run_dir(meta.run_id, boom, meta=meta)
    assert list((tmp_path / "runs").iterdir()) == []
    with pytest.raises(FileNotFoundError):
        reg.write_run_dir(meta.run_id, lambda _p: None)
    with pytest.raises(ValueError, match="does not match"):
        reg.write_run_dir(
            "MUM-20190702T1200Z-sky1.0-twin1.0-flash0.3-baked", lambda _p: None, meta=meta
        )


def test_cache_refreshes_when_run_json_changes(tmp_path: Path) -> None:
    reg = RunRegistry(tmp_path / "runs")
    meta = _meta()
    reg.write_meta(meta)
    assert reg.get(meta.run_id) is not None
    run_json = tmp_path / "runs" / meta.run_id / RUN_JSON
    data = json.loads(run_json.read_text(encoding="utf-8"))
    data["notes"] = ["edited on disk"]
    run_json.write_text(json.dumps(data), encoding="utf-8")
    import os

    os.utime(run_json, ns=(run_json.stat().st_atime_ns, run_json.stat().st_mtime_ns + 10_000_000))
    got = reg.get(meta.run_id)
    assert got is not None and got.notes == ["edited on disk"]
