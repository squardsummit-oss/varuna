"""Turn a built city into the structures the solvers run on (SPEC.md 10.1, 11.3-11.4).

This is the only place VARUNA-Twin touches the filesystem. Everything else in the service takes
:mod:`varuna_twin.types` structures and returns them, which is what makes the solvers testable
on a five-node network and a 64 x 64 grid.

Two things arrive here, and neither is guessed:

* the **terrain**, from ``city/<city>/*.tif``. Resolution, CRS and the affine transform are read
  from the GeoTIFF itself, never from a constant, so a re-run of ``make city`` with a different
  grid cannot silently disagree with the solver.
* the **drain graph**, from ``city/<city>/graph/{nodes,edges}.parquet``. The tables address each
  other by string id (``MUM-N000000``); :class:`~varuna_twin.types.DrainNetwork` addresses by
  integer index, because the kernels cannot carry a string. Building that map is most of this
  module, and an id that does not resolve raises rather than becoming ``-1`` - a silent -1 would
  route a pipe's water to whichever node happens to sit at the end of the array.

The city pipeline writes 50,110 nodes and 49,983 edges for Mumbai, so everything here is
vectorised: the id map is a single ``pandas.Index.get_indexer``, not a Python loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog
from varuna_schemas.paths import bundle_dir, city_dir

from varuna_twin.nests import NestSpec
from varuna_twin.types import DrainNetwork, TerrainGrid, TideSeries

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray
    from varuna_schemas.models.bundle import TideDatum

log = structlog.get_logger("varuna.twin.city")

__all__ = [
    "BOUNDARY_FREE",
    "BOUNDARY_INTERIOR",
    "BOUNDARY_TIDAL",
    "RASTERS",
    "SEA_RASTER",
    "CityTerrain",
    "load_nests",
    "load_network",
    "load_terrain",
    "load_tide",
]

BOUNDARY_INTERIOR = 0
"""``DrainNetwork.boundary`` code: the node only exchanges with the graph."""

BOUNDARY_TIDAL = 1
"""``DrainNetwork.boundary`` code: an outfall held at the sea stage."""

BOUNDARY_FREE = 2
"""``DrainNetwork.boundary`` code: an outfall discharging freely at its invert."""

RASTERS = {
    "z": "dem_conditioned.tif",
    "manning_n": "roughness.tif",
    "blocked": "blocked.tif",
    "imperviousness": "imperviousness.tif",
    "cn": "cn.tif",
}
"""Which ``TerrainGrid`` field comes from which raster the city pipeline writes.

``dem_conditioned.tif`` and not ``dem.tif``: the solver runs on the hydro-conditioned surface
of SPEC.md 10.1 step 4, with buildings burned, roads carved and culverts breached. Running on
the raw DEM would dam every street at its kerb line.
"""


SEA_RASTER = "sea_mask.tif"
"""The city's sea (``varuna_city.sea``): 0 land, 1 open sea, 2 tidal creek. Optional."""


@dataclass(frozen=True, slots=True)
class CityTerrain(TerrainGrid):
    """A :class:`TerrainGrid` that also carries the city's sea.

    ``sea`` is True on every cell the tide is imposed on - open sea and tidal creek alike - and
    ``None`` for a city built before the sea step wrote ``sea_mask.tif``. The runner reads it with
    ``getattr(terrain, "sea", None)``, so a plain ``TerrainGrid`` (every test grid, a nest) runs
    exactly as before, and a run on a city without the raster imposes the tide on its tidal
    outfalls' cells alone, as every run did until then.

    A subclass rather than a field on ``TerrainGrid`` only because ``types.py`` belongs to
    another part of this change; the field belongs there, and moving it is a rename.
    """

    sea: NDArray[np.bool_] | None = None


def _open_raster(path: Path):
    import rasterio

    if not path.is_file():
        msg = (
            f"No raster at {path}. Run `make city CITY={path.parent.name}` first: the Twin runs "
            "on the grid the city pipeline conditions, never on one it invents."
        )
        raise FileNotFoundError(msg)
    return rasterio.open(path)


