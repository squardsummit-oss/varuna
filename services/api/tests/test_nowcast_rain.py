"""Contract tests for the rain nowcast routes (SPEC.md 11.1 step 6, 12; task P3.8).

Two sources of rain, and both are exercised for real rather than mocked:

* a **baked run**, written here by ``varuna_sky``'s own writers into a run directory, so the
  test proves the round trip the API depends on - ``sky_products`` to ``rain/quantiles.zarr``
  to JSON - rather than proving that a stub returns what it was given;
* a **computed cycle**, run from a small replay bundle built by the storm designer, which is
  the demo path until ``make bake`` lands in Phase 5.

The city here is a temporary one: its grid record and its hotspot register are written into a
``VARUNA_CITY_DIR`` of their own, so the point the fan chart is sampled at comes from a
register in the test and never from a coordinate typed into it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from varuna_cycle.registry import RunRegistry
from varuna_cycle.sky_cycle import clear_cycle_cache
from varuna_replay.bundle import (
    RADAR_VARIABLE,
    BundleLayout,
    write_cube,
    write_gauges,
)
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.storm import RadarRender, radar_dbz, rain_field, random_storm
from varuna_schemas.constants import IST
from varuna_schemas.models import RunMeta
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.models.city import RadarDomain
from varuna_schemas.models.common import BBox
from varuna_sky.products import AoiGrid, sky_products, write_rain_products
from varuna_sky.types import RadarGrid, RainEnsemble, ZRParams

CRS = "EPSG:32643"
SKY_RES_M = 500.0
LEFT, TOP = 280_000.0, 2_140_000.0
"""A UTM 43N origin placing the domains over the Mumbai AOI; only the georeference matters."""

RAIN_URL = "/v1/nowcast/rain"
SERIES_URL = "/v1/nowcast/rain/series"

HOTSPOT_ID = "MUM-HS-01"
HOTSPOT_NAME = "Hindmata junction"
HOTSPOT_SOURCE = "https://example.org/bmc/waterlogging-register"
CHENNAI_LON, CHENNAI_LAT = 80.24, 13.01
"""A point in the Chennai AOI: far outside a radar domain centred on Mumbai."""


# ============================================================================ small artifacts
def _sky_grid(n_px: int) -> RadarGrid:
    return RadarGrid(
        crs=CRS,
        res_m=SKY_RES_M,
        n_px=n_px,
        transform=(SKY_RES_M, 0.0, LEFT, 0.0, -SKY_RES_M, TOP),
    )


def _aoi_grid(sky: RadarGrid, *, res_m: float = 30.0, width: int = 32, height: int = 48) -> AoiGrid:
    """A small city grid sitting inside the Sky domain, as Mumbai's does."""
    left, top = sky.left + 2_000.0, sky.top - 2_000.0
    return AoiGrid(
        crs=sky.crs,
        res_m=res_m,
        width=width,
        height=height,
        transform=(res_m, 0.0, left, 0.0, -res_m, top),
    )


def _lonlat(grid: RadarGrid, row: float, col: float) -> tuple[float, float]:
    """WGS84 position of a Sky cell centre."""
    x, y = grid.xy(row, col)
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return (float(lon), float(lat))


def _write_city(root: Path, city: str, grid: RadarGrid, aoi: AoiGrid) -> tuple[float, float]:
    """A city folder with the two records the rain routes read: the grid and the register."""
    folder = root / city
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pipeline.json").write_text(
        json.dumps({"city": city, "grid": aoi.to_dict()}, indent=2), encoding="utf-8", newline="\n"
    )
    lon, lat = _lonlat(grid, grid.n_px / 2, grid.n_px / 2)
    register = {
        "type": "FeatureCollection",
        "name": f"{city}_hotspots",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "hotspot_id": HOTSPOT_ID,
                    "name": HOTSPOT_NAME,
                    "slug": "hindmata-junction",
                    "lon": lon,
                    "lat": lat,
                    "sourced": True,
                    "coord_verified": True,
                    "source_url": HOTSPOT_SOURCE,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon + 0.01, lat + 0.01]},
                "properties": {
                    "hotspot_id": "MUM-HS-02",
                    "name": "Sion Circle",
                    "slug": "sion-circle",
                    "lon": lon + 0.01,
                    "lat": lat + 0.01,
                    "sourced": True,
                    "coord_verified": True,
                    "source_url": HOTSPOT_SOURCE,
                },
            },
        ],
    }
    (folder / "hotspots.geojson").write_text(
        json.dumps(register, indent=2), encoding="utf-8", newline="\n"
    )
    return (lon, lat)


