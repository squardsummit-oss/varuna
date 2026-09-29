"""One Pulse stage: observations in, a learned drain map out (SPEC.md 11.6, 11.11).

The wiring between the pieces. It reads the traffic feed and the report stream up to the cycle
time, turns each observation into a depth at a place, finds the pipe that place drains through,
runs the EnKF over those pipes' blockage, and writes the drain-health product.

**Observations are located by their inlet, not by their coordinates.** A report at Hindmata is
evidence about the pipe Hindmata drains into, and the city pipeline already knows which inlet
node each surface unit and road segment belongs to. Going through the graph rather than through
a radius search is what makes the localisation below mean something hydraulic.

**The posterior is meant to persist across cycles** (SPEC.md 11.6) and relax toward the prior
with a 30-day time constant, so a pipe that was desilted last month is not still condemned by a
flood it caused in June. `run_pulse` takes a carried-forward posterior as ``prior_mean`` and
``prior_sd``, and :func:`load_posterior`, :func:`save_posterior` and the relaxation below exist
for it - **but nothing passes one yet.** The cycle calls `run_pulse` with no prior and no run
directory holds a `posterior.json`, so each cycle re-assimilates every observation up to its own
instant from the city's prior. (This docstring used to say the carry-forward happened in memory
within a bake; no bake existed to do it.) Wiring it also means narrowing each cycle to the
observations since the last one, or every observation is counted again every cycle
(`varuna_cycle.bake`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import structlog
from varuna_schemas.paths import data_dir

from varuna_pulse.citycache import cached, digest
from varuna_pulse.enkf import assimilate, capacity_operator, hop_distances
from varuna_pulse.health import build_place_index, drain_health, pipe_places, segment_places
from varuna_pulse.reports import read_reports
from varuna_pulse.traffic import CONFOUNDER_RADIUS_M, detect_anomalies

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    import pandas as pd
    from numpy.typing import NDArray

    from varuna_pulse.health import PlaceIndex
    from varuna_pulse.traffic import TrafficObservation

log = structlog.get_logger("varuna.pulse.cycle")

__all__ = [
    "RELAX_DAYS",
    "REPORT_SNAP_M",
    "PulseResult",
    "run_pulse",
    "segment_place_names",
    "write_observations",
]

RELAX_DAYS = 30.0
"""Time constant of the posterior's relaxation back toward the prior (SPEC.md 11.6).

Blockage is physical silt and it changes: a monsoon deposits it and a desilting crew removes it.
A posterior that never forgets would keep condemning a pipe that has since been cleaned."""

MAX_OBSERVATIONS = 400
"""Observations assimilated per cycle, strongest first.

The EnKF's cost is cubic in the batch through the innovation covariance, and a heavy cycle in
Mumbai produces thousands of traffic anomalies. Four hundred of the most anomalous is a batch
that runs in well under the 3 s stage budget and carries essentially all the information - the
thousandth-slowest street tells you nothing the first four hundred did not."""

REPORT_SNAP_M = 500.0
"""How far a report may sit from the nearest drain node and still be assimilated at it.

A report is placed on the inlet nearest its coordinates. With no limit, a report filed from
Thane or from a phone whose location came back as 0, 0 was placed on whichever node happened to be
nearest and moved that pipe's blockage - evidence about a street the inferred graph does not have,
spent on one it does. 500 m is the same order as the confounder test's neighbourhood and many
inlet spacings (40 m) wide, so any report on a street the graph follows is kept."""

DISAGREEMENT_LIMIT = 25
"""Rows kept in the model-observation disagreement list (SPEC.md 11.6).

