"""Fixtures for the replay tests: a small storm domain and a complete little bundle.

Everything shared is a fixture, because pytest runs this suite with
``--import-mode=importlib`` and the test modules are not a package, so ``from conftest import
...`` would not resolve.

The bundle the factory writes is deliberately the *reconstruction* shape - every member the
contract demands - because that is what exercises every rule in
:mod:`varuna_replay.validate`. It is tiny (a 20 km domain and a one-hour window) so the whole
suite stays fast.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from varuna_replay import bundle as members
from varuna_replay.build import RADAR_CADENCE_MIN, TRUTH_CADENCE_MIN
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.storm import RadarRender, radar_dbz, rain_field, random_storm
from varuna_replay.validate import GROUND_TRUTH_FLOOR
from varuna_schemas.constants import IST
from varuna_schemas.models.bundle import BundleManifest, BundleSource
from varuna_schemas.models.city import RadarDomain
from varuna_schemas.models.common import BBox

T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
"""The replay window opens at 05:40 IST on 2 July 2019 (ADR-0007)."""

WINDOW_MIN = 60
AOI = BBox(min_lon=72.815, min_lat=18.995, max_lon=72.905, max_lat=19.135)
TEST_RADAR_DOMAIN = RadarDomain(center_lon=72.86, center_lat=19.065, size_km=20.0, res_m=500.0)
BUNDLE_ID = "MUM-TEST-2019"

CADENCES = {
    "radar": RADAR_CADENCE_MIN,
    "truth": TRUTH_CADENCE_MIN,
    "gauges": 15,
    "tide": 15,
    "traffic": 5,
    "reports": 5,
    "cycle": 5,
}

CALIBRATION_BASIS = (
    "Inferred, not measured: no hourly gauge trace covers this window, so the accumulation "
    "was scaled from the 24-hour Santacruz total reported by IMD."
)


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def aoi() -> BBox:
    return AOI


@pytest.fixture
def domain() -> StormDomain:
    """A 20 km, 500 m domain over the Mumbai area of interest (40 x 40 px)."""
    return StormDomain.from_radar_domain(TEST_RADAR_DOMAIN, 32643)


def _stamps(step_min: int, window_min: int = WINDOW_MIN) -> list[datetime]:
    return [T0 + timedelta(minutes=m) for m in range(0, window_min + 1, step_min)]


def _pin(**overrides: Any) -> dict[str, Any]:
    """One sourced ground-truth feature inside the area of interest."""
    properties: dict[str, Any] = {
        "id": "MUM19-01",
        "ts": "2019-07-02T08:07:00+05:30",
        "ts_uncertainty_min": 15,
        "name": "Hindmata junction",
        "kind": "log",
        "text": "BMC log: waterlogging, traffic diverted",
        "source_url": "https://example.org/bmc-log",
        "synthetic": False,
    }
    properties.update(overrides)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [72.8421396, 19.010099]},
        "properties": properties,
    }


def _write_full_bundle(
    root: Path,
    domain: StormDomain,
    *,
    bundle_id: str = BUNDLE_ID,
    ground_truth: list[dict[str, Any]] | None = None,
    ground_truth_n: int | None = None,
    gauges_rows: list[dict[str, Any]] | None = None,
) -> BundleManifest:
    """Write every member of a reconstruction-shaped bundle and return its manifest."""
    layout = members.BundleLayout(root=root)
    layout.root.mkdir(parents=True, exist_ok=True)
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=2)

    truth_times = step_times_min(0.0, float(WINDOW_MIN), TRUTH_CADENCE_MIN)
    radar_times = step_times_min(0.0, float(WINDOW_MIN), RADAR_CADENCE_MIN)
    members.write_cube(
        layout.truth,
        rain_field(design, domain, truth_times),
        variable=members.TRUTH_VARIABLE,
        times_min=truth_times,
        domain=domain,
        t0=T0,
        step_min=TRUTH_CADENCE_MIN,
        units="mm/h",
    )
    members.write_cube(
        layout.radar,
        radar_dbz(rain_field(design, domain, radar_times), domain, RadarRender.from_design(design)),
        variable=members.RADAR_VARIABLE,
        times_min=radar_times,
        domain=domain,
        t0=T0,
        step_min=RADAR_CADENCE_MIN,
        units="dBZ",
    )

    rows = gauges_rows
    if rows is None:
        rows = [
            {
                "ts": stamp,
                "station_id": station,
                "lat": 19.09,
                "lon": 72.85,
                "mm_5min": 2.5,
                "synthetic": True,
            }
            for stamp in _stamps(CADENCES["gauges"])
            for station in ("santacruz", "colaba")
        ]
    members.write_gauges(layout.gauges, rows)
    # The tide runs past t1 to the last cycle's forecast horizon, as the reconstruction's does
    # (`members.TIDE_LOOKAHEAD_MIN`); the clock still schedules only the window's stages.
    members.write_tide(
        layout.tide,
        [
            {"ts": stamp, "stage_m": 2.4, "source": "illustrative"}
            for stamp in _stamps(CADENCES["tide"], WINDOW_MIN + members.TIDE_LOOKAHEAD_MIN)
        ],
    )
    members.write_traffic(
        layout.traffic,
        [
            {
                "ts": stamp,
                "segment_id": segment,
                "kmh": 18.0,
                "baseline_kmh": 26.0,
                "synthetic": True,
            }
            for stamp in _stamps(CADENCES["traffic"])
            for segment in ("seg-1", "seg-2")
        ],
    )
    members.write_reports(
        layout.reports,
        [
            {
                "ts": T0 + timedelta(minutes=20),
                "lat": 19.012,
                "lon": 72.841,
                "depth_hint": "knee",
                "text": "Water above the kerb at Hindmata junction",
                "synthetic": True,
            }
        ],
    )
    # SPEC.md 10.2 sets a floor of ten sourced in-AOI pins on a reconstruction, and rule B13
    # enforces it, so the default fixture has to clear the floor to be a valid reconstruction
    # at all. A single pin made every test that asserts `report.ok` depend on the floor never
    # being checked. Ids differ so a rule keyed on them can still tell them apart.
    pins = (
        ground_truth
        if ground_truth is not None
        else [_pin(id=f"MUM19-{n:02d}") for n in range(1, GROUND_TRUTH_FLOOR + 1)]
    )
    members.write_ground_truth(
        layout.ground_truth,
        pins,
        name=f"{bundle_id} ground truth",
        description="Sourced pins for the test bundle.",
    )

    manifest = BundleManifest(
        id=bundle_id,
        city="mumbai",
        label="Reconstructed replay",
        t0=T0,
        t1=T0 + timedelta(minutes=WINDOW_MIN),
        cadences=CADENCES,
        radar_domain=TEST_RADAR_DOMAIN,
        aoi=AOI,
        sources=[
            BundleSource(
                name="IMD Santacruz 24-hour total, 2 July 2019",
                url="https://mausam.imd.gov.in/mumbai/mcdata/Highest_Scz_July.gif",
                note="375.2 mm for the 24 hours ending 08:30 IST",
                used_for="Window accumulation target",
            )
        ],
        seed=2019,
        synthetic_notes=["Radar frames: storm-designer reconstruction."],
        ground_truth_n=len(pins) if ground_truth_n is None else ground_truth_n,
        calibration={"window_accumulation_mm": 40.0},
        calibration_basis=CALIBRATION_BASIS,
        storm=design,
    )
    members.write_manifest(layout.manifest, manifest)
    return manifest


@pytest.fixture
def pin() -> Callable[..., dict[str, Any]]:
    """Factory for a ground-truth feature, so a test can break one field at a time."""
    return _pin


@pytest.fixture
def make_bundle() -> Callable[..., BundleManifest]:
    """Factory that writes a complete reconstruction-shaped bundle into a folder."""
    return _write_full_bundle


@pytest.fixture
def full_bundle(tmp_path: Path, domain: StormDomain) -> Path:
    """A complete, valid reconstruction-shaped bundle on disk. Returns its folder."""
    root = tmp_path / BUNDLE_ID
    _write_full_bundle(root, domain)
    return root


def _digest(root: Path) -> str:
    """A hash of every file under ``root``, for the byte-identical determinism tests."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


@pytest.fixture
def digest() -> Callable[[Path], str]:
    return _digest


def _weighted_centroid(frame: np.ndarray, domain: StormDomain) -> tuple[float, float]:
    """Intensity-weighted centre of one frame, in the domain's metric coordinates."""
    x, y = domain.cell_centres()
    total = float(frame.sum())
    return (float((frame * x).sum() / total), float((frame * y).sum() / total))


@pytest.fixture
def centroid() -> Callable[[np.ndarray, StormDomain], tuple[float, float]]:
    return _weighted_centroid
