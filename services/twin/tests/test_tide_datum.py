"""The tide stage is read in the DEM's frame when the bundle declares a chart-datum series.

``tide.csv`` keeps the stage as sourced (above chart datum). ``load_tide`` subtracts the mean
sea level offset the manifest's ``tide_datum`` block carries, and leaves a bundle that declares
no datum exactly as written.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from varuna_twin.city import load_tide

BUNDLE = "TEST-TIDE-DATUM"
STAGES = (0.045, 0.123, 0.238, 3.936)

DATUM_BLOCK = {
    "stage_datum": "chart_datum",
    "stage_reference": "chart datum (assumed: the 4.92 m civic statement does not name its datum)",
    "msl_above_chart_datum_m": 2.70,
    "range_m": [2.66, 2.73],
    "derivation": "RLR = chart datum - 4.478 m; annual MSL 7,154 mm RLR (2017), 7,207 mm (2020).",
    "source_urls": [
        "https://psmsl.org/data/obtaining/stations/43.php",
        "https://psmsl.org/data/obtaining/rlr.annual.data/43.rlrdata",
    ],
    "dem_datum": "Copernicus DEM GLO-30 heights are relative to EGM2008.",
    "dem_datum_source_url": "https://dataspace.copernicus.eu/copernicus-dem-handbook.pdf",
    "residual": "EGM2008 geoid versus local mean sea level offset not quantified.",
}


def _write_bundle(root: Path, manifest: dict[str, object] | None) -> Path:
    folder = root / BUNDLE
    folder.mkdir(parents=True)
    lines = ["ts,stage_m,source"]
    lines += [
        f"2019-07-02T05:{40 + 5 * i:02d}:00+05:30,{stage},illustrative"
        for i, stage in enumerate(STAGES)
    ]
    (folder / "tide.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if manifest is not None:
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "bundles"
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(root))
    return root


def test_a_chart_datum_stage_is_read_minus_mean_sea_level(bundles: Path) -> None:
    _write_bundle(bundles, {"id": BUNDLE, "tide_datum": DATUM_BLOCK})
    tide = load_tide(BUNDLE)
    assert tide is not None
    np.testing.assert_allclose(tide.stage_m, np.asarray(STAGES) - 2.70, rtol=0, atol=1e-12)
    assert tide.source == "illustrative", "the source label survives the conversion"
    assert tide.datum_note is not None
    assert "2.70 m above chart datum" in tide.datum_note
    assert "https://psmsl.org/data/obtaining/stations/43.php" in tide.datum_note
    assert "not quantified" in tide.datum_note, "the residual travels with the number"


def test_a_manifest_without_tide_datum_leaves_the_series_as_written(bundles: Path) -> None:
    _write_bundle(bundles, {"id": BUNDLE, "tide_source": "illustrative"})
    tide = load_tide(BUNDLE)
    assert tide is not None
    assert tide.stage_m.tolist() == list(STAGES), "identical, not merely close"
    assert tide.datum_note is None


def test_a_bundle_with_no_manifest_leaves_the_series_as_written(bundles: Path) -> None:
    _write_bundle(bundles, None)
    tide = load_tide(BUNDLE)
    assert tide is not None
    assert tide.stage_m.tolist() == list(STAGES)
    assert tide.datum_note is None


def test_a_stage_already_in_the_dem_frame_is_not_shifted(bundles: Path) -> None:
    block = {
        **DATUM_BLOCK,
        "stage_datum": "dem",
        "msl_above_chart_datum_m": None,
        "range_m": None,
    }
    _write_bundle(bundles, {"id": BUNDLE, "tide_datum": block})
    tide = load_tide(BUNDLE)
    assert tide is not None
    assert tide.stage_m.tolist() == list(STAGES)
    assert tide.datum_note is None


def test_a_declared_datum_that_does_not_validate_raises(bundles: Path) -> None:
    """A chart-datum stage with no offset cannot be put in the DEM's frame; say so loudly."""
    broken = {**DATUM_BLOCK, "msl_above_chart_datum_m": None}
    _write_bundle(bundles, {"id": BUNDLE, "tide_datum": broken})
    with pytest.raises(ValidationError, match="msl_above_chart_datum_m"):
        load_tide(BUNDLE)
