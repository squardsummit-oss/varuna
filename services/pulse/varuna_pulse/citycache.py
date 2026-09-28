"""The city joins Pulse needs every cycle, built once per city build (SPEC.md 11.6, task P7.1).

A Pulse stage reads the same static city tables every cycle: every drain pipe's line in lon/lat
for the drain-health map, the street each pipe runs under, and each pipe's contributing area for
the observation operator. None of them changes between cycles - they change when ``make city``
rewrites the files - yet rebuilding them was about 2 s of a stage whose budget is 3 s, most of it
converting 49,770 pipe geometries to rounded lon/lat lists.

**Keyed on the files, not on the process.** Each entry remembers the size and modification time of
every file it was built from, plus a digest of the in-memory inputs the caller passes (the edge
ids, the node order), and is rebuilt the moment any of them differs. A rebuilt city therefore
cannot be served a stale join, and neither can a test that hands in a different network.

**Built by the functions that always built them.** The cache stores what the caller's builder
returns and never computes anything itself, so a cached cycle writes exactly the bytes an uncached
one does (rule 8).

**One entry per join.** A bake is one process over every cycle and the API is one long-lived
process; either way only the latest city is worth holding, so a new key replaces the old entry
rather than growing a dict.

**Shared, so read-only.** Arrays come back with ``writeable=False`` and lists as fresh outer lists.
The inner coordinate lists of the geometry are shared between cycles; they are only ever
serialised, and anything that wants to edit one has to copy it.
"""

from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable, Iterable
    from pathlib import Path

log = structlog.get_logger("varuna.pulse.citycache")

__all__ = ["cached", "clear", "digest"]

_entries: dict[str, tuple[tuple[Any, ...], Any]] = {}
_lock = threading.Lock()


def digest(*parts: Any) -> str:
    """A stable digest of in-memory inputs: arrays by dtype, shape and bytes, anything else by repr.

    Used for the part of a key that is not a file - the network's edge order, its node order -
    because two networks can share a city directory and still differ.
    """
    h = hashlib.blake2b(digest_size=16)
    for part in parts:
        if isinstance(part, np.ndarray):
            array = np.ascontiguousarray(part)
            h.update(f"{array.dtype.str}{array.shape}".encode())
            h.update(array.tobytes())
        else:
            h.update(repr(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _fingerprint(sources: Iterable[Path]) -> tuple[Any, ...]:
    out: list[tuple[Any, ...]] = []
    for path in sources:
        try:
            stat = path.stat()
        except OSError:
            out.append((str(path), None))
        else:
            out.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def cached[T](name: str, sources: Iterable[Path], key: str, build: Callable[[], T]) -> T:
    """``build()``'s result, reused while ``sources`` and ``key`` are unchanged.

    Args:
        name: which join this is; one entry is held per name.
        sources: the files the join is read from. A missing file is part of the fingerprint too,
            so a join built without a file is rebuilt once the file appears.
        key: a digest of every in-memory input the builder reads (see :func:`digest`).
        build: the uncached builder, called on a miss.
    """
    full_key = (key, _fingerprint(sources))
    with _lock:
        hit = _entries.get(name)
        if hit is not None and hit[0] == full_key:
            return _share(hit[1])
    value = build()
    if isinstance(value, np.ndarray):
        value = value.copy()
        value.setflags(write=False)
    with _lock:
        _entries[name] = (full_key, value)
    log.debug("pulse.citycache.built", join=name)
    return _share(value)


def clear() -> None:
    """Forget every join; the next call to each rebuilds it."""
    with _lock:
        _entries.clear()


def _share[T](value: T) -> T:
    if isinstance(value, list):
        return list(value)  # type: ignore[return-value]
    return value
