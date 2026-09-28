"""``varuna bundle validate``: does this folder honour the bundle contract?

The rules are the ones SPEC.md 10.2 and rules 6-8 of section 0 impose. Each has a stable
id so a finding can be argued with, and every finding names the file it is about:

===== ===================== =====================================================================
Rule  Name                  What it checks
===== ===================== =====================================================================
B1    manifest.present      the folder exists and carries ``manifest.json``
B2    manifest.valid        the manifest validates against ``BundleManifest``
B3    members.present       every member the bundle's label requires is on disk
B4    window.cadence        cubes and streams cover ``t0``-``t1`` at the declared cadences
                            (the tide runs on to ``t1`` + 3 h, the last cycle's horizon)
B5    grid.agreement        the radar and truth cubes share one CRS, grid and transform
B6    groundtruth.sourced   every pin carries a ``source_url`` and none is marked synthetic
B7    streams.flagged       every synthetic stream says so, in the data and in the manifest
B8    sources.cited         a reconstruction cites the public sources it was built from
B9    honesty.basis         calibrated or design storms carry their labelled assumption
B10   groundtruth.count     ``ground_truth_n`` matches the pins inside the area of interest
B11   columns.present       every stream carries the columns SPEC.md 10.2 fixes for it
B12   cubes.units           the radar cube is dBZ and the truth cube is mm/h
B13   groundtruth.floor     a reconstruction carries the ten sourced in-AOI pins 10.2 demands
===== ===================== =====================================================================

Findings are errors, warnings or notes. Errors fail the bundle; warnings are things a human
should look at (a stream a design storm does not need, a provenance key nobody declared).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import numpy as np
import structlog
from pydantic import ValidationError
from varuna_schemas.models.bundle import BundleManifest, GroundTruthPin
from varuna_schemas.settings import get_settings

from varuna_replay.bundle import (
    GAUGES_COLUMNS,
    GAUGES_CSV,
    GROUND_TRUTH_EXTRA_KEYS,
    GROUND_TRUTH_GEOJSON,
    MANIFEST_NAME,
    RADAR_VARIABLE,
    RADAR_ZARR,
    REPORT_REQUIRED_KEYS,
    REPORTS_JSONL,
    TIDE_COLUMNS,
    TIDE_CSV,
    TIDE_LOOKAHEAD_MIN,
    TRAFFIC_COLUMNS,
    TRAFFIC_PARQUET,
    TRUTH_VARIABLE,
    TRUTH_ZARR,
    BundleLayout,
    CubeInfo,
    read_cube_info,
)

log = structlog.get_logger("varuna.replay.validate")

Level = Literal["error", "warning", "note"]

GROUND_TRUTH_FLOOR = 10
"""Sourced pins a reconstruction must carry inside its AOI (SPEC.md 10.2)."""

RULES: dict[str, str] = {
    "B1": "manifest.present",
    "B2": "manifest.valid",
    "B3": "members.present",
    "B4": "window.cadence",
    "B5": "grid.agreement",
    "B6": "groundtruth.sourced",
    "B7": "streams.flagged",
    "B8": "sources.cited",
    "B9": "honesty.basis",
    "B10": "groundtruth.count",
    "B11": "columns.present",
    "B12": "cubes.units",
    "B13": "groundtruth.floor",
}

REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "Reconstructed replay": (
        MANIFEST_NAME,
        RADAR_ZARR,
        TRUTH_ZARR,
        GAUGES_CSV,
        TIDE_CSV,
        TRAFFIC_PARQUET,
        REPORTS_JSONL,
        GROUND_TRUTH_GEOJSON,
    ),
    "Design storm": (MANIFEST_NAME, RADAR_ZARR, TRUTH_ZARR),
}
"""What each label must carry. A design storm has no observed event, so it needs no gauges,
tide, traffic, reports or ground truth; those are reported as notes when they are absent."""

TIME_TOLERANCE_S = 1.0
"""Timestamps are compared to the second: cadences are whole minutes."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the validator noticed, named by rule and by file."""

    rule: str
    level: Level
    file: str
    message: str

    def render(self) -> str:
        return f"{self.file}: {self.message}"


