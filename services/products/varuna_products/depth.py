"""Depth products: the rasters the map draws and the per-segment forecast it colours streets by
(SPEC.md 11.8, 10.3).

Two things come out of a Twin run and both are consumed by the console:

* **rasters** - one PNG per 5-minute step per statistic, through the *shared* depth ramp in
  ``varuna_schemas.ramps``, with a world file and a ``bounds.json``. The ramp is shared on
  purpose (SPEC.md 6.7): a map pixel and a UI chip showing "45 cm" have to be the same orange,
  and they are only guaranteed to be if both read ``tokens.json``. Nothing here defines a colour.
* **segment forecast** - depth per road segment per step, with exceedance probabilities and a
  safe-until time per vehicle profile, written as GeoParquet for the console to preload.

**Sampling a street from a raster.** SPEC.md 11.8 fixes the rule: a segment's depth is the
90th percentile of the cell depths within a 15 m buffer of it. The buffer matters because a 30 m
grid cell is wider than most roads, so a centreline sample would take whatever the cell's average
happens to be; the percentile matters because the maximum would latch onto one wet corner of one
cell and never let go. Which cells belong to which segment is fixed geometry, so it is computed
once and cached beside the city (:func:`segment_cell_index`) rather than per run.

**Honesty about the ensemble** (rule 6). A Twin run is deterministic: one rain field in, one
depth field out. So with the Twin alone ``p10 == p50 == p90`` and every exceedance probability
is 0 or 1, and this module does not dress that up as a spread - the caller records
``ensemble_n = 1`` and the run's notes say so. The spread SPEC.md 11.7 asks for arrives as a
*second* argument: :func:`segment_forecast` takes an optional ``member_depth_cm`` stack from
Flash-lite, one street depth field per ensemble member, and turns it into real quantiles and
real exceedance fractions.

Nothing here counts the members. It was twenty when the stack was one emulator run per Sky rain
member, and it is fifty since task P7.6 put a blockage draw and a storage-coefficient draw on
each of them (``varuna_flash.ensemble``); this module reads ``member_depth_cm.shape[0]`` and
reports it. What fifty costs was measured rather than assumed - `ensemble_statistics` on the
Mumbai grid, best of three with eighteen python processes on an Intel i5-1155G7: 14 ms at one
member, 822 ms at twenty, 1,747 ms at fifty and 1,599 ms at sixty, on stacks of 61, 153 and
184 MB, each of which is copied once by :func:`_member_levels`. So the spread is about a second
of the products stage, against SPEC.md 11.8's 2 s for the whole of it, and the memory is the
larger cost.

The two are combined rather than swapped, and the reason is ADR-0025: the emulator is calibrated
to the Twin but its held-out RMSE is 5.7 cm and its level is visibly low (it peaked at 106 cm on
a 2 July cycle where the Twin peaked at 252 cm), so it cannot be the depth on screen. What it
*can* say is how much the answer moves between members. So the level stays the Twin's and only
each member's deviation from the member mean is carried across. The member mean of the resulting
stack is then the Twin depth exactly - bar the streets where a member would have gone below zero
and was clipped to dry - which is what makes "Flash supplies the spread and never the level" a
statement about the file rather than a slogan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_schemas.ramps import depth_array_to_rgba

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable
    from datetime import datetime

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.products.depth")

__all__ = [
    "EXCEEDANCE_CM",
    "INTERTIDAL_MASK_FILE",
    "PROFILE_THRESHOLD_CM",
    "PROFILE_TOLERANCE",
    "SEA_MASK_FILE",
    "SEGMENT_BUFFER_M",
    "SEGMENT_CELLS_FILE",
    "SEGMENT_PERCENTILE",
    "WET_THRESHOLD_CM",
    "StaleSegmentIndex",
    "aoi_depth_band",
    "build_segment_cell_index",
    "city_intertidal_mask",
    "city_sea_mask",
    "depth_bounds",
    "intertidal_mask_digest",
    "sea_mask_digest",
    "segment_cell_index",
    "segment_forecast",
    "segment_ids_digest",
    "segment_name_aliases",
    "segment_names",
    "write_depth_rasters",
    "write_wet_segments",
]

SEGMENT_BUFFER_M = 15.0
"""Half-width of the strip around a segment centreline that is sampled, in metres.

SPEC.md 11.8 states it. It is half a cell either side of the line on the 30 m grid, so a
segment always samples the cells it actually runs through even where it clips a corner."""

SEGMENT_PERCENTILE = 90.0
"""Which percentile of the sampled cells becomes the segment's depth (SPEC.md 11.8).

The spec calls this a documented choice, and the reason is failure asymmetry: a road is
impassable at its worst point, not its average, but a maximum over a 30 m grid latches onto a
single wet cell - often the kerb of a neighbouring plot - and never releases it. The 90th
percentile tracks the wet end without being hostage to one cell."""

PROFILE_THRESHOLD_CM: dict[str, float] = {
    "two_wheeler": 15.0,
    "car": 30.0,
    "bus": 45.0,
    "truck": 45.0,
    "ambulance": 60.0,
    "pedestrian": 30.0,
}
"""Depth at which each vehicle stops being able to pass, in cm (SPEC.md 11.8).

The pedestrian rule is really ``h >= 30 cm or h*v >= 0.5 m2/s``; the velocity half needs the
flux field and is applied by the route service, so 30 cm is the depth half of it here."""

PROFILE_TOLERANCE: dict[str, float] = {
    "two_wheeler": 0.5,
    "car": 0.5,
    "bus": 0.5,
    "truck": 0.5,
    "ambulance": 0.2,
    "pedestrian": 0.5,
}
"""Exceedance probability each profile will accept before it calls a street unsafe.

