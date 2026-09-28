"""The demo seed of citizen reports: what it claims, and that every claim is checked.

``services/api/varuna_api/seed_reports.json`` puts eight reports on the 2 July 2019 replay so the
dashboard, the desk and the drain X-ray have something to show before anyone presses Send. A seed
is the easiest place in the product to invent something, so every property it asserts is tested
against the thing it was taken from:

1. **Places are the register's.** Each seed names a hotspot in ``city/mumbai/hotspots.geojson``
   and lies within 150 m of it (the register's own overlap radius, SPEC.md 10.1).
2. **Times are the replay's.** 06:40-09:40 IST on 2 July 2019, the window the demo plays.
3. **Chips agree with the run.** An ankle chip is 10 +/- 8 cm (SPEC.md 11.6); the run the
   console shows at that time must put the hotspot within 1.5 sd of it.
4. **Photos are credited, linked and never copied.** Five seeds carry a Wikimedia Commons
   thumbnail, each with its author, licence and the note that it was not taken there or then.
5. **Seeded statuses say so.** A status no officer set is marked ``seeded`` on every read, and a
   real act at the desk always wins over it.
6. **Pulse never counts a seed.** The seed is merged into ``GET /v1/reports`` at read time and
   never written to the inbox, so the bundle row it restates is assimilated once.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import ops, reports
from varuna_pulse.reports import city_for_point, read_reports

ROOT = Path(__file__).resolve().parents[3]
SEED_FILE = ROOT / "services" / "api" / "varuna_api" / "seed_reports.json"
REGISTER = ROOT / "city" / "mumbai" / "hotspots.geojson"
BUNDLE = ROOT / "bundles" / "MUM-2019-07-02"
RUNS = ROOT / "data" / "runs"

PASSPHRASE = "monsoon desk 2026"
AUTH = {ops.OPS_HEADER: PASSPHRASE}

WINDOW = (
    datetime.fromisoformat("2019-07-02T06:40:00+05:30"),
    datetime.fromisoformat("2019-07-02T09:40:00+05:30"),
)
CHIPS = {"ankle": (10.0, 8.0), "knee": (45.0, 12.0), "waist": (90.0, 15.0)}
"""Centimetres and spread per chip, SPEC.md 11.6."""
CHIP_SDS = 1.5
NEAR_REGISTER_M = 150.0
PHOTO_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
PHOTO_NOTE = "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019"


@pytest.fixture(autouse=True)
def reports_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A private data dir, so nothing here writes a report or a status into the demo's own."""
    data = tmp_path / "data"
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setenv(ops.PASSPHRASE_ENV, PASSPHRASE)
    reports.reset_rate_limit()
    ops.reset_rate_limit()
    yield data
    reports.reset_rate_limit()
    ops.reset_rate_limit()


def _seed() -> dict[str, Any]:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return math.hypot(
        (lon2 - lon1) * 111_320.0 * math.cos(math.radians(lat1)), (lat2 - lat1) * 110_540.0
    )


def _chip_agrees(chip: str, depth_cm: float) -> bool:
    centre, sd = CHIPS[chip]
    return abs(depth_cm - centre) <= CHIP_SDS * sd


# ---- the file itself -------------------------------------------------------------------------
def test_the_seed_is_eight_distinct_synthetic_reports_in_the_replay_window() -> None:
    rows = _seed()["reports"]

    assert len(rows) == 8
    assert len({row["id"] for row in rows}) == 8
    assert len({row["hotspot_id"] for row in rows}) == 8, "one seed per hotspot"
    for row in rows:
        assert row["synthetic"] is True
        assert row["id"] == f"seed-{row['bundle_report_id']}"
        assert WINDOW[0] <= datetime.fromisoformat(row["ts"]) <= WINDOW[1], row["id"]
        assert city_for_point(row["lon"], row["lat"]) == "mumbai", row["id"]
        assert row["depth_hint"] in CHIPS
        assert row["text"] and len(row["text"]) <= 280


def test_every_seed_sits_at_a_registered_hotspot_and_is_named_for_it() -> None:
    if not REGISTER.is_file():
        pytest.skip("city/mumbai is not built here (make city CITY=mumbai)")
    features = json.loads(REGISTER.read_text(encoding="utf-8"))["features"]
    register = {
        f["properties"]["hotspot_id"]: (f["geometry"]["coordinates"], f["properties"]["name"])
        for f in features
    }

    for row in _seed()["reports"]:
        assert row["hotspot_id"] in register, row["id"]
        (lon, lat), name = register[row["hotspot_id"]]
        apart = _metres(row["lon"], row["lat"], lon, lat)
        assert apart <= NEAR_REGISTER_M, (row["id"], apart)
        assert abs(apart - row["metres_from_register_point"]) <= 2.0, (row["id"], apart)
        assert row["place"] == name, row["id"]


