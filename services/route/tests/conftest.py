"""Fixtures for the routing tests that need a real baked run.

``demo/runs`` holds the seven cycles the demo ships with, committed to the repository. The
router looks runs up under :func:`varuna_schemas.paths.runs_dir`, so a test that wants a real
run points ``VARUNA_DATA_DIR`` at a temporary folder and copies the two files routing reads -
``segments_wet.json`` and ``run.json``. Copying rather than pointing at ``demo/`` keeps the
committed set read-only, and copying two files rather than the folder keeps it under a second.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from varuna_schemas.paths import repo_root

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

DEMO_0840 = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
"""The 08:40 IST cycle of 2 July 2019 - the demo's busiest, and the one TASKS.md D-01 names."""


def demo_run_source(run_id: str = DEMO_0840) -> Path:
    return repo_root() / "demo" / "runs" / run_id


@pytest.fixture(scope="session")
def demo_run_id() -> str:
    return DEMO_0840


@pytest.fixture(scope="session")
def demo_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A ``VARUNA_DATA_DIR`` holding the 08:40 demo run, or skip when it is not committed."""
    source = demo_run_source()
    if not (source / "segments_wet.json").is_file():
        pytest.skip(f"{source} is not present; the committed demo runs are needed for this test.")
    root = tmp_path_factory.mktemp("varuna-data")
    target = root / "runs" / DEMO_0840
    target.mkdir(parents=True)
    for name in ("segments_wet.json", "run.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    return root


@pytest.fixture
def demo_runs(demo_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the path helpers at the copied demo run for the duration of one test."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(demo_data_dir))
    yield demo_data_dir


@pytest.fixture
def ops_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An empty, writable ``VARUNA_DATA_DIR`` so an ops log never touches the repository."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    yield tmp_path