An ambulance is the cautious one at 0.2 (SPEC.md 7.4): it turns back on a one-in-five chance,
because the cost of being wrong is a stranded ambulance rather than a longer drive."""

RASTER_WORKERS = 8
"""Most threads :func:`write_depth_rasters` encodes PNGs on (fewer on a smaller machine).

The count changes how long the rasters take and never their bytes."""

EXCEEDANCE_CM: tuple[float, ...] = (15.0, 30.0, 45.0, 60.0)
"""Depths the parquet carries an exceedance probability for (SPEC.md 11.8, 10.3).

They are the depth ramp's own band edges, so ``p_gt_30`` answers exactly the question the map's
probability mode asks when the operator picks 30 cm."""


# ============================================================================ rasters
def depth_bounds(
    transform: tuple[float, float, float, float, float, float], shape: tuple[int, int], crs: str
) -> dict[str, object]:
    """The lon/lat bounds a deck.gl ``BitmapLayer`` needs, plus the metric ones it does not.

    The console places the raster by lon/lat, so the metric grid is reprojected here rather than
    in the browser: doing it in the browser would need the CRS definition shipped to the client
    and would put a projection library in the map's hot path.
    """
    from pyproj import Transformer

    n_rows, n_cols = shape
    res, _, left, _, _, top = transform
    right = left + n_cols * res
    bottom = top - n_rows * res

    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    west, south = to_wgs.transform(left, bottom)
    east, north = to_wgs.transform(right, top)
    return {
        "crs": crs,
        "metric": [left, bottom, right, top],
        "wgs84": [west, south, east, north],
        "shape": [n_rows, n_cols],
        "res_m": res,
    }


def write_depth_rasters(
    run_dir: Path,
    depth_m: NDArray[np.floating],
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    *,
    stat: str = "p50",
    workers: int | None = None,
    sea_mask: NDArray[np.bool_] | None = None,
) -> list[Path]:
    """One PNG per step through the shared ramp, each with its world file.

    ``sea_mask`` cells (the city's sea, ``terrain.sea``) are drawn transparent at every step.
    The Twin holds the sea at the tide's level, so its depth there is metres of seawater, and
    drawn through the depth ramp it would paint 19 km2 of Mumbai's bay in the colour that means
    "rescue vehicles only".

    The PNG is RGBA with the dry band fully transparent, so the basemap shows through where
    there is no water rather than the city being covered by a grey sheet. The world file (.pgw)
    carries the metric georeference for anything that reads the PNG as a GIS raster; the console
    uses ``bounds.json`` instead, because a BitmapLayer wants corners in lon/lat.

    **Encoding in parallel, writing in order** (task P10.4). Each step is encoded to bytes on a
    thread pool - the ramp lookup is NumPy and Pillow's zlib deflate runs without the GIL - and
    the files are then written one by one in step order. A PNG's bytes depend only on its pixels
    and the encoder settings, never on which thread made it or when, so the files are identical
    to encoding one step after another (rule 8; tested per file by sha256). ``workers=1`` is the
    sequential path.
    """
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO

    from PIL import Image

    out = run_dir / "depth"
    out.mkdir(parents=True, exist_ok=True)
    res, _, left, _, _, top = transform
    n_steps = int(depth_m.shape[0])
    # World file: pixel size, rotation terms, then the CENTRE of the top-left pixel, which is
    # half a cell in from the grid's corner. Writing the corner instead is the classic half-pixel
    # shift and would put every street 15 m north-west of where it is.
    world = f"{res}\n0.0\n0.0\n{-res}\n{left + res / 2.0}\n{top - res / 2.0}\n"

    sea = None if sea_mask is None else np.asarray(sea_mask, dtype=bool)
    if sea is not None and sea.shape != tuple(depth_m.shape[1:]):
        raise ValueError(f"sea_mask is {sea.shape}; the depth steps are {depth_m.shape[1:]}")

    def encode(step: int) -> bytes:
        field = depth_m[step] if sea is None else np.where(sea, 0.0, depth_m[step])
        rgba = depth_array_to_rgba(field, alpha=255, dry_alpha=0)
        buffer = BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    if workers is None:
        workers = min(RASTER_WORKERS, os.cpu_count() or 1)
    workers = max(1, min(int(workers), n_steps or 1))

    written: list[Path] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # `map` yields in submission order whatever order the threads finish in.
        for step, data in enumerate(pool.map(encode, range(n_steps))):
            path = out / f"{stat}_{step:02d}.png"
            path.write_bytes(data)
            (out / f"{stat}_{step:02d}.pgw").write_text(world, encoding="utf-8")
            written.append(path)

    (out / "bounds.json").write_text(
        json.dumps(depth_bounds(transform, depth_m.shape[1:], crs), indent=2) + "\n",
        encoding="utf-8",
    )
    log.info("products.rasters", stat=stat, steps=len(written), dir=str(out))
    return written


# ============================================================================ segments
SEGMENT_CELLS_FILE = "segment_cells.npz"
"""The cached segment index beside the city: ``city/<city>/segment_cells.npz``."""

SEA_MASK_FILE = "sea_mask.tif"
"""The city's sea beside it (``varuna_city.sea``): 0 land, 1 open sea, 2 tidal creek."""

NO_SEA_DIGEST = "none"
"""What :func:`sea_mask_digest` answers for a city with no sea raster, and what an index written
before sea exclusion existed is taken to have been built with."""

INTERTIDAL_MASK_FILE = "intertidal_mask.tif"
"""The wet land behind the coast wall (``varuna_city.pipeline``): 1 intertidal, 0 not, 255 no-data.

Mangrove, wetland and open water the tide may cover, 8-connected to the sea at or below the coast
wall level. The Twin keeps it as land, so the tide walks onto it at high water - 25 cells at the
08:40 cycle's +0.097 m, 124 at +1.236 m, 346 at the +2.218 m crest on Mumbai - and a street whose
buffer touched it read that seawater as its depth: 18 segments at 5 cm or more at the crest, 3 at
30 cm or more, Kalina Kurla Road at 33 cm. Streets are not built on mangroves."""

NO_INTERTIDAL_DIGEST = "none"
"""What :func:`intertidal_mask_digest` answers for no intertidal land - no raster, or one with no
cell set - and what an index written before intertidal exclusion existed is taken to have had."""


def city_sea_mask(city_root: Path, shape: tuple[int, int] | None = None):
    """``city_root/sea_mask.tif`` as a boolean mask, or ``None`` when the city has none.

    ``shape``, when given, is checked: a mask on another grid would exclude the wrong cells.
    """
    path = Path(city_root) / SEA_MASK_FILE
    if not path.is_file():
        return None
    import rasterio

    with rasterio.open(path) as src:
        band = src.read(1)
    if shape is not None and tuple(band.shape) != tuple(shape):
        raise ValueError(f"{path} is {band.shape}; the depth grid is {tuple(shape)}")
    # 255 is the writer's no-data, never sea.
    return (band != 0) & (band != 255)


def sea_mask_digest(sea) -> str:
    """sha256 of a sea mask's cells, or :data:`NO_SEA_DIGEST` for no mask."""
    if sea is None:
        return NO_SEA_DIGEST
    import hashlib

    data = np.ascontiguousarray(np.asarray(sea, dtype=bool), dtype="u1")
    return hashlib.sha256(data.tobytes()).hexdigest()


def city_intertidal_mask(city_root: Path, shape: tuple[int, int] | None = None):
    """``city_root/intertidal_mask.tif`` as a boolean mask, or ``None`` when the city has none.

    ``shape``, when given, is checked as :func:`city_sea_mask` checks it. Only the code 1 is
    intertidal; 0 and the writer's 255 no-data are not.
    """
    path = Path(city_root) / INTERTIDAL_MASK_FILE
    if not path.is_file():
        return None
    import rasterio

    with rasterio.open(path) as src:
        band = src.read(1)
    if shape is not None and tuple(band.shape) != tuple(shape):
        raise ValueError(f"{path} is {band.shape}; the depth grid is {tuple(shape)}")
    return band == 1


def intertidal_mask_digest(intertidal) -> str:
    """sha256 of an intertidal mask's cells, or :data:`NO_INTERTIDAL_DIGEST` when none is set.

    An all-zero mask answers the same as no mask, unlike :func:`sea_mask_digest`: every city build
    writes the raster, all zero where there is no wall behind a zone, and an index built without
    it excludes exactly the same cells - so it stays valid rather than being refused.
    """
    if intertidal is None:
        return NO_INTERTIDAL_DIGEST
    data = np.ascontiguousarray(np.asarray(intertidal, dtype=bool), dtype="u1")
    if not data.any():
        return NO_INTERTIDAL_DIGEST
    import hashlib

    return hashlib.sha256(data.tobytes()).hexdigest()


def segment_ids_digest(segment_ids) -> str:
    """sha256 of a segment id sequence, in order - the key :func:`segment_cell_index` caches on.

    Order is part of the key on purpose: the index is positional (segment ``k``'s cells), and so
    is everything aligned to it, from the Flash-lite emulator to every run's segment axis.
    """
    import hashlib

    # Each id followed by a newline, hashed in one call: 21,296 ids is one short buffer.
    text = "".join(f"{segment_id}\n" for segment_id in segment_ids)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StaleSegmentIndex(RuntimeError):
    """An existing ``segment_cells.npz`` disagrees with the street table, the sea or the intertidal
    land beside it.

    Raised rather than repaired, because a reader cannot tell which side is stale. On 2026-09-27
    it was the table: ``segments.parquet`` carried the 12 Sep ids (1,247 ``S0-*``) while the index,
    the fitted Flash-lite emulator and every baked run shared the 10 Sep ids, and an index
    rebuilt from the table on read made ``_flash_plan`` refuse Flash on every Mumbai cycle.
    """


def segment_cell_index(city_root: Path, transform, shape: tuple[int, int], crs: str):
    """Map each road segment to the flat cell indices within :data:`SEGMENT_BUFFER_M` of it.

    Pure geometry, so it is computed once and cached at ``city/<city>/segment_cells.npz``. On
    Mumbai that is 21,296 segments over a 522 x 323 grid and took 28-36 s to build on 2026-09-26
    (an i5-1155G7 with other work running); doing it per run - 49 cycles in a bake - would
    dominate the bake. Checking a cache against the table costs 48-60 ms warm.

    **The cache is keyed on the street table it was built from.** It stores
    :func:`segment_ids_digest` of ``segments.parquet``'s ids, and is checked against the table on
    every read. It used to be loaded whenever it existed, which is how Mumbai's index and every
    baked run kept one set of segment ids while ``segments.parquet`` was rebuilt on 12 Sep with
    another (1,247 renamed ``S0-*``, 839 more naming a different street): the products joined
    names, points and exposure on the wrong street and nothing said so. An index written before
    the digest existed is checked against its own stored ids, which is the same comparison; a
    matching one is used as it is and never rewritten. With no ``segments.parquet`` to compare
    against (a packaged index on its own), the cache is trusted, as before.

    **A read never rewrites an existing index.** A missing one is built and written; a stale one
    raises :class:`StaleSegmentIndex`, naming the digests that differ and the fix. The city
    build, which writes the table, the sea and the intertidal raster, removes the index whenever
    it rewrites any of them (``varuna_city.pipeline``), so the first read after ``make city``
    builds it afresh. Rewriting
    on read was tried first and did harm: a test that reached ``city/mumbai`` rebuilt the index
    from the stale 12 Sep table at 18:53 on 2026-09-27, and Flash, fitted on the index's ids,
    refused every Mumbai cycle from then on.

    **Sea cells are not street cells** (``sea_mask.tif``, when the city has one). 94 of Mumbai's
    segments - Carter Road, B J Road, Danda Seaface, the piers, the SCLR and BKC bridges - have
    a sea cell inside their 15 m buffer, and with the sea held at the tide's level they read up
    to 139 cm of seawater as street depth. So the index leaves sea cells out, and a segment whose
    buffer is all sea (a pier, a bridge span) keeps no cells and reads dry. The mask's digest is
    part of the cache key beside the street table's, so a new coastline rebuilds the index; an
    index written before either digest existed is taken to have had no sea.

    **Nor are intertidal cells** (``intertidal_mask.tif``, the mangroves, wetland and water the
    coast wall stands behind). The Twin keeps them as land so the tide can walk onto them, and at
    the +2.218 m crest it does, over 346 of Mumbai's cells; a street whose buffer touched one read
    that as its depth. They are left out exactly as the sea is, a street whose whole buffer is
    intertidal reads 0 cm, and the raster's :func:`intertidal_mask_digest` is a third part of
    the cache key, so an index built before the raster existed is refused rather than silently
    reading mangroves. An all-zero raster digests as none, so a city with no wall keeps its index.

    Returns ``(segment_ids, offsets, cells)`` in CSR form: segment ``k``'s cells are
    ``cells[offsets[k]:offsets[k+1]]``. Flat form rather than a list of arrays because the
    sampler indexes a flattened depth field once per step.
    """
    import geopandas as gpd

    cache = city_root / SEGMENT_CELLS_FILE
    table = city_root / "segments.parquet"
    sea = city_sea_mask(city_root, shape)
    sea_digest = sea_mask_digest(sea)
    intertidal = city_intertidal_mask(city_root, shape)
    intertidal_digest = intertidal_mask_digest(intertidal)
    expected = None
    if table.is_file():
        import pandas as pd

        expected = segment_ids_digest(pd.read_parquet(table, columns=["segment_id"])["segment_id"])
    if cache.is_file():
        # Read in full and closed before anything can replace the file: Windows refuses to
        # rename over a file another handle still has open.
        with np.load(cache, allow_pickle=True) as data:
            segment_ids = tuple(data["segment_ids"].tolist())
            offsets, cells = data["offsets"], data["cells"]
            stored = (
                str(data["segments_sha256"])
                if "segments_sha256" in data.files
                else segment_ids_digest(segment_ids)
            )
            stored_sea = str(data["sea_sha256"]) if "sea_sha256" in data.files else NO_SEA_DIGEST
            stored_intertidal = (
                str(data["intertidal_sha256"])
                if "intertidal_sha256" in data.files
                else NO_INTERTIDAL_DIGEST
            )
        streets_ok = expected is None or stored == expected
        # A packaged index with no street table to check is trusted, as before - unless the sea
        # or the intertidal land it was built under is not the one beside it.
        if streets_ok and stored_sea == sea_digest and stored_intertidal == intertidal_digest:
            return (segment_ids, offsets, cells)
        log.error(
            "products.segment_index_stale",
            cache=str(cache),
            cached_segments=len(segment_ids),
            cached_sha256=stored[:12],
            table_sha256=None if expected is None else expected[:12],
            cached_sea_sha256=stored_sea[:12],
            sea_sha256=sea_digest[:12],
            cached_intertidal_sha256=stored_intertidal[:12],
            intertidal_sha256=intertidal_digest[:12],
            action="refused; the index is not rewritten on read",
        )
        what = []
        if not streets_ok:
            what.append(
                f"it was built from street ids {stored[:12]} and segments.parquet now carries "
                f"{None if expected is None else expected[:12]}"
            )
        if stored_sea != sea_digest:
            what.append(
                f"it was built under sea {stored_sea[:12]} and {SEA_MASK_FILE} is now "
                f"{sea_digest[:12]}"
            )
        if stored_intertidal != intertidal_digest:
            what.append(
                f"it was built under intertidal land {stored_intertidal[:12]} and "
                f"{INTERTIDAL_MASK_FILE} is now {intertidal_digest[:12]}"
            )
        raise StaleSegmentIndex(
            f"{cache} does not match the city beside it: {'; and '.join(what)}. Either side can "
            f"be the stale one, so nothing is rewritten on read. Rebuild the city "
            f"(make city CITY={Path(city_root).name}), which removes the index so the next read "
            f"builds it from the new table; or, if the index is the one to keep, restore the "
            f"street table it was built from."
        )

    segments = gpd.read_parquet(table).to_crs(crs)
    segment_ids, offsets, cells = build_segment_cell_index(
        segments, transform, shape, sea=sea, intertidal=intertidal
    )
    digest = segment_ids_digest(segment_ids)
    # Written beside the target and renamed over it, so a reader in another process sees the old
    # index or the new one and never half of one.
    partial = cache.with_name(f"{cache.stem}.{os.getpid()}.partial.npz")
    with partial.open("wb") as handle:
        np.savez_compressed(
            handle,
            segment_ids=np.array(segment_ids, dtype=object),
            offsets=offsets,
            cells=cells,
            segments_sha256=np.array(digest),
            sea_sha256=np.array(sea_digest),
            intertidal_sha256=np.array(intertidal_digest),
        )
    os.replace(partial, cache)
    log.info(
        "products.segment_index_built",
        segments=len(segment_ids),
        cells=int(cells.size),
        mean_cells=round(float(cells.size / max(len(segment_ids), 1)), 1),
        segments_sha256=digest[:12],
        sea_sha256=sea_digest[:12],
        intertidal_sha256=intertidal_digest[:12],
        cache=str(cache),
    )
    return (segment_ids, offsets, cells)


def build_segment_cell_index(
    segments, transform, shape: tuple[int, int], *, sea=None, intertidal=None
):
    """The segment index itself, from a street table already in the grid's CRS; writes nothing.

    Split from :func:`segment_cell_index` so a rebuilt street table can be checked against the
    cached index in memory, without touching ``city/``. ``sea`` and ``intertidal`` cells are
    left out of every segment (:func:`segment_cell_index`); a segment left with no cells reads
    0 cm. With neither, or with both empty, the index is the one this function always built.
    """
    from rasterio.features import rasterize
    from rasterio.transform import Affine

    n_rows, n_cols = shape
    affine = Affine(*transform)

    off_street = None
    for name, mask in (("sea", sea), ("intertidal", intertidal)):
        if mask is None:
            continue
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != (n_rows, n_cols):
            raise ValueError(f"{name} is {mask.shape}; the grid is {(n_rows, n_cols)}")
        off_street = mask if off_street is None else off_street | mask
    land = None if off_street is None else ~off_street.ravel()
    offsets = np.zeros(len(segments) + 1, dtype=np.int64)
    chunks: list[NDArray[np.int64]] = []
    for i, geom in enumerate(segments.geometry):
        # rasterize one buffered segment at a time: `all_touched` so a road that only clips a
        # cell still claims it, which is what the 15 m buffer is for in the first place.
        mask = rasterize(
            [(geom.buffer(SEGMENT_BUFFER_M), 1)],
            out_shape=(n_rows, n_cols),
            transform=affine,
            fill=0,
            all_touched=True,
            dtype="uint8",
        )
        flat = np.flatnonzero(mask.ravel()).astype(np.int64)
        if land is not None:
            flat = flat[land[flat]]
        chunks.append(flat)
        offsets[i + 1] = offsets[i] + flat.size

    cells = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int64)
    segment_ids = tuple(segments["segment_id"].astype(str))
    return (segment_ids, offsets, cells)


