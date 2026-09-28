"""Citizen reports as observations (SPEC.md 11.6, 7.11; task P7.2).

A person standing in the water is the only observation VARUNA gets that measures depth directly,
and the depth chips are chosen so they can: ankle, knee and waist are body landmarks, not
guesses at a number. What the reporter is actually being asked is "how deep is it on you", which
people are good at, instead of "how many centimetres", which nobody is.

**The chips and their spread** are SPEC.md 11.6's: 10 / 45 / 90 cm with sd 8 / 12 / 15. The
spread widens with depth because the landmarks are further apart up the body and because deep
water is harder to stand in and judge.

**Deduplication** is 50 m and 10 minutes, also from the spec. Six people reporting one flooded
junction is one observation about the junction, not six - and treating it as six would let a
busy street outvote a quiet one in the EnKF for reasons that have nothing to do with water.

Every report carries whether it is synthetic. The bundle's stream is (labelled) synthetic; a
report posted through ``POST /v1/reports`` on the day is not, and the two must stay
distinguishable in the observation record (rule 7).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime
    from pathlib import Path

log = structlog.get_logger("varuna.pulse.reports")

__all__ = [
    "DEDUPE_MINUTES",
    "DEDUPE_RADIUS_M",
    "DEPTH_CHIPS",
    "REPORT_AOIS",
    "ReportObservation",
    "city_for_point",
    "observations_from",
    "read_reports",
]

DEPTH_CHIPS: dict[str, tuple[float, float]] = {
    "ankle": (10.0, 8.0),
    "knee": (45.0, 12.0),
    "waist": (90.0, 15.0),
}
"""Chip to (depth cm, sd cm), from SPEC.md 11.6."""

DEDUPE_RADIUS_M = 50.0
DEDUPE_MINUTES = 10.0

REPORT_AOIS: dict[str, tuple[float, float, float, float]] = {
    "mumbai": (72.815, 18.995, 72.905, 19.135),
    "chennai": (80.20, 12.96, 80.28, 13.05),
}
"""Each city's computation box, ``(min_lon, min_lat, max_lon, max_lat)`` in WGS84.

The same numbers as ``services/city/configs/<city>.yaml`` and SPEC.md 3.3 (MUM-CENTRAL,
CHN-SOUTH); ``services/api/tests/test_reports.py`` holds them equal. They are repeated here rather
than read from the city config because Pulse does not depend on ``varuna_city``, and a report's
city has to be decidable before any city has been built.

A report is evidence about the city whose box it falls in and no other. Pulse snaps each report
to its nearest drain node with no distance cutoff, so a report from Pune or from Chennai reaching
a Mumbai cycle became a knee-deep observation at whichever Mumbai node was least far away."""


def city_for_point(lon: float, lat: float) -> str | None:
    """The city whose box holds a point, or ``None`` when no forecast covers it.

    Boxes are closed: a point on an edge belongs to the city. The two boxes are 1,000 km apart,
    so no point can be in both.
    """
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    for city, (min_lon, min_lat, max_lon, max_lat) in REPORT_AOIS.items():
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            return city
    return None


@dataclass(frozen=True, slots=True)
class ReportObservation:
    """One de-duplicated citizen report, ready for assimilation."""

    report_id: str
    ts: datetime
    lon: float
    lat: float
    depth_cm: float
    depth_sd_cm: float
    chip: str
    place: str | None
    synthetic: bool
    n_merged: int = 1
    kind: str = "report"

    @property
    def weight(self) -> float:
        """Inverse variance, sharpened by agreement.

        Two independent people saying "knee" at the same junction is stronger evidence than one,
        so the merged spread narrows as ``sd / sqrt(n)`` - the standard error of a mean. It does
        not narrow without limit: `n_merged` is capped where it is used, because six reports from
        one crowd are not six independent measurements.
        """
        sd = self.depth_sd_cm / math.sqrt(min(self.n_merged, 4))
        return 1.0 / max(sd**2, 1e-6)


def _metres_apart(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Local flat-earth distance; exact enough at the 50 m scale this is used for."""
    lon_m = 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot((lon2 - lon1) * lon_m, (lat2 - lat1) * 110_540.0)


