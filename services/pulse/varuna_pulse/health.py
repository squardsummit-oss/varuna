"""The drain map VARUNA learned, as a product the console can draw (SPEC.md 11.6, task P7.4).

`drain_health.geojson` is the answer to the question a MoES scientist asks first: *where did you
get a drain GIS?* The answer is that we did not have one - the graph is inferred from roads and
terrain (SPEC.md 10.1 step 7), every pipe carries a blockage that starts as a prior from land
use, and Pulse moves that blockage using the streets that stopped moving and the people who
reported water.

So every element of this product carries three things the UI must show together: the posterior
mean, its **spread**, and the fact that the geometry itself is inferred. A magenta pipe on the
drain X-ray is not a measurement; it is a belief with a standard deviation, drawn dashed because
we do not know the pipe is there.

**The desilting CSV** (SPEC.md 7.3) is the product's point of contact with an actual municipal
workflow: id, street, beta, sd, capacity reduction, hotspots explained. A ward engineer with a
jetting crew and a week can act on a ranked list; that is what the whole engine is for.
"""

from __future__ import annotations

import ast
import csv
import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence
    from pathlib import Path

    from numpy.typing import NDArray

    from varuna_pulse.enkf import EnkfResult

log = structlog.get_logger("varuna.pulse.health")

__all__ = [
    "INFERRED_NOTE",
    "LOCALITY_RADIUS_M",
    "MOVED_EPS",
    "NAME_RADIUS_M",
    "PlaceIndex",
    "build_place_index",
    "desilting_csv",
    "drain_health",
    "drain_summary",
    "is_moved",
    "pipe_places",
    "segment_places",
    "write_drain_health",
    "written_features",
]

INFERRED_NOTE = (
    "Drain graph inferred from roads and terrain, not a municipal SWD model. Every pipe's "
    "blockage is a posterior with a spread, learned from traffic anomalies and citizen reports."
)

TOP_N = 25
"""How many pipes the drain-health table shows (SPEC.md 7.3)."""

MOVED_EPS = 1e-4
"""How far a pipe's posterior has to sit from its prior to count as moved this cycle.

The same threshold the product's ``n_updated`` has always used, so the summary, the written set
and the count in the header agree about which pipes Pulse learned something about."""

NAME_RADIUS_M = 200.0
"""How far from a pipe the nearest named street may be and still name it ("off Eastern Freeway").

83 % of the product's pipes run under a road OSM does not name. Measured on the 08:40 cycle of
2 July 2019 over its 6,000 written pipes: 65.5 % lie within 100 m of a named street, 83.4 % within
200 m and 95.9 % within 400 m. Past 200 m a street name stops describing where the pipe is and
starts describing a neighbourhood, which is the locality's job."""

LOCALITY_RADIUS_M = 300.0
"""How far from a pipe the nearest chronic hotspot may be and still place it ("near Wadala")."""


MAX_WRITTEN_EDGES = 6_000
"""How many pipes the written product carries: every pipe Pulse moved, then the worst blockage.

The inferred graph has 49,770 edges and each one is a line with coordinates, so writing all of
them produced a 19 MB GeoJSON per cycle - larger than every other product in the run put
together, to say that 44,000 pipes are near their prior. The drain X-ray draws the ones that
matter and the desilting list ranks the top 25; n_edges reports the true total beside what
was written, so nothing on screen mistakes the cap for the network.

**Every moved pipe is written, whichever way it moved.** The cap used to be the worst 6,000 by
posterior alone, and a pipe an observation *cleared* falls to a blockage near zero - below the
cut, and so does a pipe raised from 0.15 to 0.16 under a 0.35 market prior. At 08:40 on 2 July
2019, 52 of the 201 pipes Pulse moved were missing from the product - 26 of the 173 it raised and
26 of the 28 it lowered - while 13 of that cycle's 21 observations had lowered blockage."""


def capacity_reduction_pct(beta: NDArray[np.floating]) -> NDArray[np.floating]:
    """What blockage costs in flow, not in area.

    Manning's capacity goes as ``A * R_h^(2/3)``, and for a pipe running full both the area and
    the hydraulic radius fall with blockage, so ``Q ~ (1 - beta)^(5/3)``. Reporting ``beta``
    itself as "capacity reduction" would understate it: a pipe half blocked by area has lost
    about 68 % of its flow, not 50 %.
    """
    return (1.0 - (1.0 - np.clip(beta, 0.0, 1.0)) ** (5.0 / 3.0)) * 100.0


