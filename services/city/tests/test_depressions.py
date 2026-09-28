"""Depression detection tests (task P1.5) - the hotspot candidates of the validation report."""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import Affine
from varuna_city.depressions import fill_depressions, find_depressions, label_pits

CRS = "EPSG:32643"
RES = 10.0


def transform_for(height: int) -> Affine:
    return Affine(RES, 0.0, 0.0, 0.0, -RES, height * RES)


def bowl_dem() -> tuple[np.ndarray, Affine]:
    """A 10 m plateau with a 5 x 5 bowl 2 m deep: 2500 m2, depth 2 m."""
    dem = np.full((20, 20), 10.0)
    dem[7:12, 7:12] = 8.0
    return dem, transform_for(20)


@pytest.mark.parametrize("use_pyflwdir", [True, False])
def test_find_depressions_finds_a_known_bowl(use_pyflwdir: bool) -> None:
    dem, transform = bowl_dem()

    pits = find_depressions(dem, transform, CRS, min_area_m2=900.0, use_pyflwdir=use_pyflwdir)

    assert len(pits) == 1
    row = pits.iloc[0]
    assert row["depth_m"] == pytest.approx(2.0, abs=1e-6)
    assert row["area_m2"] == pytest.approx(2500.0)
    assert row["volume_m3"] == pytest.approx(5000.0, rel=1e-6)
    assert row["cells"] == 25
    assert row["rank"] == 1
    assert row["depression_id"].startswith("DEP-")
    assert pits.crs is not None
    # the bottom point sits inside the bowl
    left, bottom, right, top = 70.0, 80.0, 120.0, 130.0
    point = row.geometry
    assert left <= point.x <= right
    assert bottom <= point.y <= top


def test_pits_smaller_than_the_threshold_are_dropped() -> None:
    dem = np.full((20, 20), 10.0)
    dem[3:5, 3:5] = 9.0  # 4 cells -> 400 m2
    dem[12:16, 12:17] = 9.0  # 20 cells -> 2000 m2
    transform = transform_for(20)

    pits = find_depressions(dem, transform, CRS, min_area_m2=900.0, use_pyflwdir=False)

    assert len(pits) == 1
    assert pits.iloc[0]["area_m2"] == pytest.approx(2000.0)


def test_depressions_are_ranked_by_depth_times_area() -> None:
    dem = np.full((30, 30), 10.0)
    dem[3:8, 3:8] = 9.0  # 25 cells, 1 m  -> score 2500
    dem[20:24, 20:25] = 6.0  # 20 cells, 4 m  -> score 8000
    transform = transform_for(30)

    pits = find_depressions(dem, transform, CRS, min_area_m2=900.0, use_pyflwdir=False)

    assert len(pits) == 2
    assert pits.iloc[0]["score"] > pits.iloc[1]["score"]
    assert pits["rank"].tolist() == [1, 2]
    assert pits.iloc[0]["depth_m"] == pytest.approx(4.0)


def test_flat_terrain_has_no_depressions() -> None:
    dem = np.full((10, 10), 5.0)
    pits = find_depressions(dem, transform_for(10), CRS, use_pyflwdir=False)
    assert len(pits) == 0
    assert "depth_m" in pits.columns


def test_fill_never_lowers_the_terrain_and_labels_match() -> None:
    dem, _ = bowl_dem()
    filled = fill_depressions(dem, use_pyflwdir=False)
    assert np.all(filled >= dem - 1e-9)
    assert filled[9, 9] == pytest.approx(10.0)

    labels, depth = label_pits(dem, use_pyflwdir=False)
    assert labels.max() == 1
    assert int((labels > 0).sum()) == 25
    assert depth[9, 9] == pytest.approx(2.0)


def test_nodata_cells_survive_as_nan() -> None:
    dem, transform = bowl_dem()
    dem[0, 0] = np.nan
    pits = find_depressions(dem, transform, CRS, min_area_m2=900.0, use_pyflwdir=False)
    assert len(pits) == 1