def _member_levels(
    depth_cm: NDArray[np.floating], member_depth_cm: NDArray[np.floating]
) -> NDArray[np.float32]:
    """Twin depth per segment per step, re-centred into one field per ensemble member.

    ``member_depth_cm`` is ``(n_members, n_steps, n_segments)`` of *absolute* emulator depth,
    aligned to the same segment order as ``depth_cm``. Only its deviation from the member mean
    is used, so whatever bias the emulator carries cancels exactly and the member mean of the
    result is the Twin field (ADR-0025, and the module docstring).

    float32 because the stack is the largest array a cycle holds - at P7.6's fifty members,
    50 x 36 x 21,296 is 307 MB in float64 and 153 MB here - and these are centimetres of water
    read to two decimals. The copy this function makes is a second one of the same size, which
    is the biggest single allocation in a cycle; a caller that must not pay it twice should
    build fewer members rather than skip the re-centring, because the re-centring is what keeps
    the level the Twin's.

    Raises:
        ValueError: if the member stack is not 3-D or its segment axis does not match the Twin's.
            The caller guarantees the alignment by construction, so a mismatch here means a
            member array from another city, and every street would silently get the wrong spread.
    """
    members = np.asarray(member_depth_cm, dtype=np.float32)
    n_steps, n_seg = depth_cm.shape
    if members.ndim != 3 or members.shape[2] != n_seg:
        raise ValueError(
            f"member_depth_cm must be (n_members, n_steps, {n_seg}) to match the Twin's segment "
            f"axis; got {members.shape}."
        )

    level = np.broadcast_to(depth_cm.astype(np.float32), (members.shape[0], n_steps, n_seg)).copy()
    # A shorter member stack than the Twin's horizon is possible when Sky keeps fewer per-member
    # steps than the Twin ran; the steps it does not cover keep the Twin's single value rather
    # than borrowing a spread from a neighbouring step.
    shared = min(n_steps, int(members.shape[1]))
    head = members[:, :shared]
    level[:, :shared] += head - head.mean(axis=0, keepdims=True)
    # Depth below zero is not a depth. Re-centring can push a dry street negative where the
    # members disagree by more than the Twin's own level, and the console draws these numbers.
    np.maximum(level, 0.0, out=level)
    return level