def load_terrain(city: str = "mumbai") -> TerrainGrid:
    """The conditioned city grid the 2D solver runs on.

    Every raster in :data:`RASTERS` must share one grid. That is checked rather than assumed:
    a one-cell disagreement between the DEM and the roughness raster would shift friction by a
    cell across the whole city, which no test downstream would attribute to this function.

    ``blocked.tif`` is read as a mask of *non-zero* rather than of ``== 1``, because the city
    pipeline writes it as an integer mask and a future version writing 255 for true should not
    silently unblock every building.
    """
    root = city_dir(city)
    fields: dict[str, NDArray[np.floating]] = {}
    reference: tuple[int, int] | None = None
    res_m = crs = transform = None

    for name, filename in RASTERS.items():
        with _open_raster(root / filename) as src:
            band = src.read(1)
            if reference is None:
                reference = (int(src.height), int(src.width))
                res_m = float(abs(src.transform.a))
                crs = str(src.crs)
                transform = (
                    float(src.transform.a),
                    float(src.transform.b),
                    float(src.transform.c),
                    float(src.transform.d),
                    float(src.transform.e),
                    float(src.transform.f),
                )
            elif (int(src.height), int(src.width)) != reference:
                msg = (
                    f"{filename} is {src.height} x {src.width} but the grid is "
                    f"{reference[0]} x {reference[1]}; every city raster must share one grid"
                )
                raise ValueError(msg)
            fields[name] = band

    assert reference is not None and res_m is not None and crs is not None
    assert transform is not None

    sea = _read_sea(root / SEA_RASTER, reference)
    terrain = CityTerrain(
        z=np.asarray(fields["z"], dtype=np.float64),
        manning_n=np.asarray(fields["manning_n"], dtype=np.float64),
        blocked=np.asarray(fields["blocked"]) != 0,
        imperviousness=np.asarray(fields["imperviousness"], dtype=np.float64),
        cn=np.asarray(fields["cn"], dtype=np.float64),
        res_m=res_m,
        crs=crs,
        transform=transform,
        sea=sea,
    )
    log.info(
        "twin.terrain_loaded",
        city=city,
        shape=f"{terrain.n_rows} x {terrain.n_cols}",
        res_m=res_m,
        crs=crs,
        blocked_fraction=round(float(terrain.blocked.mean()), 4),
        sea_cells=None if sea is None else int(sea.sum()),
    )
    return terrain


def _read_sea(path: Path, shape: tuple[int, int]) -> NDArray[np.bool_] | None:
    """``sea_mask.tif`` as a boolean mask on the city grid, or ``None`` when the city has none.

    Absent is a city built before the sea step, and is not an error: the runner falls back to
    the tidal outfalls' cells. Present on another grid is an error, for the reason
    :func:`load_terrain` checks every raster - a mask one cell off would put the sea's level on
    the first row of streets.
    """
    if not path.is_file():
        log.info("twin.no_sea_mask", path=str(path))
        return None
    with _open_raster(path) as src:
        if (int(src.height), int(src.width)) != shape:
            msg = (
                f"{path.name} is {src.height} x {src.width} but the grid is "
                f"{shape[0]} x {shape[1]}; every city raster must share one grid"
            )
            raise ValueError(msg)
        band = src.read(1)
    # 255 is the writer's no-data; nothing the sea step writes carries it, and it is not sea.
    return np.asarray((band != 0) & (band != 255), dtype=np.bool_)


