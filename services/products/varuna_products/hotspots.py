"""Ranking the chronic spots by what this run says will happen to them (SPEC.md 11.8, P5.4).

The hotspot register is 28 sourced points for Mumbai (SPEC.md 10.1 step 9) - Hindmata, King's
Circle, Sion Circle, the Andheri and Milan subways, and the rest - each carrying the `source_url`
it was verified against. This module says which of them this run expects to flood, how deep, and
when.

**The ranking.** SPEC.md 11.8 fixes it as ``expected impact = P(impassable at peak) x
exposure_weight``. With one deterministic Twin run ``P`` is 0 or 1, so the ordering it produces
would be a coarse two-tier sort with the exposure weight breaking ties. That is not useful to an
operator scanning a rail, so the ranking here uses **peak depth** as the score and reports the
exceedance separately, which is the same intent with the information actually available. When
Flash-lite brings a real 50-member ensemble in Phase 7 the probability becomes continuous and the
spec's product becomes the right score; the field is already in the output so nothing downstream
changes shape.

**Sampling.** A hotspot is a point, but a junction is not: the register's coordinate is a marker
for a place a few tens of metres across, and a 30 m grid cell either contains the dip or misses
it. So depth is read over a small neighbourhood and the 90th percentile taken, the same rule and
the same reasoning as the road segments in :mod:`varuna_products.depth`.

**Attribution** (SPEC.md 7.2, 11.7; task P7.7). Each ranked entry can carry the pipes that
explain its peak, measured by :func:`varuna_flash.whatif.attribute_pipes` - `drain1d` re-run on
the junction's own catchment with the street depth frozen at what the Twin produced, once per
candidate pipe within five upstream hops. It replaces the Flash-lite finite difference ADR-0042
retired, which could not move a junction from a pipe that was not under it.

It is **not** computed for every hotspot, and the reason is cost: measured on this laptop
(Intel i5-1155G7, 10-12 python processes) the whole 28-point Mumbai register takes 24.2-27.0 s,
against SPEC.md 11.8's 2 s for the entire products stage. So it runs for the worst
:data:`ATTRIBUTION_MAX_HOTSPOTS` junctions that are wetter than :data:`ATTRIBUTION_MIN_PEAK_CM`,
and every other entry carries ``attribution_label`` saying it was not attempted rather than an
empty list that would read as "no pipe is responsible".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.products.hotspots")

__all__ = [
    "ATTRIBUTION_MAX_HOTSPOTS",
    "ATTRIBUTION_MIN_PEAK_CM",
    "HOTSPOT_RADIUS_M",
    "IMPASSABLE_CM",
    "rank_hotspots",
]

ATTRIBUTION_MAX_HOTSPOTS = 10
"""How many junctions get a pipe ranking, worst first.

A budget, stated as one. Attribution is a `drain1d` run per candidate pipe (see the module
docstring), and at 24.2-27.0 s for the register it is the most expensive thing in the products
stage by an order of magnitude. Ten covers every junction the 2 July replay puts above the
5 cm mark with room to spare - on the 08:40 cycle the register has twelve - so the cut falls
among junctions that are barely wet rather than among the ones an operator is looking at."""

ATTRIBUTION_MIN_PEAK_CM = 5.0
"""Peak depth a junction needs before its pipes are worth ranking, in cm.

SPEC.md 6.2's ``--depth-dry`` band: below 5 cm the map does not even draw the street as wet.
Asking which pipe explains a 0.87 cm peak spends seconds to rank noise."""

HOTSPOT_RADIUS_M = 45.0
"""Half-width of the neighbourhood a hotspot's depth is read over, in metres.

A choice, not spec. A junction is tens of metres across and the register's point is a marker for
it rather than a survey mark, so reading one 30 m cell would make the answer depend on which side
of a cell boundary the marker happened to land. 45 m is a cell and a half either way: wide enough
to contain the dip, narrow enough not to average in the road that climbs out of it."""

IMPASSABLE_CM = 30.0
"""The depth at which a car stops (SPEC.md 6.2, the --depth-3 band).

Used for the exceedance the rail reports. Other vehicles have other thresholds and the segment
forecast carries all of them; the rail needs one number and the car is the one a judge pictures."""

EXPOSURE_RADIUS_M = 300.0
"""How far around a hotspot counts as exposed (SPEC.md 3.3: "hospital/station within 300 m").

