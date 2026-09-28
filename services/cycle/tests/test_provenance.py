"""The city fingerprint in run.json, and the land peak a cycle reports.

Everything here is written into ``tmp_path``; the one test that reads the real Mumbai build only
reads it, and skips when the city is not built.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SHAPE = (6, 7)
TRANSFORM = (30.0, 0.0, 270_000.0, 0.0, -30.0, 2_105_000.0)
CRS = "EPSG:32643"


def _raster(path: Path, band: np.ndarray, dtype: str, **options: object) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=band.shape[0],
        width=band.shape[1],
        count=1,
        dtype=dtype,
        crs=CRS,
        transform=Affine(*TRANSFORM),
        **options,
    ) as dst:
        dst.write(band.astype(dtype), 1)


def _sea() -> np.ndarray:
    sea = np.zeros(SHAPE, dtype=np.uint8)
    sea[:, :2] = 1
    return sea


def _city(root: Path) -> Path:
    """Every file the fingerprint reads, small."""
    import pandas as pd

    root.mkdir(parents=True)
    pd.DataFrame({"segment_id": ["S1", "S2", "S3"], "name": ["A", "B", "C"]}).to_parquet(
        root / "segments.parquet"
    )
    _raster(root / "sea_mask.tif", _sea(), "uint8")
    _raster(root / "intertidal_mask.tif", np.zeros(SHAPE), "uint8")
    _raster(root / "dem_conditioned.tif", np.linspace(0, 5, 42).reshape(SHAPE), "float32")
    pd.DataFrame(
        {
            "node_id": ["N1", "N2"],
            "cell_row": [1, 2],
            "cell_col": [3, 4],
            "is_outfall": [False, True],
            "boundary_type": ["interior", "tidal"],
            "tidal": [False, True],
            "flap_gate": [False, False],
            "z_ground_m": [2.0, 1.0],
        }
    ).to_parquet(root / "drain_nodes.parquet")
    pd.DataFrame({"edge_id": ["E1"], "from_node": ["N1"], "to_node": ["N2"]}).to_parquet(
        root / "drain_edges.parquet"
    )
    (root / "condition.json").write_text(
        json.dumps(
            {
                "cells_burned": 10,
                "coast_wall_m": 3.5,
                "coast_wall_rule": "landward edge of the intertidal zone",
                "coast_wall_cells_raised": 382,
                "shore_ring_cells": 400,
                "intertidal_cells": 124,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_the_fingerprint_is_deterministic_and_complete(tmp_path: Path) -> None:
    from varuna_cycle import provenance

    root = _city(tmp_path / "city")
    first = provenance.city_fingerprint(root)
    assert set(first) == set(provenance.FINGERPRINT_FILES)
    assert all(isinstance(v, str) and len(v) == 12 for v in first.values()), first
    assert provenance.city_fingerprint(root) == first
    # Not a cache artefact: a fresh read of the same files gives the same digests.
    provenance._CACHE.clear()
    assert provenance.city_fingerprint(str(root)) == first


def _reorder_segments(root: Path) -> None:
    import pandas as pd

    pd.DataFrame({"segment_id": ["S1", "S3", "S2"]}).to_parquet(root / "segments.parquet")


@pytest.mark.parametrize(
    ("part", "change"),
    [
        ("segments", _reorder_segments),
        ("sea_mask", lambda r: _raster(r / "sea_mask.tif", _sea() * 2, "uint8")),
        ("intertidal_mask", lambda r: _raster(r / "intertidal_mask.tif", _sea(), "uint8")),
        (
            "dem_conditioned",
            lambda r: _raster(r / "dem_conditioned.tif", np.ones(SHAPE), "float32"),
        ),
    ],
)
def test_each_part_changes_when_its_file_changes(tmp_path: Path, part: str, change) -> None:
    from varuna_cycle.provenance import city_fingerprint

    root = _city(tmp_path / "city")
    before = city_fingerprint(root)
    change(root)
    after = city_fingerprint(root)
    assert after[part] != before[part]
    assert {k: v for k, v in after.items() if k != part} == {
        k: v for k, v in before.items() if k != part
    }


def test_a_tidal_flag_or_an_edge_changes_the_drain_parts(tmp_path: Path) -> None:
    import pandas as pd
    from varuna_cycle.provenance import city_fingerprint

    root = _city(tmp_path / "city")
    before = city_fingerprint(root)
    nodes = pd.read_parquet(root / "drain_nodes.parquet")
    # A column outside the sea coupling moves nothing: the parts hash ids and coupling only.
    nodes.assign(z_ground_m=[9.0, 9.0]).to_parquet(root / "drain_nodes.parquet")
    assert city_fingerprint(root) == before
    nodes.assign(tidal=[False, False]).to_parquet(root / "drain_nodes.parquet")
    assert city_fingerprint(root)["drain_nodes"] != before["drain_nodes"]
    pd.DataFrame({"edge_id": ["E1"], "from_node": ["N2"], "to_node": ["N1"]}).to_parquet(
        root / "drain_edges.parquet"
    )
    assert city_fingerprint(root)["drain_edges"] != before["drain_edges"]


def test_the_coast_wall_part_reads_the_wall_and_nothing_else(tmp_path: Path) -> None:
    from varuna_cycle.provenance import city_fingerprint

    root = _city(tmp_path / "city")
    before = city_fingerprint(root)["coast_wall"]
    fields = json.loads((root / "condition.json").read_text(encoding="utf-8"))
    # Another step's field, and the same fields written in another order with other spacing.
    rewritten = {"pits_breached": 1498, **dict(reversed(list(fields.items())))}
    (root / "condition.json").write_text(json.dumps(rewritten, indent=4), encoding="utf-8")
    assert city_fingerprint(root)["coast_wall"] == before
    rewritten["coast_wall_cells_raised"] = 736
    (root / "condition.json").write_text(json.dumps(rewritten), encoding="utf-8")
    assert city_fingerprint(root)["coast_wall"] != before
    # A condition step with no coast wall has nothing to hash.
    (root / "condition.json").write_text(json.dumps({"cells_burned": 10}), encoding="utf-8")
    assert city_fingerprint(root)["coast_wall"] is None


def test_a_raster_re_encoded_is_the_same_raster(tmp_path: Path) -> None:
    """Content, not bytes: another compression is the same sea."""
    from varuna_cycle.provenance import city_fingerprint

    root = _city(tmp_path / "city")
    before = city_fingerprint(root)["sea_mask"]
    raw = (root / "sea_mask.tif").read_bytes()
    _raster(root / "sea_mask.tif", _sea(), "uint8", compress="deflate")
    assert (root / "sea_mask.tif").read_bytes() != raw
    assert city_fingerprint(root)["sea_mask"] == before


def test_a_rewrite_that_keeps_size_and_time_is_still_seen(tmp_path: Path) -> None:
    """The cache is keyed on the bytes, not on size and modification time.

    A same-size rewrite inside one clock tick - which is what a test, or a rebuild step writing
    the same grid, can do - leaves ``st_size`` and ``st_mtime_ns`` as they were. Keyed on those,
    the second call would have answered the first file's digest.
    """
    import os

    from varuna_cycle.provenance import city_fingerprint

    root = _city(tmp_path / "city")
    path = root / "sea_mask.tif"
    before = city_fingerprint(root)["sea_mask"]
    stat = path.stat()
    _raster(path, _sea() * 2, "uint8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    assert city_fingerprint(root)["sea_mask"] != before


def test_a_city_without_the_files_says_so(tmp_path: Path) -> None:
    from varuna_cycle.provenance import FINGERPRINT_FILES, city_fingerprint

    empty = tmp_path / "empty"
    empty.mkdir()
    assert city_fingerprint(empty) == dict.fromkeys(FINGERPRINT_FILES)


def test_fingerprint_diff_names_each_part_and_a_run_that_has_none() -> None:
    from varuna_cycle.provenance import fingerprint_diff

    now = {"segments": "aaaaaaaaaaaa", "sea_mask": "bbbbbbbbbbbb", "intertidal_mask": None}
    assert fingerprint_diff(now, dict(now)) == ()
    assert fingerprint_diff({**now, "sea_mask": None}, now) == ("sea_mask: absent -> bbbbbbbbbbbb",)
    (reason,) = fingerprint_diff(None, now)
    assert reason.startswith("no city fingerprint")


def test_stale_runs_reads_old_runs_and_skips_other_cities(tmp_path: Path) -> None:
    from varuna_cycle.provenance import city_fingerprint, stale_runs

    root = _city(tmp_path / "city")
    runs = tmp_path / "runs"
    current = city_fingerprint(root)
    rows = {
        "MUM-A": {"city": "mumbai", "twin_revision": "r1", "city_fingerprint": current},
        "MUM-B": {"city": "mumbai"},  # baked before run.json carried provenance
        "MUM-C": {
            "city": "mumbai",
            "twin_revision": "r1",
            "city_fingerprint": {**current, "segments": "000000000000"},
        },
        "CHN-A": {"city": "chennai"},
    }
    for run_id, body in rows.items():
        (runs / run_id).mkdir(parents=True)
        (runs / run_id / "run.json").write_text(
            json.dumps({"run_id": run_id, **body}), encoding="utf-8"
        )
    (runs / ".MUM-D.tmp-1234").mkdir()  # a half-written run is never read

    stale = stale_runs("mumbai", runs_dir=runs, city_root=root, twin_revision="r1")
    assert [s.run_id for s in stale] == ["MUM-B", "MUM-C"]
    assert stale[0].reasons[0].startswith("no city fingerprint")
    assert stale[0].reasons[-1] == "twin_revision: absent -> r1"
    assert stale[1].reasons == (f"segments: 000000000000 -> {current['segments']}",)
    assert stale_runs("mumbai", runs_dir=tmp_path / "nowhere", city_root=root) == []


def _terrain(**masks: np.ndarray) -> SimpleNamespace:
    return SimpleNamespace(z=np.zeros(SHAPE), **masks)


def test_the_land_peak_leaves_out_the_sea_and_the_intertidal_zone() -> None:
    from varuna_cycle.twin_cycle import land_peak_depth_cm

    depth = np.zeros((2, *SHAPE))
    depth[1, 0, 0] = 5.29  # the sea, held at mean sea level
    depth[1, 0, 2] = 1.20  # a mangrove the tide covers
    depth[0, 4, 5] = 0.453  # a street
    sea = _sea().astype(bool)
    intertidal = np.zeros(SHAPE, dtype=bool)
    intertidal[0, 2] = True

    # No raster: exactly the whole-grid nanmax the cycle always took.
    assert land_peak_depth_cm(depth, _terrain()) == round(float(np.nanmax(depth)) * 100.0, 1)
    assert land_peak_depth_cm(depth, _terrain(sea=sea)) == 120.0
    assert land_peak_depth_cm(depth, _terrain(sea=sea, intertidal=intertidal)) == 45.3
    assert land_peak_depth_cm(depth, _terrain(intertidal=intertidal)) == 529.0
    assert land_peak_depth_cm(depth, _terrain(sea=np.ones(SHAPE, dtype=bool))) == 0.0
    with pytest.raises(ValueError, match=r"terrain\.sea has shape"):
        land_peak_depth_cm(depth, _terrain(sea=np.zeros((3, 3), dtype=bool)))

    # The cycle passes the city's intertidal raster, which the Twin's terrain does not carry;
    # it joins the terrain's own masks rather than replacing them.
    assert land_peak_depth_cm(depth, _terrain(sea=sea), intertidal=intertidal) == 45.3
    assert land_peak_depth_cm(depth, _terrain(), intertidal=intertidal) == 529.0
    assert land_peak_depth_cm(depth, _terrain(intertidal=intertidal), intertidal=None) == 529.0
    # An all-zero raster - every city build writes one - is the whole-grid peak, as before.
    no_zone = np.zeros(SHAPE, dtype=bool)
    assert land_peak_depth_cm(depth, _terrain(), intertidal=no_zone) == land_peak_depth_cm(
        depth, _terrain()
    )
    with pytest.raises(ValueError, match=r"^intertidal has shape"):
        land_peak_depth_cm(depth, _terrain(sea=sea), intertidal=np.zeros((3, 3), dtype=bool))


def test_the_real_mumbai_fingerprint_is_stable(tmp_path: Path) -> None:
    """Read-only against `city/mumbai`: two reads, one of them cold, agree."""
    from varuna_cycle import provenance
    from varuna_schemas.paths import city_dir

    root = city_dir("mumbai")
    if not (root / "segments.parquet").is_file():
        pytest.skip("city/mumbai is not built; run `make city CITY=mumbai` first")
    first = provenance.city_fingerprint(root)
    provenance._CACHE.clear()
    assert provenance.city_fingerprint(root) == first
    assert first["segments"] is not None and first["drain_nodes"] is not None