def _ensemble(
    grid: RadarGrid, cycle_ts: datetime, *, n_members: int = 4, n_steps: int = 6
) -> RainEnsemble:
    """A tiny ensemble whose members genuinely disagree, so a spread band has something to show."""
    rows, cols = np.mgrid[0 : grid.n_px, 0 : grid.n_px]
    field = 30.0 * np.exp(-((rows - grid.n_px / 2) ** 2 + (cols - grid.n_px / 2) ** 2) / 50.0)
    decay = np.exp(-np.arange(n_steps) / 4.0)
    scale = 0.5 + np.arange(n_members) / max(n_members - 1, 1)
    rain = scale[:, None, None, None] * decay[None, :, None, None] * field[None, None, :, :]
    return RainEnsemble(
        rain_mm_h=rain.astype(np.float32),
        times=tuple(cycle_ts + timedelta(minutes=5 * (step + 1)) for step in range(n_steps)),
        grid=grid,
        source="fallback_steps",
        seed=2019,
        zr=ZRParams(a=180.0, b=1.5, source="adaptive", n_pairs=12, r2=0.91),
    )


# ============================================================================ fixtures
@pytest.fixture(autouse=True)
def _fresh_cycle_cache() -> Iterator[None]:
    """No computed cycle survives into the next test."""
    clear_cycle_cache()
    yield
    clear_cycle_cache()


@pytest.fixture
def sky() -> RadarGrid:
    return _sky_grid(24)


@pytest.fixture
def city_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sky: RadarGrid) -> Path:
    root = tmp_path / "city"
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    _write_city(root, "mumbai", sky, _aoi_grid(sky))
    return root


@pytest.fixture
def hotspot_lonlat(sky: RadarGrid) -> tuple[float, float]:
    return _lonlat(sky, sky.n_px / 2, sky.n_px / 2)


@pytest.fixture
def baked_rain(
    registry: RunRegistry, run_meta: RunMeta, sky: RadarGrid, city_root: Path
) -> RunMeta:
    """A run directory carrying real ``rain/cube.zarr`` and ``rain/quantiles.zarr`` stores."""
    aoi = _aoi_grid(sky)
    ensemble = _ensemble(sky, run_meta.cycle_ts)
    products = sky_products(ensemble, aoi)
    registry.write_run_dir(
        run_meta.run_id,
        lambda folder: write_rain_products(folder, ensemble, products, aoi),
        meta=run_meta,
    )
    return run_meta


# ============================================================================ the baked path
def test_rain_serves_every_members_hyetograph_and_the_band_across_them(
    client: TestClient, baked_rain: RunMeta
) -> None:
    """The time bar's spread band (SPEC.md 7.2), read back from the run that wrote it."""
    response = client.get(RAIN_URL)
    assert response.status_code == 200
    body = response.json()

    assert body["run_id"] == baked_rain.run_id
    assert body["valid_ts"] == baked_rain.cycle_ts.isoformat()
    assert body["mode"] == "baked"
    assert body["bundle"] == baked_rain.bundle
    assert body["city"] == "mumbai"
    assert (body["n_members"], body["n_steps"], body["step_min"]) == (4, 6, 5.0)

    assert [member["member"] for member in body["members"]] == [0, 1, 2, 3]
    assert all(len(member["mm_h"]) == 6 for member in body["members"])
    assert [step["lead_min"] for step in body["steps"]] == [5, 10, 15, 20, 25, 30]
    assert body["steps"][0]["valid_ts"] == (baked_rain.cycle_ts + timedelta(minutes=5)).isoformat()

    # The band is the quantiles of the members it ships beside, not an unrelated envelope.
    members = np.array([member["mm_h"] for member in body["members"]], dtype=float)
    p10, p50, p90 = np.quantile(members, [0.1, 0.5, 0.9], axis=0, method="linear")
    assert [step["p50_mm_h"] for step in body["steps"]] == pytest.approx(p50, abs=0.002)
    assert [step["p10_mm_h"] for step in body["steps"]] == pytest.approx(p10, abs=0.002)
    assert [step["p90_mm_h"] for step in body["steps"]] == pytest.approx(p90, abs=0.002)
    assert body["peak_p50_mm_h"] == max(step["p50_mm_h"] for step in body["steps"])