The same 300 m the city pipeline used when it computed each segment's exposure weight, so the
rail's icons and the weight behind them are answering the question at the same range."""

FACILITY_KINDS = ("hospital", "fire_station", "station", "shelter")
"""Asset kinds the rail draws an icon for, in the order the icons appear.

``pumping_station``, ``depot``, ``holding_tank`` and ``mobile_pump`` are response infrastructure
rather than things at risk, so they belong to the pump board (Phase 8), not to the exposure row."""


def _segments_near(
    city_root: Path,
    windows: list[set[int]],
    index: tuple[tuple[str, ...], NDArray[np.int64], NDArray[np.int64]] | None,
) -> list[tuple[list[str], float]]:
    """For each hotspot window, the segments running through it and their top exposure weight.

    One pass over the 21,296 segments for **all** the hotspots rather than one pass each: the CSR
    index runs segment -> cells, so it is walked once and every window it touches is credited on
    the way past. The **maximum** weight is taken, not the mean, because a junction is as exposed
    as its most important road and averaging in the service lanes beside it would hide that.
    """
    if index is None:
        return [([], 0.0) for _ in windows]

    segment_ids, offsets, cells = index
    hit: list[list[str]] = [[] for _ in windows]
    for k, seg in enumerate(segment_ids):
        span = cells[offsets[k] : offsets[k + 1]]
        if not span.size:
            continue
        span_set = set(span.tolist())
        for w, window in enumerate(windows):
            if not window.isdisjoint(span_set):
                hit[w].append(seg)

    import pandas as pd

    frame = pd.read_parquet(
        city_root / "segments.parquet", columns=["segment_id", "exposure_weight"]
    ).set_index("segment_id")["exposure_weight"]
    out: list[tuple[list[str], float]] = []
    for ids in hit:
        weights = frame.reindex(ids).dropna()
        out.append((ids, round(float(weights.max()), 3) if len(weights) else 0.0))
    return out


def _facilities(city_root: Path, lon: float, lat: float) -> dict[str, Any]:
    """The real places within :data:`EXPOSURE_RADIUS_M` of a hotspot, for the row's icon set.

    Read from the assets layer so every icon stands for an OSM feature the city pipeline
    extracted, never a category guess about what is probably near a junction.
    """
    assets_path = city_root / "assets.geojson"
    if not assets_path.is_file():
        return {"facilities": []}

    features = json.loads(assets_path.read_text(encoding="utf-8")).get("features", [])
    near: dict[str, tuple[float, str]] = {}
    # Degrees to metres at Mumbai's latitude: accurate to well under a metre over 300 m, and it
    # avoids projecting 766 assets once per hotspot.
    lon_m = 111_320.0 * float(np.cos(np.deg2rad(lat)))
    for feature in features:
        props = feature.get("properties", {})
        kind = props.get("kind")
        if kind not in FACILITY_KINDS:
            continue
        fx, fy = feature["geometry"]["coordinates"][:2]
        dist = float(np.hypot((fx - lon) * lon_m, (fy - lat) * 110_540.0))
        if dist <= EXPOSURE_RADIUS_M and (kind not in near or dist < near[kind][0]):
            near[kind] = (dist, props.get("name") or kind)

    out: dict[str, Any] = {"facilities": [k for k in FACILITY_KINDS if k in near]}
    if "hospital" in near:
        out["nearest_hospital"] = near["hospital"][1]
        out["nearest_hospital_m"] = round(near["hospital"][0])
    if "station" in near:
        out["nearest_station"] = near["station"][1]
        out["nearest_station_m"] = round(near["station"][0])
    return out


_BLOCKAGE_PHRASE = {
    "posterior": "this cycle's Pulse posterior",
    "prior": "the city's prior",
}
"""How an attribution names the blockage its pipes were cleaned from."""