def test_each_seed_restates_a_bundle_row_and_adds_no_observation() -> None:
    path = BUNDLE / "reports.jsonl"
    if not path.is_file():
        pytest.skip("the MUM-2019-07-02 bundle is not built here (make bundle)")
    rows = {
        row["id"]: row
        for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        if row
    }
    for seed in _seed()["reports"]:
        source = rows[seed["bundle_report_id"]]
        assert source["synthetic"] is True
        for key in ("ts", "lat", "lon", "depth_hint", "text", "place"):
            assert seed[key] == source[key], (seed["id"], key)


def test_every_chip_agrees_with_the_run_the_console_shows_at_that_time() -> None:
    """The recorded check satisfies the chip rule, and so does the run as it is on disk now.

    The run is read rather than trusted: a re-bake that moved a hotspot out of its chip's range
    would make the seed contradict the map it sits on, and this is where that shows."""
    checked = 0
    for row in _seed()["reports"]:
        check = row["run_check"]
        assert check["chip_consistent"] is True, row["id"]
        assert _chip_agrees(row["depth_hint"], check["depth_p50_cm"]), row["id"]
        assert datetime.fromisoformat(check["valid_ts"]) <= datetime.fromisoformat(row["ts"])

        hotspots = RUNS / check["run_id"] / "hotspots.json"
        if not hotspots.is_file():
            continue
        entry = next(
            h
            for h in json.loads(hotspots.read_text(encoding="utf-8"))
            if h["hotspot_id"] == row["hotspot_id"]
        )
        first = datetime.fromisoformat(entry["peak_ts"]) - timedelta(
            minutes=entry["time_to_peak_min"]
        )
        step = int((datetime.fromisoformat(check["valid_ts"]) - first).total_seconds() // 300)
        assert 0 <= step < len(entry["depth_cm"]), row["id"]
        now_cm = float(entry["depth_cm"][step])
        assert _chip_agrees(row["depth_hint"], now_cm), (row["id"], now_cm)
        checked += 1
    if checked == 0:
        pytest.skip("none of the runs the seed was checked against is baked here (make bake)")


def test_five_seeds_carry_a_credited_commons_photo_linked_and_not_copied() -> None:
    photos = [row["photo"] for row in _seed()["reports"] if row.get("photo")]

    assert len(photos) == 5
    assert len({p["url"] for p in photos}) == 5, "no photo shown twice"
    for photo in photos:
        for key, width in (("thumb_url", "/330px-"), ("url", "/960px-")):
            url = urlparse(photo[key])
            assert url.scheme == "https" and url.hostname in PHOTO_HOSTS, photo[key]
            assert url.path.startswith("/wikipedia/commons/thumb/"), photo[key]
            assert width in url.path, photo[key]
        credit = photo["credit"]
        assert credit["title"] and credit["author"]
        assert credit["license"].startswith("CC BY")
        assert credit["license_url"].startswith("https://creativecommons.org/licenses/")
        assert credit["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
        assert PHOTO_NOTE in credit["note"]


# ---- read through the API --------------------------------------------------------------------
def test_the_public_list_merges_the_seed_labelled_rounded_and_newest_first(
    client: TestClient,
) -> None:
    rows = {row["id"]: row for row in _seed()["reports"]}

    body = client.get("/v1/reports", params={"origin": "seed", "limit": 50}).json()
    listed = body["reports"]

    assert {r["id"] for r in listed} == set(rows)
    stamps = [datetime.fromisoformat(r["ts"]) for r in listed]
    assert stamps == sorted(stamps, reverse=True)
    for report in listed:
        row = rows[report["id"]]
        assert report["origin"] == "seed" and report["synthetic"] is True
        assert report["city"] == "mumbai" and report["outside_aoi"] is False
        assert (report["lat"], report["lon"]) == (round(row["lat"], 3), round(row["lon"], 3))
        assert report["hotspot_id"] == row["hotspot_id"]
        assert report["place"] == row["place"]
        assert report["depth_cm"] == CHIPS[row["depth_hint"]][0]
        if row["photo"]:
            assert report["has_photo"] is True
            assert report["thumb_url"] == row["photo"]["thumb_url"]
            assert report["photo_url"] == row["photo"]["url"]
            assert report["credit"]["author"] == row["photo"]["credit"]["author"]
        else:
            assert report["has_photo"] is False
            assert report["photo_url"] is None and report["credit"] is None
    assert body["notes"][0].startswith("The 8 seed reports are synthetic")
    assert "5 carry an illustrative Wikimedia Commons photo" in body["notes"][0]
    assert "statuses on 2 of them are seeded" in body["notes"][0]


def test_a_citizen_report_lists_before_the_seed(client: TestClient) -> None:
    posted = client.post(
        "/v1/reports",
        json={
            "ts": "2019-07-02T08:40:00+05:30",
            "lat": 19.0091,
            "lon": 72.8419,
            "depth_hint": "knee",
        },
    ).json()

    listed = client.get("/v1/reports", params={"limit": 50}).json()["reports"]

    assert listed[0]["id"] == posted["id"] and listed[0]["origin"] == "citizen"
    assert [r["origin"] for r in listed[1:]] == ["seed"] * 8


def test_seeded_statuses_are_marked_and_name_no_officer(client: TestClient) -> None:
    listed = client.get("/v1/reports", params={"origin": "seed", "limit": 50}).json()["reports"]
    with_status = {r["id"]: r for r in listed if r["history"]}

    assert sorted(r["status"] for r in with_status.values()) == ["crew_sent", "seen"]
    for report in with_status.values():
        assert report["status_seeded"] is True
        for entry in report["history"]:
            assert entry["seeded"] is True
            assert "seeded" in entry["note"].lower()
            assert entry["role"] in reports.OFFICER_ROLES
            assert "user" not in entry
    for report in listed:
        if not report["history"]:
            assert report["status"] == "received" and report["status_seeded"] is False

    desk = client.get("/v1/ops/reports", params={"origin": "seed"}, headers=AUTH).json()
    for report in desk["reports"]:
        for entry in report["history"]:
            assert entry["seeded"] is True and entry["user"] is None


def test_a_real_status_at_the_desk_wins_over_a_seeded_one(
    client: TestClient, reports_env: Path
) -> None:
    seed_id = "seed-RPT-MUM-HS-04-ankle"
    before = client.get(f"/v1/reports/{seed_id}").json()
    assert before["status"] == "crew_sent" and before["status_seeded"] is True

    response = client.post(
        f"/v1/ops/reports/{seed_id}/status",
        json={"status": "resolved", "user": "A. Patil", "role": "field crew"},
        headers=AUTH,
    )

    assert response.status_code == 200, response.text
    after = client.get(f"/v1/reports/{seed_id}").json()
    assert after["status"] == "resolved" and after["status_seeded"] is False
    assert [(h["status"], h["seeded"]) for h in after["history"]] == [
        ("seen", True),
        ("crew_sent", True),
        ("resolved", False),
    ]
    assert "A. Patil" not in json.dumps(after)
    log = (reports_env / "ops" / "mumbai.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1, "a seeded status is never written to an ops log"


def test_a_seed_report_reads_on_its_own_with_its_credit(client: TestClient) -> None:
    report = client.get("/v1/reports/seed-RPT-MUM-HS-01-ankle").json()

    assert report["place"].startswith("Hindmata junction")
    assert report["bundle_report_id"] == "RPT-MUM-HS-01-ankle"
    assert report["credit"]["license"] == "CC BY 2.0"
    assert client.get("/v1/reports/seed-RPT-MUM-HS-01-ankle/photo").status_code == 404, (
        "a seed's photo is a Commons link; this API never serves it"
    )


# ---- Pulse -----------------------------------------------------------------------------------
def test_pulse_never_reads_a_seed(client: TestClient, reports_env: Path) -> None:
    if not (BUNDLE / "reports.jsonl").is_file():
        pytest.skip("the MUM-2019-07-02 bundle is not built here (make bundle)")
    client.get("/v1/reports", params={"limit": 50})
    posted = client.post(
        "/v1/reports",
        json={
            "ts": "2019-07-02T08:40:00+05:30",
            "lat": 19.0091,
            "lon": 72.8419,
            "depth_hint": "knee",
        },
    ).json()
    inbox = reports_env / "reports" / "inbox.jsonl"

    observed = read_reports(
        BUNDLE,
        until=datetime.fromisoformat("2019-07-02T09:40:00+05:30"),
        inbox=inbox,
        city="mumbai",
    )

    rows = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == [posted["id"]]
    assert not [o.report_id for o in observed if o.report_id.startswith("seed-")]
    baseline = read_reports(
        BUNDLE, until=datetime.fromisoformat("2019-07-02T09:40:00+05:30"), city="mumbai"
    )
    assert len(observed) - len(baseline) <= 1, "the inbox adds the one citizen report, no seed"
