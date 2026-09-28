"""An unknown ``?city=`` is refused, never served another city's run (task D-09).

``run_prefix`` has no code for a name like ``atlantis`` and returns an empty prefix, and an empty
prefix used to filter nothing: every route that resolves "the newest run for this city" answered
``?city=atlantis`` with whichever city's run sorted newest - Chennai's, since ``CHN-`` sorts after
``MUM-`` - which is exactly the cross-city leak the per-city filter exists to close. These tests
seed one run per city and pin that no route hands either of them to a city that does not exist,
that an omitted city still means the configured one, and that a city with no run is told to bake
its own bundle rather than Mumbai's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from varuna_schemas.settings import get_settings

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

MUM = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
CHN = "CHN-20260701T0120Z-sky1.0-twin1.0-flash0.0-baked"
"""Chennai's id sorts after Mumbai's, so an unfiltered "newest run" is the Chennai one."""

# Every route that takes ``?city=`` and reads a run it picked itself.
DEPTH_ROUTES = [
    "/v1/nowcast/raster/bounds",
    "/v1/nowcast/raster",
    "/v1/nowcast/segments",
    "/v1/nowcast/hotspots",
    "/v1/nowcast/surcharge",
    "/v1/alerts",
    "/v1/pumps",
    "/v1/drains/health",
    "/v1/drains/health.csv",
    "/v1/observations",
]
OPS_ROUTES = ["/v1/ops/alerts", "/v1/alerts/delivery"]


def _seed(data: Path, run_id: str, *, products: bool = True) -> Path:
    """One run directory carrying every product the city routes read, stamped with its run id."""
    run = data / "runs" / run_id
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text(
        json.dumps({"wgs84": [72.8, 19.0, 72.9, 19.1]}), encoding="utf-8"
    )
    (run / "depth" / "p50_00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "run.json").write_text(json.dumps({"run_id": run_id, "notes": []}), encoding="utf-8")
    if not products:
        return run
    (run / "segments_wet.json").write_text(
        json.dumps({"run_id": run_id, "valid_ts": [], "depth_cm": {}}), encoding="utf-8"
    )
    (run / "hotspots.json").write_text(json.dumps([]), encoding="utf-8")
    (run / "node_surcharge.json").write_text(
        json.dumps({"run_id": run_id, "nodes": [], "notes": []}), encoding="utf-8"
    )
    (run / "alerts.json").write_text(json.dumps({"alerts": []}), encoding="utf-8")
    (run / "pump_plan.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (run / "drain_health.geojson").write_text(
        json.dumps({"run_id": run_id, "features": [], "n_edges": 0}), encoding="utf-8"
    )
    (run / "desilting.csv").write_text(f"run_id\n{run_id}\n", encoding="utf-8")
    (run / "observations.json").write_text(
        json.dumps({"run_id": run_id, "observations": []}), encoding="utf-8"
    )
    return run


def _bundle(root: Path, bundle_id: str, city: str) -> None:
    """A bundle folder with just the manifest fields the no-run hint reads."""
    folder = root / bundle_id
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"id": bundle_id, "city": city}), encoding="utf-8"
    )


@pytest.fixture
def bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Both cities' bundles, as the repository ships them, in a folder this test owns."""
    root = tmp_path / "bundles"
    _bundle(root, "CHN-IDF-25yr", "chennai")
    _bundle(root, "MUM-2019-07-02", "mumbai")
    _bundle(root, "MUM-IDF-25yr", "mumbai")
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(root))
    return root


@pytest.fixture
def two_cities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path) -> Path:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)
    _seed(tmp_path, CHN)
    return tmp_path


