"""Citizen reports: what is accepted, what is kept of a photo, and what the public can read.

``POST /v1/reports`` is the one endpoint anyone on the internet can write to, so the tests are
about the edges rather than the happy path:

1. **A photo is re-encoded, never stored as sent.** A JPEG carrying GPS in its EXIF comes back
   out with no EXIF at all, upright, at most 1280 px, with a 330 px thumbnail beside it.
2. **Every refusal leaves nothing behind.** A body over 1 MB is refused before it is parsed, a
   photo that is not a JPEG, PNG or WebP is refused, a seventh report in a minute is refused.
3. **A report belongs to a city or to none.** One from outside both boxes is kept and never
   listed; the boxes are the city configs' own.
4. **The public never gets more than it needs.** Coordinates rounded to 110 m, dismissed reports
   gone, an officer named by role - and the exact view is behind the desk's passphrase.

Every test runs against a temporary ``VARUNA_DATA_DIR``: the inbox and the photo store are files
on disk, and a test that wrote into the repository's own ``data/reports`` would leave a report on
the demo map.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from varuna_api.routers import ops, reports
from varuna_pulse.reports import REPORT_AOIS

PASSPHRASE = "monsoon desk 2026"
AUTH = {ops.OPS_HEADER: PASSPHRASE}

HINDMATA = (72.8419, 19.0091)
VELACHERY = (80.22, 12.98)
PUNE = (73.856, 18.52)
SEED_ID = "seed-RPT-MUM-HS-01-ankle"
"""Hindmata's seed report; `test_reports_seed.py` holds the seed itself."""