def segment_forecast(
    depth_m: NDArray[np.floating],
    times: tuple[datetime, ...],
    index,
    run_id: str,
    member_depth_cm: NDArray[np.floating] | None = None,
):
    """Per-segment depth per step, with exceedances and safe-until per profile.

    ``depth_m`` is ``(n_steps, n_rows, n_cols)`` from one Twin run and fixes the *level*: it is
    the Twin's depth the band is centred on (exactly, bar the zero clip). With members
    ``depth_p50_cm`` is their median rather than their mean, so it can sit a little off the Twin
    where they are skewed; how far is measured into ``max_p50_shift_cm`` on every run rather than
    asserted (2.7 cm at worst on the 2 July 09:10 cycle, against a 251.7 cm peak).

    ``member_depth_cm`` is the optional Flash-lite stack, ``(n_members, n_steps, n_segments)`` in
    centimetres, in the segment order of ``index``. Given it, the quantiles are taken across the
    members (:func:`_member_levels`) and each exceedance is the fraction of members above the
    threshold, so safe-until becomes what SPEC.md 11.8 actually asks for - the first step where
    ``P(h > threshold)`` passes the profile's tolerance - rather than the first step the single
    deterministic depth crosses it. Without it every quantile is the same number and every
    exceedance is 0 or 1, which is what a run of one member honestly has to say.
    """
    # Column-wise rather than one Python row per segment per step (task P10.4, ADR-0050). The
    # module is imported here, not at the top, because it reads this module's constants and
    # `_member_levels` at call time; importing each other at load would leave one half-built.
    # Every stage below is tested bitwise against the row loop it replaced, frame dtypes and row
    # order included, so the parquet this frame becomes is the same bytes (rule 8).
    from varuna_products import segment_table

    segment_ids = index[0]
    n_steps = depth_m.shape[0]
    n_seg = len(segment_ids)

    # The 90th percentile of each segment's buffer cells, segments with equal cell counts taken
    # in one call. Every profile's threshold is asked of the probability field too, so a profile
    # added at a depth the parquet does not carry a column for still gets a probabilistic
    # safe-until; on a single-member run that probability is 0 or 1, so safe-until reduces to
    # "first step the depth crosses" and reproduces the deterministic answer.
    depth_cm = segment_table.sample_segments(depth_m, index)
    statistics = segment_table.ensemble_statistics(depth_cm, member_depth_cm)
    blobs = segment_table.safe_until_blobs(statistics.prob, times)
    frame = segment_table.forecast_frame(run_id, segment_ids, times, statistics, blobs)
    n_members = statistics.n_members
    p10, p50, p90 = statistics.p10, statistics.p50, statistics.p90

    log.info(
        "products.segment_forecast",
        segments=n_seg,
        steps=n_steps,
        rows=len(frame),
        members=n_members,
        max_cm=round(float(depth_cm.max()) if depth_cm.size else 0.0, 1),
        wet_segments=int((depth_cm.max(axis=0) > 5.0).sum()) if depth_cm.size else 0,
        # Measured, not claimed (rule 6): how wide the band actually is, and how far the member
        # median sits from the Twin level the band is centred on.
        max_band_cm=round(float((p90 - p10).max()) if n_seg else 0.0, 1),
        max_p50_shift_cm=round(float(np.abs(p50 - depth_cm).max()) if n_seg else 0.0, 2),
    )
    return frame, depth_cm


