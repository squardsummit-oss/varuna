"""A cycle on a city with a sea draws the sea transparent and reads no product off it.

The Twin holds the city's sea (``sea_mask.tif``) at the tide's level, so its depth there is
metres of seawater. ``write_depth_rasters`` learned to leave it transparent, but ``run_cycle``
called it without the mask: after the coastline rebuild every run's PNGs would have painted
19.4 km2 of Mumbai's bay in the colour that means "rescue vehicles only".

This runs :func:`run_cycle` itself - the real Twin on a real (tiny) city read from disk by the
real loaders, and the real product writers - on a temporary city with a sea strip, and reads the
run directory back. Sky, Pulse and Flash are replaced: they are not what the test is about and
each needs a bundle, a feed or a fitted emulator. Every path points into ``tmp_path``, so nothing
here reads or writes ``city/``, ``data/`` or ``bundles/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SHAPE = (12, 14)
RES = 30.0
X0, Y0 = 270_000.0, 2_105_000.0
TRANSFORM = (RES, 0.0, X0, 0.0, -RES, Y0)
CRS = "EPSG:32643"
SEA_COLS = 4
SEA_Z = -1.0
STAGE_M = 0.5
"""Tide in the DEM's frame: 1.5 m of water over every sea cell."""
N_STEPS = 2


def _write(path: Path, band: np.ndarray, dtype: str) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=SHAPE[0],
        width=SHAPE[1],
        count=1,
        dtype=dtype,
        crs=CRS,
        transform=Affine(*TRANSFORM),
    ) as dst:
        dst.write(band.astype(dtype), 1)


def _sea() -> np.ndarray:
    sea = np.zeros(SHAPE, dtype=bool)
    sea[:, :SEA_COLS] = True
    return sea


def _x(col: float) -> float:
    return X0 + (col + 0.5) * RES


def _y(row: float) -> float:
    return Y0 - (row + 0.5) * RES


POOL = (2, 10)
"""A tidal pool inland of the promenade: a pit in the DEM, marked intertidal when a test asks."""


def _pool() -> np.ndarray:
    pool = np.zeros(SHAPE, dtype=bool)
    pool[POOL] = True
    return pool


def _build_city(root: Path, *, tidal_pool: bool = False) -> None:
    """Five rasters, the sea, a street table and a one-junction register.

    ``tidal_pool`` sinks :data:`POOL` 0.6 m, so rain gathers there deeper than on any street,
    and writes ``intertidal_mask.tif`` marking it: water the tide owns, like a mangrove behind
    the coast wall, that no street and no printed peak should read.
    """
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString

    root.mkdir(parents=True)
    z = np.linspace(1.0, 4.0, SHAPE[1])[None].repeat(SHAPE[0], 0)
    z[_sea()] = SEA_Z
    if tidal_pool:
        z[POOL] -= 0.6
        _write(root / "intertidal_mask.tif", _pool().astype(np.uint8), "uint8")
    _write(root / "dem_conditioned.tif", z, "float32")
    _write(root / "roughness.tif", np.full(SHAPE, 0.03), "float32")
    _write(root / "blocked.tif", np.zeros(SHAPE), "uint8")
    _write(root / "imperviousness.tif", np.full(SHAPE, 0.9), "float32")
    _write(root / "cn.tif", np.full(SHAPE, 98.0), "float32")
    _write(root / "sea_mask.tif", _sea().astype(np.uint8), "uint8")

    gpd.GeoDataFrame(
        {
            "segment_id": ["PROMENADE", "INLAND", "PIER"],
            "name": ["Seaface Road", "Inland Road", "Pier No. 1"],
            "exposure_weight": [0.5, 0.5, 0.2],
        },
        geometry=[
            # 5 m inside the first land column: its 15 m buffer reaches onto the sea
            LineString([(X0 + SEA_COLS * RES + 5.0, _y(1)), (X0 + SEA_COLS * RES + 5.0, _y(10))]),
            LineString([(_x(10), _y(1)), (_x(10), _y(10))]),
            LineString([(_x(0), _y(6)), (_x(2), _y(6))]),
        ],
        crs=CRS,
    ).to_parquet(root / "segments.parquet")

    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(
        _x(SEA_COLS + 1), _y(6)
    )
    register = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "hotspot_id": "shore-junction",
                    "name": "Shore junction",
                    "slug": "shore-junction",
                    "sourced": True,
                    "source_url": "https://example.invalid/shore-junction",
                },
            }
        ],
    }
    (root / "hotspots.geojson").write_text(json.dumps(register), encoding="utf-8")


