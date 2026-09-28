"""Which city a run was computed on, and which runs no longer match the city on disk.

A run id carries engine versions (``...-sky1.0-twin1.0-flash0.1-baked``) and nothing about the
city, and a re-bake overwrites a run in place under the same id. So after a city rebuild - the
coastline, a new drain graph, re-split streets - a run baked before it and one baked after look
identical from outside, and a stale run keeps being served with nothing to say so. ``run.json``
now carries a :func:`city_fingerprint` and the Twin's revision
(:data:`varuna_cycle.twin_cycle.TWIN_REVISION`); :func:`stale_runs` lists the runs whose either
disagrees with the city and the code as they are now.

**Content, not bytes.** Each digest hashes what the Twin reads rather than the file on disk: the
segment ids in order, a raster's decoded band with its dtype, shape and transform, a drain
table's ids and sea-coupling columns, ``condition.json``'s coast-wall fields. A GeoTIFF or a
parquet re-encoded by another library version, or a JSON written with other whitespace, is the
same city and gets the same digest; a rebuild that moves one sea cell does not.

Digests are the first 12 hex of a sha256 - 48 bits, a false match once in 2.8e14 comparisons,
which is plenty for telling a handful of builds apart. Each file is read once per call and the
content digest is computed from those same bytes, so a file rewritten mid-call cannot give one
part two versions. The content digest is cached on a sha256 of the raw bytes - not on size and
modification time, which a rewrite of the same size inside one clock tick leaves unchanged - so
a bake pays about 25 ms a cycle on Mumbai to confirm nothing moved, rather than about 200 ms to
decode every file again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("varuna.cycle.provenance")

__all__ = [
    "COAST_WALL_PREFIXES",
    "FINGERPRINT_FILES",
    "StaleRun",
    "city_fingerprint",
    "fingerprint_diff",
    "stale_runs",
]

DIGEST_HEX = 12
"""Hex characters kept from each sha256."""

FINGERPRINT_FILES: dict[str, str] = {
    "segments": "segments.parquet",
    "sea_mask": "sea_mask.tif",
    "intertidal_mask": "intertidal_mask.tif",
    "drain_nodes": "drain_nodes.parquet",
    "drain_edges": "drain_edges.parquet",
    "coast_wall": "condition.json",
    "dem_conditioned": "dem_conditioned.tif",
}
"""Each part of the fingerprint and the file under ``city/<city>/`` it is taken from.

``dem_conditioned`` is not in the task that asked for the rest: the flattened-land repair and
the wall raise both land in the DEM, and none of the other parts would see a change to either."""

SEGMENT_COLUMNS = ("segment_id",)
NODE_COLUMNS = (
    "node_id",
    "cell_row",
    "cell_col",
    "is_outfall",
    "boundary_type",
    "tidal",
    "flap_gate",
)
"""The node ids and the columns that decide how a node meets the sea: where it sits on the grid
and whether it is an outfall, a tidal one, and gated. A column a build does not write is skipped,
and the names hashed say which were present."""
EDGE_COLUMNS = ("edge_id", "from_node", "to_node")

COAST_WALL_PREFIXES = ("coast_wall", "shore_ring", "intertidal")
"""``condition.json`` keys the coast wall writes: its level and rule, the shore ring, the cells
raised and by how much, the basins it made, and the intertidal cells it stands behind."""

_CACHE: dict[tuple[str, str], str | None] = {}
"""Content digest per part, keyed on the part and a sha256 of the file's raw bytes."""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:DIGEST_HEX]


def _table_digest(raw: bytes, columns: tuple[str, ...]) -> str | None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table_file = pq.ParquetFile(pa.BufferReader(raw))
    present = [c for c in columns if c in table_file.schema_arrow.names]
    if not present:
        return None
    table = table_file.read(columns=present)
    sha = hashlib.sha256()
    for name in present:
        sha.update(name.encode("utf-8") + b"\x00")
        values = table.column(name).to_pylist()
        sha.update(json.dumps(values, separators=(",", ":"), default=str).encode("utf-8"))
        sha.update(b"\x01")
    return sha.hexdigest()[:DIGEST_HEX]