def _boundary_codes(nodes) -> NDArray[np.int8]:
    """Read the three outfall columns the city pipeline writes into one ``boundary`` code.

    The table carries ``is_outfall`` (is this the end of the network), ``tidal`` (does the sea
    reach it) and ``boundary_type`` (a string). They agree in the data the pipeline writes, and
    this reads them defensively anyway: a node is tidal when it is an outfall AND the sea
    reaches it, free when it is an outfall the sea does not reach, and interior otherwise. A
    node flagged ``tidal`` without being an outfall is an interior node - the sea does not reach
    up into the middle of a network - and is logged, because it would mean the city pipeline and
    this reader disagree about what those columns mean.
    """
    n = len(nodes)
    codes = np.full(n, BOUNDARY_INTERIOR, dtype=np.int8)
    is_outfall = (
        nodes["is_outfall"].to_numpy(dtype=bool)
        if "is_outfall" in nodes.columns
        else np.zeros(n, dtype=bool)
    )
    tidal = (
        nodes["tidal"].to_numpy(dtype=bool) if "tidal" in nodes.columns else np.zeros(n, dtype=bool)
    )
    codes[is_outfall & ~tidal] = BOUNDARY_FREE
    codes[is_outfall & tidal] = BOUNDARY_TIDAL

    stray = int(np.count_nonzero(tidal & ~is_outfall))
    if stray:
        log.warning("twin.tidal_without_outfall", nodes=stray)
    return codes


def _crown_depth(edges) -> NDArray[np.floating]:
    """Invert-to-crown height per edge in metres: the ``D`` of the fill fraction.

    The city pipeline sizes two shapes (SPEC.md 10.1 step 7): circular pipes, which carry
    ``diameter_m``, and **box drains on the trunks, which carry ``width_m`` and ``height_m`` and
    leave ``diameter_m`` as NaN**. On Mumbai that is 2,626 of 49,983 edges - about 5 %, and they
    are the trunks, so they are exactly the edges the tide reaches first.

    Reading ``diameter_m`` alone puts a NaN on every one of them, and NaN does not announce
    itself here: it flows through ``min(H - z_inv, D)/D`` into the fill fraction, through the
    pressurisation test (every comparison against NaN is False, so a full box drain silently
    reads as empty), and into the stored volume, which is why the first city-scale run reported
    a mass balance of ``nan`` rather than a number that looked wrong.

    For a box drain the fill fraction is measured over its **height**, which is the invert-to-
    crown distance, the same thing the diameter is for a circular pipe. ``width_m`` is already
    accounted for in ``area_m2`` and ``hydraulic_radius_m``, so it is not wanted here.
    """
    diameter = edges["diameter_m"].to_numpy(dtype=np.float64)
    if "height_m" not in edges.columns:
        return diameter
    height = edges["height_m"].to_numpy(dtype=np.float64)
    depth = np.where(np.isfinite(diameter), diameter, height)

    unsized = ~np.isfinite(depth)
    if np.any(unsized):
        msg = (
            f"{int(np.count_nonzero(unsized))} drain edge(s) carry neither diameter_m nor "
            "height_m, so their fill fraction is undefined; the city graph is malformed"
        )
        raise ValueError(msg)
    log.info(
        "twin.crown_depth",
        circular=int(np.count_nonzero(np.isfinite(diameter))),
        box=int(np.count_nonzero(~np.isfinite(diameter))),
    )
    return depth