def observations_from(rows: list[dict[str, Any]], *, until: datetime) -> list[ReportObservation]:
    """De-duplicate raw reports into observations, keeping only those already made.

    ``until`` matters on a replay: a cycle at 07:40 must not assimilate a report filed at 08:20.
    Letting the future leak in is the easiest way to build a system that verifies beautifully and
    forecasts nothing.
    """
    parsed: list[ReportObservation] = []
    undated = 0
    for row in rows:
        chip = str(row.get("depth_hint") or "").lower()
        if chip not in DEPTH_CHIPS:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is not None and ts.tzinfo is None:
            # A timestamp with no offset cannot be compared with the cycle clock, and guessing a
            # zone for it would invent the one thing the report is evidence about - when. The
            # rows the bundle and the API write both carry +05:30; a client that posts its own
            # `ts` without one loses the report, and the log says how many.
            undated += 1
            continue
        if ts is None or ts > until:
            continue
        depth, sd = DEPTH_CHIPS[chip]
        parsed.append(
            ReportObservation(
                report_id=str(row.get("id") or f"RPT-{len(parsed)}"),
                ts=ts,
                lon=float(row["lon"]),
                lat=float(row["lat"]),
                depth_cm=depth,
                depth_sd_cm=sd,
                chip=chip,
                place=row.get("place"),
                synthetic=bool(row.get("synthetic", False)),
            )
        )

    parsed.sort(key=lambda r: r.ts)
    kept: list[ReportObservation] = []
    merged: list[int] = []
    # Whether every report in a group is synthetic. A group that a real person contributed to is
    # not a synthetic observation, even when the bundle's stream happened to report the junction
    # first and is the row whose id survives the merge.
    all_synthetic: list[bool] = []
    for report in parsed:
        hit = None
        for index, existing in enumerate(kept):
            close = _metres_apart(existing.lon, existing.lat, report.lon, report.lat)
            recent = abs((report.ts - existing.ts) / timedelta(minutes=1)) <= DEDUPE_MINUTES
            if close <= DEDUPE_RADIUS_M and recent:
                hit = index
                break
        if hit is None:
            kept.append(report)
            merged.append(1)
            all_synthetic.append(report.synthetic)
        else:
            merged[hit] += 1
            all_synthetic[hit] = all_synthetic[hit] and report.synthetic

    out = [
        ReportObservation(
            report_id=report.report_id,
            ts=report.ts,
            lon=report.lon,
            lat=report.lat,
            depth_cm=report.depth_cm,
            depth_sd_cm=report.depth_sd_cm,
            chip=report.chip,
            place=report.place,
            synthetic=synthetic,
            n_merged=count,
        )
        for report, count, synthetic in zip(kept, merged, all_synthetic, strict=True)
    ]
    log.info(
        "pulse.reports",
        raw=len(parsed),
        observations=len(out),
        merged=len(parsed) - len(out),
        undated=undated,
        until=str(until),
    )
    return out


def read_reports(
    bundle_dir: Path,
    *,
    until: datetime,
    inbox: Path | None = None,
    city: str | None = None,
) -> list[ReportObservation]:
    """Read the bundle's report stream and the live inbox, and de-duplicate the two together.

    ``inbox`` is ``data/reports/inbox.jsonl``, where ``POST /v1/reports`` appends what people
    send from the public map and the report flow. It has to be merged *before* the dedupe rather
    than after: a citizen report and the bundle's synthetic report about the same junction in the
    same ten minutes are one piece of evidence about that junction, and assimilating both would
    let the same water vote twice.

    **Only this city's reports.** The inbox is one file for every city, so an inbox row is kept
    only when it lies inside the box of the city this cycle runs for (:data:`REPORT_AOIS`) and
    was not marked ``outside_aoi`` by the API. ``city`` defaults to the bundle manifest's
    ``city``; with neither, a row is kept when it lies inside *some* city's box, which still drops
    a report from anywhere VARUNA does not forecast. A city this module has no box for keeps only
    the rows the API tagged with that city. The bundle's own stream is not filtered: it was
    generated inside its city.

    A row that does not say whether it is synthetic is treated as a real report, because that is
    what arrives through the API; the bundle's own stream labels itself (rule 7).

    **Limitation.** The ``until`` filter compares a report's own ``ts`` against the cycle clock,
    so a report posted while a 2019 replay is running - whose ``ts`` is today - is later than
    every cycle in the bundle and is not assimilated. It reaches Pulse in live mode, or when the
    client sends the replay clock's time as ``ts``, which the endpoint accepts and the report
    screen does not yet send.
    """
    rows = _read_jsonl(bundle_dir / "reports.jsonl")
    if inbox is not None:
        target = city if city is not None else _bundle_city(bundle_dir)
        kept = 0
        dropped = 0
        for row in _read_jsonl(inbox):
            if not _belongs_to(row, target):
                dropped += 1
                continue
            row.setdefault("synthetic", False)
            rows.append(row)
            kept += 1
        if dropped:
            log.info("pulse.reports.other_city", city=target, kept=kept, dropped=dropped)
    return observations_from(rows, until=until)


def _bundle_city(bundle_dir: Path) -> str | None:
    """The ``city`` a bundle's manifest names, or ``None`` when it has no readable manifest."""
    manifest = bundle_dir / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        body = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = body.get("city") if isinstance(body, dict) else None
    name = str(value).strip().lower() if value else ""
    return name or None


def _belongs_to(row: dict[str, Any], city: str | None) -> bool:
    """Whether an inbox row is evidence about ``city`` (see :func:`read_reports`)."""
    if row.get("outside_aoi") is True:
        return False
    try:
        lon = float(row["lon"])
        lat = float(row["lat"])
    except (KeyError, TypeError, ValueError):
        return False
    where = city_for_point(lon, lat)
    tagged = row.get("city")
    if tagged is not None and where is not None and str(tagged) != where:
        # The API tags a row by the same boxes; a disagreement means the row was edited.
        return False
    if city is None:
        return where is not None
    if city in REPORT_AOIS:
        return where == city
    return tagged is not None and str(tagged) == city


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rows of a JSONL file, skipping any line that will not parse.

    The inbox is appended to by the API while a cycle may be reading it, so a truncated last line
    is possible. One unreadable report is not worth failing the stage for; it is worth a log line
    naming how many were dropped.
    """
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    unreadable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            unreadable += 1
    if unreadable:
        log.warning("pulse.reports.unreadable", path=str(path), lines=unreadable)
    return rows


def _parse_ts(value: object) -> datetime | None:
    from datetime import datetime as dt

    if isinstance(value, dt):
        return value
    if not isinstance(value, str):
        return None
    try:
        return dt.fromisoformat(value)
    except ValueError:
        return None