def _network(terrain):
    """Three manholes on land draining east to west, to a free outfall one column off the sea."""
    from varuna_twin.types import DrainNetwork

    cells = [(6, 9), (6, 7), (6, SEA_COLS + 1)]
    n = len(cells)
    rows = np.array([r for r, _ in cells], dtype=np.int32)
    cols = np.array([c for _, c in cells], dtype=np.int32)
    z_ground = np.array([float(terrain.z[r, c]) for r, c in cells])
    boundary = np.zeros(n, dtype=np.int8)
    boundary[-1] = 2
    area = np.pi * 0.3**2
    return DrainNetwork(
        node_ids=tuple(f"N{i}" for i in range(n)),
        z_ground=z_ground,
        z_invert=z_ground - 1.5,
        storage_area=np.full(n, 1.0),
        inlet_length=np.full(n, 0.6),
        inlet_area=np.full(n, 0.04),
        kappa=np.full(n, 0.25),
        boundary=boundary,
        flap_gate=np.zeros(n, dtype=bool),
        cell_row=rows,
        cell_col=cols,
        edge_ids=tuple(f"E{i}" for i in range(n - 1)),
        from_node=np.arange(n - 1, dtype=np.int32),
        to_node=np.arange(1, n, dtype=np.int32),
        length=np.full(n - 1, 60.0),
        area=np.full(n - 1, area),
        hydraulic_radius=np.full(n - 1, 0.15),
        diameter=np.full(n - 1, 0.6),
        edge_manning_n=np.full(n - 1, 0.013),
        q_full=np.full(n - 1, 0.3),
        beta=np.full(n - 1, 0.15),
    )


@pytest.fixture
def sea_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``run_cycle`` on the temporary city; returns the result and the Twin's own depth field."""
    return _run_sea_cycle(tmp_path, monkeypatch)


def _run_sea_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tidal_pool: bool = False):
    import varuna_pulse.cycle as pulse_cycle
    import varuna_twin.city as twin_city
    import varuna_twin.runner as twin_runner
    from varuna_cycle import twin_cycle
    from varuna_schemas.constants import IST
    from varuna_twin.types import TideSeries

    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path / "bundles"))
    _build_city(tmp_path / "city" / "mumbai", tidal_pool=tidal_pool)

    t0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
    rain = np.full((N_STEPS, *SHAPE), 60.0)
    sky = SimpleNamespace(
        cycle_ts=t0,
        products=SimpleNamespace(times=[t0]),
        notes=(),
        design_storm_forced=False,
    )
    monkeypatch.setattr(twin_cycle, "_sky_rain_on_city", lambda *a, **k: (rain, sky))
    monkeypatch.setattr(twin_cycle, "_sky_ensemble", lambda *a, **k: (None, ()))
    monkeypatch.setattr(
        twin_cycle, "_flash_plan", lambda *a, **k: (None, ("Flash is off in this test.",))
    )
    monkeypatch.setattr(
        twin_city, "load_network", lambda city: _network(twin_city.load_terrain(city))
    )
    monkeypatch.setattr(
        twin_city,
        "load_tide",
        lambda bundle, **k: TideSeries(
            times=(t0 - timedelta(hours=1), t0 + timedelta(hours=4)),
            stage_m=np.array([STAGE_M, STAGE_M]),
            source="illustrative",
        ),
    )

    def no_feeds(*args, **kwargs):
        raise RuntimeError("no Pulse feeds in this test")

    monkeypatch.setattr(pulse_cycle, "run_pulse", no_feeds)

    captured = {}
    real_run_twin = twin_runner.run_twin

    def spy(inputs, **kwargs):
        result = real_run_twin(inputs, **kwargs)
        captured["depth"] = np.array(result.depth_m)
        return result

    monkeypatch.setattr(twin_runner, "run_twin", spy)

    result = twin_cycle.run_cycle(
        "SEA-TEST", t0, city="mumbai", n_steps=N_STEPS, mode="baked", overwrite=True
    )
    assert Path(result.run_dir).is_relative_to(tmp_path), result.run_dir
    return result, captured["depth"]


