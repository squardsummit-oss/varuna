"""Decode an IMD Doppler weather radar image into dBZ on the Sky grid (SPEC.md P2.9).

IMD publishes each DWR's products as rendered images on ``mausam.imd.gov.in`` - for Mumbai,
``Radar/ppi_mum.gif`` (Colaba, "Plan Position Indicator (Z) - Close Range") and ``caz_mum.gif``
(MAX(Z)), linked from ``responsive/radar.php?id=Mumbai-Colaba``. The raw volumes need a MoES
request (SPEC.md 16, "Is the radar real?"); the images do not. This module turns one of those
images back into numbers, in three steps, and refuses rather than guesses at each:

1. **Legend lookup.** The colour bar printed beside the plot is read from the image itself: the
   uniform swatches in the legend box, top to bottom, are matched to the class boundaries the
   layout states. If the image does not carry exactly that many swatches the decoder raises -
   a legend it cannot read is a legend it would misread. Every plot pixel is then looked up in
   that table. A colour that is not a legend class and not the plot background - the range
   rings, the azimuth spokes, the coastline, station names, the header - is **masked**, never
   mapped to the nearest class. Masked pixels are simply not observed.
2. **Georeference.** The image states the radar site (for Colaba, ``18.9013N 72.8075E``) and
   draws range rings at known ranges. The ring pixels are fitted as concentric circles with one
   shared centre by least squares, and the fitted radii against the stated ranges give the
   image scale. The site is placed at the fitted centre, north up, on an azimuthal-equidistant
   plane centred on the site; the rms distance of the ring pixels from their fitted circles is
   the georeference residual, in pixels and kilometres, and it is returned rather than assumed.
3. **Resample to the Sky grid.** Every observed plot pixel inside the display range is carried
   through the site's azimuthal-equidistant plane to the Sky CRS (EPSG:32643 for Mumbai) and
   binned into the 500 m cell it lands in. A cell's value is the mean linear reflectivity of its
   observed pixels - each echo pixel at its class's lower bound, each echo-free pixel at zero -
   floored back to the class it falls in, so a cell of one class decodes to exactly that class
   and a mixed cell is never promoted above the strongest class it contains. A cell with no
   observed pixel is not covered.

The output is the storm designer's convention (``varuna_replay.storm.radar_dbz``): ``dBZ`` as
the class lower bound, ``nan`` for no echo *and* for no coverage, with a separate coverage mask
saying which of the two a ``nan`` is.

**IMD's legend is 4 dBZ wide, not 5.** P2.9 and SPEC.md 11.1 speak of 5 dBZ classes, which is
how the storm designer renders the bundles. The Colaba PPI(Z) legend read on 2026-09-22 has
fifteen classes 4 dBZ wide (0-4, 4-8, ..., 52-56, and 56 and above). The decoder reports the
classes the legend states and does not re-bin them into 5 dBZ classes: re-binning 4 dBZ classes
into 5 dBZ ones would move boundaries the image never measured. ``ImdFrame.class_width_dbz``
carries the width, and that is the value to pass as ``dbz_class_width`` to
:func:`varuna_sky.zr.run_zr` (or :func:`~varuna_sky.zr.gauge_pairs`) for a decoded frame.

What the decoder cannot do - the questions a radar meteorologist asks first:

* **It reads a picture, not a measurement.** A 4 dBZ class is all that survives; the rendering
  has already thrown away the moment data, the velocity, the polarimetric fields and the
  quality flags IMD's own processing used.
* **No attenuation correction.** At C-band and S-band alike, heavy rain between the radar and a
  cell hides part of the cell. :mod:`varuna_sky.qc` flags the shadow; nothing here restores it.
* **No beam-height correction.** A PPI is one elevation (0.2 degrees for the Colaba close-range
  product): at 50 km the beam centre is already about 0.3 km up and at 150 km about 1.8 km, so
  the far field sees rain aloft, above any bright band, not at the street. A CAPPI or a MAX(Z)
  composite answers a different question; this module decodes whichever product its layout
  describes and does not convert one into the other.
* **Slant range is taken as ground range.** At a 0.2 degree elevation the difference is below
  the 0.43 km pixel over the display range.
* **The scan time is not read.** The header prints it; there is no OCR here, so the caller
  supplies ``valid_ts``.
* **Masked pixels are holes.** Where a coastline or a place name is drawn over an echo, the
  echo under it is lost; the cell is decoded from its remaining pixels or not at all.

Pillow is used to read the file. It is not a declared dependency of ``varuna-sky``; it arrives
through pySTEPS, which Sky already requires.

Determinism (rule 8): nothing here is random. The same image gives the same frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog
from pyproj import Transformer

from varuna_sky.types import RadarFrames, RadarGrid

if TYPE_CHECKING:  # pragma: no cover - keeps numpy and pydantic out of the runtime type surface
    from datetime import datetime

    from numpy.typing import NDArray
    from varuna_schemas.models.city import RadarDomain

__all__ = [
    "IMD_PPI_Z_LEGEND_RGB",
    "MUMBAI_COLABA_PPI_Z",
    "ImdDecodeError",
    "ImdDiagnostics",
    "ImdFrame",
    "ImdLayout",
    "Legend",
    "RingFit",
    "classify_pixels",
    "decode_imd",
    "fit_range_rings",
    "frames_from_imd",
    "grid_for_radar_domain",
    "load_rgb",
    "read_legend",
]

log = structlog.get_logger("varuna.sky.decode_imd")

RGB = tuple[int, int, int]

BACKGROUND_CLASS = -1
"""Class index of an echo-free plot pixel (the plot background)."""

MASKED_CLASS = -2
"""Class index of a pixel whose colour is neither a legend class nor the background."""


class ImdDecodeError(ValueError):
    """The image cannot be decoded as the layout describes it; nothing is guessed instead."""


# ============================================================================ layout
IMD_PPI_Z_LEGEND_RGB: tuple[RGB, ...] = (
    (57, 0, 159),
    (0, 0, 199),
    (0, 51, 254),
    (0, 120, 254),
    (25, 162, 254),
    (82, 208, 254),
    (134, 240, 254),
    (254, 254, 254),
    (254, 247, 192),
    (254, 229, 0),
    (254, 188, 0),
    (254, 114, 0),
    (254, 62, 0),
    (199, 0, 0),
    (199, 0, 78),
)
"""IMD's reflectivity legend colours, weakest class first (0-4 dBZ) to strongest (56 dBZ and up).

