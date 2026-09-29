"""Live cycles: today's rain and today's sea, through the same engines as the replay.

A live cycle is the 2 July 2019 replay's cycle with two inputs swapped for real-time ones and
nothing else changed - the same Twin, drain graph, Pulse, Flash-lite, products, alerts and pump
plan, written into ``data/runs/<run_id>`` with ``mode = "live"``.

**Rain.** There is no live radar here: IMD's images need three frames ten minutes apart that
nobody archives for us, and the decoder is not wired into Sky (P2.9). SPEC.md 11.11's degraded
mode for "no radar" is the honest substitute, so the rain comes from a numerical weather
prediction ensemble instead: DWD's ICON-EPS through Open-Meteo's ensemble API (free, no key,
CC BY 4.0, listed in public-apis). Twenty of its forty members are taken, sampled on a 3 x 3
point grid over the Sky domain and interpolated bilinearly onto its 500 m cells, so the cube has
the same shape Sky's STEPS ensemble has and every product downstream reads it unchanged. The
model runs at about 26 km, so the rain is a regional field, not a convective cell over a
junction; every run says so in its notes, and the console calls it "Live, NWP rain, no radar".

**Sea.** Open-Meteo's marine model's ``sea_level_height_msl`` just off the coast, which is height
above mean sea level - the DEM's frame to within the unquantified geoid residual the replay's
tide already carries (ADR-0055) - so no chart-datum conversion is applied.

**Reports.** Pulse already merges ``data/reports/inbox.jsonl`` and keeps a report whose own time
is before the cycle, so a complaint raised on ``/report`` is assimilated by the next live cycle
without any change here.

The forcing is written to ``bundles/<CODE>-LIVE/`` as ``live.json``, ``nwp.npz`` and
``tide.csv``, with **no** ``manifest.json``: it is not a replay bundle, the replay endpoints list
only folders that carry one, and ``run_cycle`` recognises it by :func:`is_live_bundle`.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from varuna_schemas.constants import IST, N_STEPS, STEP_MIN
from varuna_schemas.paths import bundles_dir, city_config_path, data_dir

log = structlog.get_logger("varuna.cycle.live")

__all__ = [
    "LIVE_SUFFIX",
    "NWP_MODEL",
    "LiveForcing",
    "forcing_from_points",
    "is_live_bundle",
    "live_bundle_id",
    "live_loop",
    "prune_live_runs",
    "read_live_forcing",
    "recent_reports",
    "run_live_cycle",
    "snap_cycle",
    "tide_from_marine",
    "write_live_bundle",
]

LIVE_SUFFIX = "-LIVE"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
SOURCE_URL = "https://open-meteo.com/en/docs/ensemble-api"
MARINE_SOURCE_URL = "https://open-meteo.com/en/docs/marine-weather-api"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

NWP_MODEL = "icon_seamless"
"""DWD ICON-EPS through Open-Meteo: 40 members, hourly, about 26 km over India."""

NWP_LABEL = "DWD ICON-EPS (Open-Meteo ensemble API)"
N_MEMBERS = 20
"""Members kept, matching Sky's ensemble size, so Flash-lite's 50 draws cover them the same way."""

GRID_HALF_DEG = 0.3
"""The 3 x 3 point grid spans +-0.3 degrees round the Sky domain's centre: past its 30 km edge."""

TIDE_POINTS: dict[str, tuple[float, float, str]] = {
    "mumbai": (72.80, 18.95, "off Colaba"),
    "chennai": (80.30, 13.00, "off Besant Nagar"),
}
"""A sea point just off each city's shore where the marine model has a wet cell."""

KEEP_RUNS = 2
"""Live runs kept on disk; older ones are removed so the 500 MB volume never fills."""

REPORT_WINDOW_H = 3.0
"""Citizen reports this recent are what a live cycle assimilates. Water seen three hours ago can
still be on the street; a report from last week, or one stamped with a 2019 replay time, is not
evidence about now."""


@dataclass(frozen=True, slots=True)
class LiveForcing:
    """What a live cycle is forced with, read back from the live folder."""

    cycle_ts: datetime
    times: tuple[datetime, ...]
    """Valid time of each forecast step: ``cycle_ts + (k + 1) * STEP_MIN``."""
    rain_mm_h: np.ndarray
    """``(members, steps, n_px, n_px)`` on the city's Sky grid, mm/h."""
    transform: tuple[float, float, float, float, float, float]
    crs: str
    res_m: float
    n_px: int
    model: str
    fetched_at: datetime
    notes: tuple[str, ...]


