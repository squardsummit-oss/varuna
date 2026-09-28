"""The JSON-schema export is complete, deterministic, idempotent and committed up to date."""

from __future__ import annotations

import json
from pathlib import Path

from varuna_schemas import export_json
from varuna_schemas.models import MODEL_REGISTRY
from varuna_schemas.paths import schemas_json_dir


def _read_all(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.schema.json"))}


def test_export_writes_one_file_per_model(tmp_path: Path) -> None:
    changed, stale = export_json.export(tmp_path)
    assert len(changed) == len(MODEL_REGISTRY) and stale == []
    files = _read_all(tmp_path)
    assert set(files) == {f"{name}.schema.json" for name in MODEL_REGISTRY}
    for name in MODEL_REGISTRY:
        schema = json.loads(files[f"{name}.schema.json"])
        assert schema["$schema"] == export_json.SCHEMA_DIALECT
        assert schema["$id"] == f"urn:varuna:schema:{name}"
        assert schema["title"] == name
        assert schema["type"] == "object"


def test_export_is_idempotent_and_deterministic(tmp_path: Path) -> None:
    export_json.export(tmp_path)
    first = _read_all(tmp_path)
    changed, stale = export_json.export(tmp_path)
    assert changed == [] and stale == []
    assert _read_all(tmp_path) == first
    other = tmp_path / "again"
    export_json.export(other)
    assert _read_all(other) == first
    for text in first.values():
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert "\r" not in text
        assert (
            json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            == text
        )


def test_check_mode_reports_missing_stale_and_orphans(tmp_path: Path) -> None:
    changed, stale = export_json.export(tmp_path, check=True)
    assert len(changed) == len(MODEL_REGISTRY) and stale == []
    assert not any(tmp_path.glob("*.schema.json"))
    export_json.export(tmp_path)
    target = tmp_path / "RunMeta.schema.json"
    target.write_text("{}\n", encoding="utf-8")
    orphan = tmp_path / "Ghost.schema.json"
    orphan.write_text("{}\n", encoding="utf-8")
    changed, stale = export_json.export(tmp_path, check=True)
    assert changed == [target] and stale == [orphan]
    assert orphan.exists()
    changed, stale = export_json.export(tmp_path)
    assert changed == [target] and stale == [orphan]
    assert not orphan.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["title"] == "RunMeta"


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    assert export_json.main(["--check", "--out", str(tmp_path)]) == 1
    assert "stale or missing" in capsys.readouterr().err
    assert export_json.main(["--out", str(tmp_path)]) == 0
    assert f"wrote {len(MODEL_REGISTRY)}" in capsys.readouterr().out
    assert export_json.main(["--check", "--out", str(tmp_path)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_committed_schemas_are_up_to_date() -> None:
    """Fails when a model changed without re-running ``python -m varuna_schemas.export_json``."""
    folder = schemas_json_dir()
    assert folder.is_dir(), "run: uv run python -m varuna_schemas.export_json"
    changed, stale = export_json.export(folder, check=True)
    assert changed == [] and stale == [], "run: uv run python -m varuna_schemas.export_json"