Read from the swatches of IMD's own colour bar on ``https://mausam.imd.gov.in/Radar/ppi_mum.gif``
(DWR Mumbai, PPI(Z)) fetched 2026-09-22 09:46:59 UTC. The decoder does not rely on this table:
it reads the legend from each image it decodes. The table exists to draw the committed test
fixture in IMD's colours and to report whether a decoded image's legend has changed."""


@dataclass(frozen=True, slots=True)
class ImdLayout:
    """Where one IMD product draws its plot, its legend and its range rings.

    Pixel coordinates are ``(col, row)`` with row 0 at the top of the image; boxes are
    ``(col0, row0, col1, row1)`` inclusive of the first and exclusive of the second.
    """

    product: str
    site_name: str
    site_lat: float
    site_lon: float
    image_size: tuple[int, int]
    """``(width, height)`` in pixels; an image of another size is refused."""

    plot_box: tuple[int, int, int, int]
    """The region the radar data is drawn in. Nothing outside it is observed."""

    legend_box: tuple[int, int, int, int]
    """The region holding the colour bar swatches (and nothing else of the legend's colours)."""

    class_lower_dbz: tuple[float, ...]
    """Lower bound of each legend class, weakest first. The top class is open above."""

    class_width_dbz: float
    ring_ranges_km: tuple[float, ...]
    display_range_km: float
    stated_km_per_px: float
    """The scale the image prints. Reported beside the fitted scale, never used to place data:
    the rings are the georeference."""

    approx_centre_px: tuple[float, float]
    """Where the site is expected to sit, ``(col, row)``; the fit starts here."""

    legend_top_is_strongest: bool = True
    background_rgb: RGB = (204, 204, 204)
    ring_rgb: RGB = (0, 0, 0)
    colour_tolerance: int = 2
    """Largest per-channel difference at which a pixel still matches a legend colour.

    A design choice. IMD serves palette GIFs, whose colours are exact; 2 absorbs a re-encode
    to PNG or a colour-managed screenshot without ever bridging two legend classes (the
    closest pair, 4-8 and 8-12 dBZ, differ by 51 in green)."""

    min_swatch_rows: int = 8
    spoke_step_deg: float = 30.0
    """Azimuth spacing of the spokes drawn from the site; 0 when the product draws none."""

    spoke_exclusion_deg: float = 3.0
    """Ring pixels this close in azimuth to a spoke are left out of the ring fit."""

    scale_search_km_per_px: tuple[float, float] = (0.1, 2.0)
    ring_search_band_px: float = 4.0
    ring_keep_band_px: float = 2.5
    max_ring_disagreement: float = 0.02
    """Largest relative spread between the scales the individual rings imply before refusing.

    A design choice: 2 % is about 3 px on the Colaba 150 km ring. Rings that disagree by more
    are not concentric circles at the stated ranges, and nothing drawn on them can be placed."""


MUMBAI_COLABA_PPI_Z = ImdLayout(
    product="PPI(Z) close range",
    site_name="DWR Mumbai (Colaba)",
    site_lat=18.9013,
    site_lon=72.8075,
    image_size=(1078, 770),
    plot_box=(0, 40, 788, 750),
    legend_box=(795, 440, 831, 745),
    class_lower_dbz=tuple(float(v) for v in range(0, 60, 4)),
    class_width_dbz=4.0,
    ring_ranges_km=(50.0, 100.0, 150.0),
    display_range_km=150.0,
    stated_km_per_px=0.4,
    approx_centre_px=(394.0, 396.0),
)
"""The Colaba close-range PPI(Z) as served at ``https://mausam.imd.gov.in/Radar/ppi_mum.gif``.

Read by eye from the image fetched 2026-09-22 09:46:59 UTC: the header states the site
("DWR MUMBAI (18.9013N, 72.8075E, 100.0000 mts)"), "Display Range 150 Km" and "Display Res
0.4 Km/Pix"; rings are drawn at 50, 100 and 150 km; the legend has fifteen 4 dBZ classes from
"0.00: 4.00" to "56.00: >60.00". IMD's radar page places its Mumbai-Colaba marker at 19.0760N
72.8777E, which is the city, not the radar; the site here is the one the image itself states.
The header and the status bar are outside ``plot_box``, so the top and bottom of the 150 km ring
are clipped and not observed.

**The printed scale is not the drawn scale.** The rings of that image sit at 116.95, 233.86 and
350.70 px, which is 0.4277 km/px with the three rings agreeing to 0.06 %, not the 0.4 km/px the
header prints - 6.9 % apart, which at 150 km is 24 px (10 km). The station markers IMD draws
(Pune, Dahanu, Harnai) sit within about 3 px of where 0.4277 km/px puts them and 16-20 px from
where 0.4 does. So the rings georeference the image and ``stated_km_per_px`` is only reported."""


# ============================================================================ grid
def grid_for_radar_domain(radar: RadarDomain, crs: int) -> RadarGrid:
    """The Sky grid a city config's ``radar_domain`` describes, snapped as the storm designer does.

    Mirrors ``varuna_replay.domain.StormDomain.from_radar_domain`` (Sky does not depend on the
    replay service), so a decoded frame lands on the grid the bundles' frames live on.
    """
    center_x, center_y = _to_crs(f"EPSG:{crs}").transform(radar.center_lon, radar.center_lat)
    res = float(radar.res_m)
    n = radar.n_px
    half = n * res / 2.0
    left = float(np.floor((center_x - half) / res) * res)
    top = float(np.ceil((center_y + half) / res) * res)
    return RadarGrid(
        crs=f"EPSG:{crs}", res_m=res, n_px=n, transform=(res, 0.0, left, 0.0, -res, top)
    )


@lru_cache(maxsize=8)
def _to_crs(dst_crs: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)


@lru_cache(maxsize=8)
def _aeqd_to(site_lat: float, site_lon: float, dst_crs: str) -> Transformer:
    aeqd = f"+proj=aeqd +lat_0={site_lat} +lon_0={site_lon} +datum=WGS84 +units=m +no_defs"
    return Transformer.from_crs(aeqd, dst_crs, always_xy=True)


# ============================================================================ reading
def load_rgb(path: str | Path) -> NDArray[np.uint8]:
    """Read an image file as ``(height, width, 3)`` 8-bit RGB (palette GIFs expanded exactly)."""
    from PIL import Image  # arrives through pySTEPS; imported only when a file is read

    with Image.open(Path(path)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()


def _pack(rgb: NDArray[np.integer]) -> NDArray[np.int64]:
    arr = np.asarray(rgb, dtype=np.int64)
    return (arr[..., 0] << 16) | (arr[..., 1] << 8) | arr[..., 2]


def _unpack(code: int) -> RGB:
    return ((code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF)


# ============================================================================ legend
@dataclass(frozen=True, slots=True)
class Legend:
    """The colour bar as read from one image, weakest class first."""

    rgb: tuple[RGB, ...]
    lower_dbz: tuple[float, ...]
    swatch_rows: tuple[tuple[int, int], ...]
    """``(first_row, last_row)`` of each swatch, in the order of ``rgb``."""

    @property
    def matches_known_imd_palette(self) -> bool:
        """Whether the colours are the ones recorded in :data:`IMD_PPI_Z_LEGEND_RGB`."""
        return self.rgb == IMD_PPI_Z_LEGEND_RGB


def read_legend(rgb: NDArray[np.uint8], layout: ImdLayout) -> Legend:
    """Read the colour bar from the image: uniform swatches in the legend box, top to bottom.

    A row belongs to a swatch when at least 75 % of the legend box's width is one colour that is
    neither the background nor the ring/text colour. Consecutive rows of one colour form a
    swatch; a swatch shorter than ``layout.min_swatch_rows`` is ignored as text.

    Raises:
        ImdDecodeError: when the number of swatches is not the number of classes the layout
            states, or two swatches share a colour.
    """
    c0, r0, c1, r1 = layout.legend_box
    box = _pack(rgb[r0:r1, c0:c1])
    width = box.shape[1]
    background = _pack(np.array(layout.background_rgb))
    ink = _pack(np.array(layout.ring_rgb))

    row_colour: list[int | None] = []
    for row in box:
        values, counts = np.unique(row, return_counts=True)
        top = int(np.argmax(counts))
        code = int(values[top])
        uniform = counts[top] >= 0.75 * width
        row_colour.append(code if uniform and code not in (int(background), int(ink)) else None)

    swatches: list[tuple[int, int, int]] = []  # (colour, first_row, last_row)
    start = 0
    while start < len(row_colour):
        colour = row_colour[start]
        end = start
        while end + 1 < len(row_colour) and row_colour[end + 1] == colour:
            end += 1
        if colour is not None and end - start + 1 >= layout.min_swatch_rows:
            swatches.append((colour, r0 + start, r0 + end))
        start = end + 1

    n_classes = len(layout.class_lower_dbz)
    if len(swatches) != n_classes:
        msg = (
            f"found {len(swatches)} legend swatches in {layout.legend_box} where "
            f"{layout.product} states {n_classes} classes; the layout no longer matches the image"
        )
        raise ImdDecodeError(msg)
    if len({s[0] for s in swatches}) != n_classes:
        msg = "two legend swatches share a colour, so the legend cannot be inverted"
        raise ImdDecodeError(msg)
    if layout.legend_top_is_strongest:
        swatches = swatches[::-1]
    return Legend(
        rgb=tuple(_unpack(s[0]) for s in swatches),
        lower_dbz=tuple(layout.class_lower_dbz),
        swatch_rows=tuple((s[1], s[2]) for s in swatches),
    )


def classify_pixels(rgb: NDArray[np.uint8], legend: Legend, layout: ImdLayout) -> NDArray[np.int16]:
    """Class index of every pixel: ``k >= 0`` a legend class, -1 background, -2 masked.

    Exact colours are looked up directly. A colour within ``layout.colour_tolerance`` of exactly
    one legend colour (or the background) on every channel takes that class; anything else is
    masked. Nothing is ever assigned to the *nearest* class.
    """
    palette = np.array([*legend.rgb, layout.background_rgb], dtype=np.int64)
    labels = np.array([*range(len(legend.rgb)), BACKGROUND_CLASS], dtype=np.int16)
    codes = _pack(rgb)
    unique, inverse = np.unique(codes.ravel(), return_inverse=True)
    colours = np.stack([(unique >> 16) & 0xFF, (unique >> 8) & 0xFF, unique & 0xFF], axis=1)
    diff = np.abs(colours[:, None, :] - palette[None, :, :]).max(axis=2)
    within = diff <= layout.colour_tolerance
    n_hits = within.sum(axis=1)
    per_colour = np.full(unique.size, MASKED_CLASS, dtype=np.int16)
    single = n_hits == 1
    per_colour[single] = labels[np.argmax(within[single], axis=1)]
    return per_colour[inverse].reshape(codes.shape)


# ============================================================================ rings
@dataclass(frozen=True, slots=True)
class RingFit:
    """Concentric range rings fitted to the image: the georeference and its residual."""

    centre_col: float
    centre_row: float
    radii_px: tuple[float, ...]
    km_per_px: float
    """Scale from the fitted radii against the stated ranges (least squares through zero)."""

    residual_px_rms: float
    """RMS distance of the kept ring pixels from their fitted circle."""

    ring_km_per_px: tuple[float, ...]
    """Each ring's own implied scale; their spread is how consistently the rings agree."""

    n_ring_pixels: int
    stated_km_per_px: float

    @property
    def residual_km_rms(self) -> float:
        return self.residual_px_rms * self.km_per_px

    @property
    def scale_vs_stated(self) -> float:
        """Fitted scale over the scale the image prints; 1.0 when they agree."""
        return self.km_per_px / self.stated_km_per_px


def _solve_concentric(
    cols: NDArray[np.float64], rows: NDArray[np.float64], ring: NDArray[np.intp], n_rings: int
) -> tuple[float, float, NDArray[np.float64]]:
    """Kasa fit with a shared centre: ``x^2 + y^2 = 2ax + 2by + c_k``."""
    design = np.zeros((cols.size, 2 + n_rings))
    design[:, 0] = 2.0 * cols
    design[:, 1] = 2.0 * rows
    design[np.arange(cols.size), 2 + ring] = 1.0
    rhs = cols**2 + rows**2
    sol, *_ = np.linalg.lstsq(design, rhs, rcond=None)
    a, b = float(sol[0]), float(sol[1])
    radii = np.sqrt(np.maximum(sol[2:] + a * a + b * b, 0.0))
    return a, b, radii


def _scale_search(
    dist: NDArray[np.float64], ranges: NDArray[np.float64], layout: ImdLayout
) -> float:
    """The km-per-pixel whose rings, drawn at the stated ranges, catch the most ring pixels.

    A 1-D search over ``layout.scale_search_km_per_px`` in steps of 0.1 %, scoring each scale by
    the ring pixels within 2 px of ``range / scale``. It needs no prior belief in the scale the
    image prints, which is the point: the stated scale may be wrong (see
    :data:`MUMBAI_COLABA_PPI_Z`).
    """
    hist = np.bincount(np.round(dist).astype(np.int64))
    cum = np.concatenate([[0], np.cumsum(hist)])
    lo, hi = layout.scale_search_km_per_px
    scales = lo * (1.001 ** np.arange(int(np.log(hi / lo) / np.log(1.001)) + 1))
    radii = ranges[None, :] / scales[:, None]
    a = np.clip(np.round(radii - 2).astype(np.int64), 0, hist.size)
    b = np.clip(np.round(radii + 2).astype(np.int64) + 1, 0, hist.size)
    score = (cum[b] - cum[a]).sum(axis=1)
    return float(scales[int(np.argmax(score))])


def fit_range_rings(rgb: NDArray[np.uint8], layout: ImdLayout) -> RingFit:
    """Fit the range rings as concentric circles and derive the image scale.

    Ring-coloured pixels inside the plot box, away from the azimuth spokes, are found at the
    scale that best explains them (:func:`_scale_search`, from the approximate centre); those
    within ``ring_search_band_px`` of each ring seed a joint concentric fit, which is repeated
    twice keeping only pixels within ``ring_keep_band_px`` of the circles just fitted - that
    drops the ring labels and any text the band caught.

    Raises:
        ImdDecodeError: when a ring has too few pixels, or the rings disagree with each other
            about the scale by more than ``layout.max_ring_disagreement``.
    """
    c0, r0, c1, r1 = layout.plot_box
    ink = np.all(rgb[r0:r1, c0:c1] == np.array(layout.ring_rgb, dtype=np.uint8), axis=2)
    ink_rows, ink_cols = np.nonzero(ink)
    rows = ink_rows.astype(np.float64) + r0
    cols = ink_cols.astype(np.float64) + c0
    n_rings = len(layout.ring_ranges_km)
    ranges = np.array(layout.ring_ranges_km, dtype=np.float64)

    centre = np.array(layout.approx_centre_px, dtype=np.float64)
    if layout.spoke_step_deg > 0:
        azimuth = np.degrees(np.arctan2(cols - centre[0], centre[1] - rows)) % 360.0
        half = layout.spoke_step_deg / 2.0
        off_spoke = np.abs((azimuth + half) % layout.spoke_step_deg - half)
        away = off_spoke > layout.spoke_exclusion_deg
        rows, cols = rows[away], cols[away]
    scale = _scale_search(np.hypot(cols - centre[0], rows - centre[1]), ranges, layout)
    radii = ranges / scale
    band = layout.ring_search_band_px
    kept = np.zeros(0, dtype=bool)
    ring = np.zeros(0, dtype=np.intp)
    for _ in range(3):
        dist = np.hypot(cols - centre[0], rows - centre[1])
        gap = np.abs(dist[:, None] - radii[None, :])
        ring = np.argmin(gap, axis=1)
        kept = gap[np.arange(dist.size), ring] <= band
        counts = np.bincount(ring[kept], minlength=n_rings)
        if np.any(counts < 50):
            msg = (
                f"range ring fit found {counts.tolist()} pixels on the rings at "
                f"{list(layout.ring_ranges_km)} km; too few to georeference the image"
            )
            raise ImdDecodeError(msg)
        a, b, radii = _solve_concentric(cols[kept], rows[kept], ring[kept], n_rings)
        centre = np.array([a, b])
        band = layout.ring_keep_band_px

    dist = np.hypot(cols - centre[0], rows - centre[1])
    residual = dist[kept] - radii[ring[kept]]
    px_per_km = float(np.dot(radii, ranges) / np.dot(ranges, ranges))
    km_per_px = 1.0 / px_per_km
    per_ring = ranges / radii
    disagreement = float(np.max(np.abs(per_ring / km_per_px - 1.0)))
    if disagreement > layout.max_ring_disagreement:
        msg = (
            f"the rings at {list(layout.ring_ranges_km)} km imply scales "
            f"{[round(float(k), 4) for k in per_ring]} km/px, which disagree by "
            f"{disagreement * 100:.1f} %; they are not the rings the layout describes"
        )
        raise ImdDecodeError(msg)
    return RingFit(
        centre_col=float(centre[0]),
        centre_row=float(centre[1]),
        radii_px=tuple(float(r) for r in radii),
        km_per_px=km_per_px,
        residual_px_rms=float(np.sqrt(np.mean(residual**2))),
        ring_km_per_px=tuple(float(k) for k in per_ring),
        n_ring_pixels=int(kept.sum()),
        stated_km_per_px=layout.stated_km_per_px,
    )


# ============================================================================ decode
@dataclass(frozen=True, slots=True)
class ImdDiagnostics:
    """What the decoder found, so a caller can report it rather than trust it."""

    legend: Legend
    rings: RingFit
    classes_seen: dict[float, int]
    """Pixel count per class lower bound, over observed pixels inside the display range."""

    pixels_in_range: int
    """Plot pixels within the display range: the image's footprint."""

    pixels_decoded: int
    """Of those, pixels read as a class or as echo-free background."""

    pixels_echo: int
    pixels_masked: int
    """Of those, pixels whose colour is neither - rings, spokes, coastline, text."""

    masked_colours: dict[RGB, int] = field(default_factory=dict)
    """The ten commonest masked colours with their counts, for a reader to check nothing
    that should have been a class was masked."""

    cells_covered: int = 0
    cells_echo: int = 0


@dataclass(frozen=True, slots=True)
class ImdFrame:
    """One decoded image on the Sky grid.

    ``dbz`` is ``(n_px, n_px)`` float32, the class lower bound where there is echo and ``nan``
    elsewhere; ``coverage`` says which ``nan`` are observed-dry (True) and which unobserved.
    """

    dbz: NDArray[np.float32]
    coverage: NDArray[np.bool_]
    grid: RadarGrid
    class_width_dbz: float
    product: str
    site_name: str
    valid_ts: datetime | None
    diagnostics: ImdDiagnostics


def decode_imd(
    image: str | Path | NDArray[np.uint8],
    grid: RadarGrid,
    layout: ImdLayout = MUMBAI_COLABA_PPI_Z,
    *,
    valid_ts: datetime | None = None,
) -> ImdFrame:
    """Decode one IMD radar image onto ``grid``.

    Args:
        image: a path to the GIF/PNG, or its ``(height, width, 3)`` RGB array.
        grid: the Sky grid to resample onto (see :func:`grid_for_radar_domain`).
        layout: the product's layout; :data:`MUMBAI_COLABA_PPI_Z` by default.
        valid_ts: the scan time. The image prints it in its header, which this module does not
            read (there is no OCR here), so the caller supplies it.

    Raises:
        ImdDecodeError: when the image size, legend or range rings do not match the layout.
    """
    rgb = load_rgb(image) if isinstance(image, str | Path) else np.asarray(image, dtype=np.uint8)
    height, width = rgb.shape[:2]
    if (width, height) != layout.image_size:
        want_w, want_h = layout.image_size
        msg = f"image is {width} x {height} px; {layout.product} is {want_w} x {want_h} px"
        raise ImdDecodeError(msg)

    legend = read_legend(rgb, layout)
    rings = fit_range_rings(rgb, layout)
    classes = classify_pixels(rgb, legend, layout)

    # Observed footprint: inside the plot box and within the display range of the fitted centre.
    c0, r0, c1, r1 = layout.plot_box
    rows, cols = np.mgrid[r0:r1, c0:c1]
    east_km = (cols - rings.centre_col) * rings.km_per_px
    north_km = (rings.centre_row - rows) * rings.km_per_px
    in_range = np.hypot(east_km, north_km) <= layout.display_range_km
    plot_classes = classes[r0:r1, c0:c1]
    footprint = plot_classes[in_range]
    masked = footprint == MASKED_CLASS
    echo = footprint >= 0

    seen_idx, seen_n = np.unique(footprint[echo], return_counts=True)
    classes_seen = {legend.lower_dbz[int(k)]: int(n) for k, n in zip(seen_idx, seen_n, strict=True)}
    masked_codes, masked_n = np.unique(
        _pack(rgb[r0:r1, c0:c1][in_range][masked]), return_counts=True
    )
    order = np.argsort(-masked_n)[:10]
    masked_colours = {_unpack(int(masked_codes[i])): int(masked_n[i]) for i in order}

    # Carry every observed pixel to the grid and bin it.
    observed = in_range & (plot_classes != MASKED_CLASS)
    ox = east_km[observed] * 1000.0
    oy = north_km[observed] * 1000.0
    oc = plot_classes[observed]
    x, y = _aeqd_to(layout.site_lat, layout.site_lon, grid.crs).transform(ox, oy)
    x = np.asarray(x)
    y = np.asarray(y)
    gcol = np.floor((x - grid.left) / grid.res_m).astype(np.int64)
    grow = np.floor((grid.top - y) / grid.res_m).astype(np.int64)
    inside = (gcol >= 0) & (gcol < grid.n_px) & (grow >= 0) & (grow < grid.n_px)
    cell = grow[inside] * grid.n_px + gcol[inside]
    lower = np.asarray(legend.lower_dbz, dtype=np.float64)
    z = np.where(oc[inside] >= 0, 10.0 ** (lower[np.maximum(oc[inside], 0)] / 10.0), 0.0)
    n_cells = grid.n_px * grid.n_px
    count = np.bincount(cell, minlength=n_cells)
    z_sum = np.bincount(cell, weights=z, minlength=n_cells)

    coverage = count > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_dbz = 10.0 * np.log10(np.where(coverage, z_sum / np.maximum(count, 1), 0.0))
    # Floor to the class; the epsilon keeps a cell of exactly one class in that class.
    k = np.searchsorted(lower, mean_dbz + 1e-6, side="right") - 1
    dbz = np.where(coverage & (k >= 0), lower[np.clip(k, 0, lower.size - 1)], np.nan)

    shape = grid.shape
    diagnostics = ImdDiagnostics(
        legend=legend,
        rings=rings,
        classes_seen=classes_seen,
        pixels_in_range=int(in_range.sum()),
        pixels_decoded=int((~masked).sum()),
        pixels_echo=int(echo.sum()),
        pixels_masked=int(masked.sum()),
        masked_colours=masked_colours,
        cells_covered=int(coverage.sum()),
        cells_echo=int(np.isfinite(dbz).sum()),
    )
    log.info(
        "imd.decoded",
        product=layout.product,
        site=layout.site_name,
        classes_seen=len(classes_seen),
        pixels_decoded=diagnostics.pixels_decoded,
        pixels_masked=diagnostics.pixels_masked,
        ring_residual_km=round(rings.residual_km_rms, 4),
        km_per_px=round(rings.km_per_px, 5),
        cells_covered=diagnostics.cells_covered,
        cells_echo=diagnostics.cells_echo,
        legend_known=legend.matches_known_imd_palette,
    )
    return ImdFrame(
        dbz=dbz.reshape(shape).astype(np.float32),
        coverage=coverage.reshape(shape),
        grid=grid,
        class_width_dbz=layout.class_width_dbz,
        product=layout.product,
        site_name=layout.site_name,
        valid_ts=valid_ts,
        diagnostics=diagnostics,
    )


def frames_from_imd(frames: list[ImdFrame]) -> RadarFrames:
    """Stack decoded frames, oldest first, into the :class:`RadarFrames` Sky consumes.

    Raises:
        ImdDecodeError: when a frame has no ``valid_ts``, the frames sit on different grids, or
            they are not in time order.
    """
    if not frames:
        msg = "no decoded frames to stack"
        raise ImdDecodeError(msg)
    times = []
    for frame in frames:
        if frame.valid_ts is None:
            msg = "a decoded frame has no valid_ts; Sky cannot place it in time"
            raise ImdDecodeError(msg)
        if frame.grid != frames[0].grid:
            msg = "decoded frames sit on different grids"
            raise ImdDecodeError(msg)
        times.append(frame.valid_ts)
    if any(b <= a for a, b in pairwise(times)):
        msg = "decoded frames are not in strictly increasing time order"
        raise ImdDecodeError(msg)
    return RadarFrames(
        dbz=np.stack([f.dbz for f in frames]).astype(np.float32),
        times=tuple(times),
        grid=frames[0].grid,
    )