@pytest.fixture(autouse=True)
def reports_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A private data dir, photos on, the default budget, a desk passphrase, fresh limits."""
    data = tmp_path / "data"
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.delenv(reports.PHOTO_ENV, raising=False)
    monkeypatch.delenv(reports.PHOTO_BUDGET_ENV, raising=False)
    monkeypatch.setenv(ops.PASSPHRASE_ENV, PASSPHRASE)
    reports.reset_rate_limit()
    ops.reset_rate_limit()
    yield data
    reports.reset_rate_limit()
    ops.reset_rate_limit()


def _report(point: tuple[float, float] = HINDMATA, **extra: Any) -> dict[str, Any]:
    lon, lat = point
    return {
        "ts": "2019-07-02T08:40:00+05:30",
        "lat": lat,
        "lon": lon,
        "depth_hint": "knee",
        "source": "public-report",
        **extra,
    }


def _jpeg(size: tuple[int, int] = (2000, 1000), *, orientation: int | None = 6) -> bytes:
    """A JPEG a phone might send: GPS in the EXIF, and rotated by the orientation tag."""
    image = Image.new("RGB", size, (40, 90, 160))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (19.0, 0.0, 44.0)
    gps[3] = "E"
    gps[4] = (72.0, 50.0, 30.0)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif, quality=90)
    return buffer.getvalue()


def _png(size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 128)).save(buffer, "PNG")
    return buffer.getvalue()


def _data_url(raw: bytes, kind: str = "jpeg") -> str:
    return f"data:image/{kind};base64,{base64.b64encode(raw).decode('ascii')}"


def _inbox(data: Path) -> list[dict[str, Any]]:
    path = data / "reports" / "inbox.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _photos(data: Path) -> list[str]:
    folder = data / "reports" / "photos"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _post(client: TestClient, body: dict[str, Any], **headers: str) -> Any:
    return client.post("/v1/reports", json=body, headers=headers)


# ---- accepting a report ---------------------------------------------------------------------
def test_a_report_inside_mumbai_is_tagged_with_its_city(
    client: TestClient, reports_env: Path
) -> None:
    response = _post(client, _report(text="Water up to the knee outside the cinema."))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["city"] == "mumbai"
    assert body["outside_aoi"] is False
    assert body["status"] == "received"
    assert body["feedback_streets"] is None, "Pulse's count, not this request's"
    assert body["photo_stored"] is False and body["photo_attached"] is False
    [row] = _inbox(reports_env)
    assert row["id"] == body["id"]
    assert row["city"] == "mumbai"
    assert row["depth_cm"] == 45.0
    assert row["text"] == "Water up to the knee outside the cinema."
    assert row["synthetic"] is False


def test_a_report_inside_chennai_is_tagged_chennai(client: TestClient) -> None:
    body = _post(client, _report(VELACHERY)).json()

    assert body["city"] == "chennai"
    assert body["outside_aoi"] is False


def test_a_report_from_outside_both_cities_is_kept_and_never_listed(
    client: TestClient, reports_env: Path
) -> None:
    response = _post(client, _report(PUNE))

    assert response.status_code == 202
    body = response.json()
    assert body["city"] is None
    assert body["outside_aoi"] is True
    assert "outside the areas VARUNA forecasts" in body["message"]
    assert "not shown on the map or used in a forecast" in body["message"]
    [row] = _inbox(reports_env)
    assert row["outside_aoi"] is True and row["city"] is None

    listed = client.get("/v1/reports", params={"origin": "citizen"}).json()
    assert listed["reports"] == []
    assert listed["n_outside_hidden"] == 1
    own = client.get(f"/v1/reports/{body['id']}").json()
    assert own["outside_aoi"] is True, "the reporter can still see it was kept, and why"


def test_the_report_boxes_are_the_city_configs_own() -> None:
    from varuna_city.config import load_city_config

    for city, box in REPORT_AOIS.items():
        assert load_city_config(city).bbox.as_tuple() == pytest.approx(box), city


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"depth_hint": "shoulder"}, "validation_error"),
        ({"ts": "2019-07-02T08:40:00"}, "bad_time"),
        ({"ts": "yesterday"}, "bad_time"),
        ({"lat": 95.0}, "validation_error"),
        ({"text": "x" * 281}, "validation_error"),
    ],
)
def test_a_malformed_report_is_refused_and_leaves_nothing(
    client: TestClient, reports_env: Path, change: dict[str, Any], code: str
) -> None:
    response = _post(client, {**_report(), **change})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert _inbox(reports_env) == []


def test_a_depth_chip_is_read_whatever_its_case(client: TestClient, reports_env: Path) -> None:
    assert _post(client, _report(depth_hint="Waist")).status_code == 202
    assert _inbox(reports_env)[0]["depth_hint"] == "waist"


# ---- size and rate ----------------------------------------------------------------------------
def test_a_body_over_a_megabyte_is_refused_before_it_is_parsed(
    client: TestClient, reports_env: Path
) -> None:
    oversized = json.dumps({**_report(), "text": "x" * 1_100_000})

    response = client.post(
        "/v1/reports",
        content=oversized,
        headers={"content-type": "application/json", "origin": "http://localhost:3000"},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "report_too_large"
    assert "Nothing was stored" in error["message"]
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000", (
        "the refusal sits inside CORS, so the report flow can read it"
    )
    assert _inbox(reports_env) == []


def test_a_chunked_body_is_counted_as_it_arrives(client: TestClient, reports_env: Path) -> None:
    """No Content-Length to trust: the limit has to be met while the body streams in."""

    def chunks() -> Iterator[bytes]:
        yield b'{"lat": 19.0, "lon": 72.84, "depth_hint": "knee", "text": "'
        for _ in range(12):
            yield b"x" * 100_000
        yield b'"}'

    response = client.post(
        "/v1/reports", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert _inbox(reports_env) == []


def test_a_photo_field_over_700_kb_is_refused(client: TestClient, reports_env: Path) -> None:
    photo = "data:image/jpeg;base64," + "A" * (reports.PHOTO_DATA_URL_MAX + 10)

    response = _post(client, _report(photo_data_url=photo))

    assert response.status_code == 422
    assert _inbox(reports_env) == []


def test_the_seventh_report_in_a_minute_from_one_phone_is_refused(
    client: TestClient, reports_env: Path
) -> None:
    phone = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(reports.REPORTS_PER_MINUTE):
        assert _post(client, _report(), **phone).status_code == 202

    refused = _post(client, _report(), **phone)
    other_phone = _post(client, _report(), **{"X-Forwarded-For": "198.51.100.4"})

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert "nothing was stored" in refused.json()["error"]["message"]
    assert other_phone.status_code == 202, "the limit is per phone, not per API"
    assert len(_inbox(reports_env)) == reports.REPORTS_PER_MINUTE + 1


# ---- photos -----------------------------------------------------------------------------------
def test_a_photo_loses_its_gps_and_is_stored_upright_with_a_thumbnail(
    client: TestClient, reports_env: Path
) -> None:
    sent = _jpeg()
    assert Image.open(io.BytesIO(sent)).getexif().get_ifd(0x8825), "the fixture carries GPS"

    body = _post(client, _report(photo_data_url=_data_url(sent))).json()

    assert body["photo_stored"] is True, body
    report_id = body["id"]
    folder = reports_env / "reports" / "photos"
    assert _photos(reports_env) == [f"{report_id}.jpg", f"{report_id}.thumb.jpg"]
    for name, longest in ((f"{report_id}.jpg", 1280), (f"{report_id}.thumb.jpg", 330)):
        raw = (folder / name).read_bytes()
        image = Image.open(io.BytesIO(raw))
        assert image.format == "JPEG"
        assert dict(image.getexif()) == {}, f"{name} kept EXIF"
        assert b"Exif" not in raw and b"GPS" not in raw, f"{name} kept metadata bytes"
        # Orientation 6 is a quarter turn: 2000 x 1000 as sent is 1000 x 2000 upright.
        assert image.height == longest and image.width == longest // 2, name
    assert body["thumb_url"] == f"/v1/reports/{report_id}/photo?size=thumb"
    [row] = _inbox(reports_env)
    assert row["has_photo"] is True and row["photo"]["w"] == 1000 and row["photo"]["h"] == 2000
    assert "photo_data_url" not in row, "the data URL is never written to the inbox"


def test_a_transparent_png_is_accepted_and_flattened_to_jpeg(
    client: TestClient, reports_env: Path
) -> None:
    body = _post(client, _report(photo_data_url=_data_url(_png(), "png"))).json()

    assert body["photo_stored"] is True
    thumb = client.get(body["thumb_url"])
    assert Image.open(io.BytesIO(thumb.content)).mode == "RGB"


@pytest.mark.parametrize(
    "photo",
    [
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        _data_url(b"<html><script>alert(1)</script></html>", "png"),
        _data_url(b"GIF89a" + b"\x00" * 40, "jpeg"),
        "data:image/jpeg;base64,not base64 at all!",
        "https://example.com/photo.jpg",
    ],
    ids=["gif", "svg", "html-as-png", "gif-as-jpeg", "bad-base64", "url"],
)
def test_a_photo_that_is_not_a_jpeg_png_or_webp_is_refused(
    client: TestClient, reports_env: Path, photo: str
) -> None:
    response = _post(client, _report(photo_data_url=photo))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "bad_photo"
    assert "Nothing was stored" in response.json()["error"]["message"]
    assert _inbox(reports_env) == []
    assert _photos(reports_env) == []


def test_a_photo_declaring_too_many_pixels_is_refused_before_decoding(
    client: TestClient, reports_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reports, "MAX_PHOTO_PIXELS", 100 * 100)

    response = _post(client, _report(photo_data_url=_data_url(_jpeg((200, 200)))))

    assert response.status_code == 422
    assert "limit is" in response.json()["error"]["message"]
    assert _photos(reports_env) == []


def test_past_the_budget_the_report_is_kept_without_its_photo(
    client: TestClient, reports_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(reports.PHOTO_BUDGET_ENV, "0.001")

    body = _post(client, _report(photo_data_url=_data_url(_jpeg()))).json()

    assert body["accepted"] is True
    assert body["photo_stored"] is False
    assert "photo store on this API is full" in body["photo_note"]
    assert body["photo_note"] in body["message"]
    assert _photos(reports_env) == []
    [row] = _inbox(reports_env)
    assert row["photo_attached"] is True and row["has_photo"] is False


def test_with_photos_off_the_report_is_kept_and_says_where_photos_live(
    client: TestClient, reports_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(reports.PHOTO_ENV, "0")

    body = _post(client, _report(photo_data_url=_data_url(_jpeg()))).json()

    assert body["photo_stored"] is False
    assert "stored only on the demo laptop" in body["photo_note"]
    assert _photos(reports_env) == []
    assert len(_inbox(reports_env)) == 1


def test_the_photo_is_served_as_jpeg_that_the_browser_may_not_sniff(client: TestClient) -> None:
    body = _post(client, _report(photo_data_url=_data_url(_jpeg()))).json()

    for size in ("thumb", "full"):
        served = client.get(f"/v1/reports/{body['id']}/photo", params={"size": size})
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert served.headers["x-content-type-options"] == "nosniff"
        assert "max-age" in served.headers["cache-control"]
        assert served.content[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/reports/..%2F..%2Fdata%2Fops%2Fmumbai.jsonl/photo",
        "/v1/reports/rpt-1-abc/photo",
        f"/v1/reports/{SEED_ID}/photo",
        "/v1/reports/rpt-1790000000000-abcdef/photo",
    ],
)
def test_only_an_id_this_api_minted_reaches_the_file_system(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert "image" not in response.headers.get("content-type", "")


def test_the_photo_path_refuses_anything_but_a_minted_id() -> None:
    for bad in ("../x", "rpt-1-abc", "rpt-1790000000000-abcdef/../../x", ""):
        with pytest.raises(ValueError, match="not a report id"):
            reports._photo_path(bad, "full")


# ---- reading ----------------------------------------------------------------------------------
def test_public_coordinates_are_rounded_and_exact_ones_are_behind_the_passphrase(
    client: TestClient,
) -> None:
    lon, lat = 72.841937, 19.009148
    report_id = _post(client, _report((lon, lat))).json()["id"]

    public = client.get("/v1/reports", params={"origin": "citizen"}).json()["reports"][0]
    refused = client.get("/v1/ops/reports")
    desk = client.get("/v1/ops/reports", headers=AUTH).json()

    assert public["id"] == report_id
    assert (public["lat"], public["lon"]) == (19.009, 72.842)
    assert refused.status_code == 401
    [exact] = [r for r in desk["reports"] if r["id"] == report_id]
    assert (exact["lat"], exact["lon"]) == (lat, lon)


def _bisect(
    listed: Any, axis: int, lo: float, hi: float, other: tuple[float, float], steps: int = 36
) -> float:
    """Halve a bbox along one axis until it pins the point ``listed`` still answers inside it."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if axis == 0:
            box = (lo, other[0], mid, other[1])
        else:
            box = (other[0], lo, other[1], mid)
        if listed(",".join(repr(v) for v in box)):
            hi = mid
        else:
            lo = mid
    return hi


