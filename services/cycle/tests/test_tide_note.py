"""A cycle's notes carry the tide's datum conversion, checked without running the Twin.

``run_cycle`` takes its tide notes from :func:`varuna_cycle.twin_cycle.tide_notes`, so the notes a
run publishes can be checked on the committed demo bundle's ``tide.csv`` and ``manifest.json``
alone - no radar, no city grid, no three-minute solve.
"""

from __future__ import annotations

import inspect
from datetime import datetime

import numpy as np
import pytest
from varuna_cycle import twin_cycle
from varuna_cycle.twin_cycle import run_cycle, tide_notes
from varuna_schemas.constants import IST
from varuna_schemas.paths import repo_root
from varuna_twin.city import load_tide
from varuna_twin.types import TideSeries

DEMO_BUNDLE = "MUM-2019-07-02"


def _series(source: str, datum_note: str | None) -> TideSeries:
    return TideSeries(
        times=(datetime(2019, 7, 2, 5, 40, tzinfo=IST),),
        stage_m=np.array([0.0]),
        source=source,
        datum_note=datum_note,
    )


def test_the_demo_bundle_tide_puts_its_datum_note_in_the_run_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(repo_root() / "bundles"))
    tide = load_tide(DEMO_BUNDLE)
    assert tide is not None
    assert tide.datum_note is not None, "the committed manifest declares a chart-datum stage"

    notes = tide_notes(tide)

    assert tide.datum_note in notes
    assert "2.70 m above chart datum" in tide.datum_note
    assert "https://psmsl.org/data/obtaining/stations/43.php" in tide.datum_note


def test_run_cycle_takes_its_tide_notes_from_the_seam() -> None:
    """The seam is only worth testing while run_cycle still calls it."""
    assert "tide_notes(tide)" in inspect.getsource(run_cycle)
    assert "tide_notes" in twin_cycle.__all__


def test_an_illustrative_tide_is_labelled_before_its_datum_note() -> None:
    notes = tide_notes(_series("illustrative", "Tide stage converted."))
    assert notes == [
        "Tide series is illustrative, not a published tide table (rule 7).",
        "Tide stage converted.",
    ]


def test_a_sourced_tide_with_no_declared_datum_adds_no_notes() -> None:
    assert tide_notes(_series("tide table https://example.org/tides", None)) == []


def test_no_tide_adds_no_notes() -> None:
    assert tide_notes(None) == []