def load_network(city: str = "mumbai") -> DrainNetwork:
    """The inferred drain graph as the flat arrays a compiled kernel can walk.

    Every element of this graph is ``confidence = "inferred"`` upstream (SPEC.md 10.1 step 7),
    which is why every screen that draws it has to say so.
    """
    import pandas as pd

    root = city_dir(city) / "graph"
    for name in ("nodes.parquet", "edges.parquet"):
        if not (root / name).is_file():
            msg = f"No drain graph at {root / name}. Run `make city CITY={city}` first."
            raise FileNotFoundError(msg)

    nodes = pd.read_parquet(root / "nodes.parquet")
    edges = pd.read_parquet(root / "edges.parquet")
    depth_m = _crown_depth(edges)

    # --- edge endpoints: string id -> integer index, vectorised ---------------------------
    index = pd.Index(nodes["node_id"].astype(str))
    if index.has_duplicates:
        dupes = index[index.duplicated()].unique()[:5].tolist()
        msg = f"nodes.parquet has duplicate node_id values, e.g. {dupes}"
        raise ValueError(msg)

    from_node = index.get_indexer(edges["from_node"].astype(str))
    to_node = index.get_indexer(edges["to_node"].astype(str))
    missing = np.count_nonzero(from_node < 0) + np.count_nonzero(to_node < 0)
    if missing:
        # get_indexer returns -1 for an id it cannot find. Letting that through would make the
        # edge point at nodes[-1], the last node in the table, and quietly route its water to
        # the wrong place - so this raises with the ids rather than repairing them.
        bad = edges.loc[(from_node < 0) | (to_node < 0), ["edge_id", "from_node", "to_node"]]
        msg = (
            f"{missing} edge endpoint(s) name a node that is not in nodes.parquet, "
            f"e.g. {bad.head(3).to_dict('records')}"
        )
        raise ValueError(msg)

    network = DrainNetwork(
        node_ids=tuple(nodes["node_id"].astype(str)),
        z_ground=nodes["z_ground_m"].to_numpy(dtype=np.float64),
        z_invert=nodes["z_invert_m"].to_numpy(dtype=np.float64),
        storage_area=nodes["storage_area_m2"].to_numpy(dtype=np.float64),
        inlet_length=nodes["inlet_length_m"].to_numpy(dtype=np.float64),
        inlet_area=nodes["inlet_area_m2"].to_numpy(dtype=np.float64),
        kappa=nodes["kappa_mean"].to_numpy(dtype=np.float64),
        boundary=_boundary_codes(nodes),
        flap_gate=nodes["flap_gate"].to_numpy(dtype=bool),
        cell_row=nodes["cell_row"].to_numpy(dtype=np.int32),
        cell_col=nodes["cell_col"].to_numpy(dtype=np.int32),
        edge_ids=tuple(edges["edge_id"].astype(str)),
        from_node=from_node.astype(np.int32),
        to_node=to_node.astype(np.int32),
        length=edges["length_m"].to_numpy(dtype=np.float64),
        area=edges["area_m2"].to_numpy(dtype=np.float64),
        hydraulic_radius=edges["hydraulic_radius_m"].to_numpy(dtype=np.float64),
        diameter=depth_m,
        edge_manning_n=edges["manning_n"].to_numpy(dtype=np.float64),
        q_full=edges["q_full_m3s"].to_numpy(dtype=np.float64),
        beta=edges["beta_mean"].to_numpy(dtype=np.float64),
    )
    log.info(
        "twin.network_loaded",
        city=city,
        n_nodes=network.n_nodes,
        n_edges=network.n_edges,
        n_tidal=int((network.boundary == BOUNDARY_TIDAL).sum()),
        n_free=int((network.boundary == BOUNDARY_FREE).sum()),
        n_flap=int(network.flap_gate.sum()),
        mean_beta=round(float(network.beta.mean()), 4),
    )
    return network