def test_a_bbox_cannot_bisect_the_public_list_down_to_the_exact_point(client: TestClient) -> None:
    """The public bbox tests the rounded point, so halving it converges on what the list prints.

    The same probe against the desk's exact read recovers the posted point to 1e-9, which is what
    the public read did before its box was tested against the rounded point.
    """
    lon, lat = 72.8419437, 19.0091234
    report_id = _post(client, _report((lon, lat))).json()["id"]

    def listed(path: str, headers: dict[str, str]) -> Any:
        def ask(bbox: str) -> bool:
            response = client.get(path, params={"origin": "citizen", "bbox": bbox}, headers=headers)
            assert response.status_code == 200, response.text
            return report_id in {r["id"] for r in response.json()["reports"]}

        return ask

    public = listed("/v1/reports", {})
    desk = listed("/v1/ops/reports", AUTH)

    public_lon = _bisect(public, 0, 72.80, 72.90, (18.9, 19.2))
    public_lat = _bisect(public, 1, 18.95, 19.05, (72.80, 72.90))
    desk_lon = _bisect(desk, 0, 72.80, 72.90, (18.9, 19.2))
    desk_lat = _bisect(desk, 1, 18.95, 19.05, (72.80, 72.90))

    assert abs(public_lon - 72.842) < 1e-9 and abs(public_lat - 19.009) < 1e-9
    assert abs(public_lon - lon) > 1e-5 and abs(public_lat - lat) > 1e-5
    assert abs(desk_lon - lon) < 1e-9 and abs(desk_lat - lat) < 1e-9, "the probe can find it"