def live_bundle_id(city: str) -> str:
    """``MUM-LIVE`` for Mumbai: the city's run-id code with the live suffix."""
    return f"{_config(city).code}{LIVE_SUFFIX}"


def is_live_bundle(bundle: str | None) -> bool:
    return bool(bundle) and str(bundle).endswith(LIVE_SUFFIX)


def snap_cycle(when: datetime | None = None) -> datetime:
    """The cycle instant for ``when``: IST, floored to the 5-minute cadence, no seconds."""
    now = (when or datetime.now(tz=UTC)).astimezone(IST)
    return now.replace(minute=now.minute - now.minute % STEP_MIN, second=0, microsecond=0)


def _config(city: str):
    from varuna_schemas.models import CityConfig

    return CityConfig.from_yaml(city_config_path(city))


def _domain(city: str):
    from varuna_replay.domain import StormDomain

    return StormDomain.from_city_config(_config(city))


def _point_grid(center_lon: float, center_lat: float) -> tuple[list[float], list[float]]:
    offsets = (-GRID_HALF_DEG, 0.0, GRID_HALF_DEG)
    return [round(center_lon + d, 4) for d in offsets], [round(center_lat + d, 4) for d in offsets]


def _members(hourly: dict[str, Any]) -> list[str]:
    """The ensemble's precipitation series, control first, then members in their own order."""
    keys = [k for k in hourly if k == "precipitation" or k.startswith("precipitation_member")]
    keys.sort(key=lambda k: (k != "precipitation", k))
    return keys


def forcing_from_points(
    payloads: list[dict[str, Any]],
    lons: list[float],
    lats: list[float],
    cell_lon: np.ndarray,
    cell_lat: np.ndarray,
    cycle_ts: datetime,
    *,
    n_steps: int = N_STEPS,
    n_members: int = N_MEMBERS,
) -> np.ndarray:
    """Hourly member totals at a 3 x 3 point grid -> ``(members, steps, rows, cols)`` mm/h.

    ``payloads`` is Open-Meteo's answer for the nine points in row-major order, latitudes
    outer and longitudes inner, as :func:`_point_grid` lays them out. An hourly value is the sum
    over the hour ending at its timestamp (Open-Meteo's definition), so a step valid at ``v``
    rains at the rate of the first hourly stamp at or after ``v``. Missing values count as dry.
    Pure, so the arithmetic is tested without a network.
    """
    from scipy.interpolate import RegularGridInterpolator

    first = payloads[0]["hourly"]
    stamps = np.asarray(first["time"], dtype=np.int64)
    keys = _members(first)
    if not keys:
        msg = "Open-Meteo sent no precipitation series."
        raise ValueError(msg)
    picks = [keys[int(i)] for i in np.linspace(0, len(keys) - 1, min(n_members, len(keys)))]
    valid = [cycle_ts + timedelta(minutes=STEP_MIN * (k + 1)) for k in range(n_steps)]
    hour_index = []
    for v in valid:
        t = int(v.timestamp())
        idx = int(np.searchsorted(stamps, t, side="left"))
        hour_index.append(min(idx, len(stamps) - 1))

    n_lat, n_lon = len(lats), len(lons)
    values = np.zeros((len(picks), len(stamps), n_lat, n_lon), dtype=np.float64)
    for p, payload in enumerate(payloads):
        row, col = divmod(p, n_lon)
        hourly = payload["hourly"]
        for m, key in enumerate(picks):
            series = hourly.get(key) or []
            clean = [float(x) if isinstance(x, (int, float)) else 0.0 for x in series]
            clean = (clean + [0.0] * len(stamps))[: len(stamps)]
            values[m, :, row, col] = np.maximum(clean, 0.0)

    points = np.column_stack([cell_lat.ravel(), cell_lon.ravel()])
    cube = np.zeros((len(picks), n_steps, *cell_lon.shape), dtype=np.float32)
    for m in range(len(picks)):
        for k, h in enumerate(hour_index):
            field = RegularGridInterpolator(
                (np.asarray(lats), np.asarray(lons)),
                values[m, h],
                bounds_error=False,
                fill_value=None,
            )(points)
            cube[m, k] = np.maximum(field.reshape(cell_lon.shape), 0.0)
    return cube


