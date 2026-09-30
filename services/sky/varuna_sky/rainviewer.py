"""Live radar from RainViewer's public mosaic: the frames a live cycle's nowcast starts from.

RainViewer (listed in public-apis, no key) merges 1,200+ national weather radars, IMD's among
them, into one mosaic every 10 minutes. Its free API has been narrowed since 1 January 2026
(https://www.rainviewer.com/api/transition-faq.html): the last two hours of past frames only, no
nowcast of its own, zoom 7 at most - about 1.16 km a pixel over Mumbai - one colour scheme
(Universal Blue), 100 requests per IP per minute, free for personal or educational use, and a
link to rainviewer.com wherever the data is shown. That is what VARUNA-Sky needs: three frames
ten minutes apart, which pySTEPS turns into motion and a 20-member STEPS nowcast, so a live cycle
nowcasts from observed radar rather than from a weather model alone.

**Decoding.** Universal Blue gives each whole dBZ from -10 to 64 its own RGBA, taken from
RainViewer's published table (:data:`TABLE_URL`, retrieved 2026-09-30); 65-74 dBZ share white
and 75 and above share green, so those decode to the lower bound of their band. A transparent
pixel inside radar coverage is *no echo* and becomes :data:`NO_ECHO_DBZ`, under Sky's 0.1 mm/h
dry floor; a pixel outside coverage (RainViewer's coverage layer is opaque there) is ``nan``,
which Sky reads as missing rather than dry. Measured on 2026-09-30 over India: every opaque
pixel of four zoom-4 tiles matched the table exactly, 0 to 65 dBZ.

**Resampling.** Each Sky cell centre (UTM, 500 m) is mapped to its Web-Mercator pixel and the
mosaic is sampled bilinearly in dBZ; a cell next to missing radar stays missing. Upsampling
1.16 km to 500 m adds no detail, and the run's notes say the radar is a 1.2 km mosaic.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger("varuna.sky.rainviewer")

INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"
TABLE_URL = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv"
TERMS_URL = "https://www.rainviewer.com/api.html"
ATTRIBUTION = "Radar: RainViewer (rainviewer.com)"
ZOOM = 7
"""The free API's ceiling since 2026-01-01; zoom 8 and above return one placeholder tile."""
TILE_PX = 256
COLOR_SCHEME = 2
"""Universal Blue, the one scheme the free API still serves."""
NO_ECHO_DBZ = -10.0
"""What a covered pixel with no echo reads as: -10 dBZ is 0.009 mm/h through Marshall-Palmer,
under Sky's 0.1 mm/h dry floor, so it is dry without being missing."""

UNIVERSAL_BLUE: dict[str, int] = {
    "#63615914": -10,
    "#66635a19": -9,
    "#69665c1e": -8,
    "#6c685d24": -7,
    "#6f6b5f29": -6,
    "#726e612e": -5,
    "#75706234": -4,
    "#78736439": -3,
    "#7c75653e": -2,
    "#7f786744": -1,
    "#827b6949": 0,
    "#857d6a4e": 1,
    "#88806c54": 2,
    "#8b826d59": 3,
    "#8e856f5e": 4,
    "#92887164": 5,
    "#9e93756e": 6,
    "#aa9e7978": 7,
    "#b6a97e82": 8,
    "#c2b4828c": 9,
    "#cec08796": 10,
    "#d2c48ba0": 11,
    "#d6c88faa": 12,
    "#dacc93b4": 13,
    "#ded097be": 14,
    "#88ddeeff": 15,
    "#6cd1ebff": 16,
    "#51c5e8ff": 17,
    "#36bae5ff": 18,
    "#1baee2ff": 19,
    "#00a3e0ff": 20,
    "#009ad5ff": 21,
    "#0091caff": 22,
    "#0088bfff": 23,
    "#007fb4ff": 24,
    "#0077aaff": 25,
    "#0070a3ff": 26,
    "#00699cff": 27,
    "#006295ff": 28,
    "#005b8eff": 29,
    "#005588ff": 30,
    "#005180ff": 31,
    "#004e78ff": 32,
    "#004a70ff": 33,
    "#004768ff": 34,
    "#ffee00ff": 35,
    "#ffe000ff": 36,
    "#ffd200ff": 37,
    "#ffc500ff": 38,
    "#ffb700ff": 39,
    "#ffaa00ff": 40,
    "#ff9f00ff": 41,
    "#ff9500ff": 42,
    "#ff8b00ff": 43,
    "#ff8100ff": 44,
    "#ff4400ff": 45,
    "#f23600ff": 46,
    "#e62800ff": 47,
    "#d91b00ff": 48,
    "#cd0d00ff": 49,
    "#c10000ff": 50,
    "#a80000ff": 51,
    "#8f0000ff": 52,
    "#760000ff": 53,
    "#5d0000ff": 54,
    "#ffaaffff": 55,
    "#ff9fffff": 56,
    "#ff95ffff": 57,
    "#ff8bffff": 58,
    "#ff81ffff": 59,
    "#ff77ffff": 60,
    "#ff6cffff": 61,
    "#ff62ffff": 62,
    "#ff58ffff": 63,
    "#ff4effff": 64,
    "#ffffffff": 65,
    "#00ff00ff": 75,
}
"""RGBA (lower-case hex) -> dBZ for the rain half of RainViewer's Universal Blue table."""