@pytest.fixture
def unknown_configured_city(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``VARUNA_CITY`` set to a city with no run-id code, for routes that take no ``?city=``."""
    monkeypatch.setenv("VARUNA_CITY", "atlantis")
    get_settings.cache_clear()
    yield
    # The environment is restored after this teardown, so the cache is only dropped here and the
    # next reader re-reads the restored value.
    get_settings.cache_clear()


def _refusal(response_json: dict) -> dict:
    error = response_json["error"]
    assert error["code"] == "unknown_city"
    return error


# ---- the refusal -------------------------------------------------------------------------------


@pytest.mark.usefixtures("two_cities")
@pytest.mark.parametrize("path", DEPTH_ROUTES + OPS_ROUTES)
def test_an_unknown_city_is_refused_on_every_route_rather_than_served(
    client: TestClient, path: str
) -> None:
    response = client.get(path, params={"city": "atlantis"})

    assert response.status_code == 404, response.text
    error = _refusal(response.json())
    # The envelope says what happened and names the cities that do exist (SPEC.md 6.8, 12).
    assert "atlantis" in error["message"]
    assert "mumbai" in error["message"]
    assert "chennai" in error["message"]
    # And no other city's run leaked into the answer.
    assert MUM not in response.text
    assert CHN not in response.text


@pytest.mark.usefixtures("two_cities")
def test_three_letters_that_are_not_a_city_code_are_refused_too(client: TestClient) -> None:
    """``city_code`` passes any three letters through; ``XYZ-`` filters to nothing, but a request
    for a city VARUNA does not have should say so rather than report it merely unbaked."""
    response = client.get("/v1/nowcast/raster/bounds", params={"city": "xyz"})

    assert response.status_code == 404
    _refusal(response.json())


@pytest.mark.usefixtures("two_cities")
def test_an_unknown_city_is_refused_beside_a_run_id(client: TestClient) -> None:
    """A run id still serves itself, but not to a request for a city that does not exist."""
    response = client.get("/v1/nowcast/raster/bounds", params={"run_id": MUM, "city": "atlantis"})

    assert response.status_code == 404
    _refusal(response.json())


# ---- what still answers ------------------------------------------------------------------------


@pytest.mark.usefixtures("two_cities")
@pytest.mark.parametrize(
    ("city", "expected"),
    [("mumbai", MUM), ("Chennai", CHN), ("CHN", CHN), (" mumbai ", MUM)],
)
def test_a_known_city_by_slug_or_code_is_served_its_own_run(
    client: TestClient, city: str, expected: str
) -> None:
    response = client.get("/v1/nowcast/raster/bounds", params={"city": city})

    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == expected


@pytest.mark.usefixtures("two_cities")
@pytest.mark.parametrize("params", [{}, {"city": ""}])
def test_an_omitted_or_blank_city_still_means_the_configured_one(
    client: TestClient, params: dict[str, str]
) -> None:
    response = client.get("/v1/nowcast/raster/bounds", params=params)

    assert response.status_code == 200
    assert response.json()["run_id"] == MUM


@pytest.mark.usefixtures("two_cities", "unknown_configured_city")
def test_an_unknown_configured_city_is_named_as_the_setting(client: TestClient) -> None:
    """With no ``?city=``, the refusal points at ``VARUNA_CITY`` - that is what to fix."""
    response = client.get("/v1/nowcast/raster/bounds")

    assert response.status_code == 404
    error = _refusal(response.json())
    assert "VARUNA_CITY" in error["message"]
    assert CHN not in response.text


@pytest.mark.usefixtures("two_cities", "unknown_configured_city")
@pytest.mark.parametrize("params", [{}, {"run_id": CHN}])
@pytest.mark.parametrize("path", OPS_ROUTES)
def test_an_ops_route_names_an_unknown_configured_city_as_the_setting(
    client: TestClient, path: str, params: dict[str, str]
) -> None:
    """The desk's routes swap ``VARUNA_CITY`` in before resolving it, so an empty ``?city=`` is
    not how the setting reaches the refusal. It used to tell the ward officer to ask for another
    city, when the fault is the server's setting and only the setting can fix it."""
    response = client.get(path, params=params)

    assert response.status_code == 404
    message = _refusal(response.json())["message"]
    assert "VARUNA_CITY" in message
    assert "Ask for" not in message
    assert CHN not in response.text


def test_a_request_for_another_unknown_city_is_still_blamed_on_the_request(
    unknown_configured_city: None,
) -> None:
    """Beside a bad setting, a request naming a different unknown city is the request's fault."""
    from fastapi import HTTPException
    from varuna_api.runs_util import resolve_city

    with pytest.raises(HTTPException) as refused:
        resolve_city("gotham")
    assert refused.value.detail["code"] == "unknown_city"
    message = refused.value.detail["message"]
    assert "'gotham'" in message
    assert "Ask for" in message
    assert "VARUNA_CITY" not in message

    # The configured name, however it is cased or padded, is the setting's fault.
    with pytest.raises(HTTPException) as refused:
        resolve_city(" Atlantis ")
    assert "VARUNA_CITY" in refused.value.detail["message"]


@pytest.mark.usefixtures("two_cities", "unknown_configured_city")
def test_the_what_if_never_borrows_another_citys_run(client: TestClient) -> None:
    """`/v1/whatif` takes no ``?city=`` and reads the configured city; an unknown one used to
    resolve to an empty prefix and run the scenario on Chennai's newest run."""
    response = client.post("/v1/whatif", json={})

    assert response.status_code == 404
    _refusal(response.json())
    assert CHN not in response.text


# ---- the no-run message names the city's own bundle --------------------------------------------


def test_a_city_with_no_run_is_told_to_bake_its_own_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path
) -> None:
    """Mumbai baked, Chennai not: the hint used to name MUM-2019-07-02 whatever was asked."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)

    response = client.get("/v1/nowcast/hotspots", params={"city": "chennai"})

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "no_baked_runs"
    assert "chennai" in error["message"]
    assert "make bake BUNDLE=CHN-IDF-25yr" in error["message"]
    assert "MUM-2019-07-02" not in error["message"]


def test_the_configured_city_keeps_its_demo_bundle_in_the_hint(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, CHN)

    response = client.get("/v1/nowcast/segments")

    assert response.status_code == 404
    message = response.json()["error"]["message"]
    assert "No baked run for mumbai" in message
    assert "make bake BUNDLE=MUM-2019-07-02" in message


def test_a_city_with_no_bundle_is_told_to_build_one(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a Mumbai bundle for a Chennai console, even when Mumbai's is the only one there."""
    root = tmp_path / "bundles"
    _bundle(root, "MUM-2019-07-02", "mumbai")
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(root))
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)

    response = client.get("/v1/nowcast/raster/bounds", params={"city": "chennai"})

    assert response.status_code == 404
    message = response.json()["error"]["message"]
    assert "chennai" in message
    assert "make bundle" in message
    assert "MUM-2019-07-02" not in message


