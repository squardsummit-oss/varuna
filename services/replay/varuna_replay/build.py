"""Building a bundle on disk: the reconstruction (P2.3-P2.6) and the design storms (P2.8).

``MUM-IDF-25yr`` and ``CHN-IDF-25yr`` need nothing but a city config: a design storm has no
observed event, so it carries a manifest, the two cubes and an empty ground-truth file, and
no gauges, tide, traffic or reports. That makes it the smallest complete exercise of the
bundle contract, which is why :func:`build_design_bundle` is also what proves the writers in
:mod:`varuna_replay.bundle` and the rules in :mod:`varuna_replay.validate` agree.

``MUM-2019-07-02`` is the reconstruction. :func:`build_reconstruction_bundle` is the one
command behind ``varuna bundle build MUM-2019-07-02``: it fits the storm to the sourced
ground-truth pins, calibrates it to a stated share of the one IMD-primary rainfall total
(:mod:`varuna_replay.evidence`), generates the streams (:mod:`varuna_replay.streams`), copies
the curated pins through untouched, and writes a manifest that says which of those numbers
were measured and which were inferred. Nothing here reaches for the network: every input is a
file already in the repository or under ``city/``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from varuna_schemas.constants import CYCLE_PERIOD_MIN, IST
from varuna_schemas.models.bundle import BundleManifest, StormDesign
from varuna_schemas.models.city import CityConfig
from varuna_schemas.paths import bundles_dir, city_config_path, city_dir, repo_root

from varuna_replay import bundle as members
from varuna_replay import evidence, streams
from varuna_replay.design import (
    DEFAULT_DURATION_MIN,
    DEFAULT_PEAK_POSITION,
    DEFAULT_STEP_MIN,
    design_bundle_id,
    design_storm,
    design_storm_field,
)
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.storm import (
    DESIGNER_NOTES,
    WIND_FROM_DEG,
    WIND_SPEED_MS,
    CalibrationResult,
    RadarRender,
    accumulation_mm,
    calibrate,
    radar_dbz,
    rain_field,
    random_storm,
)

log = structlog.get_logger("varuna.replay.build")

RADAR_CADENCE_MIN = 10
"""Radar frames every 10 minutes (SPEC.md 10.2)."""

TRUTH_CADENCE_MIN = 5
"""The truth rain field every 5 minutes (SPEC.md 10.2)."""

NOMINAL_T0 = datetime(2026, 7, 1, 5, 40, tzinfo=IST)
"""A design storm has no date. The clock starts at the same time of day as the demo replay so
the console behaves identically, and the manifest says the date is nominal."""

DESIGN_SEED = 2019
"""The demo seed (rule 8). A design storm uses it only for the radar speckle."""


@dataclass
class BuildResult:
    """What a build wrote, and how long it took."""

    bundle_id: str
    root: Path
    manifest: BundleManifest
    files: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.bundle_id} ({self.manifest.label}) -> {self.root}\n"
            + "\n".join(f"  {name}" for name in self.files)
            + f"\n  {len(self.files)} file(s) in {self.elapsed_s:.1f} s"
        )


def load_city(city: str) -> CityConfig:
    """Load ``services/city/configs/<city>.yaml``.

    The replay service reads the city config directly rather than depending on the city
    pipeline: a design storm needs the bbox, the CRS, the radar domain and the design
    intensity, none of which require the city layers to have been built.
    """
    path = city_config_path(city)
    if not path.is_file():
        available = (
            sorted(p.stem for p in path.parent.glob("*.yaml")) if path.parent.is_dir() else []
        )
        msg = f"No city config at {path}. Configured cities: {', '.join(available) or 'none'}."
        raise FileNotFoundError(msg)
    return CityConfig.from_yaml(path)


def build_design_bundle(
    config: CityConfig,
    *,
    intensity_key: str = "upgraded",
    duration_min: int = DEFAULT_DURATION_MIN,
    peak_position_r: float = DEFAULT_PEAK_POSITION,
    t0: datetime | None = None,
    seed: int = DESIGN_SEED,
    bundles_root: Path | None = None,
) -> BuildResult:
    """Write a complete design-storm bundle for one city.

    Args:
        config: the city whose ``design_intensity_mm_h`` sets the depth.
        intensity_key: ``"upgraded"`` (50 mm/h) or ``"legacy"`` (25 mm/h).
        duration_min: storm duration; the replay window is exactly this long.
        t0: nominal start; defaults to :data:`NOMINAL_T0`.
        bundles_root: the directory the bundle folder is created in, ``bundles/`` by default.
            The folder is always named for the bundle id, because the validator checks that
            a manifest's id matches the folder it sits in (rule B2).
    """
    started = time.perf_counter()
    start = (t0 or NOMINAL_T0).astimezone(IST)
    end = start + timedelta(minutes=duration_min)
    bundle_id = design_bundle_id(config)
    root = (bundles_root or bundles_dir()) / bundle_id
    layout = members.BundleLayout(root=root.resolve())
    layout.root.mkdir(parents=True, exist_ok=True)

    domain = StormDomain.from_city_config(config)
    storm = design_storm(
        config,
        intensity_key=intensity_key,
        duration_min=duration_min,
        step_min=DEFAULT_STEP_MIN,
        peak_position_r=peak_position_r,
    )

    truth_times = step_times_min(0.0, float(duration_min), TRUTH_CADENCE_MIN)
    radar_times = step_times_min(0.0, float(duration_min), RADAR_CADENCE_MIN)
    truth = design_storm_field(storm, domain, truth_times)
    radar_rain = design_storm_field(storm, domain, radar_times)
    render = RadarRender(seed=seed)
    frames = radar_dbz(radar_rain, domain, render)
    total_mm = float(accumulation_mm(truth, TRUTH_CADENCE_MIN, rule="left").mean())
    peak_block_mm_h = max(storm.hyetograph_mm_h)

    written: list[str] = []
    written.append(
        layout.relative(
            members.write_cube(
                layout.truth,
                truth,
                variable=members.TRUTH_VARIABLE,
                times_min=truth_times,
                domain=domain,
                t0=start,
                step_min=TRUTH_CADENCE_MIN,
                units="mm/h",
                attrs={
                    "bundle": bundle_id,
                    "label": "Design storm",
                    "basis": storm.basis,
                    "integration_rule": "left (each instant holds until the next)",
                },
            )
        )
    )
    written.append(
        layout.relative(
            members.write_cube(
                layout.radar,
                frames,
                variable=members.RADAR_VARIABLE,
                times_min=radar_times,
                domain=domain,
                t0=start,
                step_min=RADAR_CADENCE_MIN,
                units="dBZ",
                attrs={
                    "bundle": bundle_id,
                    "label": "Design storm",
                    "rendering": (
                        f"Marshall-Palmer inverse Z = 200 R^1.6, log-normal speckle sigma "
                        f"{render.speckle_sigma}, {render.coverage_radius_km:.0f} km coverage "
                        f"circle, {render.dbz_class_width:.0f} dBZ classes; NaN means no echo"
                    ),
                },
            )
        )
    )
    written.append(
        layout.relative(
            members.write_ground_truth(
                layout.ground_truth,
                [],
                name=f"{bundle_id} ground truth",
                description=(
                    "Empty on purpose: a design storm is a synthetic scenario, not an event "
                    "that happened, so there is nothing to observe and nothing to source."
                ),
            )
        )
    )

    manifest = BundleManifest(
        id=bundle_id,
        city=config.id,
        label="Design storm",
        t0=start,
        t1=end,
        cadences={
            "radar": RADAR_CADENCE_MIN,
            "truth": TRUTH_CADENCE_MIN,
            "cycle": CYCLE_PERIOD_MIN,
        },
        radar_domain=config.radar_domain,
        aoi=config.bbox,
        sources=[],
        seed=seed,
        synthetic_notes=[
            "Design storm: a synthetic scenario, not a recorded event.",
            f"Rain depth comes from the drainage-norm design intensity "
            f"({storm.intensity_mm_h:.0f} mm/h), not from a published "
            "intensity-duration-frequency curve.",
            "Radar frames are rendered from the rain field, not decoded from an archive.",
            "The clock is nominal: a design storm has no date.",
            f"The {peak_block_mm_h:.0f} mm/h peak block follows from the stated shape "
            f"(b = {storm.shape_b_min:.0f} min, c = {storm.shape_c:.2f}); another Chicago "
            "shape holding the same depth would peak differently.",
        ],
        description=(
            f"Chicago hyetograph, {duration_min} minutes, peak at "
            f"{peak_position_r * 100:.0f} % of the duration, "
            f"{storm.total_depth_mm:.0f} mm total over {config.name}, peaking at "
            f"{peak_block_mm_h:.0f} mm/h for {storm.step_min} minutes."
        ),
        ground_truth_n=0,
        design_storm=storm,
    )
    written.append(layout.relative(members.write_manifest(layout.manifest, manifest)))

    result = BuildResult(
        bundle_id=bundle_id,
        root=layout.root,
        manifest=manifest,
        files=written,
        elapsed_s=time.perf_counter() - started,
    )
    log.info(
        "bundle.built",
        bundle=bundle_id,
        city=config.id,
        total_depth_mm=round(total_mm, 3),
        frames=int(np.asarray(frames).shape[0]),
        elapsed_s=round(result.elapsed_s, 2),
    )
    return result


# ============================================================ the reconstruction (P2.3-P2.6)
RECONSTRUCTION_CELLS = 8
"""Convective cells in the demo storm. Eight is the designer's default (SPEC.md 10.2) and it
is also what keeps every scaled cell peak inside the 40-120 mm/h range that section states:
more cells share the calibrated depth out into peaks below 40 mm/h, fewer push them above."""

PIN_CLUSTERS = 3
"""How many groups the sourced pins are gathered into before the cells are aimed at them. The
pins run 12 km north to south along the AOI's spine - Hindmata and King's Circle in the south,
Sion, Kurla and Bandra in the middle, Vakola, Milan subway and Andheri in the north - and one
aim point could not cover that; three do, without pretending to resolve more structure than
29 points can carry."""

RESEARCH_DIR = "docs/research"
GROUND_TRUTH_DRAFT = "ground_truth_MUM-2019-07-02.draft.geojson"
GAUGE_ROSTER = "bmc_aws_stations.json"


@dataclass(frozen=True, slots=True)
class ReconstructionInputs:
    """Everything the reconstruction reads, so a test can hand it small ones.

    All four are files that already exist: two curated research files under ``docs/research``
    and two products of ``make city``. Nothing is fetched.
    """

    config: CityConfig
    pins: list[dict[str, Any]]
    gauge_sites: list[streams.GaugeSite]
    segments: streams.SegmentTable
    hotspots: list[dict[str, Any]]


def load_reconstruction_inputs(config: CityConfig) -> ReconstructionInputs:
    """Load the sourced pins, the gauge roster, the road segments and the hotspot register."""
    research = repo_root() / RESEARCH_DIR
    city = city_dir(config.id)
    segments_path = city / "segments.parquet"
    if not segments_path.is_file():
        msg = (
            f"No road segments at {segments_path}. Run 'make city CITY={config.id}' first: the "
            "traffic feed is generated on the city's own segments."
        )
        raise FileNotFoundError(msg)
    return ReconstructionInputs(
        config=config,
        pins=streams.load_ground_truth_pins(research / GROUND_TRUTH_DRAFT),
        gauge_sites=streams.load_gauge_sites(research / GAUGE_ROSTER),
        segments=streams.load_segments(segments_path, config.crs),
        hotspots=streams.load_hotspots(city / "hotspots.geojson"),
    )


def latitude_clusters(y: np.ndarray, k: int, *, iterations: int = 100) -> np.ndarray:
    """Group northings into ``k`` clusters, south to north, with a deterministic 1-D k-means.

    Quantile initialisation and a fixed iteration cap mean there is no random draw here at
    all: the same pins always give the same clusters, whatever the seed.
    """
    values = np.asarray(y, dtype=float)
    centres = np.array([np.quantile(values, (index + 0.5) / k) for index in range(k)])
    for _ in range(iterations):
        labels = np.argmin(np.abs(values[:, None] - centres[None, :]), axis=1)
        moved = np.array(
            [
                values[labels == index].mean() if (labels == index).any() else centres[index]
                for index in range(k)
            ]
        )
        if np.allclose(moved, centres):
            break
        centres = moved
    return np.argmin(np.abs(values[:, None] - np.sort(centres)[None, :]), axis=1)


def allocate_cells(counts: list[int], n_cells: int) -> list[int]:
    """Share ``n_cells`` between clusters in proportion to the pins in each, at least one each."""
    total = sum(counts) or 1
    alloc = [max(1, round(n_cells * count / total)) for count in counts]
    while sum(alloc) > n_cells and max(alloc) > 1:
        alloc[alloc.index(max(alloc))] -= 1
    while sum(alloc) < n_cells:
        alloc[alloc.index(min(alloc))] += 1
    return alloc


def fit_storm_to_pins(
    domain: StormDomain,
    pins: list[dict[str, Any]],
    *,
    seed: int,
    window_min: float,
    n_cells: int = RECONSTRUCTION_CELLS,
    clusters: int = PIN_CLUSTERS,
) -> StormDesign:
    """Draw a storm whose cells cross the places the sourced pins record.

    The pins are the record of where the water was, so they are what the storm is aimed at:
    the cells are drawn by :func:`varuna_replay.storm.random_storm` - the same seeded designer
    every bundle uses - once per pin cluster, aimed at that cluster's centroid and allocated in
    proportion to its pins, and the draws are merged into one design. Sub-seeds are
    ``seed * 1000 + cluster``, so the whole field still follows from the bundle seed.

    This is a *fitted placement*, not an observation of where the cells were: no radar frame
    for the event is retrievable. The manifest says so.
    """
    x, y = streams.project(
        [float(pin["lon"]) for pin in pins], [float(pin["lat"]) for pin in pins], domain.crs
    )
    labels = latitude_clusters(y, clusters)
    counts = [int((labels == index).sum()) for index in range(clusters)]
    allocation = allocate_cells(counts, n_cells)

    cells = []
    background: float | None = None
    for index, count in enumerate(allocation):
        member = labels == index
        aim = (float(x[member].mean()), float(y[member].mean()))
        drawn = random_storm(
            domain,
            seed=seed * 1000 + index,
            window_min=window_min,
            aoi_center_m=aim,
            n_cells=count,
        )
        if background is None:
            background = drawn.background_mm_h
        for cell in drawn.cells:
            cells.append(cell.model_copy(update={"id": f"cell-{len(cells) + 1:02d}"}))

    notes = [
        *DESIGNER_NOTES,
        f"Cell tracks are fitted to the ground truth: the {len(pins)} sourced pins inside the "
        f"area of interest are grouped into {clusters} latitudinal clusters "
        f"({', '.join(str(count) for count in counts)} pins) and the {n_cells} cells are "
        f"aimed at those centroids ({', '.join(str(item) for item in allocation)} cells each). "
        "That is a placement fitted to where water was reported, not a record of where the "
        "cells were: no radar frame for this event is publicly retrievable.",
    ]
    return StormDesign(
        crs=domain.crs,
        seed=seed,
        background_mm_h=background if background is not None else 0.0,
        wind_from_deg=WIND_FROM_DEG,
        wind_speed_ms=WIND_SPEED_MS,
        cells=cells,
        notes=notes,
    )


def _observatory_accumulations(
    accumulation: np.ndarray, domain: StormDomain, sites: list[streams.GaugeSite]
) -> dict[str, float]:
    """Accumulation at the two IMD observatories, by the same pixel sampling the pins use.

    Only the two IMD observatories are named, because the calibration target is a share of
    Santacruz's own 24-hour total; the municipal gauges are sited at their host facility's
    OpenStreetMap position and make no claim about a published total.
    """
    wanted = {"santacruz": "santacruz", "colaba": "colaba"}
    found: dict[str, float] = {}
    for site in sites:
        key = next((k for k in wanted if k in site.name.lower()), None)
        if key is None or key in found or site.lon is None or site.lat is None:
            continue
        x, y = streams.project([float(site.lon)], [float(site.lat)], domain.crs)
        row, col = streams.pixel_index(domain, x, y)
        found[key] = float(accumulation[row, col][0])
    return found


def _pin_accumulation_ratio(
    accumulation: np.ndarray, domain: StormDomain, pins: list[dict[str, Any]], mask: np.ndarray
) -> float:
    """Mean accumulation over the pins divided by the mean over the area of interest."""
    x, y = streams.project(
        [float(pin["lon"]) for pin in pins], [float(pin["lat"]) for pin in pins], domain.crs
    )
    row, col = streams.pixel_index(domain, x, y)
    return float(accumulation[row, col].mean() / accumulation[mask].mean())


def build_reconstruction_bundle(
    inputs: ReconstructionInputs,
    *,
    seed: int = evidence.SEED,
    t0: datetime | None = None,
    window_min: int = evidence.WINDOW_MIN,
    bundles_root: Path | None = None,
    bundle_id: str = evidence.BUNDLE_ID,
    n_cells: int = RECONSTRUCTION_CELLS,
) -> BuildResult:
    """Write ``bundles/MUM-2019-07-02/``: the reconstructed replay of 2 July 2019.

    The order matters and is the order the honesty argument runs in: fit the storm to the
    pins, calibrate it to a stated share of the one measured rainfall total, render the truth
    and radar cubes from the calibrated field, sample the streams from that same field, carry
    the pins through untouched, and only then write a manifest whose ``calibration_basis``
    reports what was achieved rather than what was hoped for.

    Args:
        inputs: the sourced pins, gauge sites, road segments and hotspots.
        seed: the bundle seed; 2019 for the demo (rule 8).
        t0: window start; ADR-0007's 05:40 IST on 2 July 2019 by default.
        window_min: window length; 240 minutes by default, closing at 09:40.
        bundles_root: directory the bundle folder is created in, ``bundles/`` by default.
    """
    started = time.perf_counter()
    config = inputs.config
    start = (t0 or evidence.T0).astimezone(IST)
    end = start + timedelta(minutes=window_min)
    root = (bundles_root or bundles_dir()) / bundle_id
    layout = members.BundleLayout(root=root.resolve())
    layout.root.mkdir(parents=True, exist_ok=True)

    domain = StormDomain.from_city_config(config)
    mask = domain.aoi_mask(config.bbox)

    design = fit_storm_to_pins(
        domain, inputs.pins, seed=seed, window_min=float(window_min), n_cells=n_cells
    )
    calibration = calibrate(
        design,
        domain,
        mask=mask,
        target_mm=evidence.window_target_mm(),
        t0_min=0.0,
        t1_min=float(window_min),
        step_min=TRUTH_CADENCE_MIN,
        window_label=evidence.WINDOW_LABEL,
        tolerance_frac=evidence.CALIBRATION_TOLERANCE_FRAC,
    )
    design = calibration.design

    truth_times = step_times_min(0.0, float(window_min), TRUTH_CADENCE_MIN)
    radar_times = step_times_min(0.0, float(window_min), RADAR_CADENCE_MIN)
    truth = rain_field(design, domain, truth_times)
    render = RadarRender.from_design(design)
    frames = radar_dbz(rain_field(design, domain, radar_times), domain, render)
    accumulation = accumulation_mm(truth, TRUTH_CADENCE_MIN)
    pin_ratio = _pin_accumulation_ratio(accumulation, domain, inputs.pins, mask)

    # Where the window crosses the 08:30 edge of the 24-hour total it is a share of. The
    # index is found rather than assumed, so a different window or cadence still splits at
    # the right step.
    minutes_to_boundary = int((evidence.SANTACRUZ_24H_ENDS_IST - start).total_seconds() // 60)
    boundary_step = int(np.argmin(np.abs(np.asarray(truth_times) - float(minutes_to_boundary))))
    before = accumulation_mm(truth[: boundary_step + 1], TRUTH_CADENCE_MIN)
    after = accumulation_mm(truth[boundary_step:], TRUTH_CADENCE_MIN)
    boundary = _boundary_split(
        float(before[mask].mean()), float(after[mask].mean()), minutes_to_boundary
    )
    stations = _station_pixels(_observatory_accumulations(accumulation, domain, inputs.gauge_sites))

    gauges = streams.gauge_rows(inputs.gauge_sites, truth, truth_times, domain, start, seed=seed)
    # The tide alone runs past `end`: it is the boundary the last cycle forecasts against, out
    # to its three-hour horizon (`members.TIDE_LOOKAHEAD_MIN`), not a stream observed in the
    # window. The rows inside the window are the same ones a window-only series would hold.
    tide = streams.tide_rows(
        start,
        float(window_min + members.TIDE_LOOKAHEAD_MIN),
        high_water_m=evidence.TIDE_HIGH_WATER_M,
        high_water_at=evidence.TIDE_HIGH_WATER_IST,
        period_min=evidence.TIDE_PERIOD_MIN,
    )
    snaps, unsnapped = streams.snap_pins(
        inputs.segments, inputs.pins, config.crs, start, float(window_min)
    )
    flood_anomalies = streams.pin_anomalies(inputs.segments, snaps)
    confounders = streams.confounder_anomalies(
        inputs.segments,
        float(window_min),
        seed=seed,
        exclude=[anomaly.segment_index for anomaly in flood_anomalies],
    )
    traffic = streams.traffic_frame(
        inputs.segments,
        [*flood_anomalies, *confounders],
        start,
        float(window_min),
        seed=seed,
    )
    reports = [
        *streams.synthetic_reports(inputs.hotspots, truth, truth_times, domain, start, seed=seed),
        *streams.pin_reports(inputs.pins, start, float(window_min)),
    ]
    reports.sort(key=lambda row: (row["ts"], str(row["id"])))
    synthetic_report_count = sum(1 for row in reports if row["synthetic"])
    features = streams.ground_truth_features(inputs.pins)

    written: list[str] = []
    written.append(
        layout.relative(
            members.write_cube(
                layout.truth,
                truth,
                variable=members.TRUTH_VARIABLE,
                times_min=truth_times,
                domain=domain,
                t0=start,
                step_min=TRUTH_CADENCE_MIN,
                units="mm/h",
                attrs={
                    "bundle": bundle_id,
                    "label": "Reconstructed replay",
                    "basis": calibration.summary(),
                    "integration_rule": "trapezoid (instants, 5 minutes apart)",
                },
            )
        )
    )
    written.append(
        layout.relative(
            members.write_cube(
                layout.radar,
                frames,
                variable=members.RADAR_VARIABLE,
                times_min=radar_times,
                domain=domain,
                t0=start,
                step_min=RADAR_CADENCE_MIN,
                units="dBZ",
                attrs={
                    "bundle": bundle_id,
                    "label": "Reconstructed replay",
                    "basis": evidence.RADAR_BASIS,
                },
            )
        )
    )
    written.append(layout.relative(members.write_gauges(layout.gauges, gauges)))
    written.append(layout.relative(members.write_tide(layout.tide, tide)))
    written.append(layout.relative(members.write_traffic(layout.traffic, traffic)))
    written.append(layout.relative(members.write_reports(layout.reports, reports)))
    written.append(
        layout.relative(
            members.write_ground_truth(
                layout.ground_truth,
                features,
                name=f"{bundle_id} ground truth",
                description=(
                    f"{len(features)} sourced pins inside {config.aoi_id}, curated in "
                    f"{RESEARCH_DIR}/{GROUND_TRUTH_DRAFT} and audited in "
                    f"{RESEARCH_DIR}/REVIEW.md. Every pin carries the URL it was read from and "
                    "the time uncertainty of its source; depth_cm is null on all of them "
                    "because no cached source states a depth in centimetres."
                ),
            )
        )
    )

    manifest = BundleManifest(
        id=bundle_id,
        city=config.id,
        label="Reconstructed replay",
        t0=start,
        t1=end,
        cadences={
            "radar": RADAR_CADENCE_MIN,
            "truth": TRUTH_CADENCE_MIN,
            "gauges": streams.GAUGE_CADENCE_MIN,
            "tide": streams.TIDE_CADENCE_MIN,
            "traffic": streams.TRAFFIC_CADENCE_MIN,
            "reports": CYCLE_PERIOD_MIN,
            "cycle": CYCLE_PERIOD_MIN,
        },
        radar_domain=config.radar_domain,
        aoi=config.bbox,
        sources=evidence.sources(),
        seed=seed,
        synthetic_notes=evidence.synthetic_notes(
            achieved_mm=calibration.achieved_mm,
            gauges=len(inputs.gauge_sites),
            pins_in_window=len(reports) - synthetic_report_count,
            pins_total=len(features),
            traffic_segments=len(inputs.segments),
            confounders=len(confounders),
            synthetic_reports=synthetic_report_count,
        ),
        event_date=evidence.EVENT_DATE,
        description=evidence.DESCRIPTION,
        tide_source="illustrative",
        tide_datum=evidence.tide_datum(),
        ground_truth_n=len(features),
        calibration=_calibration_numbers(
            calibration,
            pin_ratio=pin_ratio,
            pins=len(inputs.pins),
            boundary=boundary,
            stations=stations,
        ),
        calibration_basis="\n\n".join(
            [
                evidence.calibration_basis(
                    achieved_mm=calibration.achieved_mm,
                    error_frac=calibration.relative_error,
                    pin_ratio=pin_ratio,
                    clusters=PIN_CLUSTERS,
                    pins=len(inputs.pins),
                    minutes_inside=int(boundary["window_minutes_inside_24h_total"]),
                    before_mm=boundary["accumulation_before_0830_mm"],
                    after_mm=boundary["accumulation_after_0830_mm"],
                    santacruz_pixel_mm=stations.get(
                        "santacruz_pixel_accumulation_mm", float("nan")
                    ),
                    colaba_pixel_mm=stations.get("colaba_pixel_accumulation_mm", float("nan")),
                ),
                f"TIDE. {evidence.TIDE_BASIS}",
                *([f"NOTE. {note}" for note in calibration.notes]),
            ]
        ),
        storm=design,
    )
    written.append(layout.relative(members.write_manifest(layout.manifest, manifest)))

    result = BuildResult(
        bundle_id=bundle_id,
        root=layout.root,
        manifest=manifest,
        files=written,
        elapsed_s=time.perf_counter() - started,
    )
    log.info(
        "bundle.built",
        bundle=bundle_id,
        city=config.id,
        achieved_mm=round(calibration.achieved_mm, 2),
        target_mm=calibration.target_mm,
        pin_accumulation_ratio=round(pin_ratio, 3),
        gauges=len(inputs.gauge_sites),
        pins=len(features),
        reports=len(reports),
        traffic_rows=len(traffic),
        unsnapped_pins=len(unsnapped),
        elapsed_s=round(result.elapsed_s, 2),
    )
    return result


def _boundary_split(
    accumulation_before: float, accumulation_after: float, minutes_inside: int
) -> dict[str, float]:
    """How the window sits across the 08:30 edge of the 24-hour total it is a share of.

    The 375.2 mm Santacruz figure covers 08:30 to 08:30, and the replay window runs
    05:40-09:40, so only 170 of its 240 minutes are inside the total the target is derived
    from. The remaining 70 fall in the *next* day's total, for which no primary figure was
    sourced. Publishing the share without publishing the overlap invites the reader to assume
    the window sits inside the number it is a fraction of, which it does not.
    """
    return {
        "window_minutes_inside_24h_total": float(minutes_inside),
        "accumulation_before_0830_mm": round(accumulation_before, 3),
        "accumulation_after_0830_mm": round(accumulation_after, 3),
    }


def _station_pixels(values: dict[str, float]) -> dict[str, float]:
    """What the designed field accumulates at the observatories the target is named after.

    A gauge total is a point value and this one is imposed as an area mean over MUM-CENTRAL,
    so the station the calibration is named after does not read the target. Publishing both
    is the difference between a stated simplification and a number that quietly disagrees
    with its own provenance.
    """
    return {f"{name}_pixel_accumulation_mm": round(value, 3) for name, value in values.items()}


def _calibration_numbers(
    calibration: CalibrationResult,
    *,
    pin_ratio: float,
    pins: int,
    boundary: dict[str, float] | None = None,
    stations: dict[str, float] | None = None,
) -> dict[str, float]:
    """The manifest's ``calibration`` block: every number the basis text argues about."""
    return {
        **(boundary or {}),
        **(stations or {}),
        "santacruz_24h_total_mm": evidence.SANTACRUZ_24H_MM,
        "santacruz_24h_mean_mm_h": evidence.daily_mean_mm_h(),
        "window_share_of_daily_total": evidence.WINDOW_SHARE_OF_DAILY,
        "window_target_mm": calibration.target_mm,
        "window_achieved_mm": round(calibration.achieved_mm, 3),
        "window_relative_error": round(calibration.relative_error, 6),
        "window_tolerance_frac": calibration.tolerance_frac,
        "window_mean_mm_h": round(calibration.achieved_mm / (evidence.WINDOW_MIN / 60.0), 3),
        "uniform_share_mm": evidence.uniform_share_mm(),
        "persistence_share_mm": evidence.persistence_share_mm(),
        "burst_share_mm": evidence.burst_share_mm(),
        "intensity_scale": calibration.intensity_scale,
        "background_contribution_mm": round(calibration.background_mm, 3),
        "cell_peak_min_mm_h": calibration.peak_mm_h_range[0],
        "cell_peak_max_mm_h": calibration.peak_mm_h_range[1],
        "pin_accumulation_ratio": round(pin_ratio, 4),
        "ground_truth_pins_fitted": float(pins),
        "tide_high_water_m": evidence.TIDE_HIGH_WATER_M,
        "tide_forecast_not_used_m": evidence.TIDE_FORECAST_M,
    }


