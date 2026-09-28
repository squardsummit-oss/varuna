"""The city joins Pulse memoises: served while the city is unchanged, rebuilt when it is not.

A cache that outlives the files it was built from would put last month's street names on this
month's drain map without an error anywhere, so the invalidation is what is tested, not the speed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np
import pytest
from varuna_pulse import citycache

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path


@pytest.fixture(autouse=True)
def _empty_cache():
    citycache.clear()
    yield
    citycache.clear()


def _counting(value):
    calls = []

    def build():
        calls.append(1)
        return value() if callable(value) else value

    return build, calls


def test_a_join_is_built_once_while_its_files_are_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "drain_edges.parquet"
    source.write_bytes(b"v1")
    build, calls = _counting(lambda: ["Dr Ambedkar Road", None, "LBS Marg"])

    first = citycache.cached("street_names", [source], "k", build)
    second = citycache.cached("street_names", [source], "k", build)

    assert first == second == ["Dr Ambedkar Road", None, "LBS Marg"]
    assert len(calls) == 1


def test_a_rewritten_file_rebuilds_the_join(tmp_path: Path) -> None:
    """`make city` rewriting the table is the case that matters: the join must follow it."""
    source = tmp_path / "drain_edges.parquet"
    source.write_bytes(b"v1")
    version = {"n": 1}
    build, calls = _counting(lambda: [f"build {version['n']}"])

    assert citycache.cached("streets", [source], "k", build) == ["build 1"]
    version["n"] = 2
    source.write_bytes(b"v2 is longer")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert citycache.cached("streets", [source], "k", build) == ["build 2"]
    assert len(calls) == 2


def test_a_different_network_or_a_new_file_is_a_miss(tmp_path: Path) -> None:
    present = tmp_path / "segments.parquet"
    missing = tmp_path / "not_built_yet.parquet"
    present.write_bytes(b"x")
    build, calls = _counting(lambda: [1])
    edges_a = citycache.digest(("MUM-E000001", "MUM-E000002"))
    edges_b = citycache.digest(("MUM-E000001", "MUM-E000003"))

    citycache.cached("area", [present, missing], edges_a, build)
    citycache.cached("area", [present, missing], edges_b, build)  # another network
    missing.write_bytes(b"now it exists")
    citycache.cached("area", [present, missing], edges_b, build)  # the file appeared

    assert len(calls) == 3


def test_the_digest_sees_array_contents_not_only_their_length() -> None:
    a = np.arange(10, dtype=np.int64)
    b = a.copy()
    b[7] = 99
    assert citycache.digest(a) == citycache.digest(a.copy())
    assert citycache.digest(a) != citycache.digest(b)
    assert citycache.digest(a) != citycache.digest(a.astype(np.int32))


def test_what_comes_back_cannot_corrupt_the_next_cycle(tmp_path: Path) -> None:
    """Arrays are read-only and lists are fresh outer lists, so a caller's edit stays its own."""
    source = tmp_path / "drain_edges.parquet"
    source.write_bytes(b"v1")

    area = citycache.cached("area", [source], "k", lambda: np.array([900.0, 12_600.0]))
    with pytest.raises(ValueError, match="read-only"):
        area[0] = 0.0

    names = citycache.cached("names", [source], "k", lambda: ["Hindmata", "Sion Circle"])
    names.append("appended by a caller")
    assert citycache.cached("names", [source], "k", lambda: []) == ["Hindmata", "Sion Circle"]
