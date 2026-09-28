"""The Twin profiler is an instrument, so it is tested like one (task P4.6, SPEC.md rule 13).

`tools/profile_twin.py` is what every Twin timing in the performance record is supposed to trace
to. Two properties make those numbers worth quoting: it reports each kernel and the Python
around it separately with a contention label, and it leaves nothing behind but its `--out`
directory - a profiler that scribbled into `city/` or the working tree would be a measurement
that changed the thing it measured.

The tool lives under `tools/`, which is not a package, so it is loaded by path. It carries its
own synthetic city, so this runs without `city/` on disk.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "tools" / "profile_twin.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("profile_twin", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves postponed annotations through sys.modules[cls.__module__], so a
    # module loaded by path has to be registered before its body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_run_then_micro_report_every_kernel_with_a_contention_label(
    tool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert (
        tool.main(
            [
                "--out",
                str(out),
                "--synthetic-city",
                "--quiet-logs",
                "run",
                "--forcing",
                "uniform:60",
                "--steps",
                "1",
                "--snapshot-sync",
                "30",
            ]
        )
        == 0
    )
    run = json.loads((out / "profile_run.json").read_text())
    assert run["contention"] in {"idle", "under contention"}
    assert run["calls"]["surface_call"] == 60, "one surface call per 5 s sync over 5 minutes"
    assert run["calls"]["k_flux"] == run["cfl_substeps"] > 0
    assert run["calls"]["k_drain"] == 300, "five 1 s drain steps per sync"
    # Every stage the split reports must have been timed, or its 0 s is not a measurement.
    for key in ("compute_exchange", "k_exchange", "effective_rain", "py_tide_at", "drain_call"):
        assert run["calls"].get(key, 0) > 0, key
    assert run["calls"]["compute_exchange"] == run["calls"]["k_exchange"]
    for key in ("surface_kernels", "surface_python", "drain_kernel", "coupling_kernel"):
        assert key in run["split_s"]
    assert run["snapshot_sync"] == 30

    assert (
        tool.main(
            [
                "--out",
                str(out),
                "--synthetic-city",
                "--quiet-logs",
                "micro",
                "--snapshot",
                str(out / "snapshot.npz"),
                "--threads",
                "1",
                "--repeats",
                "2",
                "--rounds",
                "1",
                "--python-repeats",
                "3",
            ]
        )
        == 0
    )
    micro = json.loads((out / "profile_micro.json").read_text())
    assert micro["contention"] in {"idle", "under contention"}
    assert micro["machine_before"]["python_processes"] is None or (
        micro["machine_before"]["python_processes"] >= 1
    )
    entry = micro["rounds_data"][0]
    kernels = entry["kernels_by_threads"]["1"]
    for name in ("flux", "depth"):
        assert kernels[name]["best_ms"] > 0.0
        assert kernels[name]["median_ms"] >= kernels[name]["best_ms"]
    for name in (
        "surface_kernels_one_substep",
        "run_surface_one_substep",
        "drain_sync_5_steps",
        "exchange_call",
    ):
        assert entry[name]["best_ms"] > 0.0, name
    assert "best" in entry["run_surface_overhead_ms"]

    # Nothing but --out: the working directory it ran from is still empty.
    assert _tree(cwd) == set()
    assert _tree(out) == {"profile_run.json", "snapshot.npz", "profile_micro.json"}


def test_hooks_put_every_function_back(tool) -> None:
    from varuna_twin import swe2d

    original = swe2d._update_flux
    with tool.hooked() as hooks:
        hooks.wrap(swe2d, "_update_flux", "k_flux")
        assert swe2d._update_flux is not original
    assert swe2d._update_flux is original


def test_hooks_refuse_a_target_that_does_not_exist(tool) -> None:
    """A renamed kernel must fail the profile, not report 0 s for its stage."""
    from varuna_twin import swe2d

    with tool.hooked() as hooks, pytest.raises(AttributeError):
        hooks.wrap(swe2d, "_no_such_kernel", "k_missing")


def test_contention_label_reads_other_pythons_and_load(tool) -> None:
    assert tool.contention_label({"python_processes": 1, "cpu_load_pct": 3.0}) == "idle"
    assert tool.contention_label({"python_processes": 2, "cpu_load_pct": 3.0}) == (
        "under contention"
    )
    assert tool.contention_label({"python_processes": 1, "cpu_load_pct": 90.0}) == (
        "under contention"
    )
    # An unknown count is not evidence of an idle machine, but it is not evidence of load
    # either; the label then rests on what was measured.
    assert tool.contention_label({"python_processes": None, "cpu_load_pct": None}) == "idle"
    assert os.cpu_count() is None or tool.machine_state()["logical_cpus"] >= 1