@dataclass
class ValidationReport:
    """Every finding, plus which rules ran."""

    bundle_id: str
    root: Path
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def add(self, rule: str, level: Level, file: str, message: str) -> None:
        self.findings.append(Finding(rule=rule, level=level, file=file, message=message))

    def ran(self, rule: str) -> None:
        if rule not in self.checked:
            self.checked.append(rule)

    def for_rule(self, rule: str) -> list[Finding]:
        return [item for item in self.findings if item.rule == rule]

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def status(self, rule: str) -> str:
        if rule not in self.checked:
            return "skip"
        levels = {item.level for item in self.for_rule(rule)}
        if "error" in levels:
            return "fail"
        if "warning" in levels:
            return "warn"
        return "pass"

    def render(self) -> str:
        """The human-readable report ``varuna bundle validate`` prints."""
        lines = [f"Bundle {self.bundle_id}", f"  {self.root}", ""]
        for rule, name in RULES.items():
            status = self.status(rule)
            lines.append(f"  {status:<4}  {rule:<3} {name}")
            for item in self.for_rule(rule):
                marker = {"error": "!", "warning": "?", "note": "-"}[item.level]
                lines.append(f"          {marker} {item.render()}")
        lines.append("")
        failed = sum(1 for rule in RULES if self.status(rule) == "fail")
        if self.ok:
            lines.append(
                f"{len(RULES) - self._skipped()} rule(s) checked, "
                f"{len(self.warnings)} warning(s). Bundle is valid."
            )
        else:
            lines.append(
                f"{failed} rule(s) failed, {len(self.errors)} error(s), "
                f"{len(self.warnings)} warning(s). Bundle is not valid."
            )
        return "\n".join(lines)

    def _skipped(self) -> int:
        return sum(1 for rule in RULES if self.status(rule) == "skip")


# ============================================================================ helpers
def _time_axis_problems(
    stamps: Sequence[datetime], t0: datetime, t1: datetime, cadence_min: int
) -> list[str]:
    """Complaints about a stream's distinct timestamps against the declared window."""
    problems: list[str] = []
    if not stamps:
        return ["no timestamps"]
    ordered = sorted(stamps)
    if abs((ordered[0] - t0).total_seconds()) > TIME_TOLERANCE_S:
        problems.append(f"starts at {ordered[0].isoformat()}, manifest t0 is {t0.isoformat()}")
    if abs((ordered[-1] - t1).total_seconds()) > TIME_TOLERANCE_S:
        problems.append(f"ends at {ordered[-1].isoformat()}, manifest t1 is {t1.isoformat()}")
    expected = timedelta(minutes=cadence_min)
    for previous, current in pairwise(ordered):
        gap = current - previous
        if abs((gap - expected).total_seconds()) > TIME_TOLERANCE_S:
            problems.append(
                f"gap of {gap.total_seconds() / 60:.1f} min at {current.isoformat()} "
                f"but the manifest declares a {cadence_min}-minute cadence"
            )
            break
    return problems


def _cube_problems(info: CubeInfo, manifest: BundleManifest, cadence_key: str) -> list[str]:
    """Complaints about one cube's time axis and declared grid."""
    problems: list[str] = []
    cadence = manifest.cadences.get(cadence_key)
    if cadence is None:
        return [f"manifest.cadences has no '{cadence_key}' entry"]
    if abs((info.t0 - manifest.t0).total_seconds()) > TIME_TOLERANCE_S:
        problems.append(f"cube t0 {info.t0.isoformat()} != manifest t0 {manifest.t0.isoformat()}")
    if abs(info.step_min - cadence) > 1e-9:
        problems.append(f"cube step {info.step_min} min != declared cadence {cadence} min")
    times = list(info.times_min)
    if not times:
        problems.append("cube has no instants")
        return problems
    if abs(times[0]) > 1e-6:
        problems.append(f"first instant is {times[0]} min after t0, expected 0")
    if abs(times[-1] - manifest.duration_min) > 1e-6:
        problems.append(
            f"last instant is {times[-1]} min after t0, but the window is "
            f"{manifest.duration_min} min long"
        )
    steps = {round(b - a, 6) for a, b in pairwise(times)}
    if len(steps) > 1:
        problems.append(f"instants are not evenly spaced: steps {sorted(steps)}")
    elif steps and abs(next(iter(steps)) - cadence) > 1e-6:
        problems.append(f"instants are {next(iter(steps))} min apart, cadence says {cadence}")
    if info.n_px != manifest.radar_domain.n_px:
        problems.append(
            f"cube is {info.n_px} px across, manifest.radar_domain says "
            f"{manifest.radar_domain.n_px}"
        )
    if abs(info.res_m - manifest.radar_domain.res_m) > 1e-9:
        problems.append(
            f"cube resolution {info.res_m} m != manifest.radar_domain {manifest.radar_domain.res_m} m"
        )
    return problems