def _pack(colour: str) -> int:
    return int(colour.lstrip("#"), 16)


_PACKED = {_pack(c): float(d) for c, d in UNIVERSAL_BLUE.items()}


@dataclass(frozen=True, slots=True)
class RadarFrameRef:
    """One past frame in RainViewer's index: its valid time and its tile path."""

    time: datetime
    path: str


def radar_index(get_json: Callable[..., Any]) -> tuple[str, list[RadarFrameRef]]:
    """The tile host and the past frames, oldest first."""
    body = get_json(INDEX_URL, timeout=20.0)
    host = str(body["host"])
    frames = [
        RadarFrameRef(time=datetime.fromtimestamp(int(f["time"]), tz=UTC), path=str(f["path"]))
        for f in (body.get("radar") or {}).get("past") or []
    ]
    return host, sorted(frames, key=lambda f: f.time)


def mercator_px(
    lon: np.ndarray, lat: np.ndarray, zoom: int = ZOOM
) -> tuple[np.ndarray, np.ndarray]:
    """Global Web-Mercator pixel coordinates (fractional) at ``zoom``."""
    n = TILE_PX * (2**zoom)
    x = (np.asarray(lon, dtype=np.float64) + 180.0) / 360.0 * n
    rad = np.radians(np.asarray(lat, dtype=np.float64))
    y = (1.0 - np.log(np.tan(rad) + 1.0 / np.cos(rad)) / math.pi) / 2.0 * n
    return x, y


def _rgba(png: bytes) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def decode_tile(png: bytes) -> np.ndarray:
    """A Universal Blue tile as dBZ: ``nan`` where transparent, the table's dBZ elsewhere.

    A colour the table does not carry (a resampled edge, a changed palette) is also ``nan``;
    :func:`decode_stats` counts them, so a palette change shows in the log rather than as a
    dry city.
    """
    rgba = _rgba(png).astype(np.uint32)
    packed = (rgba[..., 0] << 24) | (rgba[..., 1] << 16) | (rgba[..., 2] << 8) | rgba[..., 3]
    out = np.full(packed.shape, np.nan, dtype=np.float32)
    for colour, dbz in _PACKED.items():
        out[packed == colour] = dbz
    return out


def decode_stats(png: bytes) -> tuple[int, int]:
    """``(opaque pixels, of which decoded)`` for a tile: equal unless the palette moved."""
    opaque = int((_rgba(png)[..., 3] > 0).sum())
    decoded = int(np.isfinite(decode_tile(png)).sum())
    return opaque, decoded


def covered_tile(png: bytes) -> np.ndarray:
    """RainViewer's coverage layer: ``True`` where a radar sees (the layer is transparent)."""
    return _rgba(png)[..., 3] == 0