WET_THRESHOLD_CM = 5.0
"""Below this a street is not wet, it is damp (SPEC.md 6.2's `--depth-dry` band)."""


def _exceedance_from_record(
    run_dir: Path, segment_ids: tuple[str, ...], n_steps: int
) -> dict[float, NDArray[np.float64]] | None:
    """Read the exceedance columns back out of the ``segment_forecast.parquet`` beside the JSON.

    The cycle writes the parquet immediately before the compact layer and hands this writer only
    the Twin level, so the record of product is where the probabilities are. They are re-keyed
    positionally - the parquet is written one segment block of ``n_steps`` rows at a time, in the
    order of ``index[0]`` - and that order is *checked* against ``segment_ids`` rather than
    assumed: a positional array from a different ordering would give every street someone else's
    probability, which is worse than giving it none.
    """
    path = run_dir / "segment_forecast.parquet"
    if not path.is_file():
        return None
    import pandas as pd

    columns = {t: f"p_gt_{t:g}" for t in EXCEEDANCE_CM}
    frame = pd.read_parquet(path, columns=["segment_id", *columns.values()])
    n_seg = len(segment_ids)
    ids = frame["segment_id"].to_numpy()
    aligned = len(frame) == n_seg * n_steps and bool(
        (ids.reshape(n_seg, n_steps) == np.asarray(segment_ids, dtype=object)[:, None]).all()
    )
    if not aligned:
        log.warning(
            "products.wet_segments.exceedance_misaligned",
            rows=len(frame),
            expected=n_seg * n_steps,
        )
        return None
    return {
        t: frame[col].to_numpy(dtype=np.float64).reshape(n_seg, n_steps).T
        for t, col in columns.items()
    }