def test_a_run_missing_a_product_names_its_own_citys_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, CHN, products=False)

    response = client.get("/v1/nowcast/hotspots", params={"city": "chennai"})

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "no_hotspots"
    assert "make bake BUNDLE=CHN-IDF-25yr" in error["message"]


def test_an_ops_route_with_no_run_names_the_citys_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, MUM)

    response = client.get("/v1/ops/alerts", params={"city": "chennai"})

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "no_run"
    assert "make bake BUNDLE=CHN-IDF-25yr" in error["message"]


@pytest.mark.usefixtures("two_cities")
@pytest.mark.parametrize("path", OPS_ROUTES)
def test_an_ops_route_refuses_an_unknown_city_beside_a_run_id(
    client: TestClient, path: str
) -> None:
    """The desk's routes refuse the city before reading the run, the way the depth routes do."""
    response = client.get(path, params={"run_id": CHN, "city": "atlantis"})

    assert response.status_code == 404
    _refusal(response.json())
    assert CHN not in response.text


def test_an_ops_run_missing_its_product_names_its_own_citys_bundle(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: Path
) -> None:
    """A named Chennai run with no alert product used to be told to bake MUM-2019-07-02."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    _seed(tmp_path, CHN, products=False)

    response = client.get("/v1/ops/alerts", params={"run_id": CHN})

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "run_not_found"
    assert "make bake BUNDLE=CHN-IDF-25yr" in error["message"]
    assert "MUM-2019-07-02" not in error["message"]


# ---- the helper, below the routes --------------------------------------------------------------


@pytest.mark.usefixtures("two_cities")
def test_latest_run_for_finds_nothing_for_a_city_with_no_code() -> None:
    from varuna_api.runs_util import latest_run_for

    assert latest_run_for("atlantis") is None
    assert latest_run_for("mumbai") is not None


@pytest.mark.usefixtures("two_cities")
def test_the_city_switcher_gives_a_codeless_city_no_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/v1/cities`` reads configs, and a config with no run code must not borrow a run."""
    from varuna_api.routers import city as city_router

    monkeypatch.setattr(city_router, "_config_ids", lambda: ["atlantis", "chennai", "mumbai"])

    rows = {row["id"]: row for row in client.get("/v1/cities").json()["cities"]}

    assert rows["atlantis"]["latest_run_id"] is None
    assert rows["chennai"]["latest_run_id"] == CHN
    assert rows["mumbai"]["latest_run_id"] == MUM