def test_a_desk_read_does_not_spend_the_write_window(client: TestClient) -> None:
    for _ in range(ops.WRITES_PER_MINUTE + 5):
        assert client.get("/v1/ops/reports", headers=AUTH).status_code == 200


def test_filters_by_city_box_status_and_time(client: TestClient) -> None:
    mumbai = _post(client, _report()).json()["id"]
    chennai = _post(client, _report(VELACHERY)).json()["id"]
    client.post(f"/v1/ops/reports/{mumbai}/status", json={"status": "seen"}, headers=AUTH)

    def ids(**params: Any) -> list[str]:
        response = client.get("/v1/reports", params={"origin": "citizen", **params})
        assert response.status_code == 200, response.text
        return [r["id"] for r in response.json()["reports"]]

    assert ids(city="mumbai") == [mumbai]
    assert ids(city="chennai") == [chennai]
    assert ids(bbox="80.1,12.9,80.3,13.1") == [chennai]
    assert ids(status="seen") == [mumbai]
    assert ids(status="received") == [chennai]
    assert set(ids(since="2026-01-01T00:00:00+05:30")) == {mumbai, chennai}
    assert ids(since="2999-01-01T00:00:00+05:30") == []
    assert client.get("/v1/reports", params={"city": "pune"}).status_code == 422
    assert client.get("/v1/reports", params={"bbox": "1,2,3"}).status_code == 422


