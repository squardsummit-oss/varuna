"""Invariant tests of the drain gravity audit (varuna_city.gravity).

These pin what the audit *measures* on small hand-built graphs whose answers are worked out by
hand. They do not assert that any city's graph is gravity-consistent: that is the audit's output,
not its contract.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from affine import Affine
from shapely.geometry import Point
from varuna_city.gravity import DrainTree, audit_gravity, downstream_sill, summary_lines

RES = 30.0
TRANSFORM = Affine(RES, 0.0, 0.0, 0.0, -RES, 300.0)
"""A north-up 30 m grid whose top-left corner is (0, 300): cell (r, c) centres on
(15 + 30 c, 285 - 30 r)."""


def _nodes(rows: list[tuple[str, float, float, str | None, int, int]]) -> pd.DataFrame:
    """``(node_id, z_ground, z_invert, boundary_type, cell_row, cell_col)`` per node."""
    return pd.DataFrame(
        {
            "node_id": [r[0] for r in rows],
            "z_ground_m": [r[1] for r in rows],
            "z_invert_m": [r[2] for r in rows],
            "boundary_type": [r[3] for r in rows],
            "is_outfall": [r[3] is not None for r in rows],
            "tidal": [r[3] == "tide" for r in rows],
            "outfall_id": ["OUT-SEA" if r[3] == "tide" else None for r in rows],
            "flap_gate": [False for _ in rows],
            "cell_row": [r[4] for r in rows],
            "cell_col": [r[5] for r in rows],
        }
    )


def _edges(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """``(from_node, to_node, length_m, stored slope)`` per edge."""
    return pd.DataFrame(
        {
            "edge_id": [f"E{i}" for i in range(len(rows))],
            "from_node": [r[0] for r in rows],
            "to_node": [r[1] for r in rows],
            "length_m": [r[2] for r in rows],
            "slope": [r[3] for r in rows],
        }
    )


def _correct_chain() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A -> B -> C -> O on ground falling toward a tidal outfall, every pipe at 1 %."""
    nodes = _nodes(
        [
            ("A", 10.0, 8.5, None, 0, 0),
            ("B", 9.0, 7.5, None, 0, 1),
            ("C", 8.0, 6.5, None, 0, 2),
            ("O", 6.0, 5.5, "tide", 0, 3),
        ]
    )
    edges = _edges([("A", "B", 100.0, 0.01), ("B", "C", 100.0, 0.01), ("C", "O", 100.0, 0.01)])
    return nodes, edges


def test_a_graded_graph_has_nothing_to_report() -> None:
    nodes, edges = _correct_chain()
    audit = audit_gravity(nodes, edges)
    assert audit["adverse"]["edges"] == 0
    assert audit["adverse"]["rise_m"] is None
    assert audit["under_min_slope"]["edges"] == 0
    assert audit["slope_field"]["disagreeing_edges"] == 0
    assert audit["sill"]["blocked_nodes"] == 0
    assert audit["sill"]["excess_m"] is None
    assert audit["inverts_above_ground"] == 0
    assert audit["tree"] == {"roots": 1, "roots_not_outfall": 0, "nodes_unreached": 0}
    tide = audit["outfalls"]["by_boundary_type"]["tide"]
    assert tide["outfalls"] == 1
    assert tide["nodes_served"] == 4


def test_an_adverse_edge_is_counted_with_its_rise_and_length() -> None:
    nodes, edges = _correct_chain()
    # B's invert drops 3 m below C's: A -> B falls 4 m, B -> C now climbs 2 m.
    nodes.loc[nodes["node_id"] == "B", "z_invert_m"] = 4.5
    audit = audit_gravity(nodes, edges)
    assert audit["adverse"]["edges"] == 1
    assert audit["adverse"]["share"] == pytest.approx(1 / 3, abs=1e-4)
    assert audit["adverse"]["length_km"] == pytest.approx(0.1)
    assert audit["adverse"]["rise_m"]["max"] == pytest.approx(2.0)
    # an adverse edge is also under the minimum slope
    assert audit["under_min_slope"]["edges"] == 1


def test_a_flat_edge_is_under_the_minimum_slope_but_not_adverse() -> None:
    nodes, edges = _correct_chain()
    nodes.loc[nodes["node_id"] == "B", "z_invert_m"] = 6.6  # B -> C falls 0.1 m over 100 m
    audit = audit_gravity(nodes, edges)
    assert audit["adverse"]["edges"] == 0
    assert audit["under_min_slope"]["edges"] == 1
    assert audit["under_min_slope"]["length_km"] == pytest.approx(0.1)


