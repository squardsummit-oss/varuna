"""The IMD radar image decoder (SPEC.md P2.9, ``varuna_sky.decode_imd``).

The committed tests decode a synthetic image drawn in IMD's own legend colours at known bearings,
ranges and classes (``fixtures/imd/make_synthetic_ppi.py``), so every answer is known by
construction and nothing depends on a download. One further test decodes the real IMD image when
one has been fetched into ``data/cache/imd/`` - it is IMD's image, never committed - and skips,
saying why, when it has not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer
from varuna_replay.domain import StormDomain
from varuna_schemas.constants import IST
from varuna_schemas.models.city import RadarDomain
from varuna_sky.decode_imd import (
    IMD_PPI_Z_LEGEND_RGB,
    MASKED_CLASS,
    MUMBAI_COLABA_PPI_Z,
    ImdDecodeError,
    classify_pixels,
    decode_imd,
    fit_range_rings,
    frames_from_imd,
    grid_for_radar_domain,
    load_rgb,
    read_legend,
)
from varuna_sky.types import RadarGrid

FIXTURES = Path(__file__).parent / "fixtures" / "imd"
REPO = Path(__file__).resolve().parents[3]
REAL_IMAGE = REPO / "data" / "cache" / "imd" / "ppi_mum.gif"
LAYOUT = MUMBAI_COLABA_PPI_Z

_NAME = "varuna_sky_tests_imd_fixture"
_spec = importlib.util.spec_from_file_location(_NAME, FIXTURES / "make_synthetic_ppi.py")
assert _spec is not None and _spec.loader is not None
fixture = importlib.util.module_from_spec(_spec)
sys.modules[_NAME] = fixture  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(fixture)

MUMBAI_RADAR = RadarDomain(center_lon=72.86, center_lat=19.065, size_km=60.0, res_m=500.0)
"""``services/city/configs/mumbai.yaml``'s ``radar_domain``, restated so the test needs no city."""


@pytest.fixture(scope="module")
def grid() -> RadarGrid:
    return grid_for_radar_domain(MUMBAI_RADAR, 32643)


@pytest.fixture(scope="module")
def drawn() -> tuple[np.ndarray, object]:
    return fixture.draw()


@pytest.fixture(scope="module")
def decoded(grid: RadarGrid):
    return decode_imd(FIXTURES / "synthetic_ppi.png", grid)


def _site_polar(grid: RadarGrid) -> tuple[np.ndarray, np.ndarray]:
    """Bearing (deg) and range (km) from the site of every grid cell centre."""
    to_aeqd = Transformer.from_crs(
        grid.crs,
        f"+proj=aeqd +lat_0={LAYOUT.site_lat} +lon_0={LAYOUT.site_lon} +datum=WGS84 +units=m",
        always_xy=True,
    )
    rows, cols = np.mgrid[0 : grid.n_px, 0 : grid.n_px]
    x = grid.left + (cols + 0.5) * grid.res_m
    y = grid.top - (rows + 0.5) * grid.res_m
    east, north = to_aeqd.transform(x, y)
    east = np.asarray(east) / 1000.0
    north = np.asarray(north) / 1000.0
    return np.degrees(np.arctan2(east, north)) % 360.0, np.hypot(east, north)


# ============================================================================ grid
def test_grid_is_the_storm_designers_grid(grid: RadarGrid) -> None:
    """A decoded frame must land on the grid the bundles' frames live on, cell for cell."""
    storm = StormDomain.from_radar_domain(MUMBAI_RADAR, 32643)
    assert grid.transform == storm.transform
    assert grid.n_px == storm.n_px == 120
    assert grid.crs == storm.crs_string


def test_committed_fixture_is_what_the_generator_draws(drawn) -> None:
    image, _ = drawn
    assert np.array_equal(load_rgb(FIXTURES / "synthetic_ppi.png"), image)


# ============================================================================ legend
def test_legend_is_read_from_the_colour_bar(drawn) -> None:
    image, _ = drawn
    legend = read_legend(image, LAYOUT)
    assert legend.rgb == IMD_PPI_Z_LEGEND_RGB
    assert legend.matches_known_imd_palette
    assert legend.lower_dbz == tuple(float(v) for v in range(0, 60, 4))
    # Weakest class first: the bottom swatch of the bar.
    assert legend.swatch_rows[0][0] > legend.swatch_rows[-1][0]


def test_a_legend_with_a_missing_class_is_refused() -> None:
    image, _ = fixture.draw(n_swatches=14)
    with pytest.raises(ImdDecodeError, match="14 legend swatches"):
        read_legend(image, LAYOUT)