def test_the_demo_seed_is_labelled_synthetic_and_credits_every_photo(client: TestClient) -> None:
    seeds = client.get("/v1/reports", params={"origin": "seed", "limit": 50}).json()["reports"]

    assert len(seeds) >= 5
    for seed in seeds:
        assert seed["synthetic"] is True and seed["origin"] == "seed"
        assert seed["city"] == "mumbai"
        assert seed["ts"].startswith("2019-07-02T0"), "inside the replay window"
        if seed["has_photo"]:
            credit = seed["credit"]
            assert credit["author"] and credit["license"].startswith("CC BY")
            assert credit["source_url"].startswith("https://commons.wikimedia.org/")
            assert "not taken at this spot or on 2 July 2019" in credit["note"]
            assert seed["thumb_url"].startswith("https://")
            assert "wikimedia.org/wikipedia/commons/" in seed["thumb_url"]


def test_each_seed_is_the_bundles_own_synthetic_report() -> None:
    """The seed invents no report: place, time, chip and words are the bundle's synthetic row."""
    bundle = Path(__file__).resolve().parents[3] / "bundles" / "MUM-2019-07-02" / "reports.jsonl"
    if not bundle.is_file():
        pytest.skip("the MUM-2019-07-02 bundle is not built here (make bundle)")
    rows = {
        row["id"]: row
        for row in (json.loads(line) for line in bundle.read_text(encoding="utf-8").splitlines())
    }
    for seed in reports._seed_rows():
        source = rows[seed["bundle_report_id"]]
        assert source["synthetic"] is True
        for key in ("ts", "lat", "lon", "depth_hint", "text", "place"):
            assert seed[key] == source[key], (seed["id"], key)


def test_the_seed_never_reaches_the_inbox_pulse_reads(
    client: TestClient, reports_env: Path
) -> None:
    client.get("/v1/reports")
    _post(client, _report())

    assert [row["id"] for row in _inbox(reports_env) if str(row["id"]).startswith("seed-")] == []