def _stamps(values: Sequence[Any]) -> tuple[list[datetime], list[str]]:
    """Parse ISO timestamps, collecting a complaint for every one that will not do.

    A timestamp without a UTC offset is rejected rather than parsed: SPEC.md 12 requires
    ISO 8601 with an offset, and letting a naive one through would make it incomparable with
    the manifest window (whose instants are IST) - the comparison would raise rather than
    report, and this validator never raises on bad data.

    Returns:
        The timestamps that parsed, and a ready-to-print complaint per timestamp that did not.
    """
    parsed: list[datetime] = []
    problems: list[str] = []
    for value in values:
        text = str(value)
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            problems.append(f"timestamp {text!r} is not ISO 8601")
            continue
        if stamp.tzinfo is None:
            problems.append(
                f"timestamp {text!r} carries no UTC offset; replay times are ISO 8601 with "
                "an offset, e.g. 2019-07-02T05:40:00+05:30 (SPEC.md 12)"
            )
            continue
        parsed.append(stamp)
    return parsed, problems


_TRUE_TOKENS: frozenset[str] = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_TOKENS: frozenset[str] = frozenset({"false", "f", "no", "n", "0"})


def _synthetic_flags(values: Sequence[Any]) -> tuple[int, int, list[str]]:
    """Read a stream's ``synthetic`` column strictly.

    A blank cell must not read as "synthetic": ``NaN`` is truthy in Python, so a column of
    blanks would otherwise pass rule B7 while saying nothing at all about the stream.

    Returns:
        How many rows say true, how many say false, and the values that say neither.
    """
    true_rows = false_rows = 0
    unreadable: list[str] = []
    for value in values:
        if isinstance(value, bool | np.bool_):
            true_rows += bool(value)
            false_rows += not bool(value)
            continue
        if value is None or (isinstance(value, float) and math.isnan(value)):
            unreadable.append("")
            continue
        token = str(value).strip().lower()
        if token in _TRUE_TOKENS:
            true_rows += 1
        elif token in _FALSE_TOKENS:
            false_rows += 1
        else:
            unreadable.append(str(value))
    return true_rows, false_rows, unreadable


# ============================================================================ the rules
def _check_members(report: ValidationReport, layout: BundleLayout, label: str) -> None:
    report.ran("B3")
    members = layout.members()
    required = REQUIRED_MEMBERS.get(label, REQUIRED_MEMBERS["Reconstructed replay"])
    for name, path in members.items():
        if path.exists():
            continue
        if name in required:
            report.add("B3", "error", name, "missing; the bundle contract requires it")
        else:
            report.add("B3", "note", name, f"absent; a bundle labelled '{label}' does not need it")


