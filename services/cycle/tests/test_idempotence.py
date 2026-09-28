"""Baking the same cycle twice produces the same files (P5.9; SPEC.md rule 8 and section 14).

Rule 8 asks that "two runs of ``make bake`` on the same inputs produce byte-identical
products" and section 14 lists "idempotent bakes (byte-identical)" under Correctness. Nothing
was checking it, and the ways a bake stops being reproducible are all quiet ones: a timestamp
written into a product rather than only into ``run.json``, a dict iterated in insertion order
that changes with a dependency, an unseeded draw somewhere in the ensemble, a parquet whose
writer stamps its own metadata.

The comparison deliberately excludes ``run.json``: it records ``stage_ms`` and the wall-clock
the run took, which are *supposed* to differ between two runs of the same cycle. Everything
else - the rasters, the segment and node forecasts, the hotspots, the alerts, the pump plan -
is a product and must not.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

# Written per run and expected to differ: timings and the wall clock are provenance, not product.
PROVENANCE = {"run.json"}


def _digest(root: Path) -> dict[str, str]:
    """sha256 of every file under ``root``, keyed by its path relative to it."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        key = str(path.relative_to(root)).replace("\\", "/")
        if key in PROVENANCE:
            continue
        out[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _city_is_built(city: str = "mumbai") -> bool:
    from varuna_schemas.paths import city_dir

    return (city_dir(city) / "map" / "segments.geojson").is_file()


@pytest.mark.skipif(
    os.environ.get("VARUNA_SKIP_BAKE") == "1",
    reason="VARUNA_SKIP_BAKE=1 set for a fast run",
)
def test_baking_the_same_cycle_twice_writes_identical_products(tmp_path: Path) -> None:
    """The demo bundle's first cycle, baked twice into two run directories.

    Skipped rather than failed when the city is not built: `make city CITY=mumbai` is a
    ten-minute prerequisite and a clean clone legitimately has not run it. The skip names the
    command, so a reader knows what to run rather than guessing why it is grey.
    """
    if not _city_is_built():
        pytest.skip("city/mumbai is not built; run `make city CITY=mumbai` first")

    from varuna_cycle.sky_cycle import clear_cycle_cache
    from varuna_cycle.twin_cycle import run_cycle

    first = run_cycle(mode="baked", overwrite=True)
    # The second bake replaces the first *in place* - the run id is a function of the cycle, so
    # both land in the same folder - which means the first bake's bytes have to be taken away
    # before the second runs. Comparing `first.run_dir` with `second.run_dir` after the fact
    # compares one directory with itself and passes whatever the writers do.
    kept = shutil.copytree(first.run_dir, tmp_path / "first")
    # And the second bake has to nowcast again rather than replay the first one's memoised Sky:
    # the rain cube is the twenty members on disk, so a cached ensemble would make the store
    # trivially identical and prove nothing about the seed (rule 8). Costs one Sky run.
    clear_cycle_cache()
    second = run_cycle(mode="baked", overwrite=True)

    assert first.run_id == second.run_id, (
        "the same cycle produced two run ids; the id is built from the city, the cycle "
        f"instant and the engine versions, none of which changed: {first.run_id} then "
        f"{second.run_id}"
    )

    left, right = _digest(kept), _digest(second.run_dir)
    assert left, f"the bake wrote no products into {first.run_dir}"

    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    assert not only_left and not only_right, (
        f"the two bakes wrote different files; only in the first: {only_left}; "
        f"only in the second: {only_right}"
    )

    differing = sorted(name for name, digest in left.items() if right[name] != digest)
    assert not differing, (
        f"{len(differing)} of {len(left)} products differ between two bakes of the same "
        f"cycle, so the bake is not reproducible (rule 8): {differing[:8]}"
    )

    _assert_rain_cube_round_trips(kept, second.run_dir)
    _assert_provenance_agrees(kept, second.run_dir)


def _assert_provenance_agrees(first: Path, second: Path) -> None:
    """run.json is excluded from the byte comparison for its timings, not for its provenance.

    The city fingerprint and the Twin revision say which city and which Twin a run came from;
    two bakes of one cycle on one city must record the same ones, or `stale_runs` would call a
    fresh re-bake stale.
    """
    import json

    left = json.loads((first / "run.json").read_text(encoding="utf-8"))
    right = json.loads((second / "run.json").read_text(encoding="utf-8"))
    for key in ("city_fingerprint", "twin_revision"):
        assert left.get(key) is not None, f"run.json carries no {key}"
        assert left[key] == right.get(key), f"{key} differs between two bakes of one cycle"


def _assert_rain_cube_round_trips(first: Path, second: Path) -> None:
    """The Sky members survive the bake, and both bakes read back the same cube.

    The digest comparison above already covers every byte under ``rain/``, but a Zarr store is
    a folder of compressed shards, and identical bytes are a stronger claim than the one that
    matters: what a reader gets back. Reading the cube is the check that a baked run can
    actually be re-ensembled offline (SPEC.md 10.3) rather than merely carrying the same
    bytes in a store nothing can open - and it is the check that would catch a writer that
    rounded, reordered or truncated the member axis on its way to disk.

    Asserting the store is *present* is part of it: the demo bundle is a reconstructed event, so
    its cycles run through Sky and a missing cube would mean the twenty members were computed
    and discarded again.
    """
    import numpy as np
    from varuna_sky.products import RAIN_CUBE, RAIN_QUANTILES, read_rain_cube

    for run_dir in (first, second):
        for store in (RAIN_CUBE, RAIN_QUANTILES):
            assert (run_dir / store).is_dir(), (
                f"{run_dir.name} has no {store}; the Sky ensemble did not survive the cycle, so "
                "this run cannot be re-ensembled without re-running Sky (SPEC.md 10.3)"
            )

    left, right = read_rain_cube(first / RAIN_CUBE), read_rain_cube(second / RAIN_CUBE)
    assert left.shape == right.shape, (
        f"two bakes of the same cycle wrote rain cubes of different shape, {left.shape} then "
        f"{right.shape}; the member axis is not reproducible (rule 8)"
    )
    assert np.array_equal(left, right), (
        "the rain cubes of two bakes of the same cycle read back different values (largest "
        f"difference {float(np.nanmax(np.abs(left - right))):.6g} mm/h), so the ensemble is not "
        "seeded end to end (rule 8)"
    )


def test_the_cycle_reports_the_numbers_a_rerun_is_allowed_to_change() -> None:
    """`stage_ms` is provenance, and this pins that it is the *only* thing excluded.

    If a future product starts carrying a wall-clock value, the exclusion list above is where
    somebody would be tempted to hide it. Keeping the list to one entry, and asserting it,
    makes that a visible decision rather than a quiet one.
    """
    assert PROVENANCE == {"run.json"}, (
        "something was added to the idempotence exclusion list; a product that cannot be "
        "reproduced is a rule 8 failure, not an exclusion"
    )