# ---- statuses ---------------------------------------------------------------------------------
def test_statuses_are_folded_in_at_read_time_and_name_the_role_not_the_officer(
    client: TestClient, reports_env: Path
) -> None:
    report_id = _post(client, _report()).json()["id"]
    before = (reports_env / "reports" / "inbox.jsonl").read_bytes()

    seen = client.post(
        f"/v1/ops/reports/{report_id}/status",
        json={"status": "seen", "user": "A. Patil"},
        headers=AUTH,
    )
    sent = client.post(
        f"/v1/ops/reports/{report_id}/status",
        json={"status": "crew_sent", "user": "A. Patil", "role": "control room", "note": "Crew 4"},
        headers=AUTH,
    )

    assert seen.status_code == 200 and sent.status_code == 200, sent.text
    assert sent.json()["report"]["history"][-1]["user"] == "A. Patil", "the desk sees the name"
    public = client.get(f"/v1/reports/{report_id}").json()
    assert public["status"] == "crew_sent"
    assert [h["status"] for h in public["history"]] == ["seen", "crew_sent"]
    assert public["history"][-1]["role"] == "control room"
    assert public["history"][-1]["note"] == "Crew 4"
    assert "A. Patil" not in json.dumps(public)
    assert (reports_env / "reports" / "inbox.jsonl").read_bytes() == before, "append-only"
    log = (reports_env / "ops" / "mumbai.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in log] == ["report_status", "report_status"]


def test_a_dismissed_report_leaves_every_public_read_and_stays_on_the_desk(
    client: TestClient,
) -> None:
    body = _post(client, _report(text="Spam", photo_data_url=_data_url(_jpeg()))).json()
    report_id = body["id"]

    client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "dismissed"}, headers=AUTH)

    listed = client.get("/v1/reports", params={"origin": "citizen"}).json()
    assert listed["reports"] == [] and listed["n_dismissed_hidden"] == 1
    own = client.get(f"/v1/reports/{report_id}").json()
    assert own["status"] == "dismissed"
    assert "text" not in own and "lat" not in own and "photo_url" not in own
    assert client.get(body["thumb_url"]).status_code == 404, "its photo stops being served"
    desk = client.get("/v1/ops/reports", params={"status": "dismissed"}, headers=AUTH).json()
    assert [r["id"] for r in desk["reports"]] == [report_id]
    assert desk["reports"][0]["text"] == "Spam"

    client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "seen"}, headers=AUTH)
    assert client.get(body["thumb_url"]).status_code == 200, "a later status brings it back"


def test_the_hidden_counts_are_of_reports_the_query_would_have_returned(
    client: TestClient,
) -> None:
    """A count of hidden reports names only reports inside the query's own city, box and time."""
    dismissed = client.post(
        f"/v1/ops/reports/{SEED_ID}/status", json={"status": "dismissed"}, headers=AUTH
    )
    assert dismissed.status_code == 200, dismissed.text
    assert _post(client, _report(PUNE)).json()["outside_aoi"] is True

    def hidden(**params: Any) -> tuple[int, int]:
        response = client.get("/v1/reports", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert SEED_ID not in [r["id"] for r in body["reports"]], "dismissed stays hidden"
        return body["n_dismissed_hidden"], body["n_outside_hidden"]

    assert hidden() == (1, 1)
    assert hidden(city="mumbai") == (1, 0), "Pune is in no city"
    assert hidden(city="chennai") == (0, 0), "a dismissed Mumbai report is not Chennai's"
    assert hidden(since="2999-01-01T00:00:00+05:30") == (0, 0)
    assert hidden(bbox="80.1,12.9,80.3,13.1") == (0, 0)
    assert hidden(origin="citizen") == (0, 1), "the dismissed report is a seed"
    assert hidden(origin="seed") == (1, 0)
    assert hidden(status="seen") == (0, 0), "neither would ever read seen"
    assert hidden(status="received") == (0, 1)


def test_a_status_on_a_seed_report_is_recorded_too(client: TestClient) -> None:
    response = client.post(
        f"/v1/ops/reports/{SEED_ID}/status", json={"status": "resolved"}, headers=AUTH
    )

    assert response.status_code == 200, response.text
    assert client.get(f"/v1/reports/{SEED_ID}").json()["status"] == "resolved"


def test_a_status_for_an_unknown_report_or_an_unknown_status_is_refused(
    client: TestClient, reports_env: Path
) -> None:
    report_id = _post(client, _report()).json()["id"]

    unknown = client.post(
        "/v1/ops/reports/rpt-1790000000000-abcdef/status", json={"status": "seen"}, headers=AUTH
    )
    bad = client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "fixed"}, headers=AUTH)
    ungated = client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "seen"})

    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "report_not_found"
    assert bad.status_code == 422
    assert ungated.status_code == 401
    assert not (reports_env / "ops" / "mumbai.jsonl").exists(), "no refusal wrote anything"


