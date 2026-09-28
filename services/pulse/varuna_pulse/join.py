"""Carry Pulse's posterior from drain edges onto road segments (SPEC.md 11.7, task P7.6).

Pulse learns on the drain graph: its state is one blockage per **pipe** (``MUM-Exxxxx``). Flash
and every product downstream of it are indexed by **road segment** (``Sxxxx-nnn``). The two id
spaces do not overlap by a single value, so without a join the learned state cannot reach the
thing the console draws - which is how ``/v1/whatif`` came to substitute a flat ``0.20`` for an
argument its own docstring calls "Pulse's posterior, joined onto segments".

The join is the one the inferred graph already implies. ``inlet_links.parquet`` says which inlet
node drains which segment (SPEC.md 10.1 step 7 puts an inlet every 40 m along a road), and a
node's incident pipes are the ones that carry that water away. So a segment's blockage is the
blockage of the pipes at its inlets.

**Worst pipe wins.** A segment with four incident pipes takes the one with the largest capacity
reduction, not the mean. Water backs up at the constriction: averaging a blocked pipe against
three clear ones would report a street as healthy because most of its drainage is fine, which is
exactly the failure the chronic-spot register is a list of. Capacity reduction rather than beta
itself because that is what blockage costs in flow (see :func:`varuna_pulse.health.
capacity_reduction_pct`); the two orders agree for a single pipe but not across diameters, and
capacity is the physical quantity.

**Join the full posterior, not the written one.** ``drain_health.geojson`` carries only the worst
:data:`varuna_pulse.health.MAX_WRITTEN_EDGES` pipes, so joining that file alone resolves 4,071 of
Mumbai's 21,296 segments where the city's own edge table resolves 16,983. A caller that has only
the written product should fall back to the prior for the rest - and say on screen that it did.

**A segment with no inlet comes back NaN, never the prior.** 4,313 of Mumbai's 21,296 segments
have no inlet link - service roads, footways, slivers between intersections - and handing those
a plausible-looking 0.20 would launder "we do not know" into "we measured 0.20" (rule 6). NaN
forces the caller to decide, and to say on screen which streets it decided for.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog
from varuna_schemas.paths import city_dir

from varuna_pulse.health import capacity_reduction_pct

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.pulse.join")

__all__ = [
    "CACHE_NAME",
    "SegmentBetaError",
    "segment_beta",
    "segment_edges",
]

CACHE_NAME = "segment_beta.parquet"
"""Where the segment-to-edge incidence is cached, under ``city/<city>/graph/``."""

_SOURCES_KEY = b"varuna.sources"
"""Parquet metadata key holding the source files' size and mtime, so the cache self-invalidates."""


class SegmentBetaError(RuntimeError):
    """Raised when a city has no drain graph to join against."""


def _sources(city: str) -> tuple[Path, Path]:
    """The two tables the join is built from: inlet links and drain edges."""
    root = city_dir(city)
    return root / "graph" / "inlet_links.parquet", root / "drain_edges.parquet"


def _fingerprint(paths: tuple[Path, ...]) -> str:
    """Size and mtime of every source, as the cache's freshness key."""
    return json.dumps(
        {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in paths},
        sort_keys=True,
    )


def _read_cache(path: Path, fingerprint: str) -> pd.DataFrame | None:
    """The cached incidence, or ``None`` when it is missing or stale."""
    if not path.is_file():
        return None
    try:
        table = pq.read_table(path)
    except (OSError, pa.ArrowInvalid):  # a half-written or corrupt cache is not an error
        return None
    stored = (table.schema.metadata or {}).get(_SOURCES_KEY)
    if stored is None or stored.decode("utf-8") != fingerprint:
        return None
    return table.to_pandas()