def test_a_sill_above_ground_blocks_every_node_behind_it_and_no_other() -> None:
    #   A (ground 5.0) -> B (ground 4.0) -> H (ground 9.0, invert 7.0) -> O
    # H's invert stands 2.0 m above A's ground and 3.0 m above B's. H itself and O are clear.
    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("B", 4.0, 2.5, None, 0, 1),
            ("H", 9.0, 7.0, None, 0, 2),
            ("O", 3.0, 1.0, "free", 0, 3),
        ]
    )
    edges = _edges([("A", "B", 100.0, 0.01), ("B", "H", 100.0, 0.003), ("H", "O", 100.0, 0.06)])
    audit = audit_gravity(nodes, edges)
    assert audit["sill"]["blocked_nodes"] == 2
    assert audit["sill"]["share"] == 0.5
    assert audit["sill"]["excess_m"]["min"] == pytest.approx(2.0)
    assert audit["sill"]["excess_m"]["max"] == pytest.approx(3.0)
    # the two pipes that start at a blocked node
    assert audit["sill"]["pipe_km_from_blocked_nodes"] == pytest.approx(0.2)

    tree = DrainTree(nodes, edges)
    sill = downstream_sill(tree, nodes["z_invert_m"].to_numpy())
    assert list(sill) == [7.0, 7.0, 1.0, float("-inf")]


def test_a_sill_on_a_side_branch_does_not_block_its_neighbours() -> None:
    # Two branches join at J. Only branch X passes over a high invert (S).
    nodes = _nodes(
        [
            ("X", 6.0, 4.5, None, 0, 0),
            ("S", 9.0, 7.5, None, 0, 1),
            ("Y", 6.0, 4.5, None, 1, 0),
            ("J", 5.0, 3.5, None, 1, 1),
            ("O", 2.0, 0.5, "river", 1, 2),
        ]
    )
    edges = _edges(
        [
            ("X", "S", 50.0, 0.003),
            ("S", "J", 50.0, 0.08),
            ("Y", "J", 50.0, 0.02),
            ("J", "O", 50.0, 0.06),
        ]
    )
    audit = audit_gravity(nodes, edges)
    assert audit["sill"]["blocked_nodes"] == 1  # X only; Y joins below the sill
    assert audit["sill"]["excess_m"]["max"] == pytest.approx(1.5)


def test_a_slope_field_that_hides_an_adverse_bed_is_reported() -> None:
    # What drains.py writes: stored = max(min_slope, fall / length). The bed climbs 1 m but the
    # column says 0.3 %.
    nodes = _nodes([("A", 5.0, 3.5, None, 0, 0), ("O", 6.0, 4.5, "free", 0, 1)])
    edges = _edges([("A", "O", 100.0, 0.003)])
    audit = audit_gravity(nodes, edges)
    field = audit["slope_field"]
    assert field["disagreeing_edges"] == 1
    assert field["stored_min"] == pytest.approx(0.003)
    assert field["adverse_edges_reported_adverse"] == 0
    assert audit["adverse"]["edges"] == 1


def test_a_signed_slope_field_that_matches_its_inverts_passes() -> None:
    nodes = _nodes([("A", 5.0, 3.5, None, 0, 0), ("O", 6.0, 4.5, "free", 0, 1)])
    edges = _edges([("A", "O", 100.0, -0.01)])
    field = audit_gravity(nodes, edges)["slope_field"]
    assert field["disagreeing_edges"] == 0
    assert field["adverse_edges_reported_adverse"] == 1


def test_millimetre_rounding_is_not_an_adverse_edge() -> None:
    nodes = _nodes([("A", 5.0, 3.5, None, 0, 0), ("O", 5.0, 3.5, "free", 0, 1)])
    edges = _edges([("A", "O", 100.0, 0.0)])
    audit = audit_gravity(nodes, edges)
    assert audit["adverse"]["edges"] == 0
    assert audit["sill"]["blocked_nodes"] == 0  # a sill level with ground is not above it


def test_hotspot_window_counts_blocked_nodes_in_five_by_five_cells() -> None:
    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 5, 5),  # blocked, in the window
            ("B", 4.0, 2.5, None, 7, 7),  # blocked, on the window's corner
            ("F", 4.0, 2.5, None, 8, 5),  # blocked, one cell outside
            ("H", 9.0, 7.0, None, 5, 6),  # the sill itself, in the window, not blocked
            ("O", 3.0, 1.0, "free", 20, 20),
        ]
    )
    edges = _edges(
        [
            ("A", "B", 90.0, 0.01),
            ("B", "H", 90.0, 0.003),
            ("F", "H", 90.0, 0.003),
            ("H", "O", 90.0, 0.06),
        ]
    )
    # A point inside cell (5, 5): x = 15 + 30*5, y = 285 - 30*5.
    hotspots = gpd.GeoDataFrame(
        {"hotspot_id": ["HS-1"], "name": ["Hindmata junction"], "sourced": [True]},
        geometry=[Point(165.0, 135.0)],
        crs="EPSG:32643",
    )
    audit = audit_gravity(nodes, edges, hotspots=hotspots, transform=TRANSFORM, crs="EPSG:32643")
    rows = audit["hotspots"]["rows"]
    assert audit["hotspots"]["window_cells"] == 5
    assert rows[0]["cell"] == [5, 5]
    assert rows[0]["nodes_in_window"] == 3
    assert rows[0]["sill_blocked"] == 2
    # A is 2.0 m under the sill and B 3.0 m; F is also 3.0 m but outside the window.
    assert rows[0]["max_excess_m"] == pytest.approx(3.0)
    assert audit["hotspots"]["with_blocked_nodes"] == 1