def _raster_digest(raw: bytes) -> str:
    import numpy as np
    from rasterio.io import MemoryFile

    with MemoryFile(raw) as memory, memory.open() as src:
        band = np.ascontiguousarray(src.read(1))
        transform = tuple(float(v) for v in tuple(src.transform)[:6])
    head = json.dumps(
        {"dtype": band.dtype.str, "shape": list(band.shape), "transform": transform},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(head.encode("utf-8") + b"\x00" + band.tobytes())


def _coast_wall_digest(raw: bytes) -> str | None:
    fields = json.loads(raw.decode("utf-8"))
    wall = {k: v for k, v in fields.items() if k.startswith(COAST_WALL_PREFIXES)}
    if not wall:
        return None
    return _digest(json.dumps(wall, sort_keys=True, separators=(",", ":")).encode("utf-8"))


_READERS: dict[str, Callable[[bytes], str | None]] = {
    "segments": lambda raw: _table_digest(raw, SEGMENT_COLUMNS),
    "sea_mask": _raster_digest,
    "intertidal_mask": _raster_digest,
    "drain_nodes": lambda raw: _table_digest(raw, NODE_COLUMNS),
    "drain_edges": lambda raw: _table_digest(raw, EDGE_COLUMNS),
    "coast_wall": _coast_wall_digest,
    "dem_conditioned": _raster_digest,
}


def _part(root: Path, part: str) -> str | None:
    path = root / FINGERPRINT_FILES[part]
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    key = (part, hashlib.sha256(raw).hexdigest())
    if key not in _CACHE:
        _CACHE[key] = _READERS[part](raw)
    return _CACHE[key]


def city_fingerprint(root: Path | str) -> dict[str, str | None]:
    """A digest of each city file a run reads, keyed as :data:`FINGERPRINT_FILES`.

    ``root`` is ``city/<city>/``. A part is ``None`` when its file is absent - a city built
    before the sea step has no ``sea_mask.tif``, and one built before the intertidal step no
    ``intertidal_mask.tif`` - or carries nothing to hash (a ``condition.json`` with no coast
    wall). Deterministic: the same files give the same dict in any process.
    """
    root = Path(root)
    return {part: _part(root, part) for part in FINGERPRINT_FILES}


def fingerprint_diff(
    recorded: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> tuple[str, ...]:
    """The parts on which a run's recorded fingerprint and the city's current one disagree.

    ``recorded`` is ``None`` for a run baked before ``run.json`` carried one, which is reported
    as its own reason rather than as every part differing.
    """
    if recorded is None:
        return ("no city fingerprint: baked before run.json carried one",)
    reasons = []
    for part in sorted(set(recorded) | set(current)):
        was, now = recorded.get(part), current.get(part)
        if was != now:
            reasons.append(f"{part}: {was or 'absent'} -> {now or 'absent'}")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class StaleRun:
    """A run that does not match the city or the Twin as they are now, and why."""

    run_id: str
    run_dir: Path
    reasons: tuple[str, ...]


def stale_runs(
    city: str = "mumbai",
    *,
    runs_dir: Path | str | None = None,
    city_root: Path | str | None = None,
    twin_revision: str | None = None,
) -> list[StaleRun]:
    """Every run of ``city`` whose fingerprint or Twin revision differs from the current ones.

    For the lead after a city rebuild: the answer is the list of runs to re-bake. Reads each
    ``runs_dir/<run_id>/run.json`` as plain JSON, so a run the registry would refuse to serve is
    still reported, and sorted by run id so two calls print the same list.

    Args:
        city: the city slug the runs are filtered on (``run.json``'s ``city``).
        runs_dir: where the runs are; defaults to ``data/runs``. Pass ``demo/runs`` to audit
            the shipped copies the API seeds from.
        city_root: the city folder to compare against; defaults to ``city/<city>/``.
        twin_revision: the revision to compare against; defaults to
            :data:`varuna_cycle.twin_cycle.TWIN_REVISION`.
    """
    from varuna_schemas.paths import city_dir
    from varuna_schemas.paths import runs_dir as default_runs_dir

    root = Path(runs_dir) if runs_dir is not None else default_runs_dir()
    if twin_revision is None:
        from varuna_cycle.twin_cycle import TWIN_REVISION

        twin_revision = TWIN_REVISION
    current = city_fingerprint(city_root if city_root is not None else city_dir(city))
    wanted = city.strip().lower()
    stale: list[StaleRun] = []
    if not root.is_dir():
        return stale
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        run_json = child / "run.json"
        if child.name.startswith(".") or not run_json.is_file():
            continue
        try:
            meta = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("provenance.unreadable_run", run=child.name, error=str(error)[:200])
            continue
        if str(meta.get("city", "")).strip().lower() != wanted:
            continue
        reasons = list(fingerprint_diff(meta.get("city_fingerprint"), current))
        recorded_revision = meta.get("twin_revision")
        if recorded_revision != twin_revision:
            reasons.append(f"twin_revision: {recorded_revision or 'absent'} -> {twin_revision}")
        if reasons:
            stale.append(
                StaleRun(
                    run_id=str(meta.get("run_id", child.name)),
                    run_dir=child,
                    reasons=tuple(reasons),
                )
            )
    return stale