def _write_cache(path: Path, pairs: pd.DataFrame, fingerprint: str) -> None:
    """Write the incidence atomically, so a concurrent reader never sees a partial file."""
    table = pa.Table.from_pandas(pairs, preserve_index=False)
    table = table.replace_schema_metadata({_SOURCES_KEY: fingerprint.encode("utf-8")})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def segment_edges(city: str, *, rebuild: bool = False) -> pd.DataFrame:
    """Every ``(segment_id, edge_id)`` pair the inferred graph implies, cached on disk.

    A pair exists when the edge is incident - in either direction - to an inlet node that drains
    the segment. Both directions count: a pipe that runs *into* a segment's inlet node can back
    up into it as readily as the one that leaves.
    """
    links_path, edges_path = _sources(city)
    missing = [p for p in (links_path, edges_path) if not p.is_file()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        msg = f"No drain graph for {city!r}: {names} not found. Run `make city CITY={city}`."
        raise SegmentBetaError(msg)

    cache_path = links_path.parent / CACHE_NAME
    fingerprint = _fingerprint((links_path, edges_path))
    if not rebuild:
        cached = _read_cache(cache_path, fingerprint)
        if cached is not None:
            return cached

    links = pd.read_parquet(links_path, columns=["node_id", "segment_id"])
    edges = pd.read_parquet(edges_path, columns=["edge_id", "from_node", "to_node"])
    pairs = pd.concat(
        [
            links.merge(edges[["edge_id", end]], left_on="node_id", right_on=end)[
                ["segment_id", "edge_id"]
            ]
            for end in ("from_node", "to_node")
        ]
    )
    pairs = (
        pairs.dropna()
        .astype({"segment_id": "string", "edge_id": "string"})
        .drop_duplicates()
        # Sorted so the cache - and every tie-break read off it - is byte-identical run to
        # run (rule 8); pandas' merge order is not a contract.
        .sort_values(["segment_id", "edge_id"], kind="stable", ignore_index=True)
    )
    _write_cache(cache_path, pairs, fingerprint)
    log.info(
        "pulse.segment_edges.built",
        city=city,
        pairs=len(pairs),
        segments=int(pairs["segment_id"].nunique()),
    )
    return pairs


def _edge_table(city: str, edges: Any) -> pd.DataFrame:
    """Normalise the edge argument to a frame with ``edge_id``, ``beta_mean`` and ``beta_sd``."""
    if edges is None:
        edges = city_dir(city) / "drain_edges.parquet"
    if isinstance(edges, str | Path):
        path = Path(edges)
        if not path.is_file():
            msg = f"No drain edges at {path}. Run `make city CITY={city}`."
            raise SegmentBetaError(msg)
        frame = pd.read_parquet(path, columns=["edge_id", "beta_mean", "beta_sd"])
    else:
        frame = pd.DataFrame(edges).drop(columns="geometry", errors="ignore")

    wanted = {"edge_id", "beta_mean", "beta_sd"}
    if not wanted.issubset(frame.columns):
        missing = ", ".join(sorted(wanted - set(frame.columns)))
        msg = f"Edge table is missing {missing}; it must carry a posterior per edge_id."
        raise SegmentBetaError(msg)

    out = frame.loc[:, ["edge_id", "beta_mean", "beta_sd"]].copy()
    out["edge_id"] = out["edge_id"].astype("string")
    out["beta_mean"] = pd.to_numeric(out["beta_mean"], errors="coerce")
    out["beta_sd"] = pd.to_numeric(out["beta_sd"], errors="coerce")
    # Taken from the frame when the caller already has it (drain_health carries it), computed
    # otherwise, so the two never disagree about which pipe is worst.
    if "capacity_reduction_pct" in frame.columns:
        out["capacity_reduction_pct"] = pd.to_numeric(
            frame["capacity_reduction_pct"], errors="coerce"
        )
    else:
        out["capacity_reduction_pct"] = capacity_reduction_pct(
            out["beta_mean"].to_numpy(dtype=np.float64)
        )
    return out.dropna(subset=["beta_mean"])


def segment_beta(
    city: str,
    edges: Any = None,
    segment_ids: Sequence[str] | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per-segment blockage mean and spread, from the worst pipe at the segment's inlets.

    Args:
        city: city id, e.g. ``"mumbai"``.
        edges: the edge state to join. A path or ``None`` reads ``city/<city>/drain_edges.parquet``
            - the **prior**, which is what a city has before Pulse has seen a storm. Pass the
            posterior frame (``edge_id``, ``beta_mean``, ``beta_sd``) to join what was learned.
        segment_ids: the segment order to return, normally ``FlashModel.segment_ids``. Defaults
            to ``city/<city>/segments.parquet`` order.

    Returns:
        ``(beta_mean, beta_sd)``, both float arrays the length of ``segment_ids``, **NaN** where
        the segment has no inlet link and therefore no pipe to inherit a blockage from.
    """
    if segment_ids is None:
        segments_path = city_dir(city) / "segments.parquet"
        if not segments_path.is_file():
            msg = f"No segments at {segments_path}. Run `make city CITY={city}`."
            raise SegmentBetaError(msg)
        ids = pd.read_parquet(segments_path, columns=["segment_id"])["segment_id"]
    else:
        ids = pd.Series(list(segment_ids), dtype="object")
    index = pd.Index(ids.astype("string"), name="segment_id")

    pairs = segment_edges(city)
    joined = pairs.merge(_edge_table(city, edges), on="edge_id", how="inner")
    # Worst pipe first; edge_id breaks ties so the choice does not depend on row order.
    worst = joined.sort_values(
        ["segment_id", "capacity_reduction_pct", "edge_id"],
        ascending=[True, False, True],
        kind="stable",
    ).drop_duplicates("segment_id")
    worst = worst.set_index("segment_id").reindex(index)

    beta = worst["beta_mean"].to_numpy(dtype=np.float64)
    sd = worst["beta_sd"].to_numpy(dtype=np.float64)
    resolved = int(np.isfinite(beta).sum())
    log.info(
        "pulse.segment_beta",
        city=city,
        segments=int(beta.size),
        resolved=resolved,
        unresolved=int(beta.size - resolved),
        beta_min=round(float(np.nanmin(beta)), 4) if resolved else None,
        beta_max=round(float(np.nanmax(beta)), 4) if resolved else None,
    )
    return beta, sd
