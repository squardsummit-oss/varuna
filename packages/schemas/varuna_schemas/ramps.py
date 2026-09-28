"""Colour ramps for water depth, drain blockage and radar rain rate, derived from ``tokens.json``.

``services/products`` writes the depth PNGs and ``services/sky`` the radar frames with these
functions so the map pixels match the UI chips exactly (SPEC.md 6.7). Depth thresholds are
5 / 15 / 30 / 45 / 60 cm and rain thresholds 0.5 / 2 / 8 / 20 / 40 / 80 mm/h, both with
inclusive lower edges; anything below 5 cm is ``dry`` and anything below 0.5 mm/h is no echo,
and both are rendered fully transparent in rasters so the basemap shows through.

NumPy is imported lazily inside :func:`depth_array_to_rgba` and :func:`rain_array_to_rgba` so
importing this module stays cheap for services that only need chip colours.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import TYPE_CHECKING

from varuna_schemas.tokens import (
    DepthBand,
    DrainBand,
    RainBand,
    depth_bands,
    drain_bands,
    probability_min_opacity,
    rain_bands,
    status_colors,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

PALETTE_SIZE = 256
"""Entries in the indexed depth palette: index = depth in whole cm, 255 clamps."""

NO_ECHO_RGBA: RGBA = (0, 0, 0, 0)
"""A radar pixel with no echo, or outside the coverage circle: fully transparent."""


# ----------------------------------------------------------------------------- hex
def hex_to_rgb(value: str) -> RGB:
    """``"#3B82F6"`` -> ``(59, 130, 246)``. Accepts 3- or 6-digit hex with or without ``#``."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        msg = f"Expected a #RRGGBB colour, got {value!r}"
        raise ValueError(msg)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        msg = f"Expected a #RRGGBB colour, got {value!r}"
        raise ValueError(msg) from exc


def hex_to_rgba(value: str, alpha: int = 255) -> RGBA:
    """``hex_to_rgb`` plus an alpha channel (0-255)."""
    _check_alpha(alpha)
    r, g, b = hex_to_rgb(value)
    return (r, g, b, alpha)


def rgb_to_hex(rgb: RGB | RGBA) -> str:
    """``(59, 130, 246)`` -> ``"#3B82F6"`` (alpha, if present, is ignored)."""
    r, g, b = rgb[:3]
    return f"#{r:02X}{g:02X}{b:02X}"


def _check_alpha(alpha: int) -> None:
    if not 0 <= alpha <= 255:
        msg = f"alpha must be 0-255, got {alpha}"
        raise ValueError(msg)


# ----------------------------------------------------------------------------- depth
@lru_cache(maxsize=1)
def _depth_bands() -> tuple[DepthBand, ...]:
    return tuple(depth_bands())


@lru_cache(maxsize=1)
def _depth_edges_cm() -> tuple[float, ...]:
    """Lower edges of bands 1..5 (5, 15, 30, 45, 60)."""
    return tuple(band.min_cm for band in _depth_bands()[1:])


def depth_band_index(cm: float) -> int:
    """0 for dry (< 5 cm, negative or NaN), 1..5 for the coloured bands; 5 for >= 60 cm."""
    if cm is None or math.isnan(cm) or cm < _depth_edges_cm()[0]:
        return 0
    index = 0
    for edge in _depth_edges_cm():
        if cm >= edge:
            index += 1
        else:
            break
    return index


def depth_band(cm: float) -> DepthBand:
    """The :class:`DepthBand` a depth in cm falls into (lower edge inclusive)."""
    return _depth_bands()[depth_band_index(cm)]


def depth_color_hex(cm: float) -> str:
    """Hex colour for a depth in cm, e.g. ``depth_color_hex(55) == "#EF4444"``."""
    return depth_band(cm).hex


def depth_color_rgba(cm: float, alpha: int = 255) -> RGBA:
    """RGBA for a depth in cm. The dry band keeps the given alpha here (chips stay visible);
    rasters use :func:`depth_palette_png` or :func:`depth_array_to_rgba` where dry is transparent."""
    return hex_to_rgba(depth_color_hex(cm), alpha)


@lru_cache(maxsize=8)
def depth_lut_rgba(alpha: int = 255, dry_alpha: int = 0) -> tuple[RGBA, ...]:
    """Six RGBA rows indexed by :func:`depth_band_index`; row 0 (dry) uses ``dry_alpha``."""
    _check_alpha(alpha)
    _check_alpha(dry_alpha)
    rows: list[RGBA] = []
    for i, band in enumerate(_depth_bands()):
        rows.append(hex_to_rgba(band.hex, dry_alpha if i == 0 else alpha))
    return tuple(rows)