def _check_cubes(report: ValidationReport, layout: BundleLayout, manifest: BundleManifest) -> None:
    report.ran("B4")
    report.ran("B5")
    report.ran("B12")
    infos = {}
    for member, path, variable, cadence_key in (
        (TRUTH_ZARR, layout.truth, TRUTH_VARIABLE, "truth"),
        (RADAR_ZARR, layout.radar, RADAR_VARIABLE, "radar"),
    ):
        if not path.exists():
            continue
        try:
            info = read_cube_info(path, variable)
        except (KeyError, ValueError, OSError) as exc:
            report.add("B4", "error", member, f"cannot be read: {exc}")
            continue
        infos[member] = info
        for problem in _cube_problems(info, manifest, cadence_key):
            report.add("B4", "error", member, problem)
        # B12. SPEC.md 10.2 fixes the physical quantity of each cube - dBZ for the radar
        # frames, mm/h for the truth field. `write_cube` records it in the attributes and
        # nothing read it back, so a cube written with the wrong units validated clean and
        # any consumer trusting the attribute would convert twice or not at all.
        expected_units = "dBZ" if member == RADAR_ZARR else "mm/h"
        if info.units.strip().lower() != expected_units.lower():
            report.add(
                "B12",
                "error",
                member,
                f"declares units {info.units!r}; SPEC.md 10.2 fixes {expected_units!r} "
                "for this cube",
            )

    if len(infos) == 2:
        truth, radar = infos[TRUTH_ZARR], infos[RADAR_ZARR]
        if truth.grid_key() != radar.grid_key():
            report.add(
                "B5",
                "error",
                f"{RADAR_ZARR} / {TRUTH_ZARR}",
                f"grids differ: radar {radar.grid_key()} vs truth {truth.grid_key()}",
            )
        if truth.shape[1:] != radar.shape[1:]:
            report.add(
                "B5",
                "error",
                f"{RADAR_ZARR} / {TRUTH_ZARR}",
                f"frame shapes differ: radar {radar.shape[1:]} vs truth {truth.shape[1:]}",
            )
    elif infos:
        report.add(
            "B5",
            "note",
            RADAR_ZARR if TRUTH_ZARR in infos else TRUTH_ZARR,
            "only one cube is present, so the grids cannot be compared",
        )
    for member, info in infos.items():
        if not info.crs.upper().startswith("EPSG:"):
            report.add("B5", "error", member, f"crs attribute is {info.crs!r}, expected 'EPSG:<n>'")


def _check_streams(
    report: ValidationReport, layout: BundleLayout, manifest: BundleManifest
) -> None:
    report.ran("B4")
    report.ran("B7")
    if not manifest.synthetic_notes:
        level: Level = "error" if manifest.is_reconstructed else "warning"
        report.add(
            "B7",
            level,
            MANIFEST_NAME,
            "synthetic_notes is empty; say which streams are synthetic in UI-ready copy",
        )

    if layout.gauges.exists():
        _check_table(
            report,
            GAUGES_CSV,
            layout.gauges,
            manifest,
            cadence_key="gauges",
            flag_column="synthetic",
            reader="csv",
            columns=GAUGES_COLUMNS,
        )
    if layout.traffic.exists():
        _check_table(
            report,
            TRAFFIC_PARQUET,
            layout.traffic,
            manifest,
            cadence_key="traffic",
            flag_column="synthetic",
            reader="parquet",
            columns=TRAFFIC_COLUMNS,
        )
    if layout.tide.exists():
        _check_tide(report, layout, manifest)
    if layout.reports.exists():
        _check_reports(report, layout, manifest)


def _check_columns(
    report: ValidationReport, member: str, present: Sequence[str], expected: Sequence[str]
) -> None:
    """Rule B11 - the columns SPEC.md 10.2 fixes for this member are all there.

    The bundle layout in 10.2 is a contract, not a suggestion: a consumer written against
    ``mm_5min`` or ``baseline_kmh`` breaks on a bundle that renamed or dropped it. The column
    tuples live in ``bundle.py`` beside the writers, so the writer and the check cannot drift.
    """
    report.ran("B11")
    if not expected:
        return
    missing = [name for name in expected if name not in set(present)]
    if missing:
        report.add(
            "B11",
            "error",
            member,
            f"is missing the column(s) {', '.join(missing)} that SPEC.md 10.2 fixes for it "
            f"(found {', '.join(map(str, present))})",
        )