def test_rain_carries_the_provenance_the_run_stamp_shows(
    client: TestClient, baked_rain: RunMeta
) -> None:
    """Which nowcaster ran, with which seed and Z-R relation, plus the run's honesty notes."""
    body = client.get(RAIN_URL).json()

    assert body["nowcaster"] == "fallback_steps"  # the fallback is named, never hidden
    assert body["seed"] == 2019
    assert body["zr"] == {"a": 180.0, "b": 1.5, "source": "adaptive", "n_pairs": None}
    assert body["notes"] == baked_rain.notes
    assert body["stage_ms"] == baked_rain.stage_ms


def test_the_series_samples_the_coordinate_the_register_holds(
    client: TestClient,
    baked_rain: RunMeta,
    hotspot_lonlat: tuple[float, float],
    sky: RadarGrid,
) -> None:
    """The fan chart at Hindmata (task P3.8): the point comes from the city register."""
    response = client.get(SERIES_URL, params={"hotspot": "hindmata"})
    assert response.status_code == 200
    body = response.json()

    lon, lat = hotspot_lonlat
    assert body["point"]["hotspot_id"] == HOTSPOT_ID
    assert body["point"]["name"] == HOTSPOT_NAME
    assert body["point"]["source_url"] == HOTSPOT_SOURCE
    assert (body["point"]["lon"], body["point"]["lat"]) == pytest.approx((lon, lat))
    assert (body["point"]["row"], body["point"]["col"]) == (sky.n_px // 2, sky.n_px // 2)
    assert body["point"]["res_m"] == 500.0

    assert body["exceedance_mm_h"] == [20.0, 40.0]
    assert len(body["steps"]) == 6
    for step in body["steps"]:
        assert step["p10_mm_h"] <= step["p50_mm_h"] <= step["p90_mm_h"]
        assert step["p_gt_20"] >= step["p_gt_40"]
    # The centre pixel is the wettest, so the first step exceeds both thresholds for someone.
    assert body["steps"][0]["p_gt_20"] > 0.0


def test_a_bare_coordinate_is_sampled_without_a_register_entry(
    client: TestClient, baked_rain: RunMeta, hotspot_lonlat: tuple[float, float]
) -> None:
    lon, lat = hotspot_lonlat
    body = client.get(SERIES_URL, params={"lon": lon, "lat": lat}).json()

    assert body["point"]["hotspot_id"] is None
    assert body["point"]["source_url"] is None
    assert len(body["steps"]) == 6


@pytest.mark.parametrize("url", [RAIN_URL, SERIES_URL])
def test_every_rain_response_carries_run_id_and_valid_ts(
    client: TestClient, baked_rain: RunMeta, url: str
) -> None:
    """The SPEC.md 12 preamble, checked on both shapes."""
    body = client.get(url, params={"hotspot": HOTSPOT_ID}).json()

    assert body["run_id"] == baked_rain.run_id
    assert datetime.fromisoformat(body["valid_ts"]) == baked_rain.cycle_ts
    assert body["valid_ts"].endswith("+05:30")
    assert all(step["valid_ts"].endswith("+05:30") for step in body["steps"])


def test_a_named_run_is_served_and_a_missing_one_is_named(
    client: TestClient, baked_rain: RunMeta
) -> None:
    assert client.get(RAIN_URL, params={"run_id": baked_rain.run_id}).status_code == 200

    response = client.get(
        RAIN_URL, params={"run_id": "MUM-20190702T0610Z-sky1.0-twin1.0-flash0.3-baked"}
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "run_not_found"
    assert "make bake" in error["message"]


# ============================================================================ nothing on disk
@pytest.mark.parametrize("url", [RAIN_URL, SERIES_URL])
def test_with_no_baked_run_the_answer_says_what_to_run(
    client: TestClient, city_root: Path, url: str
) -> None:
    """Phase 5 writes the runs; until then the empty state is copy, not a crash (rule 6)."""
    response = client.get(url, params={"hotspot": HOTSPOT_ID})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "no_rain_runs"
    assert "make bake BUNDLE=MUM-2019-07-02" in error["message"]
    assert "compute=true" in error["message"]
    assert error["run_id"] is None


def test_a_run_without_rain_products_names_the_run_and_the_fix(
    client: TestClient, baked_run: RunMeta, city_root: Path
) -> None:
    """``baked_run`` writes run.json and nothing else - a run that has not baked its rain."""
    response = client.get(RAIN_URL, params={"run_id": baked_run.run_id})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "rain_not_in_run"
    assert error["run_id"] == baked_run.run_id
    assert "rain/quantiles.zarr" in error["message"]
    assert "make bake" in error["message"]


# ============================================================================ bad points
def test_a_point_outside_the_radar_domain_is_refused_not_clamped(
    client: TestClient, baked_rain: RunMeta
) -> None:
    """Answering about Chennai with Mumbai's edge pixel would be a fabricated number."""
    response = client.get(SERIES_URL, params={"lon": CHENNAI_LON, "lat": CHENNAI_LAT})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "point_outside_domain"
    assert "outside the radar domain" in error["message"]
    assert "east" in error["message"] and "north" in error["message"]


def test_the_series_says_how_to_name_a_point(client: TestClient, baked_rain: RunMeta) -> None:
    response = client.get(SERIES_URL)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "point_missing"
    assert "hotspot=" in error["message"] and "lon=" in error["message"]


def test_an_unknown_hotspot_points_at_the_register(client: TestClient, baked_rain: RunMeta) -> None:
    response = client.get(SERIES_URL, params={"hotspot": "Marine Drive"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "hotspot_not_found"
    assert "/v1/city/mumbai/layers/hotspots" in error["message"]


def test_an_ambiguous_hotspot_lists_what_it_matched(
    client: TestClient, baked_rain: RunMeta
) -> None:
    """Both register entries end in "c" somewhere; a fan chart cannot be drawn at two places."""
    response = client.get(SERIES_URL, params={"hotspot": "n"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "hotspot_ambiguous"
    assert HOTSPOT_NAME in error["message"] and "Sion Circle" in error["message"]


def test_a_register_entry_without_a_coordinate_says_so(
    client: TestClient, baked_rain: RunMeta, city_root: Path
) -> None:
    """The register is a file on disk; a pin with no position is refused, not guessed at."""
    register = city_root / "mumbai" / "hotspots.geojson"
    payload = json.loads(register.read_text(encoding="utf-8"))
    payload["features"] = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {"hotspot_id": "MUM-HS-99", "name": "Nowhere", "slug": "nowhere"},
        }
    ]
    register.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    response = client.get(SERIES_URL, params={"hotspot": "MUM-HS-99"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "hotspot_has_no_coordinate"
    assert "make city CITY=mumbai" in error["message"]


def test_an_unbuilt_city_names_the_command_that_builds_it(
    client: TestClient, baked_rain: RunMeta, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "no-city-here"))
    response = client.get(SERIES_URL, params={"hotspot": HOTSPOT_ID})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "city_not_built"
    assert "make city CITY=mumbai" in error["message"]


# ============================================================================ computing a cycle
@pytest.fixture(scope="module")
def computable_bundle(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, str]:
    """A small but complete bundle and city: enough to run one real Sky cycle.

    Module-scoped because the storm designer and the city records are the same for every test
    that computes; the cycle itself is not cached across tests (see ``_fresh_cycle_cache``).
    """
    root = tmp_path_factory.mktemp("computable")
    bundles, city = root / "bundles", root / "city"
    bundle_id = "MUM-TEST-STORM"
    t0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
    t1 = t0 + timedelta(minutes=60)
    window_min = 60.0
    radar_min, gauge_min = 10, 15

    sky = _sky_grid(64)
    domain = StormDomain(crs=32643, res_m=SKY_RES_M, n_px=64, left=LEFT, top=TOP)
    aoi = _aoi_grid(sky, width=64, height=96)
    _write_city(city, "mumbai", sky, aoi)

    design = random_storm(domain, seed=2019, window_min=window_min, n_cells=2)
    radar_times = step_times_min(0.0, window_min, radar_min)
    truth = rain_field(design, domain, radar_times)
    layout = BundleLayout(root=bundles / bundle_id)
    write_cube(
        layout.radar,
        radar_dbz(truth, domain, RadarRender.from_design(design)),
        variable=RADAR_VARIABLE,
        times_min=radar_times,
        domain=domain,
        t0=t0,
        step_min=radar_min,
        units="dBZ",
    )

    to_wgs84 = Transformer.from_crs(sky.crs, "EPSG:4326", always_xy=True)
    rows = []
    for index, (row, col) in enumerate([(20, 20), (24, 34), (32, 28), (38, 40), (44, 24)]):
        lon, lat = to_wgs84.transform(*sky.xy(float(row), float(col)))
        for step, minutes in enumerate(range(0, int(window_min) + 1, gauge_min)):
            frame = min(step * gauge_min // radar_min, truth.shape[0] - 1)
            rows.append(
                {
                    "ts": t0 + timedelta(minutes=minutes),
                    "station_id": f"TEST-{index:02d}",
                    "name": f"Test gauge {index}",
                    "lat": float(lat),
                    "lon": float(lon),
                    # mm in the five minutes ending at ts, from the field the frames show.
                    "mm_5min": round(float(truth[frame, row, col]) / 12.0, 4),
                    "synthetic": True,
                }
            )
    write_gauges(layout.gauges, rows)

    manifest = BundleManifest(
        id=bundle_id,
        city="mumbai",
        label="Design storm",
        t0=t0,
        t1=t1,
        cadences={"radar": radar_min, "truth": 5, "gauges": gauge_min, "cycle": 5},
        radar_domain=RadarDomain(center_lon=72.86, center_lat=19.065, size_km=32.0, res_m=500.0),
        aoi=BBox(min_lon=72.815, min_lat=18.995, max_lon=72.905, max_lat=19.135),
        seed=2019,
        synthetic_notes=["Radar frames: storm-designer reconstruction."],
        description="A one-hour test storm, built by the storm designer.",
    )
    layout.manifest.write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return (bundles, city, bundle_id)


@pytest.fixture
def computing_client(
    client: TestClient, computable_bundle: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    bundles, city, _ = computable_bundle
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(city))
    return client


def test_compute_runs_a_real_cycle_from_the_bundle_and_says_it_is_live(
    computing_client: TestClient, computable_bundle: tuple[Path, Path, str]
) -> None:
    """The demo path before Phase 5: no run on disk, a forecast on screen, labelled live."""
    _, _, bundle_id = computable_bundle
    response = computing_client.get(
        SERIES_URL,
        params={
            "hotspot": HOTSPOT_ID,
            "compute": "true",
            "bundle": bundle_id,
            "t": "2019-07-02T06:23:00+05:30",
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["run_id"] is None  # nothing was published, so there is no run to name
    assert body["mode"] == "live"
    assert body["bundle"] == bundle_id
    assert body["valid_ts"] == "2019-07-02T06:20:00+05:30"  # floored onto the cycle ladder
    assert body["n_members"] == 20
    assert body["n_steps"] == 36
    assert body["nowcaster"] in {"pysteps_steps", "fallback_steps"}
    assert body["seed"] == 2019
    assert body["zr"]["source"] in {"adaptive", "marshall_palmer"}
    assert set(body["stage_ms"]) == {"qc", "zr", "merge", "motion", "nowcast", "products"}
    assert any("NWP blend disabled" in note for note in body["notes"])
    assert [step["lead_min"] for step in body["steps"]][:3] == [5, 10, 15]
    assert body["peak_p50_mm_h"] > 0.0


def test_compute_before_the_first_forecastable_cycle_is_clamped_to_it(
    computing_client: TestClient, computable_bundle: tuple[Path, Path, str]
) -> None:
    """Optical flow needs three frames, so the bundle's first cycles cannot be forecast."""
    _, _, bundle_id = computable_bundle
    response = computing_client.get(
        RAIN_URL,
        params={"compute": "true", "bundle": bundle_id, "t": "2019-07-01T00:00:00+05:30"},
    )
    # Clamped up to the first forecastable cycle rather than refused: 05:40 + two 10-minute
    # frames is 06:00, the earliest instant with three frames behind it.
    assert response.status_code == 200
    assert response.json()["valid_ts"] == "2019-07-02T06:00:00+05:30"


def test_compute_from_a_bundle_that_is_not_generated_names_make_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path / "no-bundles-here"))
    response = client.get(RAIN_URL, params={"compute": "true", "bundle": "MUM-2019-07-02"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "bundle_not_found"
    assert "make bundle BUNDLE=MUM-2019-07-02" in error["message"]
