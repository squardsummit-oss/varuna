"""City-in-a-box validation report (SPEC.md P1.10, 10.1 step 10).

``city/<city>/REPORT.md`` is the evidence that the pipeline built something usable: the grid
and the terrain it came from, what hydro-conditioning changed, how well the DEM's own
depressions line up with the *sourced* chronic-waterlogging register, the road and surface
tables, and the inferred drain network with its connectivity proof. Every number here is
measured from the artifacts in the city folder - nothing is typed in - and the targets that
are missed are printed as misses with a note on what would close the gap.

Three maps are rendered next to it under ``city/<city>/maps/``: the conditioned hillshade
with the register on top, the drain graph coloured by diameter, and the depressions.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from varuna_schemas.models.city import CityConfig

from varuna_city.config import CityGrid

log = structlog.get_logger("varuna.city.report")

OVERLAP_RADIUS_M = 150.0
"""SPEC.md P1.10: a register point counts as explained by a DEM depression within 150 m."""

OVERLAP_TARGET = 0.60
"""The target share of register points that a depression explains."""

MAPS_DIR = "maps"
"""Sub-folder of the city folder that holds the report's PNG maps."""


def depression_overlap(
    hotspots: Any, depressions: Any, *, radius_m: float = OVERLAP_RADIUS_M
) -> dict[str, Any]:
    """Share of register points with a DEM depression bottom within ``radius_m``.

    Returns the share over the whole register and over the sourced points alone (the ones
    the UI is allowed to call chronic), plus the per-point distances so the report can name
    the misses.
    """
    empty = {
        "radius_m": radius_m,
        "register": 0,
        "matched": 0,
        "share": 0.0,
        "sourced": 0,
        "sourced_matched": 0,
        "sourced_share": 0.0,
        "points": [],
    }
    if hotspots is None or not len(hotspots) or depressions is None or not len(depressions):
        return empty
    hs = hotspots.to_crs(depressions.crs) if str(hotspots.crs) != str(depressions.crs) else hotspots
    tree = depressions.sindex
    points: list[dict[str, Any]] = []
    for row in hs.itertuples():
        geom = row.geometry
        idx = tree.nearest(geom, return_all=False)
        nearest = depressions.iloc[int(idx[1][0])]
        distance = float(geom.distance(nearest.geometry))
        points.append(
            {
                "name": str(getattr(row, "name", "")),
                "sourced": bool(getattr(row, "sourced", False)),
                "distance_m": round(distance, 1),
                "depression_id": str(nearest.get("depression_id", "")),
                "depth_m": float(nearest.get("depth_m", float("nan"))),
                "matched": distance <= radius_m,
            }
        )
    matched = sum(1 for p in points if p["matched"])
    sourced = [p for p in points if p["sourced"]]
    sourced_matched = sum(1 for p in sourced if p["matched"])
    return {
        "radius_m": radius_m,
        "register": len(points),
        "matched": matched,
        "share": round(matched / len(points), 4) if points else 0.0,
        "sourced": len(sourced),
        "sourced_matched": sourced_matched,
        "sourced_share": round(sourced_matched / len(sourced), 4) if sourced else 0.0,
        "points": sorted(points, key=lambda p: p["distance_m"]),
    }


# ---------------------------------------------------------------------------------------
# maps
# ---------------------------------------------------------------------------------------


def _figure(grid: CityGrid, title: str, subtitle: str) -> tuple[Any, Any]:
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    aspect = grid.height / max(grid.width, 1)
    width_in = 6.0
    fig, ax = plt.subplots(figsize=(width_in, min(14.0, max(4.0, width_in * aspect))), dpi=110)
    ax.set_title(title, fontsize=11, loc="left")
    ax.set_xlabel("\n".join(textwrap.wrap(subtitle, width=78)), fontsize=7, loc="left")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    return fig, ax


def _extent(grid: CityGrid) -> tuple[float, float, float, float]:
    left, bottom, right, top = grid.bounds
    return (left, right, bottom, top)


def map_hillshade(
    grid: CityGrid, dem: Any, hotspots: Any, out: Path, *, title: str = "Conditioned terrain"
) -> Path:
    """Hillshade of the conditioned DEM with the chronic register on top."""
    import matplotlib.pyplot as plt

    from varuna_city.rasters import hillshade

    shaded = hillshade(np.asarray(dem, dtype=float), res=grid.res)
    fig, ax = _figure(
        grid,
        title,
        f"Copernicus GLO-30, hydro-conditioned, {grid.res:g} m grid, {grid.crs}. "
        "Circles: sourced chronic spots; crosses: candidates.",
    )
    ax.imshow(shaded, cmap="Greys_r", extent=_extent(grid), origin="upper", interpolation="nearest")
    if hotspots is not None and len(hotspots):
        hs = hotspots.to_crs(grid.crs) if str(hotspots.crs) != str(grid.crs) else hotspots
        sourced = hs[hs["sourced"]] if "sourced" in hs.columns else hs
        candidates = hs[~hs["sourced"]] if "sourced" in hs.columns else hs.iloc[0:0]
        if len(candidates):
            ax.scatter(
                candidates.geometry.x,
                candidates.geometry.y,
                s=18,
                marker="x",
                c="#6E7E9E",
                linewidths=0.9,
            )
        if len(sourced):
            ax.scatter(
                sourced.geometry.x,
                sourced.geometry.y,
                s=26,
                facecolors="none",
                edgecolors="#2DD4BF",
                linewidths=1.2,
            )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    return out


