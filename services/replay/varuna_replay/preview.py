"""Bundle cubes as preview images for the storm designer (SPEC.md 7.8, task P2.10).

The ``/replay`` screen has to animate the radar frames of the selected bundle and show the
area-of-interest accumulation beside them, and a browser cannot open a Zarr store. This module
is the server side of that: it reads a bundle's cubes through :mod:`varuna_replay.bundle` and
hands back PNG bytes plus the small index the player needs to step through them.

Two honesty rules shape what it draws:

* The radar cube is quantised dBZ, so the preview inverts it with
  :func:`varuna_replay.storm.rain_from_dbz` and colours the **rain rate**, exactly as Sky will
  in Phase 3. A preview coloured straight from dBZ would show a sharper storm than the one the
  forecast actually sees.
* Colours come from the shared rain ramp in ``packages/tokens`` (via
  :func:`varuna_schemas.ramps.rain_array_to_rgba`), so a preview pixel and a UI chip of the
  same rate are the same colour. No hex value is written here.

Rendering is deterministic: the same cube renders to the same bytes every time (rule 8).
"""

from __future__ import annotations

import io
import math
from datetime import timedelta
from typing import Any

import numpy as np
import structlog
from PIL import Image
from varuna_schemas.constants import IST
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.ramps import rain_array_to_rgba
from varuna_schemas.tokens import RainBand, rain_bands

from varuna_replay.bundle import (
    RADAR_VARIABLE,
    TRUTH_VARIABLE,
    BundleLayout,
    BundleNotFoundError,
    CubeInfo,
    load_manifest,
    read_cube,
    read_cube_info,
)
from varuna_replay.domain import StormDomain
from varuna_replay.storm import accumulation_mm, rain_from_dbz

log = structlog.get_logger("varuna.replay.preview")

ACCUMULATION_LABEL = "Accumulation over the replay window, in mm"
"""What the accumulation preview shows. The UI puts this beside the image."""

ACCUMULATION_LEGEND_NOTE = (
    "Accumulated depth in mm, coloured by the rain-rate band edges so the preview and the "
    "radar frames share one legend."
)
"""The accumulation is a depth, the ramp is a rate ladder; say so rather than let a reader
assume the numbers on the legend are millimetres per hour (SPEC.md 0.6)."""


def _cube_domain(info: CubeInfo) -> StormDomain:
    """Rebuild the storm grid a cube was written on from its own attributes.

    ``transform`` is the north-up ``(res, 0, left, 0, -res, top)`` the writer stored, so the
    grid comes back without re-reading the city config.
    """
    if len(info.transform) != 6:
        msg = f"{info.path} carries no 6-coefficient transform; it was not written by VARUNA."
        raise ValueError(msg)
    return StormDomain(
        crs=int(str(info.crs).removeprefix("EPSG:")),
        res_m=float(info.res_m),
        n_px=int(info.n_px),
        left=float(info.transform[2]),
        top=float(info.transform[5]),
    )


def _encode_png(rgba: np.ndarray) -> bytes:
    """RGBA array to PNG bytes, written the way ``varuna_city.rasters`` writes its rasters."""
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba, dtype=np.uint8), mode="RGBA").save(
        buffer, format="PNG", optimize=True
    )
    return buffer.getvalue()


def radar_frame_png(layout: BundleLayout, index: int) -> bytes:
    """One radar frame of a bundle as an RGBA PNG on the rain ramp.

    The frame is read as dBZ, inverted to mm/h with :func:`rain_from_dbz` - so no echo and
    everything below the ramp's 0.5 mm/h floor becomes fully transparent - and coloured with
    the shared rain ramp.

    Args:
        layout: the bundle whose ``radar/frames.zarr`` to read.
        index: frame number, 0 to ``n_frames - 1``.

    Raises:
        IndexError: when ``index`` is outside the cube; the message says how many frames
            the bundle has.
    """
    info = read_cube_info(layout.radar, RADAR_VARIABLE)
    n_frames = info.n_times
    if not -n_frames <= index < n_frames:
        msg = (
            f"Frame {index} is outside {layout.bundle_id}: it has {n_frames} radar frames "
            f"(0 to {n_frames - 1})."
        )
        raise IndexError(msg)
    frame = read_cube(layout.radar, RADAR_VARIABLE)[index]
    png = _encode_png(rain_array_to_rgba(rain_from_dbz(frame)))
    log.info(
        "replay.preview_frame",
        bundle=layout.bundle_id,
        index=int(index),
        bytes=len(png),
        shape=list(frame.shape),
    )
    return png


