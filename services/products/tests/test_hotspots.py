"""What the hotspot rail is allowed to claim (SPEC.md 11.8, rule 7)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from varuna_products.hotspots import IMPASSABLE_CM, rank_hotspots
from varuna_schemas.constants import IST

# A tiny synthetic AOI in UTM 43N, near enough to Mumbai that the register's degrees land in it.
CRS = "EPSG:32643"
RES = 30.0
LEFT = 300_000.0
TOP = 2_110_000.0
SHAPE = (40, 40)
TRANSFORM = (RES, 0.0, LEFT, 0.0, -RES, TOP)


def _lonlat(row: int, col: int) -> tuple[float, float]:
    """Centre of a cell, in degrees, so a register point can be aimed at a known cell."""
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return to_wgs.transform(LEFT + (col + 0.5) * RES, TOP - (row + 0.5) * RES)


def _register(tmp_path: Path, points: list[tuple[str, int, int]]) -> Path:
    features = []
    for name, row, col in points:
        lon, lat = _lonlat(row, col)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "hotspot_id": name.lower().replace(" ", "-"),
                    "name": name,
                    "slug": name.lower().replace(" ", "-"),
                    "sourced": True,
                    "source_url": f"https://example.invalid/{name}",
                },
            }
        )
    (tmp_path / "hotspots.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return tmp_path


def _times(n: int) -> tuple[datetime, ...]:
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    return tuple(t0 + timedelta(minutes=5 * k) for k in range(n))


def _rank(city: Path, depth: np.ndarray) -> list[dict]:
    return rank_hotspots(depth, _times(depth.shape[0]), city, TRANSFORM, CRS, "TEST-RUN")


def test_ranks_by_peak_depth_not_by_the_order_in_the_register(tmp_path: Path) -> None:
    """The deepest junction is rank 1 however the register happened to list it."""
    city = _register(tmp_path, [("Shallow", 10, 10), ("Deepest", 20, 20), ("Middling", 30, 30)])

    # Each junction floods over its whole neighbourhood, not in one cell: the 90th percentile
    # over 45 m is deliberately unmoved by a single wet pixel (see the sampling test below).
    depth = np.zeros((6, *SHAPE))
    depth[3, 8:13, 8:13] = 0.10
    depth[3, 18:23, 18:23] = 0.55
    depth[3, 28:33, 28:33] = 0.25

    ranked = _rank(city, depth)
    assert [h["name"] for h in ranked] == ["Deepest", "Middling", "Shallow"]
    assert [h["rank"] for h in ranked] == [1, 2, 3]


def test_every_entry_carries_its_source(tmp_path: Path) -> None:
    """Rule 7: a hotspot on screen claims this junction floods, so the claim stays traceable."""
    city = _register(tmp_path, [("Hindmata", 15, 15)])
    ranked = _rank(city, np.zeros((4, *SHAPE)))
    assert ranked[0]["source_url"] == "https://example.invalid/Hindmata"
    assert ranked[0]["sourced"] is True


def test_timing_comes_from_the_series_not_the_maximum_cell(tmp_path: Path) -> None:
    """Peak time, first impassable step and duration all read off one series."""
    city = _register(tmp_path, [("Andheri Subway", 20, 20)])

    depth = np.zeros((8, *SHAPE))
    # Rises past the car threshold at step 3 and stays there through step 5, peaking at 4.
    for step, metres in {3: 0.35, 4: 0.60, 5: 0.31}.items():
        depth[step, 19:22, 19:22] = metres

    ranked = _rank(city, depth)
    hotspot = ranked[0]
    assert hotspot["peak_depth_cm"] == pytest.approx(60.0, abs=0.5)
    assert hotspot["time_to_peak_min"] == 20  # step 4 of 5-minute steps
    assert hotspot["peak_ts"] == _times(8)[4].isoformat()
    assert hotspot["impassable_from_ts"] == _times(8)[3].isoformat()
    assert hotspot["minutes_impassable"] == 15  # three steps above 30 cm
    assert hotspot["p_impassable_at_peak"] == 1.0
    assert len(hotspot["depth_cm"]) == 8


def test_a_dry_run_leaves_every_hotspot_dry(tmp_path: Path) -> None:
    """No rain, no claims: nothing is impassable and nothing has a time to be impassable at."""
    city = _register(tmp_path, [("Sion Circle", 12, 12), ("Gandhi Market", 25, 25)])
    ranked = _rank(city, np.zeros((6, *SHAPE)))

    assert len(ranked) == 2
    assert all(h["peak_depth_cm"] == 0.0 for h in ranked)
    assert all(h["p_impassable_at_peak"] == 0.0 for h in ranked)
    assert all(h["impassable_from_ts"] is None for h in ranked)
    assert all(h["minutes_impassable"] == 0 for h in ranked)


def test_hotspots_outside_the_grid_are_left_out_not_zeroed(tmp_path: Path) -> None:
    """A registered spot north of the AOI is a real place the model says nothing about."""
    city = _register(tmp_path, [("Inside", 20, 20)])
    features = json.loads((city / "hotspots.geojson").read_text(encoding="utf-8"))
    features["features"].append(
        {
            "type": "Feature",
            # Well outside the synthetic grid.
            "geometry": {"type": "Point", "coordinates": [80.25, 13.0]},
            "properties": {"hotspot_id": "elsewhere", "name": "Elsewhere", "sourced": True},
        }
    )
    (city / "hotspots.geojson").write_text(json.dumps(features), encoding="utf-8")

    ranked = _rank(city, np.zeros((4, *SHAPE)))
    assert [h["name"] for h in ranked] == ["Inside"]


def test_the_neighbourhood_is_sampled_not_one_cell(tmp_path: Path) -> None:
    """A dip beside the register's marker still registers; a single wet cell does not dominate.

    The 90th percentile over the 45 m neighbourhood is the same rule the road segments use. One
    isolated wet cell is noise and is damped; a dip covering the junction is signal and is kept.
    """
    city = _register(tmp_path, [("Junction", 20, 20)])

    one_cell = np.zeros((2, *SHAPE))
    one_cell[1, 20, 20] = 1.00
    lone = _rank(city, one_cell)[0]["peak_depth_cm"]

    neighbourhood = np.zeros((2, *SHAPE))
    neighbourhood[1, 18:23, 18:23] = 0.40
    spread = _rank(city, neighbourhood)[0]["peak_depth_cm"]

    assert lone < IMPASSABLE_CM < spread


def test_no_register_is_an_empty_rail_not_a_crash(tmp_path: Path) -> None:
    """A city built without a hotspot register still produces a run."""
    assert _rank(tmp_path, np.zeros((3, *SHAPE))) == []
