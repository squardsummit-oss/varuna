"""Rules B11-B13: the bundle layout in SPEC.md 10.2 is a contract, not a suggestion.

Every case here was verified to validate **clean** before these rules existed. A consumer
written against `mm_5min`, `stage_m` or `baseline_kmh` breaks on a bundle that dropped the
column, and the validator's whole job is to catch that before the bundle ships.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from varuna_replay.domain import StormDomain
from varuna_replay.validate import GROUND_TRUTH_FLOOR, validate_bundle


def _findings(report: Any, rule: str) -> list[str]:
    return [item.message for item in report.for_rule(rule)]


def _files(report: Any, rule: str) -> set[str]:
    return {item.file for item in report.for_rule(rule)}


@pytest.mark.parametrize(
    ("member", "column"),
    [
        ("gauges.csv", "mm_5min"),
        ("gauges.csv", "lat"),
        ("tide.csv", "stage_m"),
    ],
)
def test_a_csv_stream_missing_a_fixed_column_fails_b11(
    tmp_path: Path,
    domain: StormDomain,
    make_bundle: Callable[..., Any],
    member: str,
    column: str,
) -> None:
    import pandas as pd

    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain)
    frame = pd.read_csv(root / member).drop(columns=[column])
    frame.to_csv(root / member, index=False, lineterminator="\n")

    report = validate_bundle(root)

    assert not report.ok, f"{member} without {column} must not validate"
    assert member in _files(report, "B11")
    assert any(column in message for message in _findings(report, "B11"))


def test_traffic_missing_its_baseline_fails_b11(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any]
) -> None:
    """Pulse's anomaly detector is `z = (v - baseline) / sd`; without the baseline it has no rule."""
    import pandas as pd

    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain)
    frame = pd.read_parquet(root / "traffic" / "speeds.parquet").drop(columns=["baseline_kmh"])
    frame.to_parquet(root / "traffic" / "speeds.parquet", index=False)

    report = validate_bundle(root)

    assert not report.ok
    assert any("baseline_kmh" in message for message in _findings(report, "B11"))


def test_a_report_row_missing_a_fixed_key_fails_b11(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any]
) -> None:
    import json

    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain)
    path = root / "reports.jsonl"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    first = json.loads(lines[0])
    first.pop("lat", None)
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    report = validate_bundle(root)

    assert not report.ok
    assert "reports.jsonl" in _files(report, "B11")
    assert any("lat" in message for message in _findings(report, "B11"))


@pytest.mark.parametrize(
    ("member", "variable", "wrong"),
    [
        ("radar/frames.zarr", "dbz", "mm/h"),
        ("truth/rain.zarr", "rain", "dBZ"),
    ],
)
def test_a_cube_with_the_wrong_units_fails_b12(
    tmp_path: Path,
    domain: StormDomain,
    make_bundle: Callable[..., Any],
    member: str,
    variable: str,
    wrong: str,
) -> None:
    """10.2 fixes dBZ for the frames and mm/h for the truth field.

    `write_cube` records the attribute and nothing read it back, so a cube carrying the other
    quantity's units validated clean - and a consumer trusting the attribute would apply the
    Marshall-Palmer inverse twice, or not at all.
    """
    import zarr

    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain)
    group = zarr.open_group(str(root / member), mode="a")
    group.attrs["units"] = wrong
    group[variable].attrs["units"] = wrong

    report = validate_bundle(root)

    assert not report.ok, f"{member} claiming {wrong} must not validate"
    assert member in _files(report, "B12")


def test_a_reconstruction_below_the_pin_floor_fails_b13(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any]
) -> None:
    """B10 only checks the manifest is honest about its count, so a bundle that lost its
    evidence agreed with itself and passed. 10.2 sets the floor at ten sourced in-AOI pins."""
    import json

    from varuna_schemas.settings import get_settings

    # The floor is scoped to the demo bundle, so the fixture has to *be* it.
    demo_id = get_settings().varuna_bundle
    root = tmp_path / demo_id
    make_bundle(root, domain, bundle_id=demo_id)
    path = root / "ground_truth.geojson"
    payload = json.loads(path.read_text(encoding="utf-8"))
    kept = payload["features"][: GROUND_TRUTH_FLOOR - 1]
    payload["features"] = kept
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    # Keep the manifest honest about the smaller count, so only B13 can fire.
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ground_truth_n"] = len(kept)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")

    report = validate_bundle(root)

    assert report.status("B10") == "pass", "the manifest agrees with the file; only the floor fails"
    assert not report.ok
    assert any(str(GROUND_TRUTH_FLOOR) in message for message in _findings(report, "B13"))


def test_the_shipped_design_storm_is_not_held_to_the_pin_floor() -> None:
    """A design storm is a hypothetical: it has no event to source pins from, and 10.2 asks
    for the floor on the reconstruction it names.

    This runs against the committed bundle rather than a fixture, so it also pins that the
    three new rules did not start failing an artifact that ships.
    """
    from varuna_schemas.paths import bundles_dir

    root = bundles_dir() / "MUM-IDF-25yr"
    # The folder alone does not mean generated: its manifest and ground truth are committed,
    # while radar/frames.zarr and truth/rain.zarr are gitignored and written by `make bundle`.
    # On a clean clone - which is what CI checks out - the folder exists and the cubes do not,
    # so testing the folder ran the validator over half a bundle and failed B3 for members a
    # clean clone was never going to have. Skip on the cubes, which is what the message meant.
    generated = all((root / cube).is_dir() for cube in ("radar/frames.zarr", "truth/rain.zarr"))
    if not generated:
        pytest.skip("MUM-IDF-25yr has not been generated in this checkout; run `make bundle`")

    report = validate_bundle(root)

    assert report.ok, report.render()
    assert report.status("B13") in {"pass", "skip"}