def test_colours_outside_the_legend_are_masked_never_guessed(drawn) -> None:
    image, truth = drawn
    legend = read_legend(image, LAYOUT)
    classes = classify_pixels(image, legend, LAYOUT)
    c0, r0, c1, r1 = LAYOUT.plot_box
    plot = np.zeros(classes.shape, dtype=bool)
    plot[r0:r1, c0:c1] = True
    # Every overlay pixel - rings, spokes, coastline, text, the unknown-colour sector - is masked,
    # and nothing else inside the plot is.
    assert np.array_equal((classes == MASKED_CLASS) & plot, truth.overlay)
    # The coastline's blue sits 51 away in red from the 8-12 dBZ class; it must not be read as it.
    coast = np.all(image == np.array(fixture.COAST, dtype=np.uint8), axis=2)
    assert coast.any()
    assert np.all(classes[coast] == MASKED_CLASS)


# ============================================================================ rings
def test_range_rings_give_the_centre_and_the_scale(drawn) -> None:
    image, _ = drawn
    rings = fit_range_rings(image, LAYOUT)
    assert rings.centre_col == pytest.approx(fixture.CENTRE[0], abs=0.25)
    assert rings.centre_row == pytest.approx(fixture.CENTRE[1], abs=0.25)
    assert rings.km_per_px == pytest.approx(fixture.KM_PER_PX, rel=2e-3)
    assert rings.residual_px_rms < 0.5
    # The scale the image prints is reported against, never used.
    assert rings.scale_vs_stated == pytest.approx(fixture.KM_PER_PX / 0.4, rel=2e-3)


@pytest.mark.parametrize(
    ("drawn_km", "reason"),
    [
        ((50.0, 100.0, 140.0), "too few"),  # the outer ring is nowhere near 150 km
        ((51.5, 100.0, 150.0), "disagree"),  # close enough to find, 3 % off the others
    ],
)
def test_rings_at_the_wrong_ratio_are_refused(drawn_km: tuple[float, ...], reason: str) -> None:
    image, _ = fixture.draw(ring_ranges_km=drawn_km)
    with pytest.raises(ImdDecodeError, match=reason):
        fit_range_rings(image, LAYOUT)


# ============================================================================ decode
def test_classes_seen_are_the_classes_drawn(decoded) -> None:
    drawn_classes = {s.dbz for s in fixture.SECTORS if s.class_index >= 0}
    assert set(decoded.diagnostics.classes_seen) == drawn_classes
    assert decoded.class_width_dbz == 4.0


def test_each_sector_decodes_to_its_class_at_its_bearing_and_range(decoded, grid) -> None:
    bearing, range_km = _site_polar(grid)
    cell_km = grid.res_m / 1000.0
    for sector in (s for s in fixture.SECTORS if s.class_index >= 0):
        # The cell under the sector's middle carries exactly the drawn class.
        mid_bearing, mid_range = sector.centre
        gap = np.hypot(
            range_km * np.radians(((bearing - mid_bearing + 180.0) % 360.0) - 180.0),
            range_km - mid_range,
        )
        row, col = np.unravel_index(np.argmin(gap), gap.shape)
        assert decoded.dbz[row, col] == sector.dbz, sector

        # Every cell at this class lies in the drawn sector, give or take one cell diagonal
        # (measured as an arc at each cell's own range), and at least 90 % of the cells well
        # inside the sector decode to it - the sector is found, not lost.
        at_class = decoded.dbz == sector.dbz
        near, inner = _sector_bands(sector, bearing, range_km, cell_km * np.sqrt(2.0))
        assert np.all(near[at_class]), sector
        assert inner.sum() >= 5, sector
        assert at_class[inner].mean() >= 0.9, (sector, at_class[inner].mean())


def _sector_bands(
    sector, bearing: np.ndarray, range_km: np.ndarray, slack_km: float
) -> tuple[np.ndarray, np.ndarray]:
    """Cells within ``slack_km`` of the sector, and cells at least ``slack_km`` inside it."""
    (b0, b1), (r0, r1) = sector.bearing_deg, sector.range_km
    slack_deg = np.degrees(slack_km / np.maximum(range_km, 1e-6))
    near = (
        (range_km >= r0 - slack_km)
        & (range_km <= r1 + slack_km)
        & (bearing >= b0 - slack_deg)
        & (bearing <= b1 + slack_deg)
    )
    inner = (
        (range_km > r0 + slack_km)
        & (range_km < r1 - slack_km)
        & (bearing > b0 + slack_deg)
        & (bearing < b1 - slack_deg)
    )
    return near, inner


def test_an_unknown_colour_is_a_hole_not_a_guess(decoded, grid) -> None:
    bearing, range_km = _site_polar(grid)
    unknown = next(s for s in fixture.SECTORS if s.class_index < 0)
    _, inner = _sector_bands(unknown, bearing, range_km, grid.res_m / 1000.0 * np.sqrt(2.0))
    assert inner.sum() >= 10
    assert not decoded.coverage[inner].any()
    assert np.all(np.isnan(decoded.dbz[inner]))