The list exists to be read - "where was the model most wrong this cycle" - and every row is
already in the observations array beside it, so it carries the worst residuals rather than a
second copy of all four hundred."""


@dataclass(frozen=True, slots=True)
class PulseResult:
    """What one Pulse stage produced."""

    beta_mean: NDArray[np.floating]
    beta_sd: NDArray[np.floating]
    health: dict[str, Any]
    n_traffic: int
    n_reports: int
    n_assimilated: int
    n_edges_updated: int
    observations: list[dict[str, Any]]
    disagreements: list[dict[str, Any]]
    """The worst residuals, model against observation (SPEC.md 11.6), largest first."""

    notes: tuple[str, ...]


def _edge_below_node(from_node: NDArray[np.integer]) -> dict[int, int]:
    """The outgoing edge of each node: the pipe an inlet's water leaves through."""
    out: dict[int, int] = {}
    for edge, node in enumerate(from_node):
        out.setdefault(int(node), edge)
    return out


def run_pulse(
    network,
    city_root: Path,
    bundle_dir: Path,
    cycle_ts: datetime,
    *,
    transform: tuple[float, float, float, float, float, float] | None = None,
    crs: str | None = None,
    rain_mm_h_at: dict[str, float] | None = None,
    prior_mean: NDArray[np.floating] | None = None,
    prior_sd: NDArray[np.floating] | None = None,
    run_id: str = "",
    seed: int = 2019,
    use_inbox: bool = True,
) -> PulseResult:
    """Assimilate this cycle's observations into the blockage posterior.

    Args:
        network: the loaded :class:`~varuna_twin.types.DrainNetwork`.
        city_root: ``city/<city>``, for the segment-to-inlet map.
        bundle_dir: the replay bundle, for the traffic feed and its report stream. Reports
            posted through the API are read alongside it from ``data/reports/inbox.jsonl``.
        cycle_ts: only observations at or before this instant are used.
        transform, crs: the terrain grid, so a report's lon/lat can be matched to the drain node
            nearest it. Reports are skipped when they are absent, and the notes say so.
        rain_mm_h_at: rain over each observed segment, for the observation operator.
        prior_mean, prior_sd: the carried-forward posterior; the city's own prior when absent.
        run_id: stamped into the product.
        use_inbox: read ``data/reports/inbox.jsonl`` beside the bundle's stream. A live cycle
            turns it off: its folder already carries the inbox's recent reports, and the whole
            inbox holds reports stamped with 2019 replay times that say nothing about today.
    """
    import pandas as pd

    beta_prior = (
        np.asarray(network.beta, dtype=np.float64)
        if prior_mean is None
        else np.asarray(prior_mean, dtype=np.float64)
    )
    sd_prior = (
        np.full(beta_prior.shape, 0.15)
        if prior_sd is None
        else np.asarray(prior_sd, dtype=np.float64)
    )

    # ---- observations -----------------------------------------------------------------
    notes: list[str] = []
    traffic: list[TrafficObservation] = []
    speeds_path = bundle_dir / "traffic" / "speeds.parquet"
    if speeds_path.is_file():
        speeds = pd.read_parquet(speeds_path)
        # `incident_segments` stays unset on purpose: the synthetic feed carries no incident
        # column (services/replay/varuna_replay/streams.py), so its confounders have to be
        # rejected on the evidence around them rather than on a label. That is the spatial
        # test below - the one filter of SPEC.md 11.6's three this feed can actually run.
        candidates = detect_anomalies(speeds, at=cycle_ts, raining=True)
        traffic, note = _reject_regional_congestion(speeds, candidates, city_root, cycle_ts)
        if note is not None:
            notes.append(note)

    # Two report sources: the bundle's synthetic stream and the inbox POST /v1/reports appends
    # to. Reading both here is what makes a report filed from the public map an observation on
    # the drain X-ray one cycle later (7.11).
    inbox = data_dir() / "reports" / "inbox.jsonl" if use_inbox else None
    reports = read_reports(bundle_dir, until=cycle_ts, inbox=inbox)

    # ---- locate each observation on the graph -----------------------------------------
    nodes = pd.read_parquet(city_root / "drain_nodes.parquet", columns=["node_id", "segment_id"])
    node_index = {str(nid): i for i, nid in enumerate(network.node_ids)}
    segment_to_node = {
        str(seg): node_index[str(nid)]
        for seg, nid in zip(nodes["segment_id"], nodes["node_id"], strict=True)
        if seg is not None and str(nid) in node_index
    }
    outgoing = _edge_below_node(np.asarray(network.from_node))
    street_of_edge = cached(
        "street_names",
        [city_root / "segments.parquet", city_root / "drain_nodes.parquet"],
        digest(network.edge_ids, network.node_ids, np.asarray(network.from_node)),
        lambda: _street_names(city_root, network, nodes, node_index),
    )

    # Where things are, in words: the named streets and the hotspot register, built once per
    # city build. A traffic anomaly arrives as a segment id and was printed as one.
    places = _place_index(city_root)
    # Every segment's place, once per city build: a cycle only looks its anomalies up.
    traffic_places = _traffic_place_index(city_root)
    # Every pipe's display name and locality, once per city build, for the product and for a
    # report that arrives with a coordinate and no place of its own.
    display_name, locality = cached(
        "pipe_places",
        [*_place_sources(city_root), city_root / "drain_edges.parquet"],
        digest(network.edge_ids, tuple(street_of_edge)),
        lambda: pipe_places(city_root, network.edge_ids, street_of_edge, index=places),
    )

    observed_edges: list[int] = []
    y: list[float] = []
    y_sd: list[float] = []
    records: list[dict[str, Any]] = []

    for observation in traffic:
        node = segment_to_node.get(observation.segment_id)
        edge = outgoing.get(node) if node is not None else None
        if edge is None:
            continue
        observed_edges.append(edge)
        y.append(observation.depth_cm)
        y_sd.append(observation.depth_sd_cm)
        segment_place, segment_locality = traffic_places.get(
            str(observation.segment_id), (None, None)
        )
        records.append(
            {
                "kind": "traffic",
                "segment_id": observation.segment_id,
                # The segment's own street, else "off <nearest named street>" within 200 m, else
                # the pipe's own name; and the nearest chronic spot within 300 m. Null when none
                # of them exists, and the screen then says "Unnamed road" (varuna_pulse.health).
                "place": segment_place or display_name[edge],
                "locality": segment_locality or locality[edge],
                "edge_id": network.edge_ids[edge],
                "ts": observation.ts.isoformat(),
                "depth_cm": observation.depth_cm,
                "depth_sd_cm": observation.depth_sd_cm,
                "speed_kmh": round(observation.speed_kmh, 1),
                "baseline_kmh": round(observation.baseline_kmh, 1),
                "z": round(observation.z, 2),
                "synthetic": True,
            }
        )

    # Reports carry a coordinate rather than a segment, so they are placed on the nearest inlet.
    node_lon, node_lat = _node_positions(network, transform, crs)
    too_far = 0
    at_outfall = 0
    for report in reports:
        edge, missed = _snap_report(node_lon, node_lat, outgoing, report.lon, report.lat)
        if edge is None:
            too_far += missed == "too_far"
            at_outfall += missed == "outfall"
            continue
        observed_edges.append(edge)
        y.append(report.depth_cm)
        y_sd.append(report.depth_sd_cm / max(np.sqrt(min(report.n_merged, 4)), 1.0))
        records.append(
            {
                "kind": "report",
                "report_id": report.report_id,
                "edge_id": network.edge_ids[edge],
                "ts": report.ts.isoformat(),
                "depth_cm": report.depth_cm,
                "depth_sd_cm": report.depth_sd_cm,
                "chip": report.chip,
                # A report filed from the public map carries the place its reporter named; one
                # posted with only a coordinate is named by the pipe it was placed on.
                "place": report.place or display_name[edge],
                "locality": None if report.place else locality[edge],
                "n_merged": report.n_merged,
                "synthetic": report.synthetic,
            }
        )

    if too_far:
        plural = too_far != 1
        notes.append(
            f"{too_far} citizen report{'s' if plural else ''} more than "
            f"{REPORT_SNAP_M:.0f} m from any drain node {'were' if plural else 'was'} "
            "not assimilated: the inferred graph has no pipe there to learn about."
        )
    if at_outfall:
        plural = at_outfall != 1
        notes.append(
            f"{at_outfall} citizen report{'s' if plural else ''} nearest a drain outfall "
            f"{'were' if plural else 'was'} not assimilated: an outfall has no pipe of its own "
            "to learn about."
        )

    if len(observed_edges) > MAX_OBSERVATIONS:
        order = np.argsort(np.asarray(y_sd))[:MAX_OBSERVATIONS]
        observed_edges = [observed_edges[i] for i in order]
        y = [y[i] for i in order]
        y_sd = [y_sd[i] for i in order]
        records = [records[i] for i in order]
        notes.append(
            f"{MAX_OBSERVATIONS} of the sharpest observations were assimilated this cycle."
        )

    # ---- assimilate -------------------------------------------------------------------
    if observed_edges:
        edges = np.asarray(observed_edges, dtype=np.int64)
        hops = hop_distances(np.asarray(network.from_node), np.asarray(network.to_node), edges)
        rain = np.array(
            [float((rain_mm_h_at or {}).get(r.get("segment_id", ""), 60.0)) for r in records]
        )
        # The catchment each pipe actually drains, from the city pipeline's rational-method
        # sizing, rather than one number for every junction. A trunk under Dadar and a lane in
        # Chembur do not fail at the same rainfall, and the filter should know that.
        catchment = cached(
            "contributing_area",
            [city_root / "drain_edges.parquet"],
            digest(network.edge_ids),
            lambda: _contributing_area(city_root, network),
        )[edges]
        operator = capacity_operator(
            edges,
            contributing_area_m2=catchment,
            rain_mm_h=rain,
            # The junction a pipe's water backs up over. A choice, held constant so the
            # observation operator stays monotone in blockage and nothing else.
            ponding_area_m2=np.full(edges.size, 1_500.0),
            q_full_m3s=np.asarray(network.q_full, dtype=np.float64)[edges],
        )
        posterior = assimilate(
            beta_prior, sd_prior, np.asarray(y), np.asarray(y_sd), operator, hops, seed=seed
        )
    else:
        posterior = assimilate(
            beta_prior,
            sd_prior,
            np.zeros(0),
            np.zeros(0),
            capacity_operator(
                np.zeros(0, dtype=np.int64),
                contributing_area_m2=np.zeros(0),
                rain_mm_h=np.zeros(0),
                ponding_area_m2=np.zeros(0),
                q_full_m3s=np.zeros(0),
            ),
            np.zeros((0, beta_prior.size), dtype=np.int32),
            seed=seed,
        )

    # ---- what each observation did to the posterior ------------------------------------
    # SPEC.md 11.6 asks for "the beta change each caused" and 7.3's timeline prints it. The
    # pair is the pipe's blockage before and after *this cycle's* batch rather than this one
    # observation alone: the EnKF updates every edge from every observation at once, and
    # splitting the move between them would be a number the filter never computed.
    innovation = np.asarray(posterior.innovation, dtype=np.float64)
    for record, edge, residual in zip(records, observed_edges, innovation, strict=True):
        record["beta_before"] = round(float(posterior.prior_mean[edge]), 4)
        record["beta_after"] = round(float(posterior.beta_mean[edge]), 4)
        # y - H(theta^f): what the model got wrong at this place before the update. Positive
        # means the street was wetter than the drain map expected.
        record["innovation_cm"] = round(float(residual), 2)
        record["modelled_depth_cm"] = round(float(record["depth_cm"] - residual), 1)

    disagreements = sorted(
        (
            {
                "kind": record["kind"],
                "place": _place_of(record, street_of_edge[edge]),
                "edge_id": record["edge_id"],
                "ts": record["ts"],
                "observed_depth_cm": record["depth_cm"],
                "modelled_depth_cm": record["modelled_depth_cm"],
                "residual_cm": record["innovation_cm"],
            }
            for record, edge in zip(records, observed_edges, strict=True)
        ),
        # Ties broken on the pipe id so two bakes of the same cycle order them the same (rule 8).
        key=lambda row: (-abs(row["residual_cm"]), row["edge_id"]),
    )[:DISAGREEMENT_LIMIT]
    if disagreements:
        # The operator is named because the modelled depth is *its* number, not the Twin's: a
        # volume balance over a fixed ponding area will predict metres where a big catchment
        # meets a small pipe, and a reader has to be able to tell that from a forecast.
        worst = disagreements[0]
        notes.append(
            f"Largest model-observation disagreement: {abs(worst['residual_cm']):.0f} cm, "
            f"{worst['place']} - {worst['observed_depth_cm']:.0f} cm observed against "
            f"{worst['modelled_depth_cm']:.0f} cm from the {posterior.operator} operator "
            f"before the update."
        )

    counts = np.zeros(beta_prior.size, dtype=np.int64)
    for edge in observed_edges:
        counts[edge] += 1

    health = drain_health(
        posterior,
        network.edge_ids,
        cached(
            "edge_geometry",
            [city_root / "drain_edges.parquet"],
            digest(network.edge_ids),
            lambda: _edge_geometry(city_root, network.edge_ids),
        ),
        diameter_m=np.asarray(network.diameter, dtype=np.float64),
        street=street_of_edge,
        display_name=display_name,
        locality=locality,
        observation_counts=counts,
        q_full_m3s=np.asarray(network.q_full, dtype=np.float64),
        observations=records,
        last_update=cycle_ts.isoformat(),
        run_id=run_id,
    )

    log.info(
        "pulse.cycle",
        run_id=run_id,
        traffic=len(traffic),
        reports=len(reports),
        assimilated=len(observed_edges),
        edges_updated=int(posterior.updated_edges.size),
    )
    return PulseResult(
        beta_mean=posterior.beta_mean,
        beta_sd=posterior.beta_sd,
        health=health,
        n_traffic=len(traffic),
        n_reports=len(reports),
        n_assimilated=len(observed_edges),
        n_edges_updated=int(posterior.updated_edges.size),
        observations=records,
        disagreements=disagreements,
        notes=tuple([*notes, *posterior.notes]),
    )