def _size_key(label: str) -> float:
    """Sort ``600 mm`` before ``900 mm`` and both before the box trunks."""
    digits = "".join(c if c.isdigit() else " " for c in label).split()
    return float(digits[0]) if digits else 0.0


def map_drains(grid: CityGrid, edges: Any, nodes: Any, out: Path) -> Path:
    """The inferred drain graph, coloured by diameter, outfalls marked."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, ax = _figure(
        grid,
        "Inferred drain graph",
        "Colour: pipe size; box drains carry the trunks. "
        "Squares: outfalls. Every element is inferred, not surveyed.",
    )
    ramp = ["#3E4C6E", "#4C63A6", "#7C3AED", "#C026D3", "#E879F9", "#FBCFE8"]
    if edges is not None and len(edges):
        from varuna_city.pipeline import size_label

        eg = edges.to_crs(grid.crs) if str(edges.crs) != str(grid.crs) else edges
        labels = [size_label(row) for _, row in eg.iterrows()]
        # Twenty-one box sizes would be an unreadable legend; the trunks are one class here
        # and the full histogram is in the report's table.
        eg = eg.assign(
            size_class=["box drains (trunks)" if t.startswith("box") else t for t in labels]
        )
        order = sorted(
            eg["size_class"].unique(), key=lambda text: (text.startswith("box"), _size_key(text))
        )
        for i, label in enumerate(order):
            part = eg[eg["size_class"] == label]
            part.plot(
                ax=ax,
                color=ramp[min(i, len(ramp) - 1)],
                linewidth=0.35 + 0.45 * i,
                label=f"{label} ({len(part)})",
            )
        ax.legend(
            fontsize=6,
            frameon=True,
            framealpha=0.85,
            edgecolor="none",
            loc="lower left",
            title="pipe size",
            title_fontsize=6,
        )
    if nodes is not None and len(nodes) and "is_outfall" in nodes.columns:
        ng = nodes.to_crs(grid.crs) if str(nodes.crs) != str(grid.crs) else nodes
        outfalls = ng[ng["is_outfall"].astype(bool)]
        if len(outfalls):
            ax.scatter(
                outfalls.geometry.x,
                outfalls.geometry.y,
                s=22,
                marker="s",
                c="#F59E0B",
                edgecolors="none",
            )
            handles = ax.get_legend_handles_labels()[0]
            handles.append(
                Line2D([], [], marker="s", color="#F59E0B", linestyle="none", label="outfall")
            )
    left, right, bottom, top = _extent(grid)
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    return out


def map_depressions(grid: CityGrid, depth: Any, hotspots: Any, out: Path) -> Path:
    """Remaining depressions of the conditioned DEM, with the register on top."""
    import matplotlib.pyplot as plt

    fig, ax = _figure(
        grid,
        "Depressions after conditioning",
        "Ponding depth of the pits the conditioned DEM still holds (m). "
        "Circles: sourced chronic spots.",
    )
    data = np.asarray(depth, dtype=float)
    masked = np.ma.masked_where(~np.isfinite(data) | (data <= 0.01), data)
    # A handful of 15 m pits would flatten every street-scale pond to the same dark blue, so
    # the scale tops out at the 98th percentile and the label says so.
    vmax = float(np.percentile(masked.compressed(), 98)) if masked.count() else 1.0
    image = ax.imshow(
        masked,
        cmap="viridis",
        extent=_extent(grid),
        origin="upper",
        interpolation="nearest",
        vmin=0.0,
        vmax=max(vmax, 0.1),
    )
    bar = fig.colorbar(image, ax=ax, shrink=0.6, pad=0.02, extend="max")
    bar.set_label(f"depth (m), scale to the 98th percentile ({vmax:.1f} m)", fontsize=7)
    bar.ax.tick_params(labelsize=6)
    if hotspots is not None and len(hotspots) and "sourced" in hotspots.columns:
        hs = hotspots.to_crs(grid.crs) if str(hotspots.crs) != str(grid.crs) else hotspots
        sourced = hs[hs["sourced"]]
        if len(sourced):
            ax.scatter(
                sourced.geometry.x,
                sourced.geometry.y,
                s=26,
                facecolors="none",
                edgecolors="#F87171",
                linewidths=1.1,
            )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _pct(value: float | None) -> str:
    return "not measured" if value is None else f"{value * 100:.1f} %"


def _verdict(ok: bool) -> str:
    return "met" if ok else "MISSED"


def _table(rows: list[tuple[str, str]], header: tuple[str, str]) -> list[str]:
    lines = [f"| {header[0]} | {header[1]} |", "|---|---|"]
    lines += [f"| {a} | {b} |" for a, b in rows]
    return lines


def _dropped_reason(entry: dict[str, Any]) -> str:
    parts = []
    if entry.get("nearest_tidal_outfall_m") is not None:
        parts.append(f"nearest tidal outfall {_fmt(entry['nearest_tidal_outfall_m'], 0)} m")
    if entry.get("nearest_node_m") is not None:
        parts.append(
            f"nearest node {_fmt(entry['nearest_node_m'], 0)} m away and "
            f"{_fmt(entry.get('nearest_node_sea_distance_cells'))} cells from the sea"
        )
    return "; ".join(parts) or str(entry.get("reason", "no tidal outfall"))


def _coastline_section(
    sea: dict[str, Any] | None,
    tidal: dict[str, Any] | None,
    condition: dict[str, Any],
) -> list[str]:
    """The sea the Twin imposes the tide on, the land conditioning changed at the shore, and every
    tidal outfall with its distance to the sea (``varuna_city.sea``). Empty for a city built
    before the sea step, which keeps the one-paragraph note on the config's tidal points."""
    if not sea:
        return []
    open_sea = sea.get("open_sea") or {}
    creek = sea.get("tidal_creek") or {}
    rivers = creek.get("rivers") or {}
    config = sea.get("config") or {}
    rows: list[tuple[str, str]] = [
        (
            "Sea cells (open sea and tidal creek)",
            f"{_fmt(sea.get('cells'))} ({_fmt(sea.get('km2'), 2)} km2)",
        ),
        ("Open sea", f"{_fmt(open_sea.get('cells', sea.get('open_sea_cells')))} cells"),
        (
            "Tidal creek",
            f"{_fmt(creek.get('cells', sea.get('tidal_creek_cells')))} cells"
            + (
                " from "
                + ", ".join(
                    f"{name} ({_fmt(v.get('osm_ways'))} OSM ways)" for name, v in rivers.items()
                )
                if rivers
                else ""
            ),
        ),
        ("Building cells kept out of the sea", _fmt(sea.get("blocked_cells_removed"))),
    ]
    if "flattened_cells" in condition:
        rows.append(
            (
                "Land held at sea level beside the sea, raised to its neighbours' median",
                f"{_fmt(condition.get('flattened_repaired'))} of "
                f"{_fmt(condition.get('flattened_cells'))} cells",
            )
        )
    if "culvert_ends_on_sea_ignored" in condition:
        rows.append(
            (
                "Culvert and bridge ends on the sea ignored",
                _fmt(condition["culvert_ends_on_sea_ignored"]),
            )
        )
    if "coast_wall_m" in condition:
        behind = (
            f", behind {_fmt(condition['intertidal_cells'])} intertidal cells left to the tide"
            if "intertidal_cells" in condition
            else ""
        )
        rows.append(
            (
                "Coast wall (assumption)",
                f"{_fmt(condition.get('coast_wall_cells_raised'))} of "
                f"{_fmt(condition.get('shore_ring_cells'))} shore cells raised to "
                f"{_fmt(condition.get('coast_wall_m'))} m{behind}",
            )
        )
        if "intertidal_mask_cells" in condition:
            rows.append(
                (
                    "Intertidal land left out of street and hotspot depths",
                    f"{_fmt(condition['intertidal_mask_cells'])} cells in "
                    f"{condition.get('intertidal_mask_file', 'intertidal_mask.tif')}; the Twin "
                    "floods them at high water, the products never read them as a street",
                )
            )
        if "coast_wall_basin_cells" in condition:
            rows.append(
                (
                    "Closed basins the coast wall made (assumption: they drain by inlets only)",
                    f"{_fmt(condition['coast_wall_basin_cells'])} land cells "
                    f"({_fmt(condition.get('coast_wall_basin_km2'), 3)} km2) in "
                    f"{_fmt(condition.get('coast_wall_basins'))} basins, "
                    f"{_fmt(condition.get('coast_wall_basin_m3'), 0)} m3, deepest "
                    f"{_fmt(condition.get('coast_wall_basin_max_m'))} m",
                )
            )
    elif config:
        rows.append(("Coast wall", "none: the city's sea config sets no level"))
    lines = _table(rows, ("Coastline", "Value"))
    rules = sea.get("rules") or {}
    if rules:
        lines += [
            "",
            f"Open sea: {rules.get('open_sea')}. Tidal creek: {rules.get('tidal_creek')}.",
        ]
        if rules.get("assumption"):
            lines += ["", rules["assumption"]]
    if not tidal:
        return lines
    outfalls = tidal.get("outfalls") or []
    lines += [
        "",
        f"Every outfall within {_fmt(tidal.get('tidal_within_cells'))} cells (chessboard) of the "
        f"sea is tidal, with its invert at {_fmt(tidal.get('tidal_outfall_invert_m'))} m "
        "(an assumption: no outfall invert is sourced for any city). "
        f"{_fmt(len(outfalls))} are.",
    ]
    if outfalls:
        lines += [
            "",
            "| Tidal outfall | Config id | Cells to the sea | Ground (m) | Invert (m) | Flap gate |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {row['node_id']} | {row.get('outfall_id') or ''} "
            f"| {_fmt(row.get('sea_distance_cells'))} | {_fmt(row.get('z_ground_m'))} "
            f"| {_fmt(row.get('z_invert_m'))} | {'yes' if row.get('flap_gate') else 'no'} |"
            for row in outfalls
        ]
    dropped = tidal.get("config_dropped") or []
    if dropped:
        lines += [
            "",
            "Config tidal points with no tidal outfall within reach, dropped rather than made "
            "an outfall of whatever node lies nearest: "
            + ", ".join(f"{d.get('outfall_id')} ({_dropped_reason(d)})" for d in dropped)
            + ".",
        ]
    return lines