def drain_health(
    posterior: EnkfResult,
    edge_ids: tuple[str, ...],
    geometry: list[list[list[float]]],
    *,
    diameter_m: NDArray[np.floating],
    street: list[str | None] | None = None,
    display_name: Sequence[str | None] | None = None,
    locality: Sequence[str | None] | None = None,
    explains: dict[str, list[str]] | None = None,
    observation_counts: NDArray[np.integer] | None = None,
    q_full_m3s: NDArray[np.floating] | None = None,
    observations: Sequence[dict[str, Any]] | None = None,
    last_update: str | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Build ``drain_health.geojson``: every pipe with its posterior, prior and what moved it.

    ``display_name`` and ``locality`` are the words a pipe is shown by (:func:`pipe_places`);
    ``q_full_m3s`` and ``observations`` feed the top-level ``summary`` (:func:`drain_summary`),
    which is computed over every pipe before the written product is capped.
    """
    beta = np.asarray(posterior.beta_mean, dtype=np.float64)
    sd = np.asarray(posterior.beta_sd, dtype=np.float64)
    prior = np.asarray(posterior.prior_mean, dtype=np.float64)
    reduction = capacity_reduction_pct(beta)
    counts = (
        np.zeros(len(edge_ids), dtype=np.int64)
        if observation_counts is None
        else np.asarray(observation_counts)
    )

    def _at(values: Sequence[str | None] | None, index: int) -> str | None:
        return values[index] if values is not None and index < len(values) else None

    features = []
    for index, edge_id in enumerate(edge_ids):
        if index >= len(geometry):
            break
        own_street = street[index] if street and index < len(street) else None
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": geometry[index]},
                "properties": {
                    "edge_id": edge_id,
                    "street": own_street,
                    # The pipe's own street when OSM names it, else "off <nearest named street>"
                    # within NAME_RADIUS_M, else null. Never a ward: `segments.ward` is empty.
                    "display_name": _at(display_name, index) or own_street,
                    "locality": _at(locality, index),
                    "beta_mean": round(float(beta[index]), 4),
                    "beta_sd": round(float(sd[index]), 4),
                    "beta_prior": round(float(prior[index]), 4),
                    "beta_delta": round(float(beta[index] - prior[index]), 4),
                    # Decided on the unrounded values, so the pipes flagged here are exactly the
                    # ones n_updated and the summary count: a rounded beta_delta of 0.0001 cannot
                    # tell a pipe that moved by 1.2e-4 from one that moved by 0.6e-4.
                    "moved": bool(abs(beta[index] - prior[index]) > MOVED_EPS),
                    "capacity_reduction_pct": round(float(reduction[index]), 1),
                    "diameter_m": round(float(diameter_m[index]), 3),
                    "observations": int(counts[index]) if index < counts.size else 0,
                    "explains": (explains or {}).get(edge_id, []),
                    # Every element, always. The UI draws them dashed because of this field.
                    "confidence": "inferred",
                    "last_update": last_update,
                },
            }
        )

    moved = np.flatnonzero(np.abs(beta - prior) > MOVED_EPS)
    summary = drain_summary(
        beta,
        prior,
        edge_ids,
        q_full_m3s=q_full_m3s,
        display_name=[f["properties"]["display_name"] for f in features],
        locality=locality,
        observations=observations,
    )
    log.info(
        "pulse.drain_health",
        run_id=run_id,
        edges=len(features),
        updated=int(moved.size),
        mean_beta=round(float(beta.mean()), 4),
        max_beta=round(float(beta.max()) if beta.size else 0.0, 4),
        operator=posterior.operator,
    )
    return {
        "type": "FeatureCollection",
        "run_id": run_id,
        "operator": posterior.operator,
        "note": INFERRED_NOTE,
        "n_edges": len(features),
        "n_updated": int(moved.size),
        "notes": list(posterior.notes),
        "summary": summary,
        "features": features,
    }


def _pipe_ref(
    index: int,
    edge_ids: Sequence[str],
    prior: NDArray[np.floating],
    beta: NDArray[np.floating],
    display_name: Sequence[str | None] | None,
    locality: Sequence[str | None] | None,
) -> dict[str, Any]:
    def _at(values: Sequence[str | None] | None) -> str | None:
        return values[index] if values is not None and index < len(values) else None

    return {
        "edge": str(edge_ids[index]),
        "name": _at(display_name),
        "locality": _at(locality),
        "prior": round(float(prior[index]), 4),
        "post": round(float(beta[index]), 4),
    }


def drain_summary(
    beta: NDArray[np.floating],
    prior: NDArray[np.floating],
    edge_ids: Sequence[str],
    *,
    q_full_m3s: NDArray[np.floating] | None = None,
    display_name: Sequence[str | None] | None = None,
    locality: Sequence[str | None] | None = None,
    observations: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What this cycle learned, in the handful of numbers the drain X-ray leads with.

    Computed over **every** pipe, before the written product is capped, so nothing here depends on
    which 6,000 were kept.

    **Capacity lost is weighted by full-flow capacity**, because that is what a flooded street
    feels: a 3 m trunk at half blockage loses more water than a lane at the same blockage. It is
    split in two because the two halves mean different things. ``capacity_lost_prior_pct`` is what
    the city pipeline *assumed* from land use before a single observation (markets and informal
    settlements at 0.35, residential 0.20, arterials 0.15); ``capacity_learned_m3s`` is what Pulse
    moved it by this cycle. On 2 July 2019 at 08:40 the first is 28.4 % and the second +55.6 m3/s
    of 63,109 (28.50 % after learning) - showing only the total would credit the land-use table
    with the learning. The +86.3 m3/s a reader gets from the file that cycle shipped with counts
    147 of the 173 pipes Pulse raised and 2 of the 28 it lowered, which is why it is computed here.

    Returns ``None`` for the capacity figures when no full-flow capacity was supplied, rather than
    a zero that would read as "nothing lost".
    """
    beta = np.asarray(beta, dtype=np.float64)
    prior = np.asarray(prior, dtype=np.float64)
    delta = beta - prior
    up = delta > MOVED_EPS
    down = delta < -MOVED_EPS

    largest_rise = (
        _pipe_ref(int(np.argmax(delta)), edge_ids, prior, beta, display_name, locality)
        if up.any()
        else None
    )
    largest_fall = (
        _pipe_ref(int(np.argmin(delta)), edge_ids, prior, beta, display_name, locality)
        if down.any()
        else None
    )

    full = lost_prior = lost_post = learned = None
    if q_full_m3s is not None:
        q = np.nan_to_num(np.asarray(q_full_m3s, dtype=np.float64), nan=0.0)
        q = np.clip(q, 0.0, None)
        if q.size == beta.size and q.sum() > 0.0:
            total = float(q.sum())
            prior_lost = float((q * capacity_reduction_pct(prior)).sum()) / 100.0
            post_lost = float((q * capacity_reduction_pct(beta)).sum()) / 100.0
            full = round(total, 1)
            lost_prior = round(100.0 * prior_lost / total, 3)
            lost_post = round(100.0 * post_lost / total, 3)
            learned = round(post_lost - prior_lost, 1)

    records = list(observations or [])
    kinds = sorted({str(r.get("kind", "unknown")) for r in records})
    by_kind = {
        kind: sum(1 for r in records if str(r.get("kind", "unknown")) == kind) for kind in kinds
    }
    synthetic = sum(1 for r in records if bool(r.get("synthetic")))

    return {
        "source": "product",
        "n_pipes": int(beta.size),
        "n_moved": int(up.sum() + down.sum()),
        "n_moved_up": int(up.sum()),
        "n_moved_down": int(down.sum()),
        "largest_rise": largest_rise,
        "largest_fall": largest_fall,
        "capacity_full_m3s": full,
        "capacity_lost_prior_pct": lost_prior,
        "capacity_lost_post_pct": lost_post,
        "capacity_learned_m3s": learned,
        "n_obs": len(records),
        "n_obs_by_kind": by_kind,
        "n_obs_synthetic": synthetic,
        "n_obs_real": len(records) - synthetic,
    }


# ---- naming ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaceIndex:
    """Where the named streets and the chronic hotspots are, in the city's metric CRS.

    Built once per city build and asked many times: which named street is nearest this pipe, and
    which hotspot of the register is nearest it. The trees are built from the tables in their file
    order, so two bakes of the same city ask the same trees and get the same answers (rule 8).
    """

    street_names: tuple[str, ...]
    street_tree: Any
    hotspot_names: tuple[str, ...]
    hotspot_tree: Any

    def streets_near(
        self, xy: NDArray[np.floating], radius_m: float = NAME_RADIUS_M
    ) -> list[str | None]:
        """The nearest named street to each point within ``radius_m``, else None."""
        return _nearest(self.street_tree, self.street_names, xy, radius_m)

    def localities_near(
        self, xy: NDArray[np.floating], radius_m: float = LOCALITY_RADIUS_M
    ) -> list[str | None]:
        """``near <hotspot>`` for the register hotspot nearest each point within ``radius_m``."""
        found = _nearest(self.hotspot_tree, self.hotspot_names, xy, radius_m)
        return [f"near {name}" if name else None for name in found]


def _nearest(
    tree: Any, names: tuple[str, ...], xy: NDArray[np.floating], radius_m: float
) -> list[str | None]:
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if tree is None or not names or points.shape[0] == 0:
        return [None] * points.shape[0]
    finite = np.isfinite(points).all(axis=1)
    out: list[str | None] = [None] * points.shape[0]
    if not finite.any():
        return out
    _, index = tree.query(points[finite], k=1, distance_upper_bound=radius_m)
    for slot, found in zip(np.flatnonzero(finite), np.atleast_1d(index), strict=True):
        if int(found) < len(names):
            out[int(slot)] = names[int(found)]
    return out


def _short_hotspot_name(name: str) -> str:
    """A register name without its parenthesis: "Hindmata junction (Hindmata Cinema, ...)"."""
    return name.split(" (", 1)[0].strip()


def _clean_name(value: Any) -> str | None:
    """A street name fit to print, or None.

    OSM ways merged into one segment can carry several names, which the city build stores as a
    list or as the list's text ("['Lal Bahadur Shastri Marg', 'Sant Rohidas Marg']", 95 of
    Mumbai's segments). Printing that text titled a drain X-ray card with Python brackets; the
    names are joined with " / " instead, each once, in the order OSM gave them.
    """
    if isinstance(value, (list, tuple, np.ndarray)):
        parts = [_clean_name(v) for v in value]
        unique = list(dict.fromkeys(p for p in parts if p))
        return " / ".join(unique) or None
    if value is None or value != value:  # None or NaN
        return None
    text = str(value).strip()
    if len(text) > 1 and text[0] == "[" and text[-1] == "]":
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return _clean_name(list(parsed))
    return text or None


def build_place_index(city_root: Path) -> PlaceIndex:
    """The named-street and hotspot trees for a built city, empty where a table is missing."""
    import geopandas as gpd
    import shapely
    from scipy.spatial import cKDTree

    street_names: tuple[str, ...] = ()
    street_tree = None
    crs = None
    segments = city_root / "segments.parquet"
    if segments.is_file():
        frame = gpd.read_parquet(segments, columns=["name", "geometry"])
        crs = frame.crs
        names = [_clean_name(v) for v in frame["name"]]
        keep = np.array([n is not None for n in names], dtype=bool)
        geoms = frame.geometry.to_numpy()[keep]
        kept_names = [n for n in names if n is not None]
        if len(geoms):
            coords, owner = shapely.get_coordinates(geoms, return_index=True)
            street_names = tuple(kept_names[int(i)] for i in owner)
            street_tree = cKDTree(coords) if len(coords) else None

    hotspot_names: tuple[str, ...] = ()
    hotspot_tree = None
    for candidate in (city_root / "export" / "hotspots.parquet", city_root / "hotspots.geojson"):
        if not candidate.is_file():
            continue
        spots = (
            gpd.read_parquet(candidate)
            if candidate.suffix == ".parquet"
            else gpd.read_file(candidate)
        )
        if "name" not in spots.columns or spots.empty:
            break
        if crs is not None and spots.crs is not None and spots.crs != crs:
            spots = spots.to_crs(crs)
        points = [
            (geom.x, geom.y, _clean_name(name))
            for geom, name in zip(spots.geometry, spots["name"], strict=True)
            if geom is not None and not geom.is_empty and _clean_name(name)
        ]
        if points:
            hotspot_names = tuple(_short_hotspot_name(str(p[2])) for p in points)
            hotspot_tree = cKDTree(np.array([[p[0], p[1]] for p in points], dtype=np.float64))
        break

    return PlaceIndex(street_names, street_tree, hotspot_names, hotspot_tree)


def _line_midpoints(frame: Any, id_column: str, ids: Sequence[str]) -> NDArray[np.floating]:
    """The midpoint of each id's line in the frame's CRS, in ``ids`` order; NaN where missing."""
    import shapely

    geoms = frame.geometry.to_numpy()
    mid = shapely.line_interpolate_point(geoms, 0.5, normalized=True)
    xy = shapely.get_coordinates(mid) if len(mid) else np.zeros((0, 2))
    by_id: dict[str, tuple[float, float]] = {}
    for key, point, geom in zip(frame[id_column].astype(str), xy, geoms, strict=False):
        if geom is not None and not shapely.is_empty(geom):
            by_id.setdefault(key, (float(point[0]), float(point[1])))
    out = np.full((len(ids), 2), np.nan)
    for row, key in enumerate(ids):
        found = by_id.get(str(key))
        if found is not None:
            out[row] = found
    return out


def pipe_places(
    city_root: Path,
    edge_ids: Sequence[str],
    street: Sequence[str | None] | None,
    index: PlaceIndex | None = None,
) -> tuple[tuple[str | None, ...], tuple[str | None, ...]]:
    """Each pipe's display name and locality, in ``edge_ids`` order.

    The display name is the pipe's own street where OSM names it, else ``"off <street>"`` for the
    nearest named street within :data:`NAME_RADIUS_M` of the pipe's midpoint, else None. The
    "off" is the honest part: the pipe does not run under that street, it runs near it. The
    locality is ``"near <hotspot>"`` for the nearest chronic spot within
    :data:`LOCALITY_RADIUS_M`. A ward is never used - ``segments.ward`` is empty in Mumbai.
    """
    import geopandas as gpd

    n = len(edge_ids)
    own = [
        (_clean_name(street[i]) if street is not None and i < len(street) else None)
        for i in range(n)
    ]
    path = city_root / "drain_edges.parquet"
    if not path.is_file():
        return tuple(own), (None,) * n
    places = index if index is not None else build_place_index(city_root)
    frame = gpd.read_parquet(path, columns=["edge_id", "geometry"])
    xy = _line_midpoints(frame, "edge_id", edge_ids)
    nearest = places.streets_near(xy)
    display = tuple(
        own[i] if own[i] else (f"off {nearest[i]}" if nearest[i] else None) for i in range(n)
    )
    return display, tuple(places.localities_near(xy))


def segment_places(
    city_root: Path,
    segment_ids: Sequence[str],
    index: PlaceIndex | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """A road segment's place and locality, for observations that arrive as a segment id.

    The same rules as :func:`pipe_places`: the segment's own OSM name, else ``"off <street>"``
    within :data:`NAME_RADIUS_M`, else None; and ``"near <hotspot>"`` within
    :data:`LOCALITY_RADIUS_M`. Before this a traffic anomaly on an unnamed road was titled by its
    raw id ("S102177717-000"), which is 10 of the 21 cards on the 08:40 cycle.
    """
    import geopandas as gpd

    wanted = list(dict.fromkeys(str(s) for s in segment_ids))
    path = city_root / "segments.parquet"
    if not wanted or not path.is_file():
        return {sid: (None, None) for sid in wanted}
    places = index if index is not None else build_place_index(city_root)
    frame = gpd.read_parquet(path, columns=["segment_id", "name", "geometry"])
    frame = frame[frame["segment_id"].astype(str).isin(set(wanted))]
    own = {
        str(sid): _clean_name(name)
        for sid, name in zip(frame["segment_id"], frame["name"], strict=True)
    }
    xy = _line_midpoints(frame, "segment_id", wanted)
    nearest = places.streets_near(xy)
    localities = places.localities_near(xy)
    out: dict[str, tuple[str | None, str | None]] = {}
    for row, sid in enumerate(wanted):
        name = own.get(sid) or (f"off {nearest[row]}" if nearest[row] else None)
        out[sid] = (name, localities[row])
    return out


def desilting_csv(health: dict[str, Any], limit: int = TOP_N) -> str:
    """The desilting priority list, worst pipe first (SPEC.md 7.3).

    Sorted by capacity reduction rather than by blockage, because that is the number that says
    how much flow a crew would win back - which is what a desilting budget is spent on.

    ``street`` is the pipe's display name: its own OSM street, else "off <nearest named street>"
    within :data:`NAME_RADIUS_M` (:func:`pipe_places`). ``locality`` is the nearest chronic spot.
    """
    rows = sorted(
        (f["properties"] for f in health.get("features", [])),
        key=lambda p: -float(p.get("capacity_reduction_pct") or 0.0),
    )[:limit]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "rank",
            "edge_id",
            "street",
            "locality",
            "beta_mean",
            "beta_sd",
            "capacity_reduction_pct",
            "observations",
            "hotspots_explained",
            "confidence",
        ]
    )
    for rank, props in enumerate(rows, start=1):
        writer.writerow(
            [
                rank,
                props.get("edge_id"),
                # The display name, so "off Eastern Freeway" rather than a blank: 83 % of the
                # written pipes run under a road OSM does not name.
                props.get("display_name") or props.get("street") or "",
                # "near Wadala": the chronic spot within 300 m, so a crew can find it.
                props.get("locality") or "",
                props.get("beta_mean"),
                props.get("beta_sd"),
                props.get("capacity_reduction_pct"),
                props.get("observations", 0),
                "; ".join(props.get("explains") or []),
                props.get("confidence", "inferred"),
            ]
        )
    return buffer.getvalue()