def _place_of(record: dict[str, Any], street: str | None) -> str:
    """Where an observation was, in the words a ward officer would use.

    A report carries the place it was filed at; a traffic anomaly carries only a segment id, so
    it falls back to the street the pipe runs under and then, for an unnamed way, to the id.
    """
    return str(record.get("place") or street or record.get("segment_id") or record["edge_id"])


def write_observations(run_dir: Path, result: PulseResult, run_id: str) -> None:
    """Write ``observations.json``: what Pulse assimilated, and where it disagreed with itself.

    The payload lives here rather than in the cycle orchestrator so the disagreement list
    SPEC.md 11.6 names travels with the observations it is derived from, and so the beta pair
    the drain X-ray's timeline prints cannot be dropped by a caller that composes its own dict.
    """
    (run_dir / "observations.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "n_traffic": result.n_traffic,
                "n_reports": result.n_reports,
                "n_assimilated": result.n_assimilated,
                "n_edges_updated": result.n_edges_updated,
                "observations": result.observations,
                "disagreements": result.disagreements,
                "notes": list(result.notes),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _reject_regional_congestion(
    speeds: pd.DataFrame,
    candidates: list[TrafficObservation],
    city_root: Path,
    cycle_ts: datetime,
) -> tuple[list[TrafficObservation], str | None]:
    """Drop the anomalies network-wide congestion explains (SPEC.md 11.6's third filter).

    The detector runs twice because it reports its rejections only to its log, and the product
    has to be able to say how many it dropped. The first pass over the whole feed finds the
    candidates; this second pass re-scores them with their neighbours' speeds in hand, and the
    difference is what the area explained away. The second pass is given only the candidates and
    the segments within 500 m of them - a few thousand rows against the feed's half a million -
    so it costs a fraction of the first.

    Returns the surviving observations and the note that says what the test did, or ``None``
    when there was nothing to test.
    """
    if not candidates:
        return [], None

    ids = [observation.segment_id for observation in candidates]
    neighbours = _neighbour_map(city_root, ids)
    if not neighbours:
        # Never silently skip a filter: say which file was missing and what is not being done.
        return candidates, (
            f"Spatial confounder test skipped: no segment geometry at "
            f"{city_root.name}/segments.parquet, so the {CONFOUNDER_RADIUS_M:.0f} m neighbour "
            f"test could not run and all {len(candidates)} traffic anomalies were kept."
        )

    # Unresolved candidates stay in the frame so they survive untested rather than vanish.
    keep = set(ids) | {n for nearby in neighbours.values() for n in nearby}
    subset = speeds[speeds["segment_id"].astype(str).isin(keep)]
    kept = detect_anomalies(subset, at=cycle_ts, raining=True, neighbours=neighbours)

    rejected_regional = len(candidates) - len(kept)
    log.info(
        "pulse.regional_congestion",
        candidates=len(candidates),
        kept=len(kept),
        rejected_regional=rejected_regional,
        radius_m=CONFOUNDER_RADIUS_M,
    )
    return kept, (
        f"Spatial confounder test: {rejected_regional} of {len(candidates)} traffic anomalies "
        f"rejected as network-wide congestion; the test drops an anomaly whose neighbours "
        f"within {CONFOUNDER_RADIUS_M:.0f} m are all equally slow."
    )


def _neighbour_map(city_root: Path, segment_ids: Sequence[str]) -> dict[str, list[str]]:
    """Segments within :data:`~varuna_pulse.traffic.CONFOUNDER_RADIUS_M` of each given segment.

    Built only for the segments that would otherwise become observations: those are the only
    keys the detector looks up, and a tree query over all 21k of Mumbai's segments would cost
    more than the test it feeds. The centroids are in the city's metric CRS, so the radius is
    metres without a projection step.
    """
    import geopandas as gpd
    from scipy.spatial import cKDTree

    path = city_root / "segments.parquet"
    if not segment_ids or not path.is_file():
        return {}
    frame = gpd.read_parquet(path, columns=["segment_id", "geometry"])
    centroid = frame.geometry.centroid
    ids = frame["segment_id"].astype(str).to_numpy()
    xy = np.column_stack([centroid.x.to_numpy(), centroid.y.to_numpy()])
    index = {sid: i for i, sid in enumerate(ids)}
    rows = [(sid, index[sid]) for sid in dict.fromkeys(segment_ids) if sid in index]
    if not rows:
        return {}
    balls = cKDTree(xy).query_ball_point(xy[[i for _, i in rows]], r=CONFOUNDER_RADIUS_M)
    # Sorted so two bakes of the same cycle produce the same map (rule 8).
    return {
        sid: sorted(str(ids[j]) for j in ball if str(ids[j]) != sid)
        for (sid, _), ball in zip(rows, balls, strict=True)
    }


def _street_names(city_root: Path, network, nodes, node_index: dict[str, int]) -> list[str | None]:
    """The road each pipe runs under, in the network's edge order.

    A desilting list that says `MUM-E020507` is not a work order. The drain graph has no street
    of its own - it was synthesised along the roads - but every node remembers the segment it was
    placed on, and segments now carry OSM's name.
    """
    import pandas as pd

    segments = city_root / "segments.parquet"
    if not segments.is_file():
        return [None] * len(network.edge_ids)
    frame = pd.read_parquet(segments, columns=["segment_id", "name"])
    name_of_segment = {
        str(sid): (None if name is None or name != name else str(name))
        for sid, name in zip(frame["segment_id"], frame["name"], strict=True)
    }
    segment_of_node: dict[int, str] = {}
    for seg, nid in zip(nodes["segment_id"], nodes["node_id"], strict=True):
        index = node_index.get(str(nid))
        if index is not None and seg is not None:
            segment_of_node[index] = str(seg)

    from_node = np.asarray(network.from_node)
    return [
        name_of_segment.get(segment_of_node.get(int(from_node[edge]), ""), None)
        for edge in range(len(network.edge_ids))
    ]


def _contributing_area(city_root: Path, network) -> NDArray[np.floating]:
    """Catchment area per pipe in m2, from the city pipeline's own sizing."""
    import pandas as pd

    path = city_root / "drain_edges.parquet"
    fallback = np.full(len(network.edge_ids), 30_000.0)
    if not path.is_file():
        return fallback
    frame = pd.read_parquet(path, columns=["edge_id", "contributing_area_m2"])
    by_id = dict(zip(frame["edge_id"].astype(str), frame["contributing_area_m2"], strict=True))
    return np.array([float(by_id.get(str(eid), 30_000.0) or 30_000.0) for eid in network.edge_ids])


def _node_positions(
    network,
    transform: tuple[float, float, float, float, float, float] | None,
    crs: str | None,
) -> tuple[NDArray[np.floating] | None, NDArray[np.floating] | None]:
    """Every drain node's lon/lat, from the 2D cell it exchanges water with.

    The node table has no coordinates of its own in the solver's view - the graph is indices and
    arrays - but each node knows its cell, and the cell is a place.
    """
    if transform is None or crs is None:
        return None, None
    from pyproj import Transformer

    res, _, left, _, _, top = transform
    rows = np.asarray(network.cell_row, dtype=np.float64)
    cols = np.asarray(network.cell_col, dtype=np.float64)
    x = left + (cols + 0.5) * res
    y = top - (rows + 0.5) * res
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x, y)
    # A node with no cell exchanges with no street; park it far away so it never wins a search.
    missing = (np.asarray(network.cell_row) < 0) | (np.asarray(network.cell_col) < 0)
    return np.where(missing, 1e6, lon), np.where(missing, 1e6, lat)


