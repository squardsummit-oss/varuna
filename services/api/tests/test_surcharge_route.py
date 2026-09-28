"""The surcharge route serves reversed-edge geometry as the products writer stored it (P6.6).

`/v1/nowcast/surcharge` returns the run's ``node_surcharge.json`` with the run's notes merged
in. The route declares no response model, so a new product field passes through; what this pins
is that it does, unchanged, and that the product's own notes about edges with no line are not
replaced by the run's.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastapi.testclient import TestClient

RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"


def _seed_run(data: Path) -> dict:
    run = data / "runs" / RUN_ID
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text("{}", encoding="utf-8")
    (run / "run.json").write_text(json.dumps({"notes": ["Inferred drain graph"]}), "utf-8")
    product = {
        "run_id": RUN_ID,
        "n_steps": 36,
        "n_reversed_edges": 2,
        "n_reversed_at_tidal_outfall": 1,
        "reversed_edge_geometry": "map/drains.geojson",
        "n_reversed_stored_without_path": 1,
        "nodes": [],
        "reversed_edges": [
            {
                "edge_id": "MUM-E026920",
                "from_node": "MUM-N026923",
                "to_node": "MUM-N026924",
                "tidal": True,
                "steps": [0, 1],
                "min_q_m3s": -1.0412,
                "path": [[72.816018, 19.024918], [72.815843, 19.025201]],
            },
            {
                "edge_id": "MUM-E999999",
                "from_node": "MUM-N1",
                "to_node": "MUM-N2",
                "tidal": False,
                "steps": [3],
                "min_q_m3s": -0.1,
                "path": None,
            },
        ],
        "notes": ["Of the 2 stored reversed edges, 1 has no line in map/drains.geojson."],
    }
    (run / "node_surcharge.json").write_text(json.dumps(product), encoding="utf-8")
    return product


def test_reversed_edge_paths_pass_through_unchanged(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    product = _seed_run(tmp_path)

    response = client.get("/v1/nowcast/surcharge", params={"run_id": RUN_ID})

    assert response.status_code == 200
    body = response.json()
    assert body["reversed_edges"] == product["reversed_edges"]
    assert body["n_reversed_stored_without_path"] == 1
    assert body["reversed_edge_geometry"] == "map/drains.geojson"
    assert body["notes"] == ["Inferred drain graph", *product["notes"]]