def _drain_attribution(
    ranked: list[dict[str, Any]],
    windows: list[set[int]],
    depth_m: NDArray[np.floating],
    city_root: Path,
    *,
    max_hotspots: int,
    min_peak_cm: float,
    network: Any = None,
    blockage_source: str = "prior",
    timings: dict[str, int] | None = None,
    sea: NDArray[np.bool_] | None = None,
) -> None:
    """Attach the responsible pipes to the worst junctions, in place.

    Every entry ends up with ``attribution`` and ``attribution_label``: a ranking and ``None``,
    or an empty list and the reason it is empty. There is deliberately no third state - a
    consumer that finds an empty list without a label would have no way to tell "no pipe is
    responsible" from "nobody asked".

    ``network`` is the drain graph to clean pipes on, carrying the blockage the ranking should
    be measured at: the cycle passes this cycle's Pulse posterior, and ``blockage_source`` names
    which one it was so every label can say so. Without one the graph is loaded from the city
    with its prior. A city with no graph, or a Flash service without the hydraulic operator,
    labels every entry and moves on: the rail, the map and the alerts do not depend on this.

    ``sea`` is the city's sea on the depth grid (``sea_mask.tif``). A node on a sea cell is
    frozen dry: the Twin holds its cell at the tide's level and keeps it out of the exchange, so
    the metres of seawater there are not street water its inlet could take.
    """
    from time import perf_counter

    for entry in ranked:
        entry["attribution"] = []
        entry["attribution_label"] = None
        # Which of the four states an entry is in, so a consumer reading the array alone can
        # tell "no pipe explains this" from "nobody looked": ranked, refused (looked, and no
        # pipe cleared the floor), not_attempted (under the wet floor or past the budget), or
        # unavailable (no graph, another city's graph, no solver).
        entry["attribution_status"] = "unavailable"

    try:
        from varuna_flash.whatif import CELL_AREA_M2, attribute_pipes, build_adjacency
        from varuna_twin.city import load_network
    except ImportError as exc:  # pragma: no cover - only when varuna-twin is absent
        for entry in ranked:
            entry["attribution_label"] = f"Attribution needs the drain solver: {exc}"
        return

    from varuna_schemas.paths import city_dir

    # `load_network` takes a city *name* and resolves it under the repo's own `city/` root, so
    # the name is only safe to use when the caller's root is that same directory. A products run
    # pointed at a fixture or an unpacked copy elsewhere would otherwise silently attribute
    # against whatever `city/<name>` happens to hold - a graph whose node indices mean nothing
    # here. Checked rather than assumed, and named when it does not hold.
    city = city_root.name
    if city_dir(city).resolve() != city_root.resolve():
        for entry in ranked:
            entry["attribution_label"] = (
                f"Attribution reads the drain graph from {city_dir(city)}, and this run's city "
                f"layers are at {city_root}. It is skipped rather than run against another "
                f"city's network."
            )
        return

    try:
        if network is None:
            network = load_network(city)
            blockage_source = "prior"
    except (FileNotFoundError, ValueError) as exc:
        # Named rather than swallowed: "no attribution" and "this city has no drain graph" are
        # different facts and only one of them is about the pipes.
        for entry in ranked:
            entry["attribution_label"] = f"No inferred drain graph for {city}: {exc}"
        log.warning("products.attribution_no_graph", city=city, error=str(exc))
        return

    n_steps, n_rows, n_cols = depth_m.shape
    row = np.asarray(network.cell_row, dtype=np.int64)
    col = np.asarray(network.cell_col, dtype=np.int64)
    has_cell = (row >= 0) & (col >= 0) & (row < n_rows) & (col < n_cols)
    if not bool(has_cell.any()):
        for entry in ranked:
            entry["attribution_label"] = (
                "No drain node in this graph is joined to a grid cell, so the street depth "
                "cannot be frozen onto the network."
            )
        return

    # The street depth every node sees, frozen from this run. One gather for the whole city,
    # because every junction reads the same array.
    surface = np.zeros((n_steps, network.n_nodes), dtype=np.float64)
    surface[:, has_cell] = np.asarray(depth_m, dtype=np.float64)[:, row[has_cell], col[has_cell]]
    # Handed to `attribute_pipes` as well as zeroed here: the zero stops a sea node's inlet, the
    # mask also stops its vent, which is the Twin's rule for a node on the sea.
    on_sea: NDArray[np.bool_] | None = None
    if sea is not None:
        # Measured on Mumbai's current graph under the rebuilt sea mask (2026-09-28): 148
        # interior nodes sit on sea cells, and the catchments of 3 of the 28 register junctions
        # (Kurla LBS Marg, Bandra Talao, Mahim) reach one down their outfall spine. Frozen at the
        # sea's depth, each was an inlet taking seawater into the pipes the junction drains to.
        on_sea = np.zeros(network.n_nodes, dtype=bool)
        on_sea[has_cell] = np.asarray(sea, dtype=bool)[row[has_cell], col[has_cell]]
        surface[:, on_sea] = 0.0
    cell_of_node = row * n_cols + col

    node_segment_ids: list[str | None] | None = None
    graph_nodes = city_root / "graph" / "nodes.parquet"
    if graph_nodes.is_file():
        import pandas as pd

        table = pd.read_parquet(graph_nodes, columns=["segment_id"])
        node_segment_ids = table["segment_id"].tolist()

    adjacency = build_adjacency(network)
    started = perf_counter()
    attempted = 0
    for entry, window in zip(ranked, windows, strict=True):
        # Depth before count, deliberately: a junction the map does not even draw as wet must
        # not spend one of the budgeted slots, and it must say *why* it was skipped rather than
        # inheriting the budget's reason from whatever rank it happened to land at.
        if entry["peak_depth_cm"] < min_peak_cm:
            entry["attribution_status"] = "not_attempted"
            entry["attribution_label"] = (
                f"Not attributed: this junction peaks at {entry['peak_depth_cm']:.1f} cm, under "
                f"the {min_peak_cm:.0f} cm the map draws as wet."
            )
            continue
        if attempted >= max_hotspots:
            entry["attribution_status"] = "not_attempted"
            entry["attribution_label"] = (
                f"Not attributed: only the worst {max_hotspots} junctions are, because each one "
                f"is a drain1d run per candidate pipe."
            )
            continue

        targets = np.flatnonzero(has_cell & np.isin(cell_of_node, list(window)))
        attempted += 1
        result = attribute_pipes(
            network,
            surface,
            target_nodes=targets,
            peak_step=int(entry["time_to_peak_min"]) // 5,
            adjacency=adjacency,
            target_label=entry.get("name") or entry.get("hotspot_id") or "",
            depth_before_cm=float(entry["peak_depth_cm"]),
            cell_area_m2=CELL_AREA_M2,
            node_segment_ids=node_segment_ids,
            node_on_sea=on_sea,
        )
        entry["attribution"] = [dict(r) for r in result.rows]
        entry["attribution_status"] = "ranked" if result.rows else "refused"
        entry["attribution_label"] = result.reason
        entry["attribution_method"] = (
            f"{result.method}, blockage at {_BLOCKAGE_PHRASE.get(blockage_source, blockage_source)}"
        )
        entry["attribution_blockage"] = blockage_source
        entry["attribution_candidates"] = result.n_candidates
        if result.combined is not None:
            entry["attribution_combined"] = result.combined

    elapsed_ms = round((perf_counter() - started) * 1000.0)
    if timings is not None:
        timings["attribution"] = elapsed_ms
    log.info(
        "products.attribution",
        attempted=attempted,
        named=sum(1 for e in ranked if e["attribution"]),
        blockage=blockage_source,
        ms=elapsed_ms,
    )