def _place_index(city_root: Path) -> PlaceIndex:
    """The named-street and hotspot trees, built once per city build."""
    return cached(
        "place_index", _place_sources(city_root), "", lambda: build_place_index(city_root)
    )


def _traffic_place_index(city_root: Path) -> dict[str, tuple[str | None, str | None]]:
    """Every road segment's place and locality, once per city build: a cycle looks many up."""
    return cached(
        "segment_places",
        _place_sources(city_root),
        "",
        lambda: segment_places(
            city_root, _all_segment_ids(city_root), index=_place_index(city_root)
        ),
    )


def segment_place_names(
    city_root: Path, segment_ids: Sequence[str]
) -> dict[str, tuple[str | None, str | None]]:
    """A few road segments' place and locality, by the rules a cycle names its anomalies with.

    For the API naming the traffic anomalies of a run baked before the cycle named them -
    "off Eastern Freeway" rather than "S102177717-000". It shares the cycle's place index (one
    per city build, 2.2 s to build on Mumbai) and looks up only the segments asked for (about
    50 ms for a cycle's worth), where naming all 21,296 segments takes about 9 s.
    """
    return segment_places(city_root, segment_ids, index=_place_index(city_root))


def _all_segment_ids(city_root: Path) -> list[str]:
    """Every road segment id of the city, in file order."""
    import pandas as pd

    path = city_root / "segments.parquet"
    if not path.is_file():
        return []
    return [str(sid) for sid in pd.read_parquet(path, columns=["segment_id"])["segment_id"]]


