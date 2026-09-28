"""The city build removes the products' segment index when it rewrites what the index is built
from (``segments.parquet``, ``sea_mask.tif``).

``varuna_products.depth.segment_cell_index`` refuses a stale index on read rather than rewriting
it, because a reader cannot tell which side is stale. The build can: it has just written the new
side. Everything here is a temporary folder; nothing reads or writes ``city/``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from varuna_city import pipeline as P


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(path=lambda name: root / name)


def test_the_index_is_removed_and_nothing_else(tmp_path: Path) -> None:
    (tmp_path / P.SEGMENT_INDEX_FILE).write_bytes(b"stale")
    (tmp_path / "segments.parquet").write_bytes(b"table")

    P._drop_segment_index(_ctx(tmp_path), reason="segments.parquet rewritten")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["segments.parquet"]


def test_no_index_is_not_an_error(tmp_path: Path) -> None:
    P._drop_segment_index(_ctx(tmp_path), reason="sea_mask.tif rewritten")
    assert list(tmp_path.iterdir()) == []


def test_the_name_is_the_one_the_products_read() -> None:
    from varuna_products.depth import SEGMENT_CELLS_FILE

    assert P.SEGMENT_INDEX_FILE == SEGMENT_CELLS_FILE