def attach_ensemble_band(ranked: list[dict[str, Any]], frame: Any) -> None:
    """Give every hotspot a p10-p90 band from the street ensemble around it, in place.

    A hotspot's series (``depth_cm``) is the 90th percentile of the Twin's depth over its window:
    one deterministic run. The ensemble lives per road segment in the segment forecast, where each
    street's p10 and p90 are its Twin level plus the members' spread (ADR-0025). The junction takes
    the same construction: its own Twin level, plus the mean of its registered segments' spread
    below and above their median, step by step. So the level on screen is still the Twin's and the
    band is the ensemble's, exactly as on the streets.

    A hotspot with none of its segments in the forecast, or a forecast without quantiles, gets no
    band and says so with ``band_segments = 0``; the drawer then draws its series flat and names
    why, rather than inventing a spread.
    """
    columns = {"segment_id", "depth_p10_cm", "depth_p50_cm", "depth_p90_cm"}
    if frame is None or not columns.issubset(getattr(frame, "columns", ())):
        for entry in ranked:
            entry["band_segments"] = 0
        return

    wanted = {sid for entry in ranked for sid in entry.get("segment_ids") or []}
    rows = frame.loc[frame["segment_id"].isin(wanted)].sort_values(["segment_id", "valid_ts"])
    spread: dict[str, tuple[NDArray[np.floating], NDArray[np.floating]]] = {}
    for segment_id, group in rows.groupby("segment_id", sort=False):
        p50 = group["depth_p50_cm"].to_numpy(dtype=np.float64)
        spread[str(segment_id)] = (
            group["depth_p10_cm"].to_numpy(dtype=np.float64) - p50,
            group["depth_p90_cm"].to_numpy(dtype=np.float64) - p50,
        )

    for entry in ranked:
        level = np.asarray(entry.get("depth_cm") or [], dtype=np.float64)
        ids = [
            sid
            for sid in entry.get("segment_ids") or []
            if sid in spread and spread[sid][0].shape == level.shape
        ]
        entry["band_segments"] = len(ids)
        if not ids or level.size == 0:
            continue
        below = np.mean([spread[sid][0] for sid in ids], axis=0)
        above = np.mean([spread[sid][1] for sid in ids], axis=0)
        entry["depth_p10_cm"] = [round(float(v), 1) for v in np.maximum(level + below, 0.0)]
        entry["depth_p90_cm"] = [round(float(v), 1) for v in np.maximum(level + above, level)]