def test_masked_pixel_count_is_the_overlay_inside_the_footprint(decoded, drawn) -> None:
    _, truth = drawn
    rings = decoded.diagnostics.rings
    rows, cols = np.mgrid[0 : truth.overlay.shape[0], 0 : truth.overlay.shape[1]]
    reach = np.hypot(cols - rings.centre_col, rows - rings.centre_row) * rings.km_per_px
    in_range = reach <= LAYOUT.display_range_km
    assert decoded.diagnostics.pixels_masked == int((truth.overlay & in_range).sum())
    d = decoded.diagnostics
    assert d.pixels_decoded + d.pixels_masked == d.pixels_in_range


def test_dry_but_observed_is_not_the_same_as_unobserved(decoded) -> None:
    """``nan`` means no echo or no coverage; the mask says which."""
    dry_observed = decoded.coverage & np.isnan(decoded.dbz)
    assert dry_observed.sum() > 0.8 * decoded.dbz.size
    assert np.all(decoded.coverage[np.isfinite(decoded.dbz)])


def test_nothing_beyond_the_display_range_is_covered() -> None:
    """A grid straddling the 150 km ring: covered cells stop at the ring, out to one cell."""
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    site_x, site_y = to_utm.transform(LAYOUT.site_lon, LAYOUT.site_lat)
    res = 500.0
    n = 60
    far = RadarGrid(
        crs="EPSG:32643",
        res_m=res,
        n_px=n,
        transform=(res, 0.0, site_x - n * res / 2.0, 0.0, -res, site_y + 150_000.0 + n * res / 2),
    )
    frame = decode_imd(FIXTURES / "synthetic_ppi.png", far)
    _, range_km = _site_polar(far)
    assert frame.coverage.any()
    assert not frame.coverage[range_km > LAYOUT.display_range_km + 0.75].any()
    assert frame.coverage[range_km < LAYOUT.display_range_km - 1.0].mean() > 0.95


def test_decoding_is_deterministic(grid) -> None:
    a = decode_imd(FIXTURES / "synthetic_ppi.png", grid)
    b = decode_imd(FIXTURES / "synthetic_ppi.png", grid)
    assert np.array_equal(a.dbz, b.dbz, equal_nan=True)
    assert np.array_equal(a.coverage, b.coverage)


def test_a_wrongly_sized_image_is_refused(grid, drawn) -> None:
    image, _ = drawn
    with pytest.raises(ImdDecodeError, match="1078 x 770"):
        decode_imd(image[:, :-10], grid)


def test_decoded_frames_stack_into_radar_frames(grid, drawn) -> None:
    image, _ = drawn
    t0 = datetime(2026, 9, 22, 14, 35, tzinfo=IST)
    frames = [decode_imd(image, grid, valid_ts=t0 + timedelta(minutes=10 * k)) for k in range(3)]
    stacked = frames_from_imd(frames)
    assert stacked.dbz.shape == (3, 120, 120)
    assert stacked.grid == grid
    assert stacked.interval == timedelta(minutes=10)
    with pytest.raises(ImdDecodeError, match="valid_ts"):
        frames_from_imd([decode_imd(image, grid)])
    with pytest.raises(ImdDecodeError, match="time order"):
        frames_from_imd(frames[::-1])


# ============================================================================ real image
def test_the_real_cached_imd_image_decodes(grid) -> None:
    """Decode IMD's own Colaba PPI(Z) when one has been fetched; skip, saying why, when not.

    The image is IMD's and is never committed. Fetch one with its provenance beside it into
    ``data/cache/imd/`` (``ppi_mum.gif`` and ``ppi_mum.source.json``) to run this.
    """
    if not REAL_IMAGE.exists():
        pytest.skip(
            f"no IMD image at {REAL_IMAGE.relative_to(REPO)}; fetch "
            "https://mausam.imd.gov.in/Radar/ppi_mum.gif there (it is IMD's image and is never "
            "committed) to decode a real one"
        )
    source = REAL_IMAGE.with_name("ppi_mum.source.json")
    if source.exists():
        provenance = json.loads(source.read_text(encoding="utf-8"))
        assert provenance["url"].startswith("https://mausam.imd.gov.in/")

    frame = decode_imd(REAL_IMAGE, grid)
    d = frame.diagnostics
    # IMD's legend as read from the image: fifteen 4 dBZ classes.
    assert len(d.legend.rgb) == 15
    # The rings are concentric and agree on one scale; the site is near the layout's guess.
    rings = d.rings
    assert rings.residual_km_rms < 0.5
    assert max(rings.ring_km_per_px) / min(rings.ring_km_per_px) < 1.02
    assert abs(rings.centre_col - LAYOUT.approx_centre_px[0]) < 4.0
    assert abs(rings.centre_row - LAYOUT.approx_centre_px[1]) < 4.0
    # Only overlay colours are masked, and they are a small share of the footprint.
    assert d.pixels_masked < 0.1 * d.pixels_in_range
    assert d.pixels_decoded + d.pixels_masked == d.pixels_in_range
    assert frame.coverage.mean() > 0.8
    finite = frame.dbz[np.isfinite(frame.dbz)]
    assert set(np.unique(finite).tolist()) <= set(LAYOUT.class_lower_dbz)
