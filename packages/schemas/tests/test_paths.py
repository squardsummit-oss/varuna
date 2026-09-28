from __future__ import annotations

from pathlib import Path

import pytest
from varuna_schemas import paths


def test_repo_root_has_uv_workspace() -> None:
    root = paths.repo_root()
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.uv.workspace]" in text
    assert (root / "packages" / "schemas").is_dir()


def test_derived_paths_are_under_root(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VARUNA_REPO_ROOT", "VARUNA_DATA_DIR", "VARUNA_CITY_DIR", "VARUNA_BUNDLES_DIR"):
        monkeypatch.delenv(name, raising=False)
    root = paths.repo_root()
    assert paths.runs_dir() == root / "data" / "runs"
    assert (
        paths.run_dir("MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked").parent == paths.runs_dir()
    )
    assert paths.city_dir("mumbai") == root / "city" / "mumbai"
    assert paths.city_cache_dir("chennai") == root / "city" / "cache" / "chennai"
    assert paths.bundle_dir("MUM-2019-07-02") == root / "bundles" / "MUM-2019-07-02"
    assert paths.tokens_path() == root / "packages" / "tokens" / "tokens.json"
    assert paths.tokens_path().is_file()
    assert paths.docs_dir() == root / "docs"
    assert paths.schemas_json_dir() == root / "packages" / "schemas" / "json"
    assert paths.city_config_path("mumbai").name == "mumbai.yaml"
    assert all(
        isinstance(p, Path) for p in (paths.runs_dir(), paths.city_root(), paths.bundles_dir())
    )


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path / "b"))
    assert paths.runs_dir() == (tmp_path / "d" / "runs").resolve()
    assert paths.city_dir("mumbai") == (tmp_path / "c" / "mumbai").resolve()
    assert paths.bundle_dir("MUM-IDF-25yr") == (tmp_path / "b" / "MUM-IDF-25yr").resolve()


def test_repo_root_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VARUNA_REPO_ROOT", str(tmp_path))
    assert paths.repo_root() == tmp_path.resolve()
    assert paths.docs_dir() == tmp_path.resolve() / "docs"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "x\x00y"])
def test_ids_cannot_escape_their_folder(bad: str) -> None:
    with pytest.raises(ValueError, match="single path segment"):
        paths.run_dir(bad)
    with pytest.raises(ValueError, match="single path segment"):
        paths.bundle_dir(bad)
    with pytest.raises(ValueError, match="single path segment"):
        paths.city_dir(bad)