def is_moved(properties: dict[str, Any]) -> bool:
    """Whether Pulse moved this pipe this cycle, from a feature's properties.

    The ``moved`` flag when the product carries it (every bake since it was added). A run baked
    before that has only ``beta_delta``, rounded to four decimals, which reads 0.0001 for a pipe
    that moved by 1.2e-4 and for one that moved by 0.6e-4; half the threshold keeps every pipe
    that truly moved, at the cost of admitting a few of the second kind.
    """
    flag = properties.get("moved")
    if flag is not None:
        return bool(flag)
    return abs(float(properties.get("beta_delta", 0.0) or 0.0)) >= MOVED_EPS / 2.0


def written_features(
    features: Sequence[dict[str, Any]], cap: int = MAX_WRITTEN_EDGES
) -> list[dict[str, Any]]:
    """The pipes the written product keeps: every moved pipe, then the worst blockage up to ``cap``.

    Every moved pipe is chosen first, both the ones Pulse raised and the ones it cleared, so a
    pipe an observation drove to near-zero blockage is still on the map. The rest of the cap is
    filled by posterior blockage, worst first. When more pipes moved than the cap allows, all of
    them are written and the cap gives way: a learned pipe is never dropped for size.

    The chosen set is then written **worst blockage first**, as the file always was, so a reader
    that slices its head still gets the worst pipes. Ties fall back to the network's edge order,
    so two bakes write the same set in the same order (rule 8).

    Which pipes moved is :func:`is_moved`.
    """

    def worst_first(feature: dict[str, Any]) -> float:
        return -float(feature["properties"].get("beta_mean", 0.0) or 0.0)

    flags = [is_moved(f["properties"]) for f in features]
    n_moved = sum(flags)
    chosen = sorted(
        range(len(features)),
        key=lambda i: (not flags[i], worst_first(features[i])),
    )[: max(cap, n_moved)]
    return [features[i] for i in sorted(chosen, key=lambda i: (worst_first(features[i]), i))]


def write_drain_health(run_dir: Path, health: dict[str, Any]) -> None:
    """Write ``drain_health.geojson`` and ``desilting.csv`` into a run directory.

    The CSV is built from the **full** product before the geometry is capped, so the desilting
    ranking is over every pipe even though the map only receives the worst few thousand.
    """
    csv_text = desilting_csv(health)

    capped = dict(health)
    capped["features"] = written_features(health.get("features", []))
    capped["n_written"] = len(capped["features"])

    (run_dir / "drain_health.geojson").write_text(
        json.dumps(capped, separators=(",", ":")), encoding="utf-8"
    )
    (run_dir / "desilting.csv").write_text(csv_text, encoding="utf-8")
