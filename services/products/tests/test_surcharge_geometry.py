"""Reversed edges carry the city export's own line, oriented as the edge is defined (P6.6, M9).

The console animates a dash along each reversed edge in the direction the water actually moves,
so the line has to be the pipe the Drains layer draws, and its first vertex has to be the edge's
``from_node``. The network the solver runs on cannot supply that: it has no node coordinates, and
80-105 of the 500 stored reversed edges per demo cycle have both ends in one 30 m cell. These tests
pin the join by ``edge_id`` against ``map/drains.geojson``, its orientation, and that an edge the
export does not carry is reported rather than given an invented line (rule 6).
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from varuna_products.surcharge import (
    DRAINS_EXPORT,
    _load_drain_paths,
    attach_edge_paths,
    drain_paths,
    surcharge_product,
)
from varuna_twin.types import DrainNetwork

N_STEPS = 6
RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
TRANSFORM = (30.0, 0.0, 270000.0, 0.0, -30.0, 2105000.0)
CRS = "EPSG:32643"

# Four manholes: N0 -> N1 -> N2 (a tidal outfall), and N3 -> N1. Three edges.
NODES = ("MUM-N0", "MUM-N1", "MUM-N2", "MUM-N3")
EDGES = ("MUM-E0", "MUM-E1", "MUM-E2")
FROM = (0, 1, 3)
TO = (1, 2, 1)
LINES = {
    # E0 is written in the edge's own orientation, with a bend in the middle.
    "MUM-E0": ("MUM-N0", "MUM-N1", [[72.8400, 19.0100], [72.8403, 19.0102], [72.8406, 19.0104]]),
    # E1 is written the other way round, so the join has to turn it over.
    "MUM-E1": ("MUM-N2", "MUM-N1", [[72.8410, 19.0110], [72.8406, 19.0104]]),
    # E2 is deliberately missing from the export.
}


def _network() -> DrainNetwork:
    n, m = len(NODES), len(EDGES)
    ones_n, ones_m = np.ones(n), np.ones(m)
    return DrainNetwork(
        node_ids=NODES,
        z_ground=ones_n * 5.0,
        z_invert=ones_n * 3.5,
        storage_area=ones_n,
        inlet_length=ones_n,
        inlet_area=ones_n * 0.1,
        kappa=ones_n * 0.25,
        boundary=np.array([0, 0, 1, 0], dtype=np.int8),
        flap_gate=np.zeros(n, dtype=np.bool_),
        # Every node in the same 30 m cell: the cell-centre line would be zero length.
        cell_row=np.full(n, 10, dtype=np.int32),
        cell_col=np.full(n, 10, dtype=np.int32),
        edge_ids=EDGES,
        from_node=np.array(FROM, dtype=np.int32),
        to_node=np.array(TO, dtype=np.int32),
        length=ones_m * 40.0,
        area=ones_m * 0.28,
        hydraulic_radius=ones_m * 0.15,
        diameter=ones_m * 0.6,
        edge_manning_n=ones_m * 0.013,
        q_full=ones_m * 0.3,
        beta=ones_m * 0.2,
    )


def _write_export(root: Path, lines: dict[str, tuple[str, str, list[list[float]]]]) -> Path:
    features = [
        {
            "type": "Feature",
            "properties": {"edge_id": edge_id, "from_node": src, "to_node": dst},
            "geometry": {"type": "LineString", "coordinates": coords},
        }
        for edge_id, (src, dst, coords) in lines.items()
    ]
    path = root / DRAINS_EXPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), "utf-8")
    return root


def _flows() -> tuple[np.ndarray, np.ndarray]:
    """Every edge runs backwards for part of the run; nothing surcharges."""
    edge_flow = np.full((N_STEPS, len(EDGES)), 0.2)
    edge_flow[2:, 0] = -0.1
    edge_flow[1:, 1] = -0.9  # the sea holding the trunk shut
    edge_flow[4:, 2] = -0.05
    return np.zeros((N_STEPS, len(NODES))), edge_flow


def _product(city_root: Path | None) -> dict:
    q_surcharge, edge_flow = _flows()
    return surcharge_product(
        q_surcharge, edge_flow, _network(), TRANSFORM, CRS, RUN_ID, city_root=city_root
    )


def _length_m(path: list[list[float]]) -> float:
    total = 0.0
    for (lon0, lat0), (lon1, lat1) in itertools.pairwise(path):
        dx = math.radians(lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2.0))
        dy = math.radians(lat1 - lat0)
        total += 6_371_000.0 * math.hypot(dx, dy)
    return total


def _by_id(product: dict) -> dict[str, dict]:
    return {e["edge_id"]: e for e in product["reversed_edges"]}


def test_joined_edges_carry_the_export_line_in_the_edge_direction(tmp_path: Path) -> None:
    edges = _by_id(_product(_write_export(tmp_path, LINES)))

    assert edges["MUM-E0"]["path"] == LINES["MUM-E0"][2]
    # Written N2 -> N1 in the export, stored N1 -> N2 as the edge is defined.
    assert edges["MUM-E1"]["path"] == LINES["MUM-E1"][2][::-1]
    assert edges["MUM-E1"]["path"][0] == [72.8406, 19.0104]
    for edge_id in ("MUM-E0", "MUM-E1"):
        assert _length_m(edges[edge_id]["path"]) > 10.0, edge_id


def test_existing_fields_and_order_are_kept(tmp_path: Path) -> None:
    product = _product(_write_export(tmp_path, LINES))

    first = product["reversed_edges"][0]
    assert first["edge_id"] == "MUM-E1"  # tidal first, as before
    assert first["tidal"] is True
    assert first["steps"] == [1, 2, 3, 4, 5]
    assert first["min_q_m3s"] == -0.9
    assert product["n_reversed_edges"] == 3
    assert product["n_reversed_at_tidal_outfall"] == 1
    assert product["reversed_edge_geometry"] == DRAINS_EXPORT


def test_a_missing_id_gets_null_and_is_counted_in_the_notes(tmp_path: Path) -> None:
    product = _product(_write_export(tmp_path, LINES))

    assert _by_id(product)["MUM-E2"]["path"] is None
    assert product["n_reversed_stored_without_path"] == 1
    assert product["notes"] == [
        f"Of the 3 stored reversed edges, 1 has no line in {DRAINS_EXPORT}; "
        "those carry path null and are not drawn."
    ]


def test_a_line_joined_to_a_different_pipe_is_refused(tmp_path: Path) -> None:
    lines = {**LINES, "MUM-E2": ("MUM-N0", "MUM-N2", [[72.84, 19.01], [72.85, 19.02]])}
    product = _product(_write_export(tmp_path, lines))

    assert _by_id(product)["MUM-E2"]["path"] is None
    assert product["n_reversed_stored_without_path"] == 1
    assert "endpoints are not the edge's own nodes" in product["notes"][0]


def test_every_edge_joined_leaves_no_note(tmp_path: Path) -> None:
    lines = {**LINES, "MUM-E2": ("MUM-N3", "MUM-N1", [[72.8399, 19.0090], [72.8406, 19.0104]])}
    product = _product(_write_export(tmp_path, lines))

    assert product["n_reversed_stored_without_path"] == 0
    assert product["notes"] == []


def test_no_export_means_no_path_anywhere_and_says_so(tmp_path: Path) -> None:
    product = _product(tmp_path)

    assert all(e["path"] is None for e in product["reversed_edges"])
    assert product["n_reversed_stored_without_path"] == 3
    assert product["notes"][0].startswith(f"No {DRAINS_EXPORT} for this city")


def test_the_city_defaults_to_the_one_the_run_id_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_export(tmp_path / "mumbai", LINES)
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path))

    edges = _by_id(_product(None))

    assert edges["MUM-E0"]["path"] == LINES["MUM-E0"][2]


def test_the_product_is_byte_identical_across_calls(tmp_path: Path) -> None:
    root = _write_export(tmp_path, LINES)
    _load_drain_paths.cache_clear()

    cold = json.dumps(_product(root), indent=2)
    warm = json.dumps(_product(root), indent=2)

    assert cold == warm
    assert _load_drain_paths.cache_info().hits >= 1


def test_a_rebuilt_export_is_read_again(tmp_path: Path) -> None:
    root = _write_export(tmp_path, LINES)
    assert _by_id(_product(root))["MUM-E2"]["path"] is None

    lines = {**LINES, "MUM-E2": ("MUM-N3", "MUM-N1", [[72.8399, 19.0090], [72.8406, 19.0104]])}
    _write_export(root, lines)
    stamp = (root / DRAINS_EXPORT).stat()
    os.utime(root / DRAINS_EXPORT, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))

    assert _by_id(_product(root))["MUM-E2"]["path"] is not None


def _demo_runs() -> list[Path]:
    from varuna_schemas.paths import repo_root

    return sorted((repo_root() / "demo" / "runs").glob("MUM-*/node_surcharge.json"))


def test_every_stored_reversed_edge_of_the_demo_runs_joins_the_mumbai_export() -> None:
    """Against the real, read-only ``city/mumbai`` export and the committed demo cycles.

    The demo runs predate the ``path`` field, so the join is applied to their stored edges here;
    it is the same function the products writer calls on a new bake.
    """
    from varuna_schemas.paths import city_dir

    root = city_dir("mumbai")
    runs = _demo_runs()
    if not (root / DRAINS_EXPORT).is_file():
        pytest.skip("city/mumbai is not built; run `make city CITY=mumbai` first")
    if not runs:
        pytest.skip("no demo runs under demo/runs")

    lines = drain_paths(root)
    assert lines is not None
    joined = 0
    for record in runs:
        product = json.loads(record.read_text(encoding="utf-8"))
        edges = copy.deepcopy(product["reversed_edges"])
        missing, disagreeing = attach_edge_paths(edges, lines)

        assert (missing, disagreeing) == (0, 0), record.parent.name
        for edge in edges:
            assert len(edge["path"]) >= 2, edge["edge_id"]
            assert _length_m(edge["path"]) > 0.0, edge["edge_id"]
            src, dst, coords = lines[edge["edge_id"]]
            assert (src, dst) == (edge["from_node"], edge["to_node"])
            assert edge["path"] == [list(pt) for pt in coords]
        joined += len(edges)
    print(f"joined {joined} stored reversed edges across {len(runs)} demo runs, 0 without path")
    assert joined > 0
