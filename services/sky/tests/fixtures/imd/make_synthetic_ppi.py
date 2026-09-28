"""Draw a synthetic IMD-style PPI(Z) image whose every answer is known by construction.

This is **not** IMD's image and contains nothing of it but the legend colours, which the decoder
module records from IMD's own colour bar (``IMD_PPI_Z_LEGEND_RGB``). It is drawn at the size and
layout of the Colaba close-range product so the decoder's default layout reads it, with:

* the site at a centre and a scale that differ from the layout's starting guess and from the
  scale the real image prints, so the ring fit has to find both;
* range rings at 50, 100 and 150 km and spokes every 30 degrees, in black;
* echo drawn as annular sectors at known bearings, ranges and legend classes;
* a coastline (IMD's blue) and a block of grey "text" drawn over echo, and one sector painted in
  a colour that is not in the legend - all three must come back masked, never guessed;
* the legend column at the layout's position, strongest class on top, under a "No Data" box.

Run as a script to rewrite the committed ``synthetic_ppi.png`` beside it; the test checks that
the committed file and this generator still agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
from varuna_sky.decode_imd import IMD_PPI_Z_LEGEND_RGB, MUMBAI_COLABA_PPI_Z

WIDTH, HEIGHT = MUMBAI_COLABA_PPI_Z.image_size
CENTRE = (394.6, 395.3)
"""Where the site is drawn, ``(col, row)``: 0.6 px and 0.7 px from the layout's guess."""

KM_PER_PX = 0.4270
"""The drawn scale: not the 0.4 km/px the real image prints."""

BACKGROUND = MUMBAI_COLABA_PPI_Z.background_rgb
INK = (0, 0, 0)
COAST = (51, 51, 255)
TEXT_GREYS = ((90, 90, 90), (26, 26, 26), (163, 163, 163))
UNKNOWN = (10, 200, 10)
LOWER_DBZ = MUMBAI_COLABA_PPI_Z.class_lower_dbz


@dataclass(frozen=True)
class Sector:
    """An annular sector of echo: bearings clockwise from north, ranges in km."""

    bearing_deg: tuple[float, float]
    range_km: tuple[float, float]
    class_index: int
    """Index into the legend, weakest first; -1 paints :data:`UNKNOWN` instead."""

    @property
    def dbz(self) -> float:
        return LOWER_DBZ[self.class_index]

    @property
    def centre(self) -> tuple[float, float]:
        """``(bearing_deg, range_km)`` of the sector's middle."""
        return (sum(self.bearing_deg) / 2.0, sum(self.range_km) / 2.0)


SECTORS = (
    Sector((20.0, 35.0), (20.0, 28.0), 11),  # 44 dBZ, crossed by the coastline
    Sector((330.0, 345.0), (30.0, 36.0), 6),  # 24 dBZ, with a text block over it
    Sector((60.0, 80.0), (12.0, 16.0), 14),  # 56 dBZ and above, the open top class
    Sector((2.0, 28.0), (5.0, 10.0), 0),  # 0 dBZ, the weakest class, across the north spoke
    Sector((120.0, 140.0), (10.0, 14.0), -1),  # a colour that is not in the legend
)

TEXT_BOX = (357, 300, 366, 306)
"""``(col0, row0, col1, row1)`` of the grey block, inside the 24 dBZ sector."""

COASTLINE = ((430.0, 330.0), (445.0, 336.0), (452.0, 346.0))
"""Vertices ``(col, row)`` of the coastline, which crosses the 44 dBZ sector."""


@dataclass(frozen=True)
class Truth:
    """What the fixture put where."""

    overlay: np.ndarray
    """Pixels drawn in a colour that is neither a legend class nor the background."""


def _polar(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float64)
    east = (cols - CENTRE[0]) * KM_PER_PX
    north = (CENTRE[1] - rows) * KM_PER_PX
    bearing = np.degrees(np.arctan2(east, north)) % 360.0
    return bearing, np.hypot(east, north)