def test_the_sea_is_transparent_in_every_depth_png(sea_cycle) -> None:
    from PIL import Image
    from varuna_products.depth import depth_array_to_rgba

    result, depth = sea_cycle
    sea = _sea()
    # The Twin held the sea at the tide: well inside the ramp, so drawn it would be coloured.
    assert np.all(depth[:, sea] >= 1.0), float(depth[:, sea].min())
    assert np.all(depth_array_to_rgba(depth[0], alpha=255, dry_alpha=0)[sea][:, 3] == 255)

    pngs = sorted((Path(result.run_dir) / "depth").glob("p50_*.png"))
    assert len(pngs) == N_STEPS
    for step, path in enumerate(pngs):
        with Image.open(path) as image:
            rgba = np.asarray(image.convert("RGBA"))
        assert rgba.shape[:2] == SHAPE
        assert np.all(rgba[sea][:, 3] == 0), f"{path.name} draws the sea"
        # The land is drawn exactly as the ramp draws it.
        drawn = depth_array_to_rgba(depth[step], alpha=255, dry_alpha=0)
        assert np.array_equal(rgba[~sea], drawn[~sea]), f"{path.name} changed a land pixel"


def test_no_product_of_the_run_reads_the_sea(sea_cycle) -> None:
    """The segment table and the hotspot rank read the same sea the rasters leave out."""
    import pandas as pd

    result, depth = sea_cycle
    land_peak_cm = float(depth[:, ~_sea()].max()) * 100.0
    run = Path(result.run_dir)

    frame = pd.read_parquet(run / "segment_forecast.parquet")
    by_segment = frame.groupby("segment_id")["depth_p50_cm"].max()
    assert by_segment["PIER"] == 0.0
    assert by_segment.max() <= land_peak_cm + 1e-6

    hotspots = json.loads((run / "hotspots.json").read_text(encoding="utf-8"))
    assert [h["hotspot_id"] for h in hotspots] == ["shore-junction"]
    assert hotspots[0]["peak_depth_cm"] <= land_peak_cm + 1e-6


def test_the_cycle_peak_is_the_lands_not_the_seas(sea_cycle) -> None:
    """`CycleResult.peak_depth_cm` is what `make bake` and the onboarding log print.

    Over the whole grid it is the sea the Twin holds at the tide - 150 cm here, Chennai's
    "peak 529.1 cm" on its first onboarded forecast - so it is taken over land only.
    """
    from varuna_cycle.bake import describe_result

    result, depth = sea_cycle
    sea = _sea()
    sea_peak_cm = float(depth[:, sea].max()) * 100.0
    land_peak_cm = round(float(depth[:, ~sea].max()) * 100.0, 1)
    assert sea_peak_cm >= 150.0 - 1e-6
    assert land_peak_cm < sea_peak_cm
    assert result.peak_depth_cm == land_peak_cm
    assert f"land peak {land_peak_cm:.1f} cm" in describe_result(result)


def test_the_cycle_peak_leaves_out_the_intertidal_zone_too(tmp_path, monkeypatch) -> None:
    """A city with ``intertidal_mask.tif``: the printed peak skips it as the products do.

    The Twin's terrain does not carry the intertidal zone, so the cycle reads the same raster
    the segment index and the hotspot windows read. Here the deepest land water is a tidal pool;
    the peak is the deepest water off the sea and off the pool, and run.json hashes the raster.
    """
    from varuna_cycle.bake import describe_result

    result, depth = _run_sea_cycle(tmp_path, monkeypatch, tidal_pool=True)
    sea, pool = _sea(), _pool()
    pool_cm = float(depth[:, pool].max()) * 100.0
    street_cm = round(float(depth[:, ~(sea | pool)].max()) * 100.0, 1)
    # The pool holds the deepest land water, so leaving it out is what moves the number.
    assert pool_cm > street_cm + 1.0, (pool_cm, street_cm)
    assert result.peak_depth_cm == street_cm
    assert f"land peak {street_cm:.1f} cm" in describe_result(result)

    raw = json.loads((Path(result.run_dir) / "run.json").read_text(encoding="utf-8"))
    part = raw["city_fingerprint"]["intertidal_mask"]
    assert isinstance(part, str) and len(part) == 12
    assert raw["stage_ms"]["provenance"] >= 0