def _place_sources(city_root: Path) -> list[Path]:
    """The files the place index is built from, for the city cache's fingerprint."""
    return [
        city_root / "segments.parquet",
        city_root / "export" / "hotspots.parquet",
        city_root / "hotspots.geojson",
    ]


def _node_distance_sq_m(
    node_lon: NDArray[np.floating], node_lat: NDArray[np.floating], lon: float, lat: float
) -> NDArray[np.floating]:
    """Squared distance in metres from every node to a lon/lat, on a local equirectangular plane.

    Degrees used to be compared directly, which weighs a degree of longitude as a degree of
    latitude (5 % wrong at Mumbai's 19 N) and gave a cutoff no unit to be held in.
    """
    kx = 111_320.0 * float(np.cos(np.radians(lat)))
    ky = 110_574.0
    return ((node_lon - lon) * kx) ** 2 + ((node_lat - lat) * ky) ** 2


def _nearest_node_m(
    node_lon: NDArray[np.floating], node_lat: NDArray[np.floating], lon: float, lat: float
) -> float:
    """Distance in metres to the drain node nearest a lon/lat."""
    if node_lon.size == 0:
        return float("inf")
    return float(np.sqrt(np.min(_node_distance_sq_m(node_lon, node_lat, lon, lat))))


def _nearest_edge(
    node_lon: NDArray[np.floating] | None,
    node_lat: NDArray[np.floating] | None,
    outgoing: dict[int, int],
    lon: float,
    lat: float,
    max_m: float = REPORT_SNAP_M,
) -> int | None:
    """The outgoing pipe of the drain node nearest a lon/lat, or None past ``max_m`` metres."""
    if node_lon is None or node_lat is None or node_lon.size == 0:
        return None
    distance = _node_distance_sq_m(node_lon, node_lat, lon, lat)
    nearest = int(np.argmin(distance))
    if float(distance[nearest]) > max_m * max_m:
        return None
    return outgoing.get(nearest)


