"""The bundle validator (task P2.1): every rule fires on the thing it is about.

Each test breaks one thing in a bundle that was otherwise valid, and checks that the report
fails the right rule and names the right file - because a validation message nobody can act
on is worse than none.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from varuna_replay import bundle as members
from varuna_replay.bundle import LF
from varuna_replay.domain import StormDomain
from varuna_replay.validate import RULES, validate_bundle


def _findings(report, rule: str) -> list[str]:
    return [finding.message for finding in report.for_rule(rule) if finding.level == "error"]


def _files(report, rule: str) -> set[str]:
    return {finding.file for finding in report.for_rule(rule) if finding.level == "error"}


# --------------------------------------------------------------------------- happy path
def test_a_complete_bundle_passes_every_rule(full_bundle: Path) -> None:
    report = validate_bundle(full_bundle)
    assert report.ok, report.render()
    assert report.status("B3") == "pass"
    assert all(report.status(rule) in {"pass", "warn"} for rule in RULES), report.render()


def test_the_report_names_the_rule_and_the_file(full_bundle: Path) -> None:
    (full_bundle / "gauges.csv").unlink()
    report = validate_bundle(full_bundle)
    text = report.render()
    assert not report.ok
    assert "fail  B3  members.present" in text
    assert "gauges.csv" in text
    assert "Bundle is not valid" in text
    assert str(full_bundle) in text


# --------------------------------------------------------------------------- B1, B2
def test_a_missing_folder_or_manifest_fails_first(tmp_path: Path) -> None:
    report = validate_bundle(tmp_path / "MUM-GHOST")
    assert not report.ok
    assert _findings(report, "B1") == ["no such bundle folder"]
    assert report.status("B2") == "skip", "nothing else can be checked without a manifest"

    empty = tmp_path / "MUM-EMPTY"
    empty.mkdir()
    report = validate_bundle(empty)
    assert "manifest.json" in _files(report, "B1")


def test_a_manifest_that_does_not_validate_fails_b2(full_bundle: Path) -> None:
    payload = json.loads((full_bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["cadences"]["radar"] = 0
    (full_bundle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(full_bundle)
    assert "manifest.json" in _files(report, "B2")
    assert "positive minutes" in _findings(report, "B2")[0]


def test_a_manifest_id_must_match_its_folder(full_bundle: Path) -> None:
    payload = json.loads((full_bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["id"] = "MUM-SOMETHING-ELSE"
    (full_bundle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(full_bundle)
    assert any("sits in folder" in message for message in _findings(report, "B2"))


# --------------------------------------------------------------------------- B3
@pytest.mark.parametrize(
    "member", ["gauges.csv", "tide.csv", "reports.jsonl", "ground_truth.geojson"]
)
def test_a_reconstruction_needs_every_member(full_bundle: Path, member: str) -> None:
    (full_bundle / member).unlink()
    report = validate_bundle(full_bundle)
    assert member in _files(report, "B3")
    assert "the bundle contract requires it" in _findings(report, "B3")[0]


# --------------------------------------------------------------------------- B4
def test_a_cube_whose_window_disagrees_with_the_manifest_fails_b4(full_bundle: Path) -> None:
    payload = json.loads((full_bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["cadences"]["truth"] = 15  # the cube is written every 5 minutes
    (full_bundle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(full_bundle)
    assert "truth/rain.zarr" in _files(report, "B4")
    assert any("cadence" in message for message in _findings(report, "B4"))


def test_a_stream_that_stops_early_fails_b4(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], t0
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(
        root,
        domain,
        gauges_rows=[
            {
                "ts": t0 + timedelta(minutes=minute),
                "station_id": "santacruz",
                "lat": 19.09,
                "lon": 72.85,
                "mm_5min": 1.0,
                "synthetic": True,
            }
            for minute in (0, 15, 30)  # the window runs to 60
        ],
    )
    report = validate_bundle(root)
    assert "gauges.csv" in _files(report, "B4")
    assert any("ends at" in message for message in _findings(report, "B4"))


def test_a_report_outside_the_window_fails_b4(full_bundle: Path) -> None:
    layout = members.BundleLayout(root=full_bundle)
    rows = [json.loads(line) for line in layout.reports.read_text(encoding="utf-8").splitlines()]
    rows.append({**rows[0], "ts": "2019-07-02T23:00:00+05:30"})
    layout.reports.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    report = validate_bundle(full_bundle)
    assert "reports.jsonl" in _files(report, "B4")
    assert any("outside the replay window" in message for message in _findings(report, "B4"))


def test_a_timestamp_without_an_offset_is_reported_not_raised(full_bundle: Path) -> None:
    """The validator never raises on bad data, and replay times carry +05:30 (SPEC.md 12).

    A naive timestamp cannot be compared with the manifest window at all, so parsing one and
    carrying on would crash the run instead of naming the file and the rule.
    """
    gauges = full_bundle / "gauges.csv"
    naive = gauges.read_text(encoding="utf-8").replace("+05:30", "")
    gauges.write_text(naive, encoding="utf-8", newline=LF)
    report = validate_bundle(full_bundle)
    assert "gauges.csv" in _files(report, "B4")
    assert any("no UTC offset" in message for message in _findings(report, "B4"))


def test_an_unparseable_timestamp_is_reported(full_bundle: Path) -> None:
    reports = full_bundle / "reports.jsonl"
    rows = [json.loads(line) for line in reports.read_text(encoding="utf-8").splitlines() if line]
    rows[0]["ts"] = "2 July 2019, about eight"
    text = "".join(json.dumps(row, sort_keys=True) + LF for row in rows)
    reports.write_text(text, encoding="utf-8", newline=LF)
    report = validate_bundle(full_bundle)
    assert any("is not ISO 8601" in message for message in _findings(report, "B4"))


# --------------------------------------------------------------------------- B5
def test_cubes_on_different_grids_fail_b5(full_bundle: Path, domain: StormDomain, t0) -> None:
    from varuna_replay.domain import step_times_min
    from varuna_replay.storm import RadarRender, radar_dbz, rain_field, random_storm

    other = StormDomain(crs=32643, res_m=500.0, n_px=domain.n_px, left=0.0, top=0.0)
    times = step_times_min(0.0, 60.0, 10.0)
    design = random_storm(other, seed=1, window_min=60.0, n_cells=1)
    members.write_cube(
        full_bundle / members.RADAR_ZARR,
        radar_dbz(rain_field(design, other, times), other, RadarRender.from_design(design)),
        variable=members.RADAR_VARIABLE,
        times_min=times,
        domain=other,
        t0=t0,
        step_min=10.0,
        units="dBZ",
    )
    report = validate_bundle(full_bundle)
    assert any("grids differ" in message for message in _findings(report, "B5"))


# --------------------------------------------------------------------------- B6
def test_a_pin_without_a_source_url_fails_b6(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    """Rule 7: no fabricated ground truth - a pin with no source is an error, not a warning."""
    root = tmp_path / "MUM-TEST-2019"
    bad = pin()
    del bad["properties"]["source_url"]
    make_bundle(root, domain, ground_truth=[bad])
    report = validate_bundle(root)
    assert "ground_truth.geojson" in _files(report, "B6")
    assert any("source_url" in message for message in _findings(report, "B6"))


def test_a_pin_marked_synthetic_fails_b6(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain, ground_truth=[pin(synthetic=True)])
    report = validate_bundle(root)
    assert any("synthetic" in message for message in _findings(report, "B6"))


def test_coordinates_must_agree_between_geometry_and_properties(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain, ground_truth=[pin(lon=72.9, lat=19.1)])
    report = validate_bundle(root)
    assert any("the geometry says" in message for message in _findings(report, "B6"))


def test_an_undeclared_property_is_a_warning_not_an_error(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(
        root, domain, ground_truth=[pin(cached_path="docs/research/_raw/x.txt", mood="wet")]
    )
    report = validate_bundle(root)
    assert report.ok, report.render()
    warnings = [finding.message for finding in report.for_rule("B6")]
    assert any("'mood'" in message for message in warnings)
    assert not any("cached_path" in message for message in warnings), "provenance keys are allowed"


def test_a_design_storm_may_not_carry_ground_truth(tmp_path: Path) -> None:
    from varuna_replay.build import build_design_bundle, load_city

    result = build_design_bundle(load_city("mumbai"), bundles_root=tmp_path)
    payload = json.loads((result.root / "ground_truth.geojson").read_text(encoding="utf-8"))
    payload["features"] = [{"type": "Feature", "geometry": None, "properties": {}}]
    (result.root / "ground_truth.geojson").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(result.root)
    assert any("a design storm has no observed event" in m for m in _findings(report, "B6"))


# --------------------------------------------------------------------------- B7
def test_a_stream_without_a_synthetic_flag_fails_b7(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], t0
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain)
    import pandas as pd

    frame = pd.read_csv(root / "gauges.csv").drop(columns=["synthetic"])
    frame.to_csv(root / "gauges.csv", index=False, lineterminator="\n")
    report = validate_bundle(root)
    assert "gauges.csv" in _files(report, "B7")
    assert any("must say so" in message for message in _findings(report, "B7"))


def test_a_blank_synthetic_value_fails_b7(full_bundle: Path) -> None:
    """A blank flag says nothing, and must not read as 'synthetic'.

    ``NaN`` is truthy in Python, so a column of blanks would otherwise sail through the rule
    that exists to keep an unlabelled stream off the console (SPEC.md 0.7).
    """
    import pandas as pd

    frame = pd.read_csv(full_bundle / "gauges.csv")
    frame["synthetic"] = ""
    frame.to_csv(full_bundle / "gauges.csv", index=False, lineterminator=LF)
    report = validate_bundle(full_bundle)
    assert "gauges.csv" in _files(report, "B7")
    assert any("blank or unreadable" in message for message in _findings(report, "B7"))


def test_a_row_marked_not_synthetic_is_a_note_not_an_error(full_bundle: Path) -> None:
    import pandas as pd

    frame = pd.read_csv(full_bundle / "gauges.csv")
    frame["synthetic"] = [i > 0 for i in range(len(frame))]
    frame.to_csv(full_bundle / "gauges.csv", index=False, lineterminator=LF)
    report = validate_bundle(full_bundle)
    assert report.ok, report.render()
    notes = [f.message for f in report.for_rule("B7") if f.level == "note"]
    assert any("1 row(s) are marked not synthetic" in message for message in notes)


def test_a_tide_row_without_a_source_fails_b7(full_bundle: Path) -> None:
    import pandas as pd

    frame = pd.read_csv(full_bundle / "tide.csv")
    frame.loc[0, "source"] = ""
    frame.to_csv(full_bundle / "tide.csv", index=False, lineterminator="\n")
    report = validate_bundle(full_bundle)
    assert any("blank 'source'" in message for message in _findings(report, "B7"))


def test_a_reconstruction_without_synthetic_notes_fails_b7(full_bundle: Path) -> None:
    payload = json.loads((full_bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["synthetic_notes"] = []
    (full_bundle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(full_bundle)
    assert "manifest.json" in _files(report, "B7")


# --------------------------------------------------------------------------- B9, B10
def test_calibration_without_a_basis_is_refused_by_the_contract(full_bundle: Path) -> None:
    """The model itself blocks it, so the validator reports it as an invalid manifest."""
    payload = json.loads((full_bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["calibration_basis"] = None
    (full_bundle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    report = validate_bundle(full_bundle)
    assert any("calibration_basis" in message for message in _findings(report, "B2"))


def test_a_wrong_ground_truth_count_fails_b10(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    root = tmp_path / "MUM-TEST-2019"
    make_bundle(root, domain, ground_truth=[pin()], ground_truth_n=9)
    report = validate_bundle(root)
    assert "manifest.json" in _files(report, "B10")
    assert any("1 pin(s) fall inside" in message for message in _findings(report, "B10"))


def test_pins_outside_the_area_of_interest_are_not_counted(
    tmp_path: Path, domain: StormDomain, make_bundle: Callable[..., Any], pin
) -> None:
    """The research file keeps pins outside the area; only the inside ones count."""
    root = tmp_path / "MUM-TEST-2019"
    outside = pin(id="MUM19-99", name="Thane")
    outside["geometry"]["coordinates"] = [72.97, 19.20]
    make_bundle(root, domain, ground_truth=[pin(), outside], ground_truth_n=1)
    report = validate_bundle(root)
    assert report.status("B10") == "pass", report.render()