def _check_table(
    report: ValidationReport,
    member: str,
    path: Path,
    manifest: BundleManifest,
    *,
    cadence_key: str,
    flag_column: str,
    reader: str,
    columns: tuple[str, ...] = (),
) -> None:
    import pandas as pd

    try:
        frame = pd.read_csv(path) if reader == "csv" else pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        report.add("B4", "error", member, f"cannot be read: {exc}")
        return
    if frame.empty:
        report.add("B4", "error", member, "is empty")
        return
    _check_columns(report, member, list(frame.columns), columns)
    if flag_column not in frame.columns:
        report.add(
            "B7",
            "error",
            member,
            f"has no '{flag_column}' column; a synthetic stream must say so (SPEC.md 0.7)",
        )
    else:
        _, not_synthetic, unreadable = _synthetic_flags(frame[flag_column].tolist())
        if unreadable:
            report.add(
                "B7",
                "error",
                member,
                f"{len(unreadable)} row(s) have a blank or unreadable '{flag_column}' value "
                f"(first {unreadable[0]!r}); a stream must say what it is, true or false "
                "(SPEC.md 0.7)",
            )
        if not_synthetic:
            report.add(
                "B7",
                "note",
                member,
                f"{not_synthetic} row(s) are marked not synthetic; the manifest must cite "
                "where they came from",
            )
    if "ts" not in frame.columns:
        report.add("B4", "error", member, "has no 'ts' column")
        return
    stamps, bad = _stamps(sorted(set(frame["ts"].astype(str))))
    for problem in bad[:3]:
        report.add("B4", "error", member, problem)
    cadence = manifest.cadences.get(cadence_key)
    if cadence is None:
        report.add("B4", "error", MANIFEST_NAME, f"cadences has no '{cadence_key}' entry")
        return
    for problem in _time_axis_problems(stamps, manifest.t0, manifest.t1, cadence):
        report.add("B4", "error", member, problem)


def _check_tide(report: ValidationReport, layout: BundleLayout, manifest: BundleManifest) -> None:
    import pandas as pd

    try:
        frame = pd.read_csv(layout.tide)
    except (OSError, ValueError) as exc:
        report.add("B4", "error", TIDE_CSV, f"cannot be read: {exc}")
        return
    _check_columns(report, TIDE_CSV, list(frame.columns), TIDE_COLUMNS)
    if "source" not in frame.columns:
        report.add(
            "B7",
            "error",
            TIDE_CSV,
            "has no 'source' column; a tide series is a tide-table URL or 'illustrative'",
        )
    elif frame["source"].isna().any() or (frame["source"].astype(str).str.strip() == "").any():
        report.add("B7", "error", TIDE_CSV, "has rows with a blank 'source'")
    if "ts" not in frame.columns:
        report.add("B4", "error", TIDE_CSV, "has no 'ts' column")
        return
    stamps, bad = _stamps(sorted(set(frame["ts"].astype(str))))
    for problem in bad[:3]:
        report.add("B4", "error", TIDE_CSV, problem)
    cadence = manifest.cadences.get("tide")
    if cadence is None:
        report.add("B4", "error", MANIFEST_NAME, "cadences has no 'tide' entry")
        return
    _check_tide_extent(report, stamps, manifest, cadence)