@lru_cache(maxsize=8)
def depth_palette_png(alpha: int = 255, dry_alpha: int = 0) -> tuple[RGBA, ...]:
    """A 256-entry RGBA palette: entry ``i`` is the colour of a depth of ``i`` cm.

    Entries below 5 cm carry ``dry_alpha`` (0 = fully transparent so the basemap shows
    through); entry 255 is the top band, so writers clamp depths >= 255 cm to 255.
    Suitable for a PNG in mode ``P`` with a ``tRNS`` chunk (see :func:`depth_palette_bytes`).
    """
    lut = depth_lut_rgba(alpha, dry_alpha)
    return tuple(lut[depth_band_index(float(i))] for i in range(PALETTE_SIZE))


def depth_palette_bytes(alpha: int = 255, dry_alpha: int = 0) -> tuple[bytes, bytes]:
    """``(rgb_bytes, alpha_bytes)``: 768 RGB bytes for ``Image.putpalette`` and 256 alpha
    bytes for the ``transparency`` PNG info key."""
    palette = depth_palette_png(alpha, dry_alpha)
    rgb = bytes(channel for entry in palette for channel in entry[:3])
    alphas = bytes(entry[3] for entry in palette)
    return rgb, alphas


def depth_array_to_rgba(
    depth_m: NDArray[np.floating],
    alpha: int = 255,
    dry_alpha: int = 0,
) -> NDArray[np.uint8]:
    """Vectorised ramp: an ``[H, W]`` array of depth in **metres** -> ``uint8 [H, W, 4]`` RGBA.

    Uses the same 5/15/30/45/60 cm edges as :func:`depth_band`. NaN, negative and
    sub-5 cm cells become the dry band with ``dry_alpha`` (transparent by default).
    """
    import numpy as np

    arr = np.asarray(depth_m, dtype=np.float64)
    if arr.ndim != 2:
        msg = f"depth_m must be a 2-D array [H, W], got shape {arr.shape}"
        raise ValueError(msg)
    cm = np.nan_to_num(arr * 100.0, nan=0.0, posinf=1e9, neginf=0.0)
    edges = np.asarray(_depth_edges_cm(), dtype=np.float64)
    index = np.digitize(cm, edges, right=False)  # 0..5, lower edge inclusive
    lut = np.asarray(depth_lut_rgba(alpha, dry_alpha), dtype=np.uint8)
    return lut[index]


def depth_cm_to_palette_index(depth_m: NDArray[np.floating]) -> NDArray[np.uint8]:
    """Depth in metres -> palette index (whole cm, clamped to 0..255) for a mode-``P`` PNG."""
    import numpy as np

    arr = np.asarray(depth_m, dtype=np.float64)
    cm = np.nan_to_num(arr * 100.0, nan=0.0, posinf=float(PALETTE_SIZE - 1), neginf=0.0)
    return np.clip(np.floor(cm), 0, PALETTE_SIZE - 1).astype(np.uint8)


# ----------------------------------------------------------------------------- drains
@lru_cache(maxsize=1)
def _drain_bands() -> tuple[DrainBand, ...]:
    return tuple(drain_bands())


def drain_band_index(beta: float) -> int:
    """0..3 for posterior blockage beta (clamped to [0, 1]; NaN -> 0). Lower edges inclusive,
    so 0.25 -> band 1, 0.5 -> band 2, 0.75 and above -> band 3."""
    if beta is None or math.isnan(beta):
        return 0
    value = min(max(beta, 0.0), 1.0)
    index = 0
    for band in _drain_bands()[1:]:
        if value >= band.min_beta:
            index += 1
        else:
            break
    return index


def drain_band(beta: float) -> DrainBand:
    return _drain_bands()[drain_band_index(beta)]


def drain_color_hex(beta: float) -> str:
    """Hex colour for a blockage beta, magenta when blocked (``beta >= 0.75``)."""
    return drain_band(beta).hex


def drain_color_rgba(beta: float, alpha: int = 255) -> RGBA:
    return hex_to_rgba(drain_color_hex(beta), alpha)


# ----------------------------------------------------------------------------- rain
@lru_cache(maxsize=1)
def _rain_bands() -> tuple[RainBand, ...]:
    return tuple(rain_bands())


@lru_cache(maxsize=1)
def _rain_edges_mm_h() -> tuple[float, ...]:
    """Lower edges of the six rain bands (0.5, 2, 8, 20, 40, 80)."""
    return tuple(band.min_mm_h for band in _rain_bands())


def rain_band_index(mm_h: float) -> int | None:
    """0..5 for the six rain bands, ``None`` for no echo.

    A radar pixel below the first edge (0.5 mm/h), negative, or NaN — NaN means no echo or
    outside the coverage circle — has no band and is drawn fully transparent. Lower edges are
    inclusive, so 20.0 mm/h is band 3 (``--rain-4``), the lower nowcast exceedance threshold.
    """
    if mm_h is None or math.isnan(mm_h) or mm_h < _rain_edges_mm_h()[0]:
        return None
    index = -1
    for edge in _rain_edges_mm_h():
        if mm_h >= edge:
            index += 1
        else:
            break
    return index


def rain_band(mm_h: float) -> RainBand | None:
    """The :class:`RainBand` a rain rate in mm/h falls into, or ``None`` for no echo."""
    index = rain_band_index(mm_h)
    return None if index is None else _rain_bands()[index]