def _snap_report(
    node_lon: NDArray[np.floating] | None,
    node_lat: NDArray[np.floating] | None,
    outgoing: dict[int, int],
    lon: float,
    lat: float,
) -> tuple[int | None, Literal["too_far", "outfall", "no_graph"] | None]:
    """The pipe a report is assimilated at, or why it is not.

    ``too_far`` is more than :data:`REPORT_SNAP_M` from every drain node. ``outfall`` is near the
    graph, but the nearest node is an outfall: it has no pipe of its own for the report to move,
    and which inflowing pipe to blame instead is a modelling choice a cycle does not make
    silently. ``no_graph`` is a city without node positions, where no report can be placed at
    all. The first two are counted into the cycle's notes, so a report near the city is never
    dropped without a word.
    """
    if node_lon is None or node_lat is None or node_lon.size == 0:
        return None, "no_graph"
    edge = _nearest_edge(node_lon, node_lat, outgoing, lon, lat)
    if edge is not None:
        return edge, None
    if _nearest_node_m(node_lon, node_lat, lon, lat) > REPORT_SNAP_M:
        return None, "too_far"
    return None, "outfall"


def _edge_geometry(city_root: Path, edge_ids: tuple[str, ...]) -> list[list[list[float]]]:
    """Each edge's line in lon/lat, in the network's own edge order."""
    import geopandas as gpd

    path = city_root / "drain_edges.parquet"
    if not path.is_file():
        return []
    frame = gpd.read_parquet(path, columns=["edge_id", "geometry"]).to_crs("EPSG:4326")
    by_id = {
        str(eid): [[round(x, 6), round(y, 6)] for x, y in geom.coords]
        for eid, geom in zip(frame["edge_id"], frame.geometry, strict=True)
        if geom is not None
    }
    return [by_id.get(str(eid), []) for eid in edge_ids]


def relax_toward_prior(
    posterior_mean: NDArray[np.floating],
    prior_mean: NDArray[np.floating],
    days_since: float,
) -> NDArray[np.floating]:
    """Exponential relaxation of the posterior back toward the prior (SPEC.md 11.6)."""
    weight = float(np.exp(-max(days_since, 0.0) / RELAX_DAYS))
    return weight * np.asarray(posterior_mean) + (1.0 - weight) * np.asarray(prior_mean)


def load_posterior(path: Path) -> tuple[NDArray[np.floating], NDArray[np.floating]] | None:
    """Read a carried-forward posterior, or None when this is the first cycle."""
    if not path.is_file():
        return None
    blob = json.loads(path.read_text(encoding="utf-8"))
    return np.asarray(blob["beta_mean"]), np.asarray(blob["beta_sd"])


def save_posterior(path: Path, result: PulseResult, cycle_ts: datetime) -> None:
    """Persist the posterior so the next cycle - or the next boot - starts where this ended."""
    path.write_text(
        json.dumps(
            {
                "cycle_ts": cycle_ts.isoformat(),
                "beta_mean": [round(float(v), 5) for v in result.beta_mean],
                "beta_sd": [round(float(v), 5) for v in result.beta_sd],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
