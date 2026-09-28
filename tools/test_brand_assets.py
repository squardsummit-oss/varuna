"""`tools/brand_assets.py` rebuilds the committed brand files byte for byte from the team's logo."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]


def _brand_assets() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "brand_assets", Path(__file__).with_name("brand_assets.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRAND = _brand_assets()
LOGO = BRAND.default_source(REPO)


def test_committed_brand_files_are_what_the_logo_gives() -> None:
    if not LOGO.is_file():
        # A checkout without the logo cannot rebuild its brand files. Locally that is a skip; in
        # CI it is a failure, so committing the derived files without their source is never
        # hidden behind a skipped test.
        message = (
            f"the team's logo is not in this checkout: commit {BRAND.LOGO_AT_ROOT} "
            f"or {BRAND.LOGO_IN_REPO.as_posix()} with the brand files"
        )
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    assert BRAND.main(["--check"]) == 0


def test_reads_the_docs_copy_before_the_file_at_the_root(tmp_path: Path) -> None:
    in_repo = tmp_path / BRAND.LOGO_IN_REPO
    at_root = tmp_path / BRAND.LOGO_AT_ROOT
    # Neither: the error names a tracked place to put it.
    assert BRAND.default_source(tmp_path) == in_repo
    at_root.write_bytes(b"root")
    assert BRAND.default_source(tmp_path) == at_root
    in_repo.parent.mkdir(parents=True)
    in_repo.write_bytes(b"copy")
    assert BRAND.default_source(tmp_path) == in_repo
    # The 1.6 MB original is loaded by nothing in the app, so it is never kept where Next serves it.
    assert "public" not in BRAND.LOGO_IN_REPO.parts


def test_names_the_missing_logo_rather_than_writing_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert BRAND.main(["--root", str(tmp_path)]) == 2
    assert "varuna-logo.png not found" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())