def test_the_ops_log_lists_report_statuses_by_kind(client: TestClient) -> None:
    report_id = _post(client, _report()).json()["id"]
    client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "seen"}, headers=AUTH)

    log = client.get("/v1/ops/log", params={"kind": "report_status"}).json()

    assert [e["report_id"] for e in log["entries"]] == [report_id]
    assert [e["role"] for e in log["entries"]] == ["ward officer"]


def test_the_ungated_ops_log_never_names_the_officer_on_a_report_status(
    client: TestClient,
) -> None:
    """The status response promises the reporter never sees the name; the open log keeps that."""
    report_id = _post(client, _report()).json()["id"]
    posted = client.post(
        f"/v1/ops/reports/{report_id}/status",
        json={"status": "dismissed", "user": "Asha Patil", "note": "Duplicate of an earlier one"},
        headers=AUTH,
    )
    closure = client.post(
        "/v1/ops/closures",
        json={
            "segment_id": "seg-1",
            "reason": "Water over the kerb",
            "user": "R. Shinde",
            "city": "mumbai",
        },
        headers=AUTH,
    )
    assert posted.status_code == 200, posted.text
    assert closure.status_code == 200, closure.text

    for params in ({"city": "mumbai"}, {"city": "mumbai", "kind": "report_status"}):
        for headers in ({}, {ops.OPS_HEADER: "not the passphrase"}):
            response = client.get("/v1/ops/log", params=params, headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            assert "Asha Patil" not in json.dumps(body), (params, headers)
            assert body["officer_names"] == "withheld"
            [status] = [e for e in body["entries"] if e["kind"] == "report_status"]
            assert status["report_id"] == report_id and status["role"] == "ward officer"
            assert status["user_withheld"] is True and "user" not in status

    desk = client.get(
        "/v1/ops/log", params={"city": "mumbai", "kind": "report_status"}, headers=AUTH
    ).json()
    assert desk["officer_names"] == "shown"
    assert [e["user"] for e in desk["entries"]] == ["Asha Patil"], "the desk still sees it"
    opened = client.get("/v1/ops/log", params={"city": "mumbai", "kind": "closure"}).json()
    assert [e["user"] for e in opened["entries"]] == ["R. Shinde"], "a closure stays public"


def test_the_ops_log_refuses_a_report_status_it_cannot_fold() -> None:
    from varuna_route import ops_overlay

    with pytest.raises(ValueError, match="Unknown report status"):
        ops_overlay.append("mumbai", {"kind": "report_status", "report_id": "x", "status": "done"})
    with pytest.raises(ValueError, match="must name the report_id"):
        ops_overlay.append("mumbai", {"kind": "report_status", "status": "seen"})
    assert tuple(reports.REPORT_STATUSES) == ops_overlay.REPORT_STATES


def _digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_no_report_or_status_changes_a_byte_of_a_run(client: TestClient, reports_env: Path) -> None:
    """Rule 8: reports and their statuses sit beside the runs, never inside one."""
    run = reports_env / "runs" / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.3-baked"
    run.mkdir(parents=True)
    (run / "run.json").write_text('{"run_id": "fixture"}', encoding="utf-8")
    (run / "alerts.json").write_text('{"alerts": []}', encoding="utf-8")
    before = _digest(reports_env / "runs")

    report_id = _post(client, _report(photo_data_url=_data_url(_jpeg()))).json()["id"]
    client.post(f"/v1/ops/reports/{report_id}/status", json={"status": "seen"}, headers=AUTH)

    assert _digest(reports_env / "runs") == before