def test_run_json_carries_the_twin_revision_and_the_city_it_read(sea_cycle, tmp_path) -> None:
    """The run id keeps its versions; run.json says which Twin and which city files ran."""
    from varuna_cycle.provenance import FINGERPRINT_FILES, city_fingerprint
    from varuna_cycle.twin_cycle import TWIN_REVISION, TWIN_VERSION
    from varuna_schemas.models.run import RunMeta

    result, _ = sea_cycle
    # The id format is untouched: the coastline is a revision, not a version (TWIN_REVISION).
    assert f"-twin{TWIN_VERSION}-" in result.run_id and TWIN_VERSION == "1.0"

    raw = json.loads((Path(result.run_dir) / "run.json").read_text(encoding="utf-8"))
    assert raw["twin_revision"] == TWIN_REVISION
    fingerprint = raw["city_fingerprint"]
    assert set(fingerprint) == set(FINGERPRINT_FILES)
    assert fingerprint == city_fingerprint(tmp_path / "city" / "mumbai")
    for part in ("segments", "sea_mask", "dem_conditioned"):
        assert isinstance(fingerprint[part], str) and len(fingerprint[part]) == 12, part
    # The test city has no intertidal raster, no drain tables (the network is built in memory)
    # and no condition.json, and says so rather than hashing nothing.
    for part in ("intertidal_mask", "drain_nodes", "drain_edges", "coast_wall"):
        assert fingerprint[part] is None, part
    meta = RunMeta.model_validate(raw)
    assert meta.city_fingerprint is not None and meta.twin_revision == TWIN_REVISION
    # The fingerprint's time is billed to its own key, outside the cycle stages the total sums.
    from varuna_schemas.models.run import top_level_stage_ms

    assert isinstance(raw["stage_ms"]["provenance"], int)
    assert "provenance" not in top_level_stage_ms(raw["stage_ms"])


def test_the_fingerprint_is_stable_across_two_bakes_of_the_cycle(sea_cycle) -> None:
    """Rule 8 excludes run.json from the byte comparison; the fingerprint in it still agrees."""
    from varuna_cycle import twin_cycle
    from varuna_schemas.constants import IST

    result, _ = sea_cycle
    run_json = Path(result.run_dir) / "run.json"
    first = json.loads(run_json.read_text(encoding="utf-8"))
    again = twin_cycle.run_cycle(
        "SEA-TEST",
        datetime(2019, 7, 2, 8, 40, tzinfo=IST),
        city="mumbai",
        n_steps=N_STEPS,
        mode="baked",
        overwrite=True,
    )
    assert again.run_id == result.run_id
    second = json.loads(run_json.read_text(encoding="utf-8"))
    assert second["city_fingerprint"] == first["city_fingerprint"]
    assert second["twin_revision"] == first["twin_revision"]
    assert again.peak_depth_cm == result.peak_depth_cm


def test_stale_runs_names_a_run_once_its_sea_changes(sea_cycle, tmp_path) -> None:
    from varuna_cycle.provenance import stale_runs

    result, _ = sea_cycle
    runs = Path(result.run_dir).parent
    city_root = tmp_path / "city" / "mumbai"
    assert stale_runs("mumbai", runs_dir=runs, city_root=city_root) == []
    # Another city's runs are never listed against this one.
    assert stale_runs("chennai", runs_dir=runs, city_root=city_root) == []

    moved = _sea()
    moved[:, SEA_COLS] = True
    _write(city_root / "sea_mask.tif", moved.astype(np.uint8), "uint8")
    stale = stale_runs("mumbai", runs_dir=runs, city_root=city_root)
    assert [s.run_id for s in stale] == [result.run_id]
    assert len(stale[0].reasons) == 1 and stale[0].reasons[0].startswith("sea_mask: ")

    newer = stale_runs("mumbai", runs_dir=runs, city_root=city_root, twin_revision="9.9+test")
    assert newer[0].reasons[-1].startswith("twin_revision: ")


def test_the_api_serves_the_provenance_with_the_run(sea_cycle, monkeypatch) -> None:
    """`GET /v1/runs/{id}` passes run.json through RunMeta, so the new fields reach the console."""
    from fastapi.testclient import TestClient
    from varuna_api.main import create_app
    from varuna_api.state import AppState
    from varuna_cycle.bus import Bus
    from varuna_cycle.registry import RunRegistry
    from varuna_cycle.twin_cycle import TWIN_REVISION
    from varuna_schemas.settings import Settings

    monkeypatch.setenv("VARUNA_SEED_DEMO_RUNS", "0")
    result, _ = sea_cycle
    settings = Settings(
        varuna_city="mumbai",
        varuna_bundle="MUM-2019-07-02",
        varuna_mode="replay",
        varuna_offline=True,
        _env_file=None,  # type: ignore[call-arg]
    )
    state = AppState(
        settings=settings, registry=RunRegistry(Path(result.run_dir).parent), bus=Bus()
    )
    response = TestClient(create_app(state=state)).get(f"/v1/runs/{result.run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    raw = json.loads((Path(result.run_dir) / "run.json").read_text(encoding="utf-8"))
    assert body["twin_revision"] == TWIN_REVISION
    assert body["city_fingerprint"] == raw["city_fingerprint"]
