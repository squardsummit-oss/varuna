"""Does what Pulse learned reach the streets? (SPEC.md 11.7, task P7.6)

Pulse's state is per pipe and Flash's index is per road segment, and the two id spaces share no
values, so the join is the only thing standing between "the city revealed its drains" and a
console drawing a flat prior. The claims tested here are the two that matter to a caller:

* a segment takes the blockage of the **worst** pipe at its inlets, not the mean and not the
  first one the merge happened to emit; and
* a segment with no inlet comes back **NaN**, so nothing can silently inherit the prior.

The tiny city is three segments and five pipes, small enough that the right answer can be read
off by hand. The real Mumbai graph is exercised at the end, where the only assertion is about
coverage and spread - the numbers there belong to the run, not to this file.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from varuna_pulse.join import CACHE_NAME, SegmentBetaError, segment_beta, segment_edges

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

LINKS = pd.DataFrame(
    {
        # S-001 has two inlets, so it sees three pipes; S-002 has one; S-003 has none at all.
        "node_id": ["N-1", "N-2", "N-3", "N-9"],
        "segment_id": ["S-001", "S-001", "S-002", None],
    }
)

EDGES = pd.DataFrame(
    {
        "edge_id": ["E-1", "E-2", "E-3", "E-4", "E-5"],
        "from_node": ["N-1", "N-2", "N-3", "N-9", "N-1"],
        "to_node": ["N-2", "N-7", "N-7", "N-3", "N-8"],
        "beta_mean": [0.20, 0.68, 0.31, 0.90, 0.50],
        "beta_sd": [0.14, 0.05, 0.11, 0.02, 0.09],
    }
)
"""S-001's inlets N-1 and N-2 touch E-1, E-2 and E-5, of which E-2 is the worst. E-4 reaches N-3
from the other end, so S-002's worst is E-4 rather than its own E-3 - a pipe that backs up *into*
a segment's inlet floods it too."""


@pytest.fixture
def city(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A three-segment city with a drain graph, wired in through ``VARUNA_CITY_DIR``."""
    root = tmp_path / "city"
    city_root = root / "toytown"
    (city_root / "graph").mkdir(parents=True)
    LINKS.to_parquet(city_root / "graph" / "inlet_links.parquet", index=False)
    EDGES.to_parquet(city_root / "drain_edges.parquet", index=False)
    pd.DataFrame({"segment_id": ["S-001", "S-002", "S-003"]}).to_parquet(
        city_root / "segments.parquet", index=False
    )
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    return city_root


def test_segment_takes_its_worst_pipe(city: Path) -> None:
    """S-001's inlets touch three pipes; the one that has lost the most capacity wins."""
    beta, sd = segment_beta("toytown")

    assert beta[0] == pytest.approx(0.68)  # E-2, not the 0.20 and 0.50 beside it
    assert sd[0] == pytest.approx(0.05)
    assert beta[1] == pytest.approx(0.90)  # E-4, incident to N-3 as its from_node
    assert sd[1] == pytest.approx(0.02)


def test_a_segment_without_an_inlet_is_nan(city: Path) -> None:
    """S-003 drains nowhere we know of, and says so rather than inheriting the prior."""
    beta, sd = segment_beta("toytown")

    assert np.isnan(beta[2])
    assert np.isnan(sd[2])
    assert np.isfinite(beta).sum() == 2


def test_unknown_segment_ids_come_back_nan(city: Path) -> None:
    """A caller's own segment order is honoured, including ids this city has never heard of."""
    beta, _ = segment_beta("toytown", segment_ids=["S-002", "S-404", "S-001"])

    assert beta[0] == pytest.approx(0.90)
    assert np.isnan(beta[1])
    assert beta[2] == pytest.approx(0.68)


def test_posterior_frame_overrides_the_prior(city: Path) -> None:
    """The point of the join: pass what Pulse learned and the segments move with it."""
    posterior = EDGES.assign(beta_mean=[0.20, 0.22, 0.31, 0.25, 0.50], beta_sd=0.03)

    beta, sd = segment_beta("toytown", posterior)

    assert beta[0] == pytest.approx(0.50)  # E-5 is now the worst pipe S-001 can see
    assert beta[1] == pytest.approx(0.31)  # and E-3 the worst of S-002's two
    assert sd[0] == pytest.approx(0.03)


def test_incidence_is_cached_and_invalidated_by_mtime(city: Path) -> None:
    """Built once, then reused - unless a source table has been rewritten since."""
    cache = city / "graph" / CACHE_NAME
    first = segment_edges("toytown")
    assert cache.is_file()
    assert len(first) == 5  # S-001 x 3 pipes, S-002 x 2; the null-segment link is dropped

    # A rewritten graph must not be read through a stale cache: bump the mtime and change the
    # table under it, then ask again.
    trimmed = EDGES[EDGES.edge_id != "E-2"]
    edges_path = city / "drain_edges.parquet"
    trimmed.to_parquet(edges_path, index=False)
    stat = edges_path.stat()
    os.utime(edges_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    beta, _ = segment_beta("toytown")
    assert beta[0] == pytest.approx(0.50)  # E-2 is gone, so E-5 is S-001's worst


def test_a_city_without_a_graph_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never a silent empty join: the error names the target that would build the graph."""
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))

    with pytest.raises(SegmentBetaError, match="make city CITY=ghosttown"):
        segment_beta("ghosttown")


def test_mumbai_resolves_most_segments_with_a_real_spread() -> None:
    """The join has to reach most of the city, or the products it feeds are mostly prior.

    Skipped when Mumbai has not been built, because `make city` is a ten-minute step and this
    file is part of `make test`.
    """
    from varuna_schemas.paths import city_dir

    if not (city_dir("mumbai") / "graph" / "inlet_links.parquet").is_file():
        pytest.skip("city/mumbai has not been built")

    beta, sd = segment_beta("mumbai")
    resolved = int(np.isfinite(beta).sum())

    assert resolved > beta.size // 2
    # The defect this join exists to remove is a constant: assert the result is not one.
    assert float(np.nanmax(beta)) > float(np.nanmin(beta))
    assert np.isfinite(sd[np.isfinite(beta)]).all()