def test_outfall_inventory_splits_by_boundary_type_and_checks_the_tide() -> None:
    nodes = _nodes(
        [
            ("A", 4.0, 2.5, None, 0, 0),
            ("T", 1.5, -1.5, "tide", 0, 1),
            ("B", 9.0, 7.5, None, 1, 0),
            ("R", 8.0, 5.0, "river", 1, 1),
        ]
    )
    edges = _edges([("A", "T", 50.0, 0.08), ("B", "R", 50.0, 0.05)])
    audit = audit_gravity(nodes, edges, tide_max_m=3.9)
    inv = audit["outfalls"]["by_boundary_type"]
    assert inv["tide"]["nodes_served"] == 2
    assert inv["tide"]["invert_above_tide_max"] == 0
    assert inv["river"]["ground_above_5_m"] == 1
    assert inv["river"]["invert_above_tide_max"] == 1
    tidal = audit["outfalls"]["tidal"]
    assert tidal == [
        {
            "node_id": "T",
            "outfall_id": "OUT-SEA",
            "flap_gate": False,
            "z_ground_m": 1.5,
            "z_invert_m": -1.5,
            "nodes_served": 2,
            "invert_below_tide_max": True,
        }
    ]
    assert audit["outfalls"]["datum_note"]


def test_a_root_that_is_not_an_outfall_is_reported() -> None:
    nodes = _nodes([("A", 5.0, 3.5, None, 0, 0), ("B", 4.0, 2.5, None, 0, 1)])
    edges = _edges([("A", "B", 50.0, 0.02)])
    assert audit_gravity(nodes, edges)["tree"]["roots_not_outfall"] == 1


def test_a_cycle_leaves_nodes_unreached_rather_than_looping() -> None:
    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("B", 5.0, 3.5, None, 0, 1),
            ("O", 2.0, 0.5, "free", 0, 2),
        ]
    )
    edges = _edges([("A", "B", 50.0, 0.0), ("B", "A", 50.0, 0.0)])
    audit = audit_gravity(nodes, edges)
    assert audit["tree"]["nodes_unreached"] == 2
    assert audit["sill"]["blocked_nodes"] == 0


def test_a_node_with_two_downstream_edges_is_refused() -> None:
    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("O1", 2.0, 0.5, "free", 0, 1),
            ("O2", 2.0, 0.5, "free", 0, 2),
        ]
    )
    edges = _edges([("A", "O1", 50.0, 0.06), ("A", "O2", 50.0, 0.06)])
    with pytest.raises(ValueError, match="must be a tree"):
        audit_gravity(nodes, edges)


def test_an_edge_to_a_missing_node_is_refused() -> None:
    nodes, edges = _correct_chain()
    edges.loc[0, "to_node"] = "NOPE"
    with pytest.raises(ValueError, match="not in drain_nodes"):
        audit_gravity(nodes, edges)


def test_the_audit_does_not_modify_its_inputs() -> None:
    nodes, edges = _correct_chain()
    before_nodes, before_edges = nodes.copy(), edges.copy()
    audit_gravity(nodes, edges, tide_max_m=3.0)
    pd.testing.assert_frame_equal(nodes, before_nodes)
    pd.testing.assert_frame_equal(edges, before_edges)


def test_summary_lines_render_every_section() -> None:
    nodes, edges = _correct_chain()
    nodes.loc[nodes["node_id"] == "B", "z_invert_m"] = 4.5
    text = "\n".join(summary_lines(audit_gravity(nodes, edges, tide_max_m=3.0)))
    assert "adverse edges: 1" in text
    assert "sill-blocked nodes: 0" in text
    assert "tidal OUT-SEA -> O" in text