def tide_from_marine(payload: dict[str, Any], cycle_ts: datetime, n_steps: int) -> list[dict]:
    """The marine model's hourly sea level as ``tide.csv`` rows at 15 minutes, IST.

    Linear between hours, from an hour before the cycle to the end of its forecast, so the Twin's
    boundary never holds the sea flat for want of a row.
    """
    hourly = payload.get("hourly") or {}
    stamps = np.asarray(hourly.get("time") or [], dtype=np.float64)
    levels = np.asarray(
        [
            float(x) if isinstance(x, (int, float)) else np.nan
            for x in hourly.get("sea_level_height_msl") or []
        ],
        dtype=np.float64,
    )
    ok = np.isfinite(levels)
    if ok.sum() < 2:
        return []
    start = cycle_ts - timedelta(hours=1)
    end = cycle_ts + timedelta(minutes=STEP_MIN * (n_steps + 3))
    rows = []
    t = start
    while t <= end:
        level = float(np.interp(t.timestamp(), stamps[ok], levels[ok]))
        rows.append({"ts": t.isoformat(), "stage_m": round(level, 3)})
        t += timedelta(minutes=15)
    return rows


def recent_reports(
    rows: list[dict[str, Any]], city: str, cycle_ts: datetime, hours: float = REPORT_WINDOW_H
) -> list[dict[str, Any]]:
    """The inbox rows a live cycle assimilates: this city's, seen in the last ``hours``."""
    start = cycle_ts - timedelta(hours=hours)
    kept = []
    for row in rows:
        if row.get("outside_aoi") is True or row.get("status") == "dismissed":
            continue
        if row.get("city") not in (None, city):
            continue
        try:
            ts = datetime.fromisoformat(str(row.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if start <= ts <= cycle_ts:
            kept.append({**row, "synthetic": bool(row.get("synthetic", False))})
    return kept


def _inbox_rows() -> list[dict[str, Any]]:
    path = data_dir() / "reports" / "inbox.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write_live_bundle(
    city: str,
    cycle_ts: datetime,
    *,
    get_json: Callable[..., Any] | None = None,
    n_steps: int = N_STEPS,
) -> Path:
    """Fetch today's rain ensemble and sea level for ``city`` and write the live folder.

    Raises whatever the network raises for the rain: a live cycle with no rain forcing would be
    a forecast of nothing. The sea is optional - without it the Twin runs with closed coastal
    boundaries and the run's notes say the tide is missing.
    """
    from pyproj import Transformer
    from varuna_schemas.net import get_json as default_get_json

    fetch = get_json or default_get_json
    domain = _domain(city)
    config = _config(city)
    center_lon = float(config.radar_domain.center_lon)
    center_lat = float(config.radar_domain.center_lat)
    lons, lats = _point_grid(center_lon, center_lat)
    grid_lats = [lat for lat in lats for _ in lons]
    grid_lons = [lon for _ in lats for lon in lons]
    raw = fetch(
        ENSEMBLE_URL,
        {
            "latitude": ",".join(str(v) for v in grid_lats),
            "longitude": ",".join(str(v) for v in grid_lons),
            "hourly": "precipitation",
            "models": NWP_MODEL,
            "past_hours": 2,
            "forecast_hours": math.ceil(n_steps * STEP_MIN / 60) + 3,
            "timeformat": "unixtime",
        },
        timeout=20.0,
    )
    payloads = raw if isinstance(raw, list) else [raw]
    if len(payloads) != len(grid_lats):
        msg = f"Open-Meteo answered {len(payloads)} points of {len(grid_lats)} asked."
        raise ValueError(msg)

    xs, ys = domain.cell_centres()
    to_wgs84 = Transformer.from_crs(domain.crs_string, "EPSG:4326", always_xy=True)
    cell_lon, cell_lat = to_wgs84.transform(xs, ys)
    cube = forcing_from_points(
        payloads,
        lons,
        lats,
        np.asarray(cell_lon),
        np.asarray(cell_lat),
        cycle_ts,
        n_steps=n_steps,
    )

    root = bundles_dir() / live_bundle_id(city)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "nwp.tmp.npz"
    np.savez_compressed(
        tmp,
        rain_mm_h=cube,
        transform=np.asarray(domain.transform, dtype=np.float64),
    )
    os.replace(tmp, root / "nwp.npz")

    tide_rows: list[dict] = []
    tide_note = None
    point = TIDE_POINTS.get(city)
    if point is not None:
        try:
            marine = fetch(
                MARINE_URL,
                {
                    "latitude": point[1],
                    "longitude": point[0],
                    "hourly": "sea_level_height_msl",
                    "past_hours": 3,
                    "forecast_hours": math.ceil(n_steps * STEP_MIN / 60) + 3,
                    "timeformat": "unixtime",
                },
                timeout=20.0,
            )
            tide_rows = tide_from_marine(marine, cycle_ts, n_steps)
        except Exception as error:  # the sea is optional; the rain is not
            tide_note = f"No live sea level this cycle ({error}); the coast was held closed."
            log.warning("live.tide_failed", city=city, error=str(error))
    tide_path = root / "tide.csv"
    if tide_rows:
        source = f"Open-Meteo marine model sea level above MSL {point[2]} ({MARINE_SOURCE_URL})"
        lines = ["ts,stage_m,source"] + [
            f"{row['ts']},{row['stage_m']},{source}" for row in tide_rows
        ]
        tide_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif tide_path.exists():
        tide_path.unlink()

    reports = recent_reports(_inbox_rows(), city, cycle_ts)
    (root / "reports.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in reports),
        encoding="utf-8",
    )

    total_mm = float(cube.mean(axis=(0, 2, 3)).sum()) * STEP_MIN / 60.0
    meta = {
        "city": city,
        "bundle": live_bundle_id(city),
        "cycle_ts": cycle_ts.isoformat(),
        "fetched_at": datetime.now(tz=IST).isoformat(),
        "model": NWP_LABEL,
        "members": int(cube.shape[0]),
        "points": [{"lon": lo, "lat": la} for lo, la in zip(grid_lons, grid_lats, strict=True)],
        "domain_mean_mm_3h": round(total_mm, 2),
        "source_url": SOURCE_URL,
        "attribution": ATTRIBUTION,
        "tide_point": {"lon": point[0], "lat": point[1], "label": point[2]} if point else None,
        "tide_note": tide_note,
        "reports": len(reports),
        "report_window_h": REPORT_WINDOW_H,
    }
    (root / "live.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info(
        "live.forcing_written",
        city=city,
        cycle_ts=cycle_ts.isoformat(),
        members=int(cube.shape[0]),
        domain_mean_mm_3h=round(total_mm, 2),
        tide_rows=len(tide_rows),
    )
    return root


def read_live_forcing(bundle: str) -> LiveForcing:
    """The forcing :func:`write_live_bundle` left in ``bundles/<bundle>/``."""
    root = bundles_dir() / bundle
    meta = json.loads((root / "live.json").read_text(encoding="utf-8"))
    with np.load(root / "nwp.npz") as data:
        cube = np.asarray(data["rain_mm_h"], dtype=np.float64)
        transform = tuple(float(v) for v in data["transform"])
    cycle_ts = datetime.fromisoformat(meta["cycle_ts"])
    domain = _domain(meta["city"])
    notes = [
        f"Live cycle: no radar, so the rain is the {meta['model']} forecast, {meta['members']} "
        f"members interpolated from a 3 x 3 point grid onto the 500 m Sky grid ({SOURCE_URL}). "
        "The model runs at about 26 km, so it places rain over the region, not over a junction.",
        f"{ATTRIBUTION}.",
    ]
    if meta.get("tide_note"):
        notes.append(str(meta["tide_note"]))
    notes.append(
        f"{meta.get('reports', 0)} citizen report(s) from the last "
        f"{meta.get('report_window_h', REPORT_WINDOW_H):g} h were offered to Pulse."
    )
    return LiveForcing(
        cycle_ts=cycle_ts,
        times=tuple(cycle_ts + timedelta(minutes=STEP_MIN * (k + 1)) for k in range(cube.shape[1])),
        rain_mm_h=cube,
        transform=transform,  # type: ignore[arg-type]
        crs=domain.crs_string,
        res_m=float(domain.res_m),
        n_px=int(domain.n_px),
        model=str(meta["model"]),
        fetched_at=datetime.fromisoformat(meta["fetched_at"]),
        notes=tuple(notes),
    )


def live_sky(bundle: str, city: str):
    """``(ensemble, RainCycle)`` for a live folder, shaped exactly as a Sky cycle's."""
    from varuna_sky.products import load_aoi_grid, sky_products
    from varuna_sky.types import RadarGrid, RainEnsemble, ZRParams

    from varuna_cycle.sky_cycle import RainCycle

    forcing = read_live_forcing(bundle)
    grid = RadarGrid(
        crs=forcing.crs, res_m=forcing.res_m, n_px=forcing.n_px, transform=forcing.transform
    )
    ensemble = RainEnsemble(
        rain_mm_h=forcing.rain_mm_h,
        times=forcing.times,
        grid=grid,
        source="nwp_ensemble",
        seed=int(forcing.cycle_ts.timestamp()) % (2**31),
        zr=ZRParams(
            a=200.0,
            b=1.6,
            source="marshall_palmer",
            n_pairs=0,
            reason="No radar in a live cycle: the rain is an NWP forecast, so no Z-R was applied.",
        ),
    )
    products = sky_products(ensemble, load_aoi_grid(city))
    cycle = RainCycle(
        products=products,
        cycle_ts=forcing.cycle_ts,
        city=city,
        mode="live",
        bundle=bundle,
        nowcaster=forcing.model,
        seed=ensemble.seed,
        notes=forcing.notes,
    )
    return ensemble, cycle


def prune_live_runs(city: str, keep: int = KEEP_RUNS) -> list[str]:
    """Remove all but the newest ``keep`` live runs of ``city``; returns the ids removed."""
    root = data_dir() / "runs"
    code = _config(city).code
    live = sorted(
        (p for p in root.glob(f"{code}-*-live") if p.is_dir()),
        key=lambda p: p.name,
    )
    removed = []
    for path in live[:-keep] if keep > 0 else live:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    if removed:
        log.info("live.pruned", city=city, removed=removed)
    return removed


def run_live_cycle(city: str = "mumbai", when: datetime | None = None, **fetch: Any):
    """Fetch the live forcing, run one full cycle on it, publish it, and prune old live runs."""
    from varuna_cycle.twin_cycle import run_cycle

    cycle_ts = snap_cycle(when)
    write_live_bundle(city, cycle_ts, **fetch)
    result = run_cycle(live_bundle_id(city), cycle_ts, city=city, mode="live", overwrite=True)
    prune_live_runs(city)
    return result


def live_loop(city: str = "mumbai", every_min: int = 30, *, first_delay_s: float = 0.0) -> None:
    """Run a live cycle every ``every_min`` minutes, forever, surviving any one failure."""
    if first_delay_s > 0:
        time.sleep(first_delay_s)
    while True:
        started = time.monotonic()
        try:
            result = run_live_cycle(city)
            log.info(
                "live.cycle_done",
                run_id=result.run_id,
                total_ms=result.stage_ms.get("total"),
                peak_cm=round(result.peak_depth_cm, 1),
                wet_segments=result.wet_segments,
            )
        except Exception as error:
            log.warning("live.cycle_failed", city=city, error=str(error))
        elapsed = time.monotonic() - started
        time.sleep(max(60.0, every_min * 60.0 - elapsed))


def main(argv: list[str] | None = None) -> int:
    """``python -m varuna_cycle.live [--city mumbai] [--every 30] [--once] [--delay 0]``."""
    import argparse

    parser = argparse.ArgumentParser(description="Run VARUNA's live cycles from today's weather.")
    parser.add_argument("--city", default="mumbai")
    parser.add_argument("--every", type=int, default=30, help="minutes between cycles")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait first")
    args = parser.parse_args(argv)
    if args.once:
        if args.delay > 0:
            time.sleep(args.delay)
        result = run_live_cycle(args.city)
        print(
            f"{result.run_id}: {result.stage_ms.get('total', 0) / 1000:.1f} s, "
            f"{result.wet_segments} streets above 5 cm, deepest {result.peak_depth_cm:.0f} cm"
        )
        return 0
    live_loop(args.city, args.every, first_delay_s=args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