def bilinear(field: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sample ``field`` at fractional pixel coordinates (pixel ``i`` is centred on ``i + 0.5``).

    A cell is missing when a missing corner carries weight; a missing corner at zero weight -
    the cell sits exactly on its neighbour's row or column - does not blank it.
    """
    h, w = field.shape
    x = np.clip(np.asarray(x, dtype=np.float64) - 0.5, 0.0, w - 1.0)
    y = np.clip(np.asarray(y, dtype=np.float64) - 0.5, 0.0, h - 1.0)
    x0 = np.floor(x).astype(np.intp)
    y0 = np.floor(y).astype(np.intp)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = x - x0
    fy = y - y0
    total = np.zeros(np.shape(x), dtype=np.float64)
    missing = np.zeros(np.shape(x), dtype=bool)
    for yy, xx, weight in (
        (y0, x0, (1 - fx) * (1 - fy)),
        (y0, x1, fx * (1 - fy)),
        (y1, x0, (1 - fx) * fy),
        (y1, x1, fx * fy),
    ):
        value = field[yy, xx]
        finite = np.isfinite(value)
        missing |= ~finite & (weight > 0)
        total += np.where(finite, value, 0.0) * weight
    return np.where(missing, np.nan, total)


@dataclass(frozen=True, slots=True)
class LiveRadar:
    """The newest frames on the Sky grid, oldest first, ready for :class:`RadarFrames`."""

    dbz: np.ndarray
    """``(n_frames, n_px, n_px)``: dBZ, :data:`NO_ECHO_DBZ` where covered and dry, ``nan``
    outside coverage."""
    times: tuple[datetime, ...]
    covered_fraction: float
    max_dbz: float
    notes: tuple[str, ...]


def fetch_radar(
    cell_lon: np.ndarray,
    cell_lat: np.ndarray,
    *,
    n_frames: int = 3,
    get_json: Callable[..., Any],
    get_bytes: Callable[..., bytes],
) -> LiveRadar | None:
    """The newest ``n_frames`` RainViewer frames resampled onto the given cell centres.

    Returns ``None`` when the index carries fewer frames than asked for, so the caller can fall
    back to the NWP forcing and say why. A 60 km domain needs one or two zoom-7 tiles, so a
    cycle makes about eight requests, far under the 100 a minute the free API allows.
    """
    host, frames = radar_index(get_json)
    if len(frames) < n_frames:
        log.warning("rainviewer.too_few_frames", frames=len(frames), wanted=n_frames)
        return None
    chosen = frames[-n_frames:]
    gx, gy = mercator_px(cell_lon, cell_lat)
    tx0, ty0 = int(np.floor(gx.min() / TILE_PX)), int(np.floor(gy.min() / TILE_PX))
    tx1, ty1 = int(np.floor(gx.max() / TILE_PX)), int(np.floor(gy.max() / TILE_PX))
    height, width = (ty1 - ty0 + 1) * TILE_PX, (tx1 - tx0 + 1) * TILE_PX
    lx, ly = gx - tx0 * TILE_PX, gy - ty0 * TILE_PX

    def mosaic(url_of: Callable[[int, int], str], decode: Callable[[bytes], np.ndarray], fill):
        out = np.full((height, width), fill)
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                tile = decode(get_bytes(url_of(tx, ty), timeout=20.0))
                oy, ox = (ty - ty0) * TILE_PX, (tx - tx0) * TILE_PX
                out[oy : oy + TILE_PX, ox : ox + TILE_PX] = tile
        return out

    covered = mosaic(
        lambda x, y: f"{host}/v2/coverage/0/{TILE_PX}/{ZOOM}/{x}/{y}/0/0_0.png",
        covered_tile,
        True,
    ).astype(bool)
    stack = []
    for frame in chosen:
        raw = mosaic(
            lambda x, y, p=frame.path: f"{host}{p}/{TILE_PX}/{ZOOM}/{x}/{y}/{COLOR_SCHEME}/0_0.png",
            decode_tile,
            np.nan,
        ).astype(np.float64)
        filled = np.where(covered, np.where(np.isfinite(raw), raw, NO_ECHO_DBZ), np.nan)
        stack.append(bilinear(filled, lx, ly))
    dbz = np.stack(stack).astype(np.float64)
    covered_fraction = float(np.isfinite(dbz[-1]).mean())
    max_dbz = float(np.nanmax(dbz)) if np.isfinite(dbz).any() else float("nan")
    first, last = chosen[0].time, chosen[-1].time
    notes = (
        f"Live radar: the {n_frames} newest RainViewer mosaic frames ({first:%H:%M} to "
        f"{last:%H:%M} UTC, 10 min apart) at zoom {ZOOM}, about 1.2 km a pixel, resampled onto "
        f"the 500 m Sky grid; {covered_fraction:.0%} of the domain inside radar coverage. "
        f"{ATTRIBUTION}, free for educational use ({TERMS_URL}).",
    )
    log.info(
        "rainviewer.frames",
        frames=[f.time.isoformat() for f in chosen],
        covered=round(covered_fraction, 3),
        max_dbz=None if math.isnan(max_dbz) else round(max_dbz, 1),
        tiles=(tx1 - tx0 + 1) * (ty1 - ty0 + 1),
    )
    return LiveRadar(
        dbz=dbz,
        times=tuple(f.time for f in chosen),
        covered_fraction=covered_fraction,
        max_dbz=max_dbz,
        notes=notes,
    )


__all__ = [
    "ATTRIBUTION",
    "INDEX_URL",
    "NO_ECHO_DBZ",
    "UNIVERSAL_BLUE",
    "LiveRadar",
    "RadarFrameRef",
    "bilinear",
    "covered_tile",
    "decode_stats",
    "decode_tile",
    "fetch_radar",
    "mercator_px",
    "radar_index",
]
