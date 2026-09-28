"""Attribution is handed the city's sea, not only a dry street on it (the coastline, wave B).

The Twin keeps a node on a sea cell out of the exchange: no inlet capture and no surcharge.
``rank_hotspots`` freezes such a node's street depth at 0 m, which stops the inlet, and it must
also hand the mask to :func:`varuna_flash.whatif.attribute_pipes`, which stops the vent. The mask
is the sea alone: intertidal land behind the coast wall is land to the Twin, so a node there
keeps its inlet, its vent and the Twin's depth. Every city is a temporary folder; nothing here
reads or writes ``city/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

CRS = "EPSG:32643"
RES = 30.0
X0, Y0 = 270_000.0, 2_105_000.0
TRANSFORM = (RES, 0.0, X0, 0.0, -RES, Y0)
SHAPE = (20, 20)
SEA_COLS = 5
ROW = 10


def _write_mask(path: Path, codes: np.ndarray) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=codes.shape[0],
        width=codes.shape[1],
        count=1,
        dtype="uint8",
        crs=CRS,
        transform=Affine(*TRANSFORM),
    ) as dst:
        dst.write(codes.astype("uint8"), 1)


def _register(root: Path, row: int, col: int) -> None:
    from pyproj import Transformer

    x, y = X0 + (col + 0.5) * RES, Y0 - (row + 0.5) * RES
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(x, y)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "hotspot_id": "shore-junction",
            "name": "shore-junction",
            "slug": "shore-junction",
            "sourced": True,
            "source_url": "https://example.invalid/shore-junction",
        },
    }
    (root / "hotspots.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8"
    )


def _chain(cols: list[int]):
    """A drain line along row ``ROW`` through ``cols``, ending at a free outfall."""
    from varuna_twin.types import DrainNetwork

    n = len(cols)
    boundary = np.zeros(n, dtype=np.int8)
    boundary[-1] = 2
    return DrainNetwork(
        node_ids=tuple(f"N{i}" for i in range(n)),
        z_ground=np.full(n, 2.0),
        z_invert=np.full(n, 0.5),
        storage_area=np.full(n, 1.0),
        inlet_length=np.full(n, 0.6),
        inlet_area=np.full(n, 0.04),
        kappa=np.full(n, 0.25),
        boundary=boundary,
        flap_gate=np.zeros(n, dtype=bool),
        cell_row=np.full(n, ROW, dtype=np.int32),
        cell_col=np.asarray(cols, dtype=np.int32),
        edge_ids=tuple(f"E{i}" for i in range(n - 1)),
        from_node=np.arange(n - 1, dtype=np.int32),
        to_node=np.arange(1, n, dtype=np.int32),
        length=np.full(n - 1, 40.0),
        area=np.full(n - 1, np.pi * 0.3**2),
        hydraulic_radius=np.full(n - 1, 0.15),
        diameter=np.full(n - 1, 0.6),
        edge_manning_n=np.full(n - 1, 0.013),
        q_full=np.full(n - 1, 0.3),
        beta=np.full(n - 1, 0.15),
    )


def _handed_to_attribution(
    root: Path, monkeypatch: pytest.MonkeyPatch, network: Any
) -> dict[str, Any]:
    """What ``rank_hotspots`` handed ``attribute_pipes``, captured by a spy."""
    import varuna_flash.whatif as whatif
    from varuna_products.hotspots import rank_hotspots
    from varuna_schemas.constants import IST

    captured: dict[str, Any] = {}

    def spy(network: Any, surface_depth_m: Any, **kwargs: Any) -> Any:
        captured["surface"] = np.array(surface_depth_m)
        captured["node_on_sea"] = kwargs.get("node_on_sea", "not passed")
        return whatif.PipeAttributionResult(
            target=str(kwargs.get("target_label", "")),
            depth_before_cm=float(kwargs.get("depth_before_cm", 0.0)),
            rows=(),
            combined=None,
            reason="captured by the test",
            n_candidates=0,
            n_nodes=int(network.n_nodes),
            method="spy",
            ms=0.0,
        )

    monkeypatch.setattr(whatif, "attribute_pipes", spy)
    # `rank_hotspots` attributes only against the folder `city_dir` names.
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root.parent))
    depth = np.full((3, *SHAPE), 0.2)  # 20 cm on the land
    depth[:, :, :SEA_COLS] = 3.0  # three metres of sea
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    times = tuple(t0 + timedelta(minutes=5 * k) for k in range(depth.shape[0]))
    rank_hotspots(depth, times, root, TRANSFORM, CRS, "TEST-RUN", network=network)
    assert "surface" in captured, "attribution never ran for the shore junction"
    return captured


def test_a_node_on_the_sea_is_handed_to_attribution_as_sea(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, ROW, SEA_COLS + 2)
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[:, :SEA_COLS] = 1
    _write_mask(tmp_path / "sea_mask.tif", codes)
    # The junction, a street node on land, then the outfall spine's last node on the sea.
    network = _chain([SEA_COLS + 2, SEA_COLS, SEA_COLS - 1])

    got = _handed_to_attribution(tmp_path, monkeypatch, network)
    mask = got["node_on_sea"]
    assert isinstance(mask, np.ndarray)
    assert mask.shape == (network.n_nodes,)
    assert mask.tolist() == [False, False, True]
    assert np.all(got["surface"][:, 2] == 0.0)


def test_intertidal_land_is_land_to_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node on the mangroves behind the wall keeps the Twin's depth and is not handed as sea."""
    _register(tmp_path, ROW, SEA_COLS + 2)
    sea = np.zeros(SHAPE, dtype=np.uint8)
    sea[:, :SEA_COLS] = 1
    _write_mask(tmp_path / "sea_mask.tif", sea)
    intertidal = np.zeros(SHAPE, dtype=np.uint8)
    intertidal[:, SEA_COLS] = 1
    _write_mask(tmp_path / "intertidal_mask.tif", intertidal)
    network = _chain([SEA_COLS + 2, SEA_COLS, SEA_COLS - 1])

    got = _handed_to_attribution(tmp_path, monkeypatch, network)
    assert got["node_on_sea"].tolist() == [False, False, True]
    assert np.allclose(got["surface"][:, 1], 0.2), "an intertidal node reads the Twin's depth"


def test_a_city_without_a_sea_raster_hands_no_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, ROW, SEA_COLS + 2)
    network = _chain([SEA_COLS + 2, SEA_COLS, SEA_COLS - 1])
    got = _handed_to_attribution(tmp_path, monkeypatch, network)
    assert got["node_on_sea"] is None
    assert np.allclose(got["surface"][:, 2], 3.0)