def load_tide(bundle: str, *, city: str = "mumbai") -> TideSeries | None:
    """The bundle's sea stage, carrying the ``source`` label it was written with.

    That label is not decoration. SPEC.md rule 7 and the :class:`TideSeries` docstring both
    require the console to be able to say whether the series came from a published tide table or
    is labelled illustrative, and for MUM-2019-07-02 it is illustrative - one sourced high-water
    height with an assumed harmonic around it (ADR-0007). Dropping the column here would turn an
    assumption into a measurement by the time it reached a judge.

    Returns ``None`` when the bundle carries no tide, which is the honest state for a design
    storm rather than a reason to invent a flat sea.

    **The datum.** ``tide.csv`` keeps the stage as sourced, and a tide table states heights
    above chart datum while the terrain is in the DEM's frame (EGM2008 for Copernicus GLO-30).
    When the manifest beside the series declares ``tide_datum`` with a chart-datum stage, the
    mean-sea-level offset it carries is subtracted here, once, and ``datum_note`` says so. A
    manifest that declares no datum leaves the series exactly as written, which is what every
    bundle did before the block existed. A declared block that does not validate raises: a
    silently wrong datum moves the sea by metres at the boundary.
    """
    import pandas as pd

    root = bundle_dir(bundle)
    path = root / "tide.csv"
    if not path.is_file():
        log.info("twin.no_tide", bundle=bundle, path=str(path))
        return None

    frame = pd.read_csv(path, parse_dates=["ts"])
    if frame.empty:
        return None
    source = str(frame["source"].iloc[0]) if "source" in frame.columns else "unlabelled"
    stage_m = frame["stage_m"].to_numpy(dtype=np.float64)

    datum = _tide_datum(root / "manifest.json")
    datum_note: str | None = None
    if datum is not None and datum.offset_to_dem_m != 0.0:
        stage_m = stage_m - datum.offset_to_dem_m
        # A chart-datum block cannot validate without its range, so this is never "unknown".
        low, high = datum.range_m if datum.range_m is not None else (float("nan"),) * 2
        datum_note = (
            f"Tide stage converted from {datum.stage_reference} to the DEM's frame by "
            f"subtracting mean sea level at {datum.offset_to_dem_m:.2f} m above chart datum "
            f"(range {low:.2f}-{high:.2f} m; {', '.join(datum.source_urls)}). {datum.residual}"
        )

    series = TideSeries(
        times=tuple(pd.to_datetime(frame["ts"]).dt.to_pydatetime()),
        stage_m=stage_m,
        source=source,
        datum_note=datum_note,
    )
    log.info(
        "twin.tide_loaded",
        bundle=bundle,
        n=len(series.times),
        min_m=round(float(series.stage_m.min()), 3),
        max_m=round(float(series.stage_m.max()), 3),
        source=source,
        stage_datum=datum.stage_datum if datum is not None else "undeclared",
        offset_to_dem_m=datum.offset_to_dem_m if datum is not None else 0.0,
    )
    return series


def _tide_datum(manifest_path: Path) -> TideDatum | None:
    """The manifest's ``tide_datum`` block, or ``None`` when there is no manifest or no block.

    Only the block is validated, not the whole manifest: the Twin needs the datum, and a test
    bundle that carries a tide and a partial manifest should not fail on fields the solver never
    reads. An unreadable manifest is treated as declaring nothing and logged.
    """
    import json

    from varuna_schemas.models.bundle import TideDatum

    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("twin.manifest_unreadable", path=str(manifest_path), error=str(exc)[:200])
        return None
    block = payload.get("tide_datum") if isinstance(payload, dict) else None
    return None if block is None else TideDatum.model_validate(block)


