"""``GET /v1/nowcast/segments?bbox=`` answers for the streets inside the box (SPEC.md 12, 14).

Section 12 describes the call as "segment quantiles ... in bbox" and section 14 budgets "segments
in bbox" at 200 ms. The handler used to ignore the parameter - it was declared on a stub that
shadowed the real route in the OpenAPI document - and served the whole AOI whatever was asked.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"

POINTS = {
    "S-HINDMATA": (72.8413, 19.0105),
    "S-SION": (72.8620, 19.0390),
    "S-ANDHERI": (72.8440, 19.1190),
}
"""Three segments' midpoints, one inside the test box and two outside it."""

BOX = "72.83,19.00,72.85,19.02"
"""Around Hindmata only."""


@pytest.fixture
def run_with_three_streets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    run = tmp_path / "runs" / RUN
    (run / "depth").mkdir(parents=True)
    # A run is served only once it has depth products (`_resolve`), so the fixture carries them.
    (run / "depth" / "bounds.json").write_text(
        json.dumps({"wgs84": [72.8, 19.0, 72.9, 19.1]}), encoding="utf-8"
    )
    (run / "run.json").write_text(
        json.dumps({"run_id": RUN, "city": "mumbai", "notes": [], "ensemble_n": 50}),
        encoding="utf-8",
    )
    (run / "segments_wet.json").write_text(
        json.dumps(
            {
                "run_id": RUN,
                "valid_ts": ["2019-07-02T08:45:00+05:30"],
                "min_depth_cm": 5.0,
                "n_segments_total": 3,
                "n_segments_wet": 3,
                "depth_cm": {sid: [20.0] for sid in POINTS},
                "p_gt": {"30": {sid: [0.4] for sid in POINTS}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "varuna_products.depth.segment_points", lambda _root: dict(POINTS), raising=True
    )
    return run


def test_a_box_returns_only_the_streets_inside_it(
    client: TestClient, run_with_three_streets: Path
) -> None:
    body = client.get("/v1/nowcast/segments", params={"run_id": RUN, "bbox": BOX}).json()
    assert set(body["depth_cm"]) == {"S-HINDMATA"}
    # Every per-segment map is cut the same way, or the probability layer would draw streets
    # the depth layer does not know.
    assert set(body["p_gt"]["30"]) == {"S-HINDMATA"}
    assert body["n_segments_wet"] == 1


def test_no_box_is_the_whole_run(client: TestClient, run_with_three_streets: Path) -> None:
    body = client.get("/v1/nowcast/segments", params={"run_id": RUN}).json()
    assert set(body["depth_cm"]) == set(POINTS)


@pytest.mark.parametrize("bad", ["72.8,19.0,72.9", "a,b,c,d", "72.9,19.0,72.8,19.1"])
def test_a_malformed_box_is_refused_with_what_to_send(
    client: TestClient, run_with_three_streets: Path, bad: str
) -> None:
    response = client.get("/v1/nowcast/segments", params={"run_id": RUN, "bbox": bad})
    assert response.status_code == 400
    assert "minlon,minlat,maxlon,maxlat" in response.json()["error"]["message"]


def test_the_contract_describes_the_handler_that_answers(client: TestClient) -> None:
    """The OpenAPI document lists the real route's parameters, bbox among them, not a stub's."""
    params = {
        p["name"]
        for p in client.get("/openapi.json").json()["paths"]["/v1/nowcast/segments"]["get"][
            "parameters"
        ]
    }
    assert {"run_id", "city", "min_depth_cm", "bbox"} <= params
    assert "profile" not in params
