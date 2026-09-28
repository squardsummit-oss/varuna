"""Design storms and the Chicago hyetograph (task P2.8, SPEC.md 10.2).

The two properties that matter are that the depth is exactly the stated intensity times the
stated duration - nothing is invented on top of it - and that the peak lands where it was
asked for. The third is honesty: the basis must say the storm is not an IDF fit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from varuna_replay.build import (
    RADAR_CADENCE_MIN,
    TRUTH_CADENCE_MIN,
    build_design_bundle,
    load_city,
)
from varuna_replay.design import (
    DEFAULT_PEAK_POSITION,
    SHAPE_B_MIN,
    SHAPE_C,
    chicago_hyetograph,
    chicago_shape,
    design_bundle_id,
    design_storm,
    design_storm_field,
    hyetograph_at,
)
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.storm import accumulation_mm
from varuna_replay.validate import validate_bundle


# --------------------------------------------------------------------------- hyetograph
@pytest.mark.parametrize(
    ("intensity", "duration", "step"), [(25.0, 180, 5), (50.0, 180, 5), (50.0, 60, 10)]
)
def test_total_depth_is_intensity_times_duration(
    intensity: float, duration: int, step: int
) -> None:
    blocks = chicago_hyetograph(intensity, duration, step_min=step)
    assert len(blocks) == duration // step
    depth = blocks.sum() * step / 60.0
    assert depth == pytest.approx(intensity * duration / 60.0, rel=1e-12)


@pytest.mark.parametrize("r", [0.2, 0.4, 0.5, 0.75])
def test_the_peak_sits_in_the_block_that_was_asked_for(r: float) -> None:
    """The heaviest block must be the one holding the requested peak time.

    When the peak falls exactly on a block boundary (r = 0.5 or 0.75 of 180 minutes) either
    neighbour may hold it, so the check is that the winning block touches the peak time.
    """
    duration, step = 180, 5
    blocks = chicago_hyetograph(50.0, duration, peak_position_r=r, step_min=step)
    index = int(np.argmax(blocks))
    peak_min = r * duration
    assert index * step <= peak_min <= (index + 1) * step


def test_the_demo_design_storm_peaks_in_the_block_holding_minute_72() -> None:
    blocks = chicago_hyetograph(50.0, 180, peak_position_r=DEFAULT_PEAK_POSITION, step_min=5)
    assert int(np.argmax(blocks)) == 14  # the block [70, 75) minutes, holding 0.4 * 180 = 72


def test_the_shape_falls_away_on_both_sides_of_the_peak() -> None:
    duration = 180.0
    t = np.linspace(0.0, duration, 361)
    shape = chicago_shape(
        t,
        duration_min=duration,
        peak_position_r=DEFAULT_PEAK_POSITION,
        b_min=SHAPE_B_MIN,
        c=SHAPE_C,
    )
    peak = int(np.argmax(shape))
    assert np.all(np.diff(shape[: peak + 1]) > 0), "the rising limb must rise"
    assert np.all(np.diff(shape[peak:]) < 0), "the falling limb must fall"
    assert t[peak] == pytest.approx(DEFAULT_PEAK_POSITION * duration, abs=0.5)


def test_a_bad_peak_position_or_duration_is_refused() -> None:
    with pytest.raises(ValueError, match="peak_position_r"):
        chicago_hyetograph(50.0, 180, peak_position_r=0.0)
    with pytest.raises(ValueError, match="whole number"):
        chicago_hyetograph(50.0, 47, step_min=5)
    with pytest.raises(ValueError, match="negative"):
        chicago_hyetograph(-1.0, 180)


# --------------------------------------------------------------------------- design storm
def test_the_design_storm_takes_its_intensity_from_the_city_config() -> None:
    config = load_city("mumbai")
    storm = design_storm(config, intensity_key="upgraded")
    assert storm.intensity_mm_h == config.design_intensity_mm_h.upgraded == 50.0
    assert "mumbai.yaml" in storm.intensity_source
    legacy = design_storm(config, intensity_key="legacy")
    assert legacy.intensity_mm_h == config.design_intensity_mm_h.legacy == 25.0
    with pytest.raises(ValueError, match="intensity_key"):
        design_storm(config, intensity_key="invented")


def test_the_basis_says_the_storm_is_not_an_idf_fit() -> None:
    """Rule 6 and 7: the gap in the evidence is named on screen, not buried."""
    storm = design_storm(load_city("chennai"))
    basis = storm.basis.lower()
    assert "not derived from a published intensity-duration-frequency curve" in basis
    assert "not a fitted return period" in basis
    assert "cpheeo" in basis, "the basis must name what would settle it"
    assert storm.total_depth_mm == pytest.approx(50.0 * 180 / 60.0)


def test_bundle_ids_follow_the_spec() -> None:
    assert design_bundle_id(load_city("mumbai")) == "MUM-IDF-25yr"
    assert design_bundle_id(load_city("chennai")) == "CHN-IDF-25yr"


def test_the_field_is_uniform_block_constant_and_integrates_to_the_stated_depth(
    domain: StormDomain,
) -> None:
    storm = design_storm(load_city("mumbai"))
    times = step_times_min(0.0, float(storm.duration_min), TRUTH_CADENCE_MIN)
    field = design_storm_field(storm, domain, times)

    assert field.shape == (times.size, *domain.shape)
    for frame in field:
        assert frame.min() == frame.max(), "a design storm has no spatial structure to claim"
    assert field[-1].max() == 0.0, "the storm is over at the last instant"
    assert hyetograph_at(storm, float(storm.duration_min)) == 0.0
    assert hyetograph_at(storm, -1.0) == 0.0

    depth = accumulation_mm(field, TRUTH_CADENCE_MIN, rule="left")
    assert float(depth.mean()) == pytest.approx(storm.total_depth_mm, rel=1e-6)


# --------------------------------------------------------------------------- the bundles
@pytest.mark.parametrize("city", ["mumbai", "chennai"])
def test_a_design_bundle_is_written_and_validates(city: str, tmp_path: Path) -> None:
    """P2.8 end to end: the writer and the validator agree on a complete bundle."""
    config = load_city(city)
    bundle_id = design_bundle_id(config)
    result = build_design_bundle(config, bundles_root=tmp_path)

    assert result.root == (tmp_path / bundle_id).resolve(), (
        "the folder must be named for the bundle id, or rule B2 fails on the id it declares"
    )

    assert result.bundle_id == bundle_id
    assert result.manifest.label == "Design storm"
    assert result.manifest.design_storm is not None
    assert result.manifest.cadences["radar"] == RADAR_CADENCE_MIN
    assert result.manifest.cadences["truth"] == TRUTH_CADENCE_MIN
    assert result.manifest.ground_truth_n == 0
    assert set(result.files) == {
        "manifest.json",
        "radar/frames.zarr",
        "truth/rain.zarr",
        "ground_truth.geojson",
    }

    report = validate_bundle(result.root)
    assert report.ok, report.render()
    assert not report.warnings, report.render()
    # the streams a design storm does not need are reported as notes, not silence
    absent = {finding.file for finding in report.for_rule("B3")}
    assert absent == {"gauges.csv", "tide.csv", "traffic/speeds.parquet", "reports.jsonl"}


def test_the_bundle_says_how_hard_the_storm_peaks(tmp_path: Path) -> None:
    """A stated shape has a consequence, and the bundle card must show it.

    Holding 150 mm over three hours, this Chicago shape spikes to roughly nine times the
    mean intensity in one five-minute block. Nobody reading "150 mm total" would guess that,
    so the peak goes in the description and in the synthetic notes, where the /replay card
    and the mode banner read from (SPEC.md 0.6).
    """
    result = build_design_bundle(load_city("mumbai"), bundles_root=tmp_path)
    storm = result.manifest.design_storm
    assert storm is not None
    peak = max(storm.hyetograph_mm_h)
    assert peak == pytest.approx(448.83, abs=0.1)
    assert f"{peak:.0f} mm/h" in (result.manifest.description or "")
    assert any(f"{peak:.0f} mm/h peak block" in note for note in result.manifest.synthetic_notes)


def test_two_builds_of_the_same_design_bundle_are_byte_identical(tmp_path: Path, digest) -> None:
    config = load_city("mumbai")
    for name in ("a", "b"):
        build_design_bundle(config, bundles_root=tmp_path / name)
    assert digest(tmp_path / "a") == digest(tmp_path / "b")