def _check_tide_extent(
    report: ValidationReport,
    stamps: Sequence[datetime],
    manifest: BundleManifest,
    cadence_min: int,
) -> None:
    """Rule B4 for the tide: from ``t0`` to ``t1 + TIDE_LOOKAHEAD_MIN`` at the declared cadence.

    The tide is the one stream that runs past ``t1`` (:data:`bundle.TIDE_LOOKAHEAD_MIN` says
    why): a cycle at ``t1`` forecasts three hours on, and the Twin holds the sea flat after the
    last row. So a series has to open at ``t0`` and reach ``t1`` - anything less leaves the
    window itself without a sea - and one that stops between ``t1`` and the horizon is a warning,
    because every late cycle then runs part of its forecast against a frozen stage. Past the
    horizon is an error: nothing reads it, so it can only mean the window or the series is not
    the one the manifest describes.
    """
    if not stamps:
        report.add("B4", "error", TIDE_CSV, "no timestamps")
        return
    ordered = sorted(stamps)
    first, last = ordered[0], ordered[-1]
    horizon = manifest.t1 + timedelta(minutes=TIDE_LOOKAHEAD_MIN)
    if abs((first - manifest.t0).total_seconds()) > TIME_TOLERANCE_S:
        report.add(
            "B4",
            "error",
            TIDE_CSV,
            f"starts at {first.isoformat()}, manifest t0 is {manifest.t0.isoformat()}",
        )
    if (manifest.t1 - last).total_seconds() > TIME_TOLERANCE_S:
        report.add(
            "B4",
            "error",
            TIDE_CSV,
            f"ends at {last.isoformat()}, before manifest t1 {manifest.t1.isoformat()}",
        )
    elif (horizon - last).total_seconds() > TIME_TOLERANCE_S:
        short = (horizon - last).total_seconds() / 60.0
        report.add(
            "B4",
            "warning",
            TIDE_CSV,
            f"ends at {last.isoformat()}, {short:.0f} min before the last cycle's forecast "
            f"horizon {horizon.isoformat()} (t1 + {TIDE_LOOKAHEAD_MIN} min); the Twin holds the "
            "sea at the last stage for the rest of every cycle that reaches past it",
        )
    elif (last - horizon).total_seconds() > TIME_TOLERANCE_S:
        report.add(
            "B4",
            "error",
            TIDE_CSV,
            f"ends at {last.isoformat()}, after the last cycle's forecast horizon "
            f"{horizon.isoformat()} (t1 + {TIDE_LOOKAHEAD_MIN} min), which nothing reads",
        )
    expected = timedelta(minutes=cadence_min)
    for previous, current in pairwise(ordered):
        gap = current - previous
        if abs((gap - expected).total_seconds()) > TIME_TOLERANCE_S:
            report.add(
                "B4",
                "error",
                TIDE_CSV,
                f"gap of {gap.total_seconds() / 60:.1f} min at {current.isoformat()} "
                f"but the manifest declares a {cadence_min}-minute cadence",
            )
            break


def _check_reports(
    report: ValidationReport, layout: BundleLayout, manifest: BundleManifest
) -> None:
    text = layout.reports.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            report.add("B4", "error", REPORTS_JSONL, f"line {number} is not JSON: {exc.msg}")
    report.ran("B11")
    for number, row in enumerate(rows, start=1):
        absent = [key for key in REPORT_REQUIRED_KEYS if key not in row]
        if absent:
            report.add(
                "B11",
                "error",
                REPORTS_JSONL,
                f"line {number} is missing the key(s) {', '.join(absent)} that SPEC.md 10.2 "
                "fixes for a report",
            )
            break
    for number, row in enumerate(rows, start=1):
        if "synthetic" not in row:
            report.add(
                "B7",
                "error",
                REPORTS_JSONL,
                f"line {number} has no 'synthetic' field; every report must say what it is",
            )
            break
    stamps, bad = _stamps([row.get("ts", "") for row in rows])
    for problem in bad[:3]:
        report.add("B4", "error", REPORTS_JSONL, problem)
    outside = [stamp for stamp in stamps if stamp < manifest.t0 or stamp > manifest.t1]
    if outside:
        report.add(
            "B4",
            "error",
            REPORTS_JSONL,
            f"{len(outside)} report(s) fall outside the replay window, the first at "
            f"{sorted(outside)[0].isoformat()}",
        )