def _probability(value: float) -> float | int:
    """Two decimals, as ``depth_cm`` gets one - and a certain 0 or 1 written as an integer.

    Most of a run's exceedances are certain (a dry street is 0 at every threshold), and ``0``
    against ``0.0`` is what keeps the added key near double the payload rather than four times
    it. JSON and the browser read the two as the same number."""
    rounded = round(float(value), 2)
    return int(rounded) if rounded in (0.0, 1.0) else rounded


def _one_decimal(value: float) -> float:
    """A depth in cm as the wet-segment layer writes it: Python's ``round`` to one decimal."""
    return round(float(value), 1)


def _per_column(field: NDArray[Any], convert: Callable[[float], object]) -> list[list[object]]:
    """``[[convert(v) for v in field[:, j]] for j in ...]`` with ``convert`` run once per value.

    A cycle's layer holds about a million numbers and very few *distinct* ones - a 20-member
    exceedance takes one of 21 values - so converting each distinct value once and laying the
    results out by position gives the same Python objects, and so the same JSON bytes, for a
    small fraction of the calls (task P10.4). Values are told apart by their float64 bit
    pattern rather than by ``==``, so ``-0.0`` stays ``-0.0`` and a NaN keeps its own entry:
    nothing two values would print differently can share a result. The float64 view is the
    ``float(v)`` the per-value loop took, for float32 and integer fields alike.
    """
    values = np.ascontiguousarray(field, dtype=np.float64)
    distinct, inverse = np.unique(values.view(np.uint64), return_inverse=True)
    converted = np.empty(distinct.size, dtype=object)
    converted[:] = [convert(v) for v in distinct.view(np.float64).tolist()]
    return converted[np.asarray(inverse).reshape(values.shape)].T.tolist()