def rank_hotspots(
    depth_m: NDArray[np.floating],
    times: tuple[datetime, ...],
    city_root: Path,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    run_id: str,
    index: tuple[tuple[str, ...], NDArray[np.int64], NDArray[np.int64]] | None = None,
    *,
    attribution: bool = True,
    max_attributed: int = ATTRIBUTION_MAX_HOTSPOTS,
    min_attributed_peak_cm: float = ATTRIBUTION_MIN_PEAK_CM,
    network: Any = None,
    blockage_source: str = "prior",
    timings: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Rank the register's hotspots by the peak depth this run gives them.

    Every entry carries the register's own ``source_url``, because a hotspot on screen is a claim
    that this junction floods and SPEC.md rule 7 requires that claim to be traceable.

    ``network`` and ``blockage_source`` choose the drain graph attribution cleans pipes on and
    say which blockage it carries; ``timings`` receives the attribution's wall clock as
    ``"attribution"`` so the cycle can report its share of the products stage.
    """
    register = city_root / "hotspots.geojson"
    if not register.is_file():
        log.warning("products.no_hotspot_register", path=str(register))
        return []

    from pyproj import Transformer

    from varuna_products.depth import city_intertidal_mask, city_sea_mask

    features = json.loads(register.read_text(encoding="utf-8")).get("features", [])
    res, _, left, _, _, top = transform
    n_steps, n_rows, n_cols = depth_m.shape
    radius_cells = max(round(HOTSPOT_RADIUS_M / res), 1)
    # The sea is not part of a junction. The Twin holds it at the tide's level, so a window that
    # reached a sea cell would read seawater as the junction's depth; none of the register's
    # chronic spots is on the shore today, but a window is a square and the shore is not.
    sea = city_sea_mask(city_root, (n_rows, n_cols))
    # Nor is the intertidal land behind the coast wall (`intertidal_mask.tif`): the Twin lets the
    # tide onto the mangroves, and that is not water on the junction's streets. Left out of the
    # window - its series and its cell set - exactly as the sea is. Attribution still freezes
    # the Twin's depth on an intertidal node (`sea=` below is the sea alone), because to the
    # Twin's drains that cell is land and its inlet takes water.
    intertidal = city_intertidal_mask(city_root, (n_rows, n_cols))
    off_street = sea
    if intertidal is not None and intertidal.any():
        off_street = intertidal if sea is None else sea | intertidal

    to_metric = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    ranked: list[dict[str, Any]] = []
    windows: list[set[int]] = []

    for feature in features:
        props = feature.get("properties", {})
        lon, lat = feature["geometry"]["coordinates"][:2]
        x, y = to_metric.transform(lon, lat)
        col = int((x - left) // res)
        row = int((top - y) // res)
        if not (0 <= row < n_rows and 0 <= col < n_cols):
            # Outside the AOI. Registered hotspots north of the grid are real places; they are
            # simply not in this model's domain, and saying nothing about them is correct.
            continue

        r0, r1 = max(row - radius_cells, 0), min(row + radius_cells + 1, n_rows)
        c0, c1 = max(col - radius_cells, 0), min(col + radius_cells + 1, n_cols)
        window = depth_m[:, r0:r1, c0:c1].reshape(n_steps, -1)
        land = None if off_street is None else ~off_street[r0:r1, c0:c1].ravel()
        if land is not None and land.any():
            window = window[:, land]
        elif land is not None:
            # A register point whose whole window is sea or intertidal has no street to read; it
            # reads dry rather than borrowing the tide's depth.
            window = np.zeros((n_steps, 1), dtype=window.dtype)
        series_cm = np.percentile(window, 90.0, axis=1) * 100.0

        peak_index = int(np.argmax(series_cm))
        peak_cm = float(series_cm[peak_index])
        over = np.flatnonzero(series_cm > IMPASSABLE_CM)
        windows.append(
            {
                r * n_cols + c
                for r in range(r0, r1)
                for c in range(c0, c1)
                if off_street is None or not off_street[r, c]
            }
        )

        ranked.append(
            {
                "_register_index": len(ranked),
                "hotspot_id": props.get("hotspot_id"),
                "name": props.get("name"),
                "slug": props.get("slug"),
                "lon": float(lon),
                "lat": float(lat),
                "ward": props.get("ward"),
                "is_sink": bool(props.get("is_sink", False)),
                "source_url": props.get("source_url"),
                "sourced": bool(props.get("sourced", False)),
                "run_id": run_id,
                "peak_depth_cm": round(peak_cm, 1),
                "peak_ts": times[peak_index].isoformat() if times else None,
                "time_to_peak_min": peak_index * 5,
                "depth_cm": [round(float(v), 1) for v in series_cm],
                # 0 or 1 on a deterministic run; continuous once Flash brings the ensemble.
                "p_impassable_at_peak": float(peak_cm > IMPASSABLE_CM),
                "impassable_from_ts": times[int(over[0])].isoformat() if over.size else None,
                "minutes_impassable": int(over.size) * 5,
            }
        )

    # Exposure, for the rail's icon set and the spec's ranking score.
    for entry, (segment_ids, weight), facilities in zip(
        ranked,
        _segments_near(city_root, windows, index),
        (_facilities(city_root, h["lon"], h["lat"]) for h in ranked),
        strict=True,
    ):
        entry["segment_ids"] = segment_ids
        # Carried on the entry rather than in a parallel list, because the ranking below
        # re-orders the entries and a junction attributed against another junction's window
        # would be a silent defect. Popped before the entry is written out.
        entry["_window"] = windows[entry["_register_index"]]
        entry["exposure"] = {"weight": weight, **facilities}
        # SPEC.md 11.8's score, reported even though the ordering below does not use it: on a
        # deterministic run its probability factor is 0 or 1, so it sorts into two tiers rather
        # than a ranking. It becomes the right key once Flash brings a real ensemble (Phase 7).
        entry["expected_impact"] = round(entry["p_impassable_at_peak"] * weight, 3)

    ranked.sort(key=lambda h: h["peak_depth_cm"], reverse=True)
    for position, entry in enumerate(ranked, start=1):
        entry["rank"] = position
        entry.pop("_register_index", None)

    windows_ranked = [entry.pop("_window") for entry in ranked]
    if attribution:
        _drain_attribution(
            ranked,
            windows_ranked,
            depth_m,
            city_root,
            max_hotspots=max_attributed,
            min_peak_cm=min_attributed_peak_cm,
            network=network,
            blockage_source=blockage_source,
            timings=timings,
            sea=sea,
        )
    else:
        # Off is a state the product records, not a silence: a reader who finds no ranking
        # should be able to tell "nobody asked" from "no pipe is responsible" (SPEC.md 6.8).
        for entry in ranked:
            entry["attribution"] = []
            entry["attribution_status"] = "off"
            entry["attribution_label"] = "Attribution was not run for this product."

    log.info(
        "products.hotspots_ranked",
        n=len(ranked),
        wet=sum(1 for h in ranked if h["peak_depth_cm"] > 5.0),
        impassable=sum(1 for h in ranked if h["peak_depth_cm"] > IMPASSABLE_CM),
        worst=ranked[0]["name"] if ranked else None,
        worst_cm=ranked[0]["peak_depth_cm"] if ranked else None,
    )
    return ranked
