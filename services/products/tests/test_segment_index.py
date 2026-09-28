"""The segment index is keyed on the street table it was built from (SPEC.md rule 6, 11.8).

``city/<city>/segment_cells.npz`` used to be loaded whenever it existed. Mumbai's index and every
baked run kept one set of segment ids while ``segments.parquet`` was rebuilt with another, and
the products joined names, points and exposure on the wrong street with no error. A stale index
is refused on read, never rewritten: rewriting it from the table put the stale 12 Sep ids into
Mumbai's index on 2026-09-27 and Flash refused every Mumbai cycle. These tests build a
three-street city in a temporary directory; nothing here reads or writes ``city/``.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString
from varuna_products import depth as D

CRS = "EPSG:32643"
RES = 30.0
X0, Y0 = 270_000.0, 2_105_000.0
TRANSFORM = tuple(Affine(RES, 0.0, X0, 0.0, -RES, Y0))[:6]
SHAPE = (20, 20)


def _streets(ids: list[str], *, reverse: bool = False) -> gpd.GeoDataFrame:
    lines = [
        LineString([(X0 + 45.0, Y0 - 45.0), (X0 + 300.0, Y0 - 45.0)]),
        LineString([(X0 + 45.0, Y0 - 200.0), (X0 + 45.0, Y0 - 500.0)]),
        LineString([(X0 + 400.0, Y0 - 400.0), (X0 + 550.0, Y0 - 550.0)]),
    ]
    frame = gpd.GeoDataFrame({"segment_id": ids}, geometry=lines, crs=CRS)
    return frame.iloc[::-1].reset_index(drop=True) if reverse else frame


def _index(root: Path) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    return D.segment_cell_index(root, TRANSFORM, SHAPE, CRS)


def test_the_index_records_the_digest_of_the_table_it_was_built_from(tmp_path) -> None:
    _streets(["S1-000", "S2-000", "S3-000"]).to_parquet(tmp_path / "segments.parquet")
    ids, offsets, cells = _index(tmp_path)

    assert ids == ("S1-000", "S2-000", "S3-000")
    assert offsets.size == 4 and cells.size == offsets[-1] > 0
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["segments_sha256"]) == D.segment_ids_digest(ids)
    # No partial file is left beside the index.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["segment_cells.npz", "segments.parquet"]


def test_a_matching_index_is_used_without_rebuilding(tmp_path, monkeypatch) -> None:
    _streets(["S1-000", "S2-000", "S3-000"]).to_parquet(tmp_path / "segments.parquet")
    first = _index(tmp_path)
    before = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()

    def refuse(*_args, **_kwargs):
        raise AssertionError("a fresh index must not be rebuilt")

    monkeypatch.setattr(D, "build_segment_cell_index", refuse)
    second = _index(tmp_path)

    assert second[0] == first[0]
    assert np.array_equal(second[1], first[1]) and np.array_equal(second[2], first[2])
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("ids", "reverse"),
    [
        (["S0-000", "S0-001", "S3-000"], False),  # a rebuild renamed two streets
        (["S1-000", "S2-000", "S3-000"], True),  # same ids, different order
    ],
)
def test_an_index_from_another_street_table_is_refused_and_left_alone(
    tmp_path, ids, reverse
) -> None:
    _streets(["S1-000", "S2-000", "S3-000"]).to_parquet(tmp_path / "segments.parquet")
    _index(tmp_path)
    before = (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes()

    table = _streets(ids, reverse=reverse)
    table.to_parquet(tmp_path / "segments.parquet")
    with pytest.raises(D.StaleSegmentIndex, match="make city") as refused:
        _index(tmp_path)

    # Both digests are named, so the message says which two things disagree.
    assert D.segment_ids_digest(("S1-000", "S2-000", "S3-000"))[:12] in str(refused.value)
    assert D.segment_ids_digest(tuple(table["segment_id"]))[:12] in str(refused.value)
    # Either side can be the stale one, so the index on disk is exactly what it was.
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == before


def test_a_removed_index_is_built_from_the_new_table(tmp_path) -> None:
    """What the city build does after rewriting the table: remove the index, and the next read
    builds it - exactly a fresh index for the new table, digest included."""
    _streets(["S1-000", "S2-000", "S3-000"]).to_parquet(tmp_path / "segments.parquet")
    _index(tmp_path)
    table = _streets(["S0-000", "S0-001", "S3-000"])
    table.to_parquet(tmp_path / "segments.parquet")
    (tmp_path / D.SEGMENT_CELLS_FILE).unlink()

    rebuilt = _index(tmp_path)

    assert rebuilt[0] == tuple(table["segment_id"])
    fresh = D.build_segment_cell_index(table, TRANSFORM, SHAPE)
    assert np.array_equal(rebuilt[1], fresh[1]) and np.array_equal(rebuilt[2], fresh[2])
    with np.load(tmp_path / D.SEGMENT_CELLS_FILE, allow_pickle=True) as data:
        assert str(data["segments_sha256"]) == D.segment_ids_digest(rebuilt[0])


def _legacy(root: Path, ids: tuple[str, ...]) -> bytes:
    """An index written before the digest existed: ids, offsets and cells only."""
    _, offsets, cells = D.build_segment_cell_index(_streets(list(ids)), TRANSFORM, SHAPE)
    np.savez_compressed(
        root / D.SEGMENT_CELLS_FILE,
        segment_ids=np.array(ids, dtype=object),
        offsets=offsets,
        cells=cells,
    )
    return (root / D.SEGMENT_CELLS_FILE).read_bytes()


def test_an_older_index_that_matches_is_used_and_left_alone(tmp_path) -> None:
    """Mumbai's and Chennai's shipped indexes carry no digest; a matching one is not rewritten."""
    ids = ("S1-000", "S2-000", "S3-000")
    _streets(list(ids)).to_parquet(tmp_path / "segments.parquet")
    before = _legacy(tmp_path, ids)

    assert _index(tmp_path)[0] == ids
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == before


def test_an_older_index_that_does_not_match_is_refused(tmp_path) -> None:
    """Mumbai's case on 2026-09-27: a 10 Sep index with no digest beside a 12 Sep table."""
    before = _legacy(tmp_path, ("S1-000", "S2-000", "S3-000"))
    _streets(["S1-000", "S0-000", "S3-000"]).to_parquet(tmp_path / "segments.parquet")

    with pytest.raises(D.StaleSegmentIndex):
        _index(tmp_path)
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == before


def test_an_index_with_no_street_table_beside_it_is_trusted(tmp_path) -> None:
    ids = ("S1-000", "S2-000", "S3-000")
    before = _legacy(tmp_path, ids)

    assert D.segment_cell_index(tmp_path, None, None, None)[0] == ids
    assert (tmp_path / D.SEGMENT_CELLS_FILE).read_bytes() == before


def test_the_digest_depends_on_order_and_on_every_id() -> None:
    assert D.segment_ids_digest(["a", "b"]) != D.segment_ids_digest(["b", "a"])
    assert D.segment_ids_digest(["ab"]) != D.segment_ids_digest(["a", "b"])
    assert D.segment_ids_digest(("a", "b")) == D.segment_ids_digest(np.array(["a", "b"]))