def aoi_depth_band(
    depth_cm: NDArray[np.floating],
    member_depth_cm: NDArray[np.floating] | None,
) -> dict[str, list[float]] | None:
    """The time bar's spread band: p10, p50 and p90 of mean street depth, per step (SPEC.md 7.2).

    Section 7.2 draws "the ensemble spread band (p10-p90 of AOI-mean depth)" under the time bar.
    These are quantiles **of the mean**, not the mean of each street's quantiles: every member's
    street depths are averaged first and the percentiles taken across members, because the band is
    meant to say how much the whole city's water could differ, and averaging per-street percentiles
    would add up spreads that do not happen together.

    The level is the Twin's, as everywhere (ADR-0025): the mean of its street depths, with each
    member contributing only its deviation from the member mean. ``depth_cm`` is the Twin's
    ``(n_steps, n_segments)``; ``member_depth_cm`` the emulator's ``(n_members, n_steps,
    n_segments)``. Fewer than two members is no spread, and returns ``None`` rather than a band of
    zero width that would read as certainty.
    """
    if member_depth_cm is None:
        return None
    members = np.asarray(member_depth_cm, dtype=np.float64)
    if members.ndim != 3 or members.shape[0] < 2:
        return None
    level = np.asarray(depth_cm, dtype=np.float64)
    steps = min(level.shape[0], members.shape[1])
    means = members[:, :steps, :].mean(axis=2)
    deviation = means - means.mean(axis=0, keepdims=True)
    base = level[:steps].mean(axis=1)
    p10, p50, p90 = np.percentile(deviation, [10.0, 50.0, 90.0], axis=0)
    return {
        name: [round(float(v), 3) for v in np.maximum(base + q, 0.0)]
        for name, q in (("p10", p10), ("p50", p50), ("p90", p90))
    }


def write_wet_segments(
    run_dir: Path,
    depth_cm: NDArray[np.floating],
    segment_ids: tuple[str, ...],
    times: tuple[datetime, ...],
    run_id: str,
    p_gt: dict[float, NDArray[np.floating]] | None = None,
) -> dict[str, Any]:
    """Write the console's segment layer as a small JSON, once, at bake time.

    The parquet beside it is the product of record - every segment, every step, every profile's
    safe-until, 19 MB of it - and it is the right thing for `services/verify` and for anyone
    who wants the numbers. It is the wrong thing to put on the wire: the API was reading all
    19 MB with pandas, filtering it and re-serialising it **on every request**, which is most of
    why the console took so long to show a map.

    So the shape the map actually draws is computed once here: the segments that get wet, their
    depth at each step, one decimal, and nothing else. On a heavy Mumbai cycle that is about
    6,500 of 21,296 segments and lands near 1 MB - a file the API can stream straight off disk.

    **Exceedance** (SPEC.md 6.2, 7.2, task P7.6). Probability mode draws opacity as
    ``P(depth > threshold)``, and without this key the console can only compute that from the
    depth itself, which makes it 0 or 1 by construction. So each wet segment's ``p_gt`` series
    at 15/30/45/60 cm goes on the wire as ``{"15": {segment_id: [per step]}, ...}``, taken from
    ``p_gt`` (``{threshold_cm: (n_steps, n_segments)}``) or, when the caller passes none, from
    the parquet beside this file. It is written **only when some value lies strictly between 0
    and 1**: a single-member run's exceedances are the depth comparison the console already
    makes, and shipping them would double the file to say nothing new (rule 6 - the key's
    presence is itself the claim that the run measured a spread).
    """
    depth_cm = np.asarray(depth_cm)
    n_steps = depth_cm.shape[0]
    peak = depth_cm.max(axis=0)
    wet = np.flatnonzero(peak >= WET_THRESHOLD_CM)
    # Python's `round`, once per distinct value (`_per_column`), so the JSON is the bytes the
    # per-value loop wrote (rule 8). np.round is deliberately not used: it is not always the
    # correctly rounded decimal Python's `round` is, and the file would drift in its last digit.
    series = {
        str(segment_ids[k]): column
        for k, column in zip(wet.tolist(), _per_column(depth_cm[:, wet], _one_decimal), strict=True)
    }
    product: dict[str, Any] = {
        "run_id": run_id,
        "valid_ts": [t.isoformat() for t in times],
        "min_depth_cm": WET_THRESHOLD_CM,
        "n_segments_total": len(segment_ids),
        "n_segments_wet": len(series),
        "depth_cm": series,
    }

    if p_gt is None:
        p_gt = _exceedance_from_record(run_dir, segment_ids, n_steps)
    uncertain = 0
    if p_gt is not None:
        missing = [t for t in EXCEEDANCE_CM if t not in p_gt]
        if missing:
            raise ValueError(f"p_gt has no series for {missing} cm; the console reads all four.")
        fields = {t: np.asarray(p_gt[t])[:, wet] for t in EXCEEDANCE_CM}
        for t, field in fields.items():
            if field.shape != (n_steps, wet.size):
                raise ValueError(
                    f"p_gt[{t:g}] is {np.asarray(p_gt[t]).shape}; expected (n_steps, n_segments)"
                    f" = {depth_cm.shape}."
                )
        # Counted over what is written, not over the city: a spread on a street that never
        # gets wet is not one the map can draw.
        uncertain = int(
            sum(int(((f > 0.0) & (f < 1.0)).any(axis=0).sum()) for f in fields.values())
        )
        if uncertain:
            product["p_gt"] = {
                f"{t:g}": {
                    str(segment_ids[k]): column
                    for k, column in zip(
                        wet.tolist(), _per_column(field, _probability), strict=True
                    )
                }
                for t, field in fields.items()
            }

    text = json.dumps(product, separators=(",", ":"))
    (run_dir / "segments_wet.json").write_text(text, encoding="utf-8")
    log.info(
        "products.wet_segments",
        run_id=run_id,
        wet=len(series),
        of=len(segment_ids),
        p_gt="p_gt" in product,
        # Segment-threshold series with at least one step strictly between 0 and 1: the number
        # of places probability mode draws something a deterministic run could not.
        uncertain_series=uncertain,
        bytes=len(text),
    )
    return product