def _line(
    img: np.ndarray,
    mask: np.ndarray,
    p0: tuple[float, float],
    p1: tuple[float, float],
    colour: tuple[int, int, int],
) -> None:
    n = int(np.ceil(np.hypot(p1[0] - p0[0], p1[1] - p0[1]) * 2)) + 1
    cols = np.round(np.linspace(p0[0], p1[0], n)).astype(int)
    rows = np.round(np.linspace(p0[1], p1[1], n)).astype(int)
    ok = (rows >= 0) & (rows < img.shape[0]) & (cols >= 0) & (cols < img.shape[1])
    img[rows[ok], cols[ok]] = colour
    mask[rows[ok], cols[ok]] = True


def draw(
    ring_ranges_km: tuple[float, ...] = (50.0, 100.0, 150.0),
    n_swatches: int = len(IMD_PPI_Z_LEGEND_RGB),
) -> tuple[np.ndarray, Truth]:
    """The fixture image as ``(height, width, 3)`` uint8, and where its overlays are.

    ``ring_ranges_km`` and ``n_swatches`` exist so tests can draw a broken image on purpose.
    """
    img = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    img[:] = BACKGROUND
    overlay = np.zeros((HEIGHT, WIDTH), dtype=bool)
    bearing, range_km = _polar((HEIGHT, WIDTH))

    for sector in SECTORS:
        b0, b1 = sector.bearing_deg
        r0, r1 = sector.range_km
        inside = (bearing >= b0) & (bearing < b1) & (range_km >= r0) & (range_km < r1)
        if sector.class_index < 0:
            img[inside] = UNKNOWN
            overlay |= inside
        else:
            img[inside] = IMD_PPI_Z_LEGEND_RGB[sector.class_index]

    c0, r0, c1, r1 = TEXT_BOX
    for k, row in enumerate(range(r0, r1)):
        img[row, c0:c1] = TEXT_GREYS[k % len(TEXT_GREYS)]
    overlay[r0:r1, c0:c1] = True

    for p0, p1 in pairwise(COASTLINE):
        _line(img, overlay, p0, p1, COAST)

    radius_px = range_km / KM_PER_PX
    for ring_km in ring_ranges_km:
        on_ring = np.abs(radius_px - ring_km / KM_PER_PX) <= 0.6
        img[on_ring] = INK
        overlay |= on_ring
    outer = max(ring_ranges_km) / KM_PER_PX
    for az in range(0, 360, 30):
        theta = np.radians(az)
        end = (CENTRE[0] + outer * np.sin(theta), CENTRE[1] - outer * np.cos(theta))
        _line(img, overlay, CENTRE, end, INK)

    # Header and status bar, as on the real product: outside the plot box, never observed.
    img[0:40, :] = BACKGROUND
    img[750:, :] = (184, 207, 229)
    img[:, 788:790] = INK

    # Legend: a black-bordered "No Data" box, then the swatches, strongest on top.
    legend = MUMBAI_COLABA_PPI_Z.legend_box
    img[456:473, 795:831] = BACKGROUND
    img[456, 795:832] = INK
    img[472, 795:832] = INK
    img[456:473, 795] = INK
    img[456:473, 831] = INK
    colours = IMD_PPI_Z_LEGEND_RGB[::-1][:n_swatches]
    for k, colour in enumerate(colours):
        top = 475 + 18 * k
        img[top : top + 16, legend[0] : legend[2]] = colour

    plot = np.zeros_like(overlay)
    pc0, pr0, pc1, pr1 = MUMBAI_COLABA_PPI_Z.plot_box
    plot[pr0:pr1, pc0:pc1] = True
    return img, Truth(overlay=overlay & plot)


if __name__ == "__main__":  # pragma: no cover - regenerates the committed PNG
    from PIL import Image

    image, _ = draw()
    Image.fromarray(image).save(Path(__file__).with_name("synthetic_ppi.png"), optimize=True)