def load_nests(city: str = "mumbai") -> tuple[NestSpec, ...]:
    """Resolve the city config's ``nests`` block against the verified hotspot register.

    SPEC.md 3.3 lists Hindmata and King's Circle with coordinates prefixed "approximately" and
    then instructs that each one be **verified with Nominatim/OSM before use** and stored with
    its ``source_url``. The city pipeline did that; ``city/<city>/hotspots.geojson`` is the
    result and it is the only coordinate this function will hand to :func:`varuna_twin.nests.
    build_nest`. The ``lon``/``lat`` in ``services/city/configs/<city>.yaml`` are the same
    approximate literals SPEC.md prints, and on Mumbai they miss the register by **240 m at
    Hindmata and 130 m at King's Circle** - a quarter of a 1 km window, on a feature the size of
    a junction. So the config is read for *which* nests exist and how big they are, and the
    register is read for *where* they are.

    **The match is by name, because the schema has no id to match on.**
    ``varuna_schemas.models.city.NestSpec`` carries ``id``, ``name``, ``lon``, ``lat``,
    ``size_m``, ``res_m`` and ``tier`` and forbids extra keys, so the config cannot yet name a
    ``hotspot_id`` and this function slugifies the config's ``name`` and requires **exactly one**
    register entry whose slug starts with it. "Hindmata junction" resolves to
    ``hindmata-junction-hindmata-cinema-dr-b-ambedkar-`` and "King's Circle" to
    ``king-s-circle-maheshwari-udyan-junction``; a name that matches none, or more than one, is
    an error rather than a guess, because guessing here silently moves a nest to the wrong
    junction and every depth it reports would be honest arithmetic about the wrong street. A
    ``hotspot_id`` field on that model would make the binding exact and is the better fix; it is
    recorded in ``.wf/NESTS-requests.md`` rather than made here.

    **The config literal is still checked.** The resolved point must lie within half the
    window of the config's own coordinate. That is not a coordinate source - it is the guard
    that catches a register entry which drifted, or a config whose label no longer describes
    the place it was written for.

    Args:
        city: city slug with a config under ``services/city/configs/``.

    Returns:
        One :class:`~varuna_twin.nests.NestSpec` per config entry, in config order.

    Raises:
        FileNotFoundError: no city config, or no hotspot register beside the built city.
        ValueError: a nest's name matches no register point or several, or the register point
            it matched is further from the config literal than half the window.
    """
    import json
    from math import cos, radians

    from varuna_schemas.models.city import CityConfig
    from varuna_schemas.paths import city_config_path

    config = CityConfig.from_yaml(city_config_path(city))
    if not config.nests:
        log.info("twin.no_nests", city=city)
        return ()

    register_path = city_dir(city) / config.hotspots_path
    if not register_path.is_file():
        msg = (
            f"No hotspot register at {register_path}. Run `make city CITY={city}` first: a nest "
            "is centred on a verified, sourced register point (SPEC.md 3.3), never on the "
            "approximate literal in the config."
        )
        raise FileNotFoundError(msg)
    features = json.loads(register_path.read_text(encoding="utf-8")).get("features", [])
    points = [f.get("properties", {}) for f in features]

    resolved: list[NestSpec] = []
    for entry in config.nests:
        want = _slug(entry.name)
        hits = [p for p in points if str(p.get("slug", "")).startswith(want)]
        if len(hits) != 1:
            names = sorted(str(p.get("name", "?")) for p in hits)
            msg = (
                f"nest {entry.id}: the config calls it {entry.name!r}, which slugifies to "
                f"{want!r} and matches {len(hits)} register points"
                + (f" ({', '.join(names)})" if names else "")
                + f" in {register_path}. A nest is centred on exactly one verified point; "
                "rename the config entry to the register's own name, or add the point."
            )
            raise ValueError(msg)
        hit = hits[0]
        lon, lat = float(hit["lon"]), float(hit["lat"])
        # Planar enough at 1 km and 19 degrees north to be a guard rather than a measurement.
        dx = (lon - entry.lon) * 111_320.0 * cos(radians(lat))
        dy = (lat - entry.lat) * 110_540.0
        drift_m = (dx * dx + dy * dy) ** 0.5
        if drift_m > entry.size_m / 2.0:
            msg = (
                f"nest {entry.id}: the register's {hit.get('name')!r} sits {drift_m:.0f} m from "
                f"the config's own ({entry.lon}, {entry.lat}), which is more than half the "
                f"{entry.size_m:.0f} m window. One of the two is describing a different place; "
                "the register is authoritative, so fix the config or the match."
            )
            raise ValueError(msg)
        resolved.append(
            NestSpec(
                id=entry.id,
                name=entry.name,
                lon=lon,
                lat=lat,
                size_m=float(entry.size_m),
                res_m=float(entry.res_m),
                tier=str(entry.tier),
                hotspot_id=str(hit.get("hotspot_id")) if hit.get("hotspot_id") else None,
                register_name=str(hit.get("name")) if hit.get("name") else None,
                source_url=str(hit.get("source_url")) if hit.get("source_url") else None,
            )
        )
        log.info(
            "twin.nest_resolved",
            city=city,
            nest=entry.id,
            hotspot_id=hit.get("hotspot_id"),
            register=hit.get("name"),
            drift_from_config_m=round(drift_m, 1),
            sourced=bool(hit.get("source_url")),
            confidence=hit.get("confidence"),
        )
    return tuple(resolved)


def _slug(name: str) -> str:
    """The register's own slug rule: lower case, non-alphanumerics to single hyphens.

    Kept identical to what the city pipeline wrote into ``hotspots.geojson`` so that a config
    name and a register name that describe the same junction produce the same prefix. An
    apostrophe becomes a hyphen, which is why "King's Circle" is ``king-s-circle``.
    """
    import re

    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", name.lower())).strip("-")
