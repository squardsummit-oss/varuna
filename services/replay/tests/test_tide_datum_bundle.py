"""The tide's vertical datum: the sourced arithmetic, the manifest block, the bus and the validator.

``tide.csv`` stays in chart datum as sourced; ``manifest.tide_datum`` carries the offset to mean
sea level (PSMSL station 43) that puts it in the DEM's frame. These tests pin the arithmetic to
the numbers the sources state, check the committed demo manifest carries the block with both
PSMSL URLs, and check the replay clock and the validator say which datum a stage is in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from varuna_replay import evidence
from varuna_replay.bundle import BundleLayout
from varuna_replay.clock import ReplayStreams
from varuna_replay.validate import RULES, validate_bundle
from varuna_schemas.models.bundle import BundleManifest, TideDatum
from varuna_schemas.paths import repo_root

PSMSL_URLS = {
    "https://psmsl.org/data/obtaining/stations/43.php",
    "https://psmsl.org/data/obtaining/rlr.annual.data/43.rlrdata",
}


# ------------------------------------------------------------------ the arithmetic
def test_rlr_sits_4478_mm_below_chart_datum() -> None:
    """BM 2PP1 is 8.522 m above chart datum and RLR is 13.0 m below BM 2PP1."""
    assert evidence.rlr_above_chart_datum_m() == pytest.approx(-4.478, abs=1e-9)


def test_the_annual_means_convert_to_the_heights_the_derivation_states() -> None:
    converted = {
        year: evidence.msl_above_chart_datum_for(year) for year in evidence.RLR_ANNUAL_MSL_MM
    }
    assert converted == {2015: 2.663, 2017: 2.676, 2020: 2.729, 2024: 2.733}
    for height in ("2.663", "2.676", "2.729", "2.733", "4.478 m", "2.711 m"):
        assert height in evidence.TIDE_DATUM_DERIVATION


def test_the_value_used_sits_inside_the_range_and_near_the_2019_interpolation() -> None:
    """2019 has no annual value; 2.70 m is one centimetre from the 2017-2020 interpolation."""
    low, high = evidence.MSL_ABOVE_CHART_DATUM_RANGE_M
    y2017, y2020 = (
        evidence.msl_above_chart_datum_for(2017),
        evidence.msl_above_chart_datum_for(2020),
    )
    interpolated_2019 = y2017 + (y2020 - y2017) * (2019 - 2017) / (2020 - 2017)
    assert interpolated_2019 == pytest.approx(2.711, abs=5e-4)
    assert abs(evidence.MSL_ABOVE_CHART_DATUM_M - interpolated_2019) <= 0.02
    assert low <= evidence.MSL_ABOVE_CHART_DATUM_M <= high
    recent = [evidence.msl_above_chart_datum_for(year) for year in evidence.RLR_ANNUAL_MSL_MM]
    assert low == pytest.approx(min(recent), abs=0.005)
    assert high == pytest.approx(max(recent), abs=0.005)


# ------------------------------------------------------------------ the manifest block
def test_the_evidence_block_cites_both_psmsl_urls_and_the_dem_handbook() -> None:
    datum = evidence.tide_datum()
    assert datum.stage_datum == "chart_datum"
    assert datum.offset_to_dem_m == 2.70
    assert datum.range_m == (2.66, 2.73)
    assert set(datum.source_urls) == PSMSL_URLS
    assert datum.dem_datum_source_url == evidence.COPERNICUS_DEM_HANDBOOK_URL
    assert "EGM2008; EPSG 3855" in datum.dem_datum
    assert "not quantified" in datum.residual.lower()
    assert "assumed" in datum.stage_reference


def test_the_committed_demo_manifest_carries_the_datum_block() -> None:
    path = repo_root() / "bundles" / evidence.BUNDLE_ID / "manifest.json"
    manifest = BundleManifest.model_validate_json(path.read_text(encoding="utf-8"))
    assert manifest.tide_datum == evidence.tide_datum()
    assert set(manifest.tide_datum.source_urls) == PSMSL_URLS


def test_a_manifest_without_tide_datum_serialises_without_the_key() -> None:
    """Design storms carry no tide; their manifests must not change by a byte (rule 8)."""
    for bundle in ("MUM-IDF-25yr", "CHN-IDF-25yr"):
        text = (repo_root() / "bundles" / bundle / "manifest.json").read_text(encoding="utf-8")
        manifest = BundleManifest.model_validate_json(text)
        assert manifest.tide_datum is None
        assert manifest.model_dump_json(indent=2) + "\n" == text


def test_a_chart_datum_block_needs_its_offset_inside_its_range() -> None:
    block = evidence.tide_datum().model_dump()
    with pytest.raises(ValidationError, match="outside"):
        TideDatum.model_validate({**block, "msl_above_chart_datum_m": 3.1})
    with pytest.raises(ValidationError, match="needs msl_above_chart_datum_m"):
        TideDatum.model_validate({**block, "range_m": None})
    with pytest.raises(ValidationError):
        TideDatum.model_validate({**block, "source_urls": []})


# ------------------------------------------------------------------ the bus and the validator
def _with_datum(folder: Path, datum: TideDatum | None) -> None:
    layout = BundleLayout.for_bundle(folder)
    payload = json.loads(layout.manifest.read_text(encoding="utf-8"))
    if datum is None:
        payload.pop("tide_datum", None)
    else:
        payload["tide_datum"] = json.loads(datum.model_dump_json())
    layout.manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_the_tide_event_carries_the_datum_and_the_stage_in_the_dem_frame(
    full_bundle: Path,
) -> None:
    _with_datum(full_bundle, evidence.tide_datum())
    tide = next(e for e in ReplayStreams.from_bundle(full_bundle).events if e.topic == "tide.stage")
    assert tide.payload["stage_m"] == 2.4, "the sourced stage is published unchanged"
    assert tide.payload["stage_datum"] == "chart_datum"
    assert tide.payload["datum"] == evidence.TIDE_STAGE_REFERENCE
    assert tide.payload["offset_to_dem_m"] == 2.70
    assert tide.payload["stage_dem_frame_m"] == pytest.approx(2.4 - 2.70, abs=1e-9)


def test_a_tide_event_without_a_declared_datum_says_so(full_bundle: Path) -> None:
    _with_datum(full_bundle, None)
    tide = next(e for e in ReplayStreams.from_bundle(full_bundle).events if e.topic == "tide.stage")
    assert tide.payload["stage_m"] == 2.4
    assert tide.payload["stage_datum"] is None
    assert tide.payload["datum"] is None
    assert tide.payload["stage_dem_frame_m"] is None


def test_validation_passes_with_the_datum_and_notes_its_absence(full_bundle: Path) -> None:
    _with_datum(full_bundle, evidence.tide_datum())
    declared = validate_bundle(full_bundle)
    assert declared.ok, declared.render()
    assert len(RULES) == 13
    assert not declared.warnings
    assert not [f for f in declared.for_rule("B9") if "tide" in f.message]

    _with_datum(full_bundle, None)
    undeclared = validate_bundle(full_bundle)
    assert undeclared.ok
    assert not undeclared.warnings
    notes = [f for f in undeclared.for_rule("B9") if "tide_datum" in f.message]
    assert len(notes) == 1 and notes[0].level == "note"


def test_a_datum_block_with_no_tide_is_an_error(full_bundle: Path) -> None:
    _with_datum(full_bundle, evidence.tide_datum())
    BundleLayout.for_bundle(full_bundle).tide.unlink()
    report = validate_bundle(full_bundle)
    assert any(f.level == "error" and "no tide.csv" in f.message for f in report.for_rule("B9")), (
        report.render()
    )