def _accumulation_rule(layout: BundleLayout) -> str:
    """Which integration rule this bundle's truth cube needs.

    A design storm is block-constant, so its blocks integrate exactly with the left rule; a
    reconstructed convective field is sampled at instants and wants the trapezoid rule
    (:func:`varuna_replay.storm.accumulation_mm`).
    """
    try:
        manifest = load_manifest(layout.root)
    except (BundleNotFoundError, ValueError):
        return "trapezoid"
    return "left" if manifest.design_storm is not None else "trapezoid"


def accumulation_png(layout: BundleLayout, *, rule: str | None = None) -> bytes:
    """The bundle's accumulated rainfall over its whole window as an RGBA PNG.

    Integrates ``truth/rain.zarr`` - the mm/h field, never the quantised radar frames, which
    under-read by up to :data:`varuna_replay.storm.QUANTISATION_RATIO` - and colours the
    resulting depth with the same ramp the frames use. See
    :data:`ACCUMULATION_LEGEND_NOTE` for what that shared legend does and does not mean.

    Args:
        layout: the bundle whose ``truth/rain.zarr`` to integrate.
        rule: ``"trapezoid"`` or ``"left"``; by default the manifest decides.
    """
    info = read_cube_info(layout.truth, TRUTH_VARIABLE)
    cube = read_cube(layout.truth, TRUTH_VARIABLE)
    depth_mm = accumulation_mm(cube, info.step_min, rule=rule or _accumulation_rule(layout))
    png = _encode_png(rain_array_to_rgba(depth_mm))
    log.info(
        "replay.preview_accumulation",
        bundle=layout.bundle_id,
        bytes=len(png),
        max_mm=round(float(np.nanmax(depth_mm)), 2) if depth_mm.size else 0.0,
    )
    return png


def _band_row(band: RainBand) -> dict[str, Any]:
    """One legend row: what the colour means, in the ramp's own units."""
    return {"min_mm_h": band.min_mm_h, "hex": band.hex, "label": band.label}


def _aoi_pixels(info: CubeInfo, manifest: BundleManifest) -> dict[str, int]:
    """The AOI rectangle in cube pixels, clamped to the grid.

    ``project_bbox`` samples the bbox edges before projecting, so a bent UTM graticule cannot
    clip the area of interest; the rectangle is then rounded outward so it never cuts a pixel
    the AOI touches.
    """
    domain = _cube_domain(info)
    left_m, bottom_m, right_m, top_m = domain.project_bbox(manifest.aoi)
    res = domain.res_m
    left = math.floor((left_m - domain.left) / res)
    right = math.ceil((right_m - domain.left) / res)
    top = math.floor((domain.top - top_m) / res)
    bottom = math.ceil((domain.top - bottom_m) / res)
    left = max(0, min(domain.n_px, left))
    right = max(left, min(domain.n_px, right))
    top = max(0, min(domain.n_px, top))
    bottom = max(top, min(domain.n_px, bottom))
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def radar_index(manifest: BundleManifest, layout: BundleLayout) -> dict[str, Any]:
    """Everything the storm-designer player needs to step through the radar frames.

    Returns a JSON-ready dict: the frame count and cadence, the pixel size of a frame, one row
    per frame with its IST timestamp, the area-of-interest rectangle in cube pixels, and the
    ramp bands so the legend is drawn from the same tokens as the images.
    """
    info = read_cube_info(layout.radar, RADAR_VARIABLE)
    height, width = int(info.shape[1]), int(info.shape[2])
    step = float(info.step_min)
    frames = [
        {
            "index": index,
            "ts": (info.t0 + timedelta(minutes=index * step)).astimezone(IST).isoformat(),
        }
        for index in range(info.n_times)
    ]
    index_doc: dict[str, Any] = {
        "bundle": manifest.id,
        "label": manifest.label,
        "variable": RADAR_VARIABLE,
        "n_frames": info.n_times,
        "step_min": step,
        "width": width,
        "height": height,
        "t0": info.t0.astimezone(IST).isoformat(),
        "frames": frames,
        "aoi_px": _aoi_pixels(info, manifest),
        "bands": [_band_row(band) for band in rain_bands()],
        "accumulation_label": ACCUMULATION_LABEL,
        "accumulation_note": ACCUMULATION_LEGEND_NOTE,
    }
    log.info(
        "replay.preview_index",
        bundle=manifest.id,
        n_frames=info.n_times,
        step_min=step,
        size=f"{width} x {height}",
    )
    return index_doc


__all__ = [
    "ACCUMULATION_LABEL",
    "ACCUMULATION_LEGEND_NOTE",
    "accumulation_png",
    "radar_frame_png",
    "radar_index",
]