def build_bundle(
    bundle_id: str, *, bundles_root: Path | None = None, **options: Any
) -> BuildResult:
    """Build any bundle by id: the reconstruction, or a city's design storm.

    This is what ``varuna bundle build <id>`` calls, so the CLI never has to know which kind of
    bundle an id names.
    """
    if bundle_id == evidence.BUNDLE_ID:
        return build_reconstruction_bundle(
            load_reconstruction_inputs(load_city(evidence.CITY)),
            bundles_root=bundles_root,
            **options,
        )
    for city in ("mumbai", "chennai"):
        config = load_city(city)
        if design_bundle_id(config) == bundle_id:
            return build_design_bundle(config, bundles_root=bundles_root, **options)
    known = [evidence.BUNDLE_ID, *(design_bundle_id(load_city(c)) for c in ("mumbai", "chennai"))]
    msg = f"No builder for bundle {bundle_id!r}. Known bundles: {', '.join(known)}."
    raise KeyError(msg)


__all__ = [
    "DESIGN_SEED",
    "GAUGE_ROSTER",
    "GROUND_TRUTH_DRAFT",
    "NOMINAL_T0",
    "PIN_CLUSTERS",
    "RADAR_CADENCE_MIN",
    "RECONSTRUCTION_CELLS",
    "RESEARCH_DIR",
    "TRUTH_CADENCE_MIN",
    "BuildResult",
    "ReconstructionInputs",
    "allocate_cells",
    "build_bundle",
    "build_design_bundle",
    "build_reconstruction_bundle",
    "fit_storm_to_pins",
    "latitude_clusters",
    "load_city",
    "load_reconstruction_inputs",
]