def rain_color_hex(mm_h: float) -> str | None:
    """Hex colour for a rain rate in mm/h, ``None`` for no echo.

    e.g. ``rain_color_hex(45) == "#9AA6F9"`` (very heavy rain).
    """
    band = rain_band(mm_h)
    return None if band is None else band.hex


def rain_color_rgba(mm_h: float, alpha: int = 255) -> RGBA:
    """RGBA for a rain rate in mm/h. No echo (below 0.5 mm/h, negative or NaN) is
    ``(0, 0, 0, 0)`` so the basemap shows through."""
    _check_alpha(alpha)
    hex_value = rain_color_hex(mm_h)
    return NO_ECHO_RGBA if hex_value is None else hex_to_rgba(hex_value, alpha)


@lru_cache(maxsize=8)
def rain_lut_rgba(alpha: int = 255) -> tuple[RGBA, ...]:
    """Six RGBA rows indexed by :func:`rain_band_index`, one per rain band."""
    _check_alpha(alpha)
    return tuple(hex_to_rgba(band.hex, alpha) for band in _rain_bands())


@lru_cache(maxsize=8)
def _rain_lut_with_no_echo(alpha: int = 255) -> tuple[RGBA, ...]:
    """:func:`rain_lut_rgba` with a transparent no-echo row prepended, so ``np.digitize``
    output (0 = below the first edge) indexes it directly."""
    return (NO_ECHO_RGBA, *rain_lut_rgba(alpha))


def rain_palette_bytes(alpha: int = 255) -> tuple[bytes, bytes]:
    """``(rgb_bytes, alpha_bytes)`` for a mode-``P`` radar PNG: 21 RGB bytes for
    ``Image.putpalette`` and 7 alpha bytes for the ``transparency`` PNG info key.

    Palette index 0 is no echo (transparent) and 1..6 are the rain bands, which is what
    ``np.digitize(mm_h, rain edges)`` produces — unlike the depth palette, rain rate is not
    indexed by value, because it runs to cloudburst rates well past 255.
    """
    palette = _rain_lut_with_no_echo(alpha)
    rgb = bytes(channel for entry in palette for channel in entry[:3])
    alphas = bytes(entry[3] for entry in palette)
    return rgb, alphas


def rain_array_to_rgba(
    rain_mm_h: NDArray[np.floating],
    alpha: int = 255,
) -> NDArray[np.uint8]:
    """Vectorised ramp: an ``[H, W]`` array of rain rate in **mm/h** -> ``uint8 [H, W, 4]`` RGBA.

    Uses the same 0.5/2/8/20/40/80 mm/h edges as :func:`rain_band`. NaN (no echo or outside
    the coverage circle), negative and sub-0.5 mm/h cells become ``(0, 0, 0, 0)``.
    """
    import numpy as np

    arr = np.asarray(rain_mm_h, dtype=np.float64)
    if arr.ndim != 2:
        msg = f"rain_mm_h must be a 2-D array [H, W], got shape {arr.shape}"
        raise ValueError(msg)
    mm_h = np.nan_to_num(arr, nan=0.0, posinf=1e9, neginf=0.0)
    edges = np.asarray(_rain_edges_mm_h(), dtype=np.float64)
    index = np.digitize(mm_h, edges, right=False)  # 0 = no echo, 1..6 = bands
    lut = np.asarray(_rain_lut_with_no_echo(alpha), dtype=np.uint8)
    return lut[index]


# ----------------------------------------------------------------------------- misc
def probability_opacity(p: float) -> float:
    """Segment opacity in probability mode: ``P(> threshold)`` floored at the token minimum
    (0.15) so a segment never vanishes (SPEC.md 6.2)."""
    if p is None or math.isnan(p):
        return probability_min_opacity()
    return max(probability_min_opacity(), min(1.0, p))


def status_color_hex(mode: str) -> str:
    """Mode-banner colour for ``live``, ``replay``, ``baked`` or ``degraded``."""
    colors = status_colors()
    try:
        return colors[mode]
    except KeyError as exc:
        msg = f"Unknown status {mode!r}; expected one of {sorted(colors)}"
        raise KeyError(msg) from exc


__all__ = [
    "NO_ECHO_RGBA",
    "PALETTE_SIZE",
    "RGB",
    "RGBA",
    "depth_array_to_rgba",
    "depth_band",
    "depth_band_index",
    "depth_cm_to_palette_index",
    "depth_color_hex",
    "depth_color_rgba",
    "depth_lut_rgba",
    "depth_palette_bytes",
    "depth_palette_png",
    "drain_band",
    "drain_band_index",
    "drain_color_hex",
    "drain_color_rgba",
    "hex_to_rgb",
    "hex_to_rgba",
    "probability_opacity",
    "rain_array_to_rgba",
    "rain_band",
    "rain_band_index",
    "rain_color_hex",
    "rain_color_rgba",
    "rain_lut_rgba",
    "rain_palette_bytes",
    "rgb_to_hex",
    "status_color_hex",
]