def _check_ground_truth(
    report: ValidationReport, layout: BundleLayout, manifest: BundleManifest
) -> None:
    report.ran("B6")
    report.ran("B10")
    if not layout.ground_truth.exists():
        return
    try:
        payload = json.loads(layout.ground_truth.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.add("B6", "error", GROUND_TRUTH_GEOJSON, f"is not JSON: {exc.msg}")
        return
    features = payload.get("features")
    if not isinstance(features, list):
        report.add("B6", "error", GROUND_TRUTH_GEOJSON, "has no 'features' array")
        return
    if manifest.label == "Design storm" and features:
        report.add(
            "B6",
            "error",
            GROUND_TRUTH_GEOJSON,
            f"holds {len(features)} pin(s), but a design storm has no observed event and so "
            "no ground truth",
        )
        return

    known = set(GroundTruthPin.model_fields)
    inside = 0
    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        label = properties.get("id") or feature.get("id") or f"index {index}"
        where = f"feature {index} ({label})"
        lon, lat = _feature_lonlat(report, feature, properties, where)
        if lon is None or lat is None:
            continue
        payload_pin = {key: value for key, value in properties.items() if key in known}
        payload_pin.setdefault("lon", lon)
        payload_pin.setdefault("lat", lat)
        try:
            GroundTruthPin.model_validate(payload_pin)
        except ValidationError as exc:
            for error in exc.errors()[:3]:
                location = ".".join(str(part) for part in error["loc"]) or "properties"
                report.add(
                    "B6", "error", GROUND_TRUTH_GEOJSON, f"{where}: {location} {error['msg']}"
                )
        unknown = set(properties) - known - GROUND_TRUTH_EXTRA_KEYS
        if unknown:
            report.add(
                "B6",
                "warning",
                GROUND_TRUTH_GEOJSON,
                f"{where}: undeclared propert{'y' if len(unknown) == 1 else 'ies'} "
                f"{sorted(unknown)}",
            )
        if manifest.aoi.contains(lon, lat):
            inside += 1

    if manifest.ground_truth_n != inside:
        report.add(
            "B10",
            "error",
            MANIFEST_NAME,
            f"ground_truth_n is {manifest.ground_truth_n} but {inside} pin(s) fall inside the "
            "declared area of interest",
        )

    # B13. SPEC.md 10.2 sets a floor on the *demo* bundle's evidence: "a minimum of 10 pins
    # inside the AOI for MUM-2019-07-02; if the event does not yield 10, switch the demo bundle
    # to another Mumbai event and document the choice". B10 only checks that the manifest's
    # count is honest, so a bundle that lost its pins would agree with itself and pass. The
    # floor is what makes the verification scores of 11.12 mean anything.
    #
    # It is an error for the bundle the demo ships and a note for any other reconstruction,
    # because the spec scopes the number to that bundle. Miniatures exist and are legitimate:
    # the reconstruction tests build a 20 km, six-pin bundle on purpose, and holding it to a
    # figure the spec sets for the demo would be inventing a requirement.
    report.ran("B13")
    if manifest.is_reconstructed and inside < GROUND_TRUTH_FLOOR:
        is_demo = manifest.id == get_settings().varuna_bundle
        report.add(
            "B13",
            "error" if is_demo else "note",
            GROUND_TRUTH_GEOJSON,
            f"carries {inside} sourced pin(s) inside the area of interest, below the "
            f"{GROUND_TRUTH_FLOOR} SPEC.md 10.2 sets for the demo bundle"
            + (
                ". Either curate more, or switch the demo bundle to another Mumbai event and "
                "record the choice in an ADR"
                if is_demo
                else " - a note, because the floor is scoped to the demo bundle"
            ),
        )


def _feature_lonlat(
    report: ValidationReport, feature: dict[str, Any], properties: dict[str, Any], where: str
) -> tuple[float | None, float | None]:
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        report.add(
            "B6",
            "error",
            GROUND_TRUTH_GEOJSON,
            f"{where}: geometry must be a Point, got {geometry.get('type')!r}",
        )
        return (None, None)
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        report.add("B6", "error", GROUND_TRUTH_GEOJSON, f"{where}: geometry has no coordinates")
        return (None, None)
    lon, lat = float(coordinates[0]), float(coordinates[1])
    for name, value in (("lon", lon), ("lat", lat)):
        stated = properties.get(name)
        if stated is not None and not math.isclose(float(stated), value, abs_tol=1e-6):
            report.add(
                "B6",
                "error",
                GROUND_TRUTH_GEOJSON,
                f"{where}: properties.{name} is {stated} but the geometry says {value}",
            )
    return (lon, lat)


def _check_honesty(
    report: ValidationReport, manifest: BundleManifest, layout: BundleLayout
) -> None:
    report.ran("B9")
    # The tide's vertical datum is a labelled assumption like the calibration basis: a stage
    # above chart datum read as if it were in the DEM's frame floods the coast by metres. B2
    # already validates a declared block; this says out loud when a tide carries none, and
    # when a block describes a tide the bundle does not have.
    has_tide = layout.tide.exists()
    if has_tide and manifest.tide_datum is None:
        report.add(
            "B9",
            "note",
            MANIFEST_NAME,
            "tide.csv is present but tide_datum is not, so every consumer reads stage_m as "
            "already in the DEM's frame",
        )
    if not has_tide and manifest.tide_datum is not None:
        report.add(
            "B9",
            "error",
            MANIFEST_NAME,
            "declares tide_datum but the bundle carries no tide.csv for it to describe",
        )
    if manifest.is_reconstructed:
        report.ran("B8")
        if not manifest.sources:
            report.add(
                "B8", "error", MANIFEST_NAME, "a reconstructed replay must cite its public sources"
            )
        elif not any(source.used_for for source in manifest.sources):
            report.add(
                "B8",
                "warning",
                MANIFEST_NAME,
                "no source says what it was used_for; name the one the storm was calibrated to",
            )
        if manifest.storm is None:
            report.add(
                "B9",
                "warning",
                MANIFEST_NAME,
                "no storm design recorded, so the cubes cannot be regenerated from the manifest",
            )
        if manifest.calibration and not manifest.calibration_basis:
            report.add(
                "B9",
                "error",
                MANIFEST_NAME,
                "calibration numbers without calibration_basis: say what was measured, what was "
                "inferred and which source says so",
            )
    if manifest.label == "Design storm":
        if manifest.design_storm is None:
            report.add(
                "B9",
                "error",
                MANIFEST_NAME,
                "a design storm must carry design_storm with the hyetograph it was built from",
            )
        else:
            basis = manifest.design_storm.basis.lower()
            if "intensity-duration-frequency" not in basis and "idf" not in basis:
                report.add(
                    "B9",
                    "error",
                    MANIFEST_NAME,
                    "design_storm.basis must say that the storm is not derived from a published "
                    "intensity-duration-frequency curve (docs/research/data_sources.md section 6)",
                )


# ============================================================================ entry point
def validate_bundle(bundle: str | Path) -> ValidationReport:
    """Check one bundle against every rule and return the report (never raises on bad data)."""
    layout = BundleLayout.for_bundle(bundle)
    report = ValidationReport(bundle_id=layout.bundle_id, root=layout.root)

    report.ran("B1")
    if not layout.root.is_dir():
        report.add("B1", "error", layout.root.as_posix(), "no such bundle folder")
        return report
    if not layout.manifest.is_file():
        report.add("B1", "error", MANIFEST_NAME, "missing; a bundle folder must carry a manifest")
        return report

    report.ran("B2")
    try:
        manifest = BundleManifest.model_validate_json(layout.manifest.read_text(encoding="utf-8"))
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.add("B2", "error", MANIFEST_NAME, f"does not validate: {exc}")
        return report
    if manifest.id != layout.bundle_id:
        report.add(
            "B2",
            "error",
            MANIFEST_NAME,
            f"declares id {manifest.id!r} but sits in folder {layout.bundle_id!r}",
        )

    _check_members(report, layout, manifest.label)
    _check_cubes(report, layout, manifest)
    _check_streams(report, layout, manifest)
    _check_ground_truth(report, layout, manifest)
    _check_honesty(report, manifest, layout)
    log.info(
        "bundle.validated",
        bundle=report.bundle_id,
        ok=report.ok,
        errors=len(report.errors),
        warnings=len(report.warnings),
    )
    return report


__all__ = [
    "REQUIRED_MEMBERS",
    "RULES",
    "Finding",
    "ValidationReport",
    "validate_bundle",
]