def test_report_section_puts_the_hydraulic_figure_next_to_topology() -> None:
    from types import SimpleNamespace

    from varuna_city.report import _gravity, _gravity_section

    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("B", 4.0, 2.5, None, 0, 1),
            ("H", 9.0, 7.0, None, 0, 2),
            ("O", 3.0, 1.0, "free", 0, 3),
        ]
    )
    edges = _edges([("A", "B", 100.0, 0.01), ("B", "H", 100.0, 0.003), ("H", "O", 100.0, 0.06)])
    config = SimpleNamespace(min_drain_slope=0.003)
    grid = SimpleNamespace(transform=TRANSFORM, crs="EPSG:32643")

    audit, error = _gravity(config, grid, nodes, edges, None)  # type: ignore[arg-type]
    assert error is None
    assert audit is not None
    text = "\n".join(_gravity_section(audit, error))
    assert "| Sill-blocked nodes | 2 of 4 (50.0 %)" in text
    # B (invert 2.5) -> H (invert 7.0) climbs 4.5 m: the sill is an adverse edge.
    assert "| Adverse edges (downstream invert above upstream) | 1 of 3" in text
    assert "| free | 1 | 4 |" in text


def test_report_section_says_why_when_there_is_no_graph() -> None:
    from types import SimpleNamespace

    from varuna_city.report import _gravity, _gravity_section

    grid = SimpleNamespace(transform=TRANSFORM, crs="EPSG:32643")
    audit, error = _gravity(SimpleNamespace(), grid, None, None, None)  # type: ignore[arg-type]
    assert audit is None
    assert _gravity_section(audit, error) == ["Not measured: no drain graph loaded."]


def test_connectivity_rows_put_the_sill_blocked_share_under_the_topological_row() -> None:
    from varuna_city.report import _connectivity_rows

    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("B", 4.0, 2.5, None, 0, 1),
            ("H", 9.0, 7.0, None, 0, 2),
            ("O", 3.0, 1.0, "free", 0, 3),
        ]
    )
    edges = _edges([("A", "B", 100.0, 0.01), ("B", "H", 100.0, 0.003), ("H", "O", 100.0, 0.06)])
    topological, hydraulic = _connectivity_rows(1.0, audit_gravity(nodes, edges), None)
    # Every node reaches O, so the topological row is met while half the nodes are blocked.
    assert "| 100.0 % topologically | met |" in topological
    assert "hydraulic connectivity" in hydraulic
    assert "| 50.0 % (2 of 4 nodes sill-blocked; see Drain gravity) | reported |" in hydraulic


def test_connectivity_rows_say_why_the_hydraulic_figure_is_missing() -> None:
    from varuna_city.report import _connectivity_rows

    _, hydraulic = _connectivity_rows(1.0, None, "no drain graph loaded")
    assert "| not measured (no drain graph loaded) | reported |" in hydraulic


def test_cli_audits_a_city_folder_and_compares_outfalls_with_the_tide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from typer.testing import CliRunner
    from varuna_city.cli import app

    nodes = _nodes(
        [
            ("A", 5.0, 3.5, None, 0, 0),
            ("B", 4.0, 2.5, None, 0, 1),
            ("H", 9.0, 7.0, None, 0, 2),
            ("O", 3.0, 1.0, "free", 0, 3),
        ]
    )
    edges = _edges([("A", "B", 100.0, 0.01), ("B", "H", 100.0, 0.003), ("H", "O", 100.0, 0.06)])
    city = tmp_path / "city" / "mumbai"
    city.mkdir(parents=True)
    nodes.to_parquet(city / "drain_nodes.parquet", index=False)
    edges.to_parquet(city / "drain_edges.parquet", index=False)
    bundle = tmp_path / "bundles" / "TEST-BUNDLE"
    bundle.mkdir(parents=True)
    pd.DataFrame({"ts": ["t0", "t1"], "stage_m": [0.2, 0.5], "source": ["x", "x"]}).to_csv(
        bundle / "tide.csv", index=False
    )
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path / "bundles"))

    out = tmp_path / "audit.json"
    result = CliRunner().invoke(
        app,
        ["audit-gravity", "--city", "mumbai", "--bundle", "TEST-BUNDLE", "--json", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "adverse edges: 1 (33.3 %)" in result.output
    assert "sill-blocked nodes: 2 (50.0 %)" in result.output
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["sill"]["blocked_nodes"] == 2
    assert written["outfalls"]["tide_max_m"] == pytest.approx(0.5)
    # O's invert (1.0 m) stands above the 0.5 m tide maximum.
    assert written["outfalls"]["by_boundary_type"]["free"]["invert_above_tide_max"] == 1
    # The audit is read-only: the city folder holds exactly what the test wrote.
    assert sorted(p.name for p in city.iterdir()) == ["drain_edges.parquet", "drain_nodes.parquet"]


def test_cli_names_the_command_that_builds_a_missing_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner
    from varuna_city.cli import app

    (tmp_path / "mumbai").mkdir()
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["audit-gravity", "--city", "mumbai"])
    assert result.exit_code == 1
    assert "make city CITY=mumbai" in result.output