def segment_names(city_root: Path) -> dict[str, str]:
    """Segment id to the street's OSM name, for products that have to say *where*.

    Only named ways. A road with no name in OSM is left out rather than given a placeholder, so
    nothing downstream has to decide whether "Unnamed road" came from the map or from us.

    Normalised on the way out (:func:`varuna_products.names.street_name`). A way that OSM gives
    several names is stored by an old build as the text ``"['Dr Ambedkar Road', 'Kalachowki
    Road']"``, and that string was reaching alert headlines and the pump board; one of the two
    it reaches is on the demo route. The build no longer writes it, and this repairs the city
    already on disk without a rebuild.
    """
    return {
        segment_id: primary for segment_id, (primary, _) in _names_and_aliases(city_root).items()
    }


def segment_name_aliases(city_root: Path) -> dict[str, tuple[str, ...]]:
    """Segment id to the *other* names its way carries, for the segments that have any.

    The names :func:`segment_names` did not pick. Kept rather than dropped (rule 6): a headline
    needs one street, and somebody searching may know it by the other one. Read from the
    ``name_aliases`` column when the city was built with it, and recovered from the stored value
    otherwise, so both generations of city answer the same question.
    """
    return {
        segment_id: aliases
        for segment_id, (_, aliases) in _names_and_aliases(city_root).items()
        if aliases
    }


def _names_and_aliases(city_root: Path) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Every named segment as ``id -> (name, aliases)``, from either generation of city table."""
    import pandas as pd

    from varuna_products.names import split_names

    table = city_root / "segments.parquet"
    if not table.is_file():
        return {}
    columns = ["segment_id", "name"]
    if _has_column(table, "name_aliases"):
        columns.append("name_aliases")
    frame = pd.read_parquet(table, columns=columns)
    named = frame[frame["name"].notna()]
    stored = named["name_aliases"] if "name_aliases" in named.columns else [None] * len(named)

    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for segment_id, value, extra in zip(named["segment_id"], named["name"], stored, strict=True):
        primary, aliases = split_names(value)
        if primary is None:
            continue  # a name that is only whitespace, or an empty stored list
        if extra is not None and len(extra):
            # A city built with the column: those aliases are authoritative, and the stored name
            # is already a single street, so nothing was recovered from it above.
            aliases = tuple(str(a) for a in extra)
        out[str(segment_id)] = (primary, aliases)
    return out


def _has_column(table: Path, column: str) -> bool:
    """Whether ``table`` carries ``column``, without reading a row of it."""
    import pyarrow.parquet as pq

    return column in pq.read_schema(table).names


def segment_points(city_root: Path) -> dict[str, tuple[float, float]]:
    """Segment id to a representative lon/lat, for products that must put a pin somewhere.

    The midpoint of the segment's line rather than its bounding-box centre, so the point is on
    the road even where it bends. Used by alerts (the CAP area circle) and by the pump board,
    which has to dispatch a lorry to a place rather than to an id.

    Memoised in-process on ``segments.parquet``'s mtime and size
    (:func:`varuna_products.segment_table.segment_points_cached`): reading and reprojecting
    21,296 lines is about a second, and a bake or a live API process was paying it every cycle
    for geometry that had not changed. A fresh dict is returned each call, and a rewritten table
    is read again. The first call in a process still pays it.
    """
    from varuna_products import segment_table

    return segment_table.segment_points_cached(city_root)


def _read_segment_points(city_root: Path) -> dict[str, tuple[float, float]]:
    """:func:`segment_points` without the memo: the midpoints read from the table every call."""
    import geopandas as gpd

    table = city_root / "segments.parquet"
    if not table.is_file():
        return {}
    frame = gpd.read_parquet(table, columns=["segment_id", "geometry"]).to_crs("EPSG:4326")
    points = frame.geometry.interpolate(0.5, normalized=True)
    return {
        str(sid): (float(p.x), float(p.y))
        for sid, p in zip(frame["segment_id"], points, strict=True)
    }