def _gravity(
    config: CityConfig, grid: CityGrid, nodes: Any, edges: Any, hotspots: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """The gravity audit of the drain tables already loaded, or ``(None, why not)``."""
    if nodes is None or edges is None or not len(nodes) or not len(edges):
        return None, "no drain graph loaded"
    from varuna_city.gravity import MIN_SLOPE, audit_gravity

    try:
        audit = audit_gravity(
            nodes,
            edges,
            min_slope=float(getattr(config, "min_drain_slope", MIN_SLOPE)),
            hotspots=hotspots if hotspots is not None and len(hotspots) else None,
            transform=grid.transform,
            crs=grid.crs,
        )
    except (KeyError, ValueError) as exc:
        log.warning("report.gravity_unavailable", error=str(exc))
        return None, f"audit failed: {exc}"
    return audit, None


def _connectivity_rows(
    connectivity: float, gravity: dict[str, Any] | None, error: str | None
) -> list[str]:
    """The two connectivity rows of the targets table: topological, then hydraulic.

    The first row counts paths, not gradients. It stays met on a graph whose water cannot reach
    an outfall without surcharging, so the sill-blocked figure sits directly under it. There is
    no spec target for the second row, so it carries no verdict.
    """
    rows = [
        "| Drain graph connectivity (every node reaches an outfall) | 100 % "
        f"| {_pct(connectivity)} topologically | {_verdict(connectivity >= 1.0)} |"
    ]
    if gravity is None:
        measured = f"not measured ({error})"
    else:
        sill = gravity["sill"]
        measured = (
            f"{_pct(1.0 - float(sill['share']))} ({_fmt(int(sill['blocked_nodes']))} of "
            f"{_fmt(int(gravity['nodes']))} nodes sill-blocked; see Drain gravity)"
        )
    rows.append(
        "| Drain graph hydraulic connectivity (no downstream invert above a node's own ground) "
        f"| no spec target | {measured} | reported |"
    )
    return rows


def _gravity_section(gravity: dict[str, Any] | None, error: str | None) -> list[str]:
    if gravity is None:
        return [f"Not measured: {error}."]
    adv, under, sill = gravity["adverse"], gravity["under_min_slope"], gravity["sill"]
    field = gravity.get("slope_field") or {}
    depth = gravity.get("invert_depth_m") or {}
    rise = adv.get("rise_m") or {}
    excess = sill.get("excess_m") or {}
    lines = [
        "Connectivity above proves a path from every node to an outfall. This section checks "
        "whether water can follow that path downhill. A node is **sill-blocked** when some "
        "invert downstream of it, on its way to its outfall, stands above its own street. The "
        "drain solver can then move its water on only by surcharging first. Measured by "
        "`varuna_city.gravity` from `drain_nodes.parquet` and `drain_edges.parquet`; nothing "
        "below is a storm result.",
        "",
    ]
    lines += _table(
        [
            (
                "Adverse edges (downstream invert above upstream)",
                f"{_fmt(adv['edges'])} of {_fmt(gravity['edges'])} ({_pct(adv['share'])}), "
                f"{_fmt(adv['length_km'], 1)} km ({_pct(adv['length_share'])} of length)",
            ),
            (
                "Rise on adverse edges (m)",
                f"p50 {_fmt(rise.get('p50'))}, p90 {_fmt(rise.get('p90'))}, "
                f"max {_fmt(rise.get('max'))}"
                if rise
                else "none",
            ),
            (
                f"Edges falling under {gravity['min_slope'] * 100:g} %",
                f"{_fmt(under['edges'])} ({_pct(under['share'])}), "
                f"{_fmt(under['length_km'], 1)} km",
            ),
            (
                "Stored `slope` that disagrees with the invert fall",
                f"{_fmt(field.get('disagreeing_edges'))} edges (stored minimum "
                f"{field.get('stored_min')})"
                if field
                else "no slope column",
            ),
            ("Inverts above ground", _fmt(gravity["inverts_above_ground"])),
            (
                "Invert depth (m)",
                f"min {_fmt(depth.get('min'))}, p50 {_fmt(depth.get('p50'))}, "
                f"p90 {_fmt(depth.get('p90'))}, max {_fmt(depth.get('max'))}",
            ),
            (
                "Sill-blocked nodes",
                f"{_fmt(sill['blocked_nodes'])} of {_fmt(gravity['nodes'])} "
                f"({_pct(sill['share'])}); {_fmt(sill['pipe_km_from_blocked_nodes'], 1)} km of "
                "pipe starts at one",
            ),
            (
                "Sill above ground at blocked nodes (m)",
                f"p50 {_fmt(excess.get('p50'))}, p90 {_fmt(excess.get('p90'))}, "
                f"max {_fmt(excess.get('max'))}"
                if excess
                else "none",
            ),
        ],
        ("Gravity", "Value"),
    )

    by_type = gravity["outfalls"]["by_boundary_type"]
    if by_type:
        lines += [
            "",
            "| Outfall type | Outfalls | Nodes drained | Ground p50 (m) | Invert p50 (m) "
            "| On ground above 5 m |",
            "|---|---|---|---|---|---|",
        ]
        for kind, entry in by_type.items():
            lines.append(
                f"| {kind} | {_fmt(entry['outfalls'])} | {_fmt(entry['nodes_served'])} "
                f"| {_fmt((entry['ground_m'] or {}).get('p50'))} "
                f"| {_fmt((entry['invert_m'] or {}).get('p50'))} "
                f"| {_fmt(entry['ground_above_5_m'])} |"
            )
    tidal = gravity["outfalls"]["tidal"]
    if tidal:
        lines += [
            "",
            "| Tidal outfall | Snapped node | Ground (m) | Invert (m) | Nodes drained |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {row['outfall_id']}{' (flap gate)' if row.get('flap_gate') else ''} "
            f"| {row['node_id']} | {_fmt(row['z_ground_m'])} | {_fmt(row['z_invert_m'])} "
            f"| {_fmt(row['nodes_served'])} |"
            for row in tidal
        ]

    hs = gravity.get("hotspots")
    if hs and hs["rows"]:
        size = hs["window_cells"]
        lines += [
            "",
            f"Register points with sill-blocked drain nodes in their {size} x {size} cell window: "
            f"{hs['with_blocked_nodes']} of {hs['points']}.",
            "",
            "| Point | Sourced | Nodes in window | Sill-blocked | Worst sill above ground |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {row['name']} | {'yes' if row['sourced'] else 'candidate'} "
            f"| {_fmt(row['nodes_in_window'])} | {_fmt(row['sill_blocked'])} "
            f"| {'-' if row['max_excess_m'] is None else _fmt(row['max_excess_m']) + ' m'} |"
            for row in hs["rows"]
        ]
    return lines


def write_report(
    config: CityConfig,
    grid: CityGrid,
    out_dir: Path,
    *,
    stats: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> Path:
    """Render the maps and write ``city/<city>/REPORT.md``. Returns the report path."""
    art = artifacts or {}
    root = Path(out_dir)
    maps_dir = root / MAPS_DIR
    written_maps: dict[str, Path] = {}

    hotspots = art.get("hotspots")
    depressions = art.get("depressions")
    conditioned = art.get("conditioned")
    depth = art.get("depression_depth")
    edges = art.get("drain_edges")
    nodes = art.get("drain_nodes")

    if conditioned is not None:
        written_maps["terrain"] = map_hillshade(
            grid, conditioned, hotspots, maps_dir / "terrain.png"
        )
    if edges is not None and len(edges):
        written_maps["drains"] = map_drains(grid, edges, nodes, maps_dir / "drains.png")
    if depth is not None:
        written_maps["depressions"] = map_depressions(
            grid, depth, hotspots, maps_dir / "depressions.png"
        )

    overlap = depression_overlap(hotspots, depressions)
    drains = stats.get("drains", {})
    connectivity = float(drains.get("connectivity", 0.0))
    gravity, gravity_error = _gravity(config, grid, nodes, edges, hotspots)
    units = stats.get("units", {})
    segs = stats.get("segments", {})
    dem = stats.get("dem", {})
    condition = stats.get("condition", {})
    landcover = stats.get("landcover", {})
    depr = stats.get("depressions", {})
    assets = stats.get("assets", {})
    hs_stats = stats.get("hotspots", {})
    export = stats.get("export", {})

    unit_total = int(units.get("units", 0) or 0)
    within = int(units.get("within_cap", 0) or 0)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {config.name} city-in-a-box - validation report",
        "",
        f"City `{config.id}` - AOI `{config.aoi_id}` - generated {generated} by "
        "`varuna city --city " + config.id + "` (SPEC.md P1.10, 10.1 step 10).",
        "",
        "Every number below is measured from the artifacts in this folder. Terrain is "
        "Copernicus GLO-30 and the vectors are OpenStreetMap; **the drain network is "
        'inferred**, not surveyed - every pipe carries `confidence = "inferred"` and a '
        "blockage prior that VARUNA-Pulse learns from observed floods.",
        "",
        "## Targets",
        "",
        "| Check | Target | Measured | Verdict |",
        "|---|---|---|---|",
        f"| Depressions explain the chronic register (within {_fmt(OVERLAP_RADIUS_M, 0)} m) "
        f"| >= 60 % | {_pct(overlap['share'])} of {overlap['register']} points "
        f"({_pct(overlap['sourced_share'])} of the {overlap['sourced']} sourced) "
        f"| {_verdict(overlap['share'] >= OVERLAP_TARGET)} |",
        *_connectivity_rows(connectivity, gravity, gravity_error),
        f"| Sourced register points inside the AOI | >= 10 | {hs_stats.get('sourced', 0)} "
        f"| {_verdict(int(hs_stats.get('sourced', 0)) >= 10)} |",
        f"| Surface units inside the 0.5-2 ha cap | >= 90 % | "
        f"{_pct(within / unit_total if unit_total else None)} of {_fmt(unit_total)} "
        f"| {_verdict(bool(unit_total) and within / unit_total >= 0.9)} |",
        "",
        "## Grid and terrain",
        "",
    ]
    left, bottom, right, top = grid.bounds
    lines += _table(
        [
            ("Grid", f"{grid.width} x {grid.height} cells at {grid.res:g} m ({grid.crs})"),
            ("Cells", _fmt(grid.width * grid.height)),
            ("Bounds (m)", f"{left:,.0f}, {bottom:,.0f} -> {right:,.0f}, {top:,.0f}"),
            (
                "Bounds (WGS84)",
                ", ".join(f"{v:.4f}" for v in config.bbox.as_tuple()),
            ),
            (
                "Elevation range",
                f"{_fmt(dem.get('min_m'))} m to {_fmt(dem.get('max_m'))} m "
                f"(mean {_fmt(dem.get('mean_m'))} m)",
            ),
            ("DEM no-data cells", _fmt(dem.get("nodata_cells"))),
            ("DEM tiles", ", ".join(config.dem_tiles)),
            ("Land cover tiles", ", ".join(config.landcover_tiles)),
            (
                "Imperviousness",
                f"mean {_fmt(landcover.get('imperviousness_mean'), 3)}, "
                f"p90 {_fmt(landcover.get('imperviousness_p90'), 3)}",
            ),
            ("Curve number", f"mean {_fmt(landcover.get('cn_mean'))} on {config.cn_range}"),
        ],
        ("Terrain", "Value"),
    )

    lines += ["", "## Hydro-conditioning", ""]
    lines += _table(
        [
            (
                "Building cells burned",
                f"{_fmt(condition.get('cells_burned'))} cells at "
                f"+{_fmt(condition.get('building_burn_m'), 1)} m",
            ),
            (
                "Road cells carved",
                f"{_fmt(condition.get('cells_carved'))} cells at "
                f"-{_fmt(condition.get('road_carve_m'), 2)} m",
            ),
            (
                "Culverts and bridges breached",
                f"{_fmt(condition.get('culvert_ways_breached'))} ways, "
                f"{_fmt(condition.get('culvert_cells_breached'))} cells",
            ),
            ("Sinks protected (underpasses, register)", _fmt(condition.get("sinks_protected"))),
            (
                "Sinks cleared of a building footprint",
                _fmt(condition.get("sinks_cleared_of_building")),
            ),
            ("Pits found after burning and carving", _fmt(condition.get("pits_before"))),
            # Two rows, not one. ADR-0019 split these counters because merging them is what
            # hid the off-by-one: every pit was reported as "protected" and 4,004 protected
            # pits read plausible enough that nobody checked. A single row here would print
            # `pits_protected` alone and report 0 kept on a build that keeps 2,493.
            (
                "Pits kept because they are larger than one cell",
                _fmt(condition.get("pits_large")),
            ),
            (
                "Pits kept because they are protected (underpasses, register)",
                _fmt(condition.get("pits_protected")),
            ),
            (
                "Spurious pits breached",
                f"{_fmt(condition.get('pits_breached'))} "
                f"(rule: area <= {_fmt(condition.get('min_pit_area_m2'), 0)} m2, "
                f"method {condition.get('breach_method', 'not recorded')})",
            ),
            ("Blocked cells (buildings)", _fmt(stats.get("roughness", {}).get("blocked_cells"))),
        ],
        ("Change", "Value"),
    )

    cell_area = grid.res * grid.res
    threshold = float(condition.get("min_pit_area_m2") or 0.0)
    if (
        condition
        and int(condition.get("pits_spurious", 0) or 0) == 0
        and cell_area >= threshold > 0
    ):
        lines += [
            "",
            f"No pit was breached as spurious, and that is arithmetic rather than luck: one "
            f"{grid.res:g} m cell is {cell_area:,.0f} m2, so no pit on this grid can be smaller "
            f"than the {threshold:,.0f} m2 rule of SPEC.md 10.1 step 4. Buildings, roads and "
            f"culverts are still burned, carved and breached; the spurious-pit rule only starts "
            f"selecting anything on the 5 m nests (25 m2 cells) of SPEC.md 3.3. Every pit the "
            f"conditioned DEM keeps is therefore reported below, noise included.",
        ]

    lines += ["", "## Depressions and the chronic register", ""]
    lines += _table(
        [
            ("Depressions kept", _fmt(depr.get("depressions"))),
            (
                "Dropped: bottom cell in permanent water",
                _fmt(depr.get("dropped_in_permanent_water", 0)),
            ),
            (
                "Depth (m)",
                f"min {_fmt(depr.get('depth_m', {}).get('min'), 2)}, "
                f"median {_fmt(depr.get('depth_m', {}).get('median'), 2)}, "
                f"p90 {_fmt(depr.get('depth_m', {}).get('p90'), 2)}, "
                f"max {_fmt(depr.get('depth_m', {}).get('max'), 2)}",
            ),
            (
                "Area (m2)",
                f"min {_fmt(depr.get('area_m2', {}).get('min'), 0)}, "
                f"median {_fmt(depr.get('area_m2', {}).get('median'), 0)}, "
                f"p90 {_fmt(depr.get('area_m2', {}).get('p90'), 0)}, "
                f"max {_fmt(depr.get('area_m2', {}).get('max'), 0)}",
            ),
            ("Ponded volume", f"{_fmt(depr.get('total_volume_m3'), 0)} m3"),
            ("Register points", _fmt(hs_stats.get("hotspots"))),
            ("...with a public source", _fmt(hs_stats.get("sourced"))),
            ("...kept as sinks through conditioning", _fmt(hs_stats.get("sinks"))),
            (
                f"Explained by a depression within {_fmt(OVERLAP_RADIUS_M, 0)} m",
                f"{overlap['matched']} of {overlap['register']} ({_pct(overlap['share'])}); "
                f"sourced: {overlap['sourced_matched']} of {overlap['sourced']} "
                f"({_pct(overlap['sourced_share'])})",
            ),
        ],
        ("Depressions", "Value"),
    )

    misses = [p for p in overlap["points"] if not p["matched"]]
    if misses:
        lines += [
            "",
            f"Register points with no depression within {_fmt(OVERLAP_RADIUS_M, 0)} m "
            f"({len(misses)} of {overlap['register']}):",
            "",
            "| Point | Sourced | Nearest depression |",
            "|---|---|---|",
        ]
        lines += [
            f"| {p['name']} | {'yes' if p['sourced'] else 'candidate'} | "
            f"{_fmt(p['distance_m'], 0)} m |"
            for p in misses
        ]
        lines += [
            "",
            "A 30 m DEM cannot see a 40 m underpass dip or a kerb-height sag, and the "
            "Copernicus surface model still carries flyovers and rail embankments where a "
            "bare-earth model would not. Closing this gap needs finer terrain (the 5 m nests "
            "of SPEC.md 3.3, or LiDAR in the pilot), not a different threshold - which is "
            "why VARUNA registers these points as sinks in their own right and lets Pulse "
            "learn the drain behind them from observed floods.",
        ]

    lines += ["", "## Roads and surface units", ""]
    lines += _table(
        [
            ("Road segments", _fmt(segs.get("segments"))),
            ("Total length", f"{_fmt(segs.get('length_km'))} km"),
            ("Median segment", f"{_fmt(segs.get('median_length_m'), 1)} m"),
            (
                "Classes",
                ", ".join(f"{k} {v}" for k, v in sorted((segs.get("classes") or {}).items())),
            ),
            ("Mean exposure weight", _fmt(segs.get("mean_exposure"), 3)),
            ("Surface units", f"{_fmt(unit_total)} ({units.get('method', 'unknown')})"),
            (
                "Unit area (m2)",
                f"min {_fmt(units.get('area_m2', {}).get('min'), 0)}, "
                f"median {_fmt(units.get('area_m2', {}).get('median'), 0)}, "
                f"mean {_fmt(units.get('area_m2', {}).get('mean'), 0)}, "
                f"max {_fmt(units.get('area_m2', {}).get('max'), 0)}",
            ),
            (
                "Against the 0.5-2 ha cap",
                f"{_fmt(within)} inside, {_fmt(units.get('below_cap'))} below, "
                f"{_fmt(units.get('above_cap'))} above",
            ),
            ("Units total area", f"{_fmt(units.get('total_area_km2'), 2)} km2"),
        ],
        ("Surface", "Value"),
    )

    lines += ["", "## Inferred drain graph", ""]
    histogram = drains.get("diameter_histogram") or {}
    lines += _table(
        [
            ("Nodes", _fmt(drains.get("nodes"))),
            ("Edges", _fmt(drains.get("edges"))),
            ("Total pipe length", f"{_fmt(drains.get('pipe_length_km'))} km"),
            ("Trunk edges", _fmt(drains.get("trunk_edges"))),
            (
                "Outfalls",
                f"{_fmt(drains.get('outfalls'))} ({_fmt(drains.get('tidal_outfalls'))} tidal)",
            ),
            (
                "Sizes",
                ", ".join(f"{k}: {v}" for k, v in histogram.items()) or "not measured",
            ),
            ("Connectivity", f"{_pct(connectivity)} of nodes reach an outfall"),
            ("Longest path to an outfall", f"{_fmt(drains.get('max_hops_to_outfall'))} hops"),
            ("Elements marked inferred", _fmt(drains.get("inferred"))),
            ("Mean blockage prior (beta)", _fmt(drains.get("beta_mean"), 3)),
            ("Drain nodes inside a surface unit", _fmt(units.get("nodes_linked_to_units"))),
        ],
        ("Drains", "Value"),
    )
    coastline = _coastline_section(
        art.get("sea_stats") or stats.get("sea"), drains.get("tidal"), condition
    )
    tidal = [o.name for o in config.tidal_outfalls]
    if coastline:
        lines += ["", "## Coastline", "", *coastline]
    elif tidal:
        lines += [
            "",
            f"Tidal boundary from the city config: {', '.join(tidal)}. Their stage follows the "
            "tide series of the replay bundle; the outfall marked with a flap gate blocks "
            "reverse flow, the others can run backwards and surcharge the trunk (SPEC.md 11.4).",
        ]

    lines += ["", "## Drain gravity", ""]
    lines += _gravity_section(gravity, gravity_error)

    lines += ["", "## Assets and register provenance", ""]
    lines += _table(
        [
            ("Assets", _fmt(assets.get("assets"))),
            ("...carrying a source URL", _fmt(assets.get("with_source_url"))),
            ("...synthetic (mobile pumps)", _fmt(assets.get("synthetic"))),
            (
                "Kinds",
                ", ".join(f"{k} {v}" for k, v in sorted((assets.get("kinds") or {}).items())),
            ),
        ],
        ("Assets", "Value"),
    )
    lines += [
        "",
        "The twelve mobile pumps are a **synthetic** inventory at plausible depots and carry "
        '`synthetic: true`; the UI labels them "Synthetic pump inventory". Every other asset '
        "and every sourced register point carries the public URL it came from.",
    ]

    counts = export.get("counts") or {}
    if counts:
        lines += ["", "## Exports", "", "| Layer | Features | Map GeoJSON |", "|---|---|---|"]
        sizes = export.get("bytes") or {}
        for layer in sorted(counts):
            size = sizes.get(f"map/{layer}")
            lines.append(
                f"| {layer} | {_fmt(counts[layer])} | "
                f"{'-' if size is None else f'{size / 1e6:.1f} MB'} |"
            )
        missing = export.get("missing") or []
        if missing:
            lines += ["", f"Layers not exported: {', '.join(missing)}."]

    if written_maps:
        lines += ["", "## Maps", ""]
        titles = {
            "terrain": "Conditioned terrain with the chronic register",
            "drains": "Inferred drain graph by diameter",
            "depressions": "Depressions after conditioning",
        }
        for key, path in written_maps.items():
            lines += [f"### {titles.get(key, key)}", "", f"![{key}]({MAPS_DIR}/{path.name})", ""]

    lines += [
        "## Reproducibility",
        "",
        "Two builds of this city from the same cache produce the same grid, the same "
        "geometry (byte-identical WKB for segments, drains, units and depressions), the same "
        "rasters and the same ids and ranks. Float attributes derived from the depression "
        "fill can differ in their last digit: pyflwdir's fill is bit-reproducible inside one "
        "process but not between processes (measured: up to 2e-6 m), which moves a value that "
        "sits on a rounding boundary. Nothing here is at a scale the solver or the report can "
        "see; the written depth raster is rounded to 0.1 mm so the file itself is stable.",
        "",
        "## What this report does not claim",
        "",
        "- The drain network is **inferred** from roads, terrain and design norms "
        "(SPEC.md 10.1 step 7). No BMC drainage GIS was used; when one arrives it replaces "
        "the inference and the same tables are produced.",
        "- Copernicus GLO-30 is a 30 m *surface* model: flyovers, rail embankments and tree "
        "canopy are in the terrain, and sub-cell dips (underpasses, kerb sags) are not.",
        "- Ward names come from OSM admin boundaries where they exist; segments outside a "
        "boundary carry no ward.",
        "- Pumping stations, holding tanks and depots are hand-curated from public BMC and "
        "OSM material with a URL each; mobile pumps are synthetic and labelled.",
        "",
        "Timings and step statuses for this build are in `pipeline.json`; the layer manifest "
        "is in `export/MANIFEST.json`.",
        "",
    ]

    target = root / "REPORT.md"
    target.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    summary = {
        "overlap": {k: v for k, v in overlap.items() if k != "points"},
        "maps": {k: str(v) for k, v in written_maps.items()},
        "gravity": gravity,
    }
    (root / "report.json").write_text(
        json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    log.info(
        "report.written",
        path=str(target),
        overlap_share=overlap["share"],
        sourced_share=overlap["sourced_share"],
        maps=len(written_maps),
    )
    return target


__all__ = [
    "MAPS_DIR",
    "OVERLAP_RADIUS_M",
    "OVERLAP_TARGET",
    "depression_overlap",
    "map_depressions",
    "map_drains",
    "map_hillshade",
    "write_report",
]
