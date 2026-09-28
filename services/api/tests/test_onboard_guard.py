"""City-in-a-box can be switched off where a build would take the demo down (SPEC.md 7.9, 12).

On 13 September 2026 a Chennai build started against the public Railway API filled its 500 MB
volume, failed at `export` with ENOSPC, and left the API unable to come back. The image now sets
``VARUNA_ONBOARD_ENABLED=0``; these tests pin that the switch refuses before any job exists and
that the laptop default still accepts a build.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from varuna_schemas.settings import get_settings


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    """`get_settings` is cached for the process; each case reads the environment it sets."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_disabled_deployment_refuses_with_the_envelope_and_starts_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    import varuna_api.routers.onboard as onboard_router

    started: list[object] = []
    monkeypatch.setattr(onboard_router, "start_job", lambda **kw: started.append(kw))
    monkeypatch.setenv("VARUNA_ONBOARD_ENABLED", "0")

    res = client.post("/v1/onboard", json={"city": "chennai"})

    assert res.status_code == 403
    error = res.json()["error"]
    assert error["code"] == "onboard_disabled"
    # Section 6.8: the refusal names what to do instead, not just that it refused.
    assert "make demo" in error["message"]
    assert "make city CITY=chennai" in error["message"]
    assert started == [], "a refused request must not start a build"


@pytest.mark.parametrize("asked", ["mumbai", "Mumbai", " mumbai "])
def test_the_replay_city_is_refused_and_nothing_is_rebuilt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None, asked: str
) -> None:
    """One click on /onboard?city=mumbai would rewrite city/mumbai/ under the demo, and its
    design-storm run would displace the 2 July 2019 bake as Mumbai's default run."""
    import varuna_api.routers.onboard as onboard_router

    started: list[object] = []
    monkeypatch.setattr(onboard_router, "start_job", lambda **kw: started.append(kw))
    monkeypatch.delenv("VARUNA_ONBOARD_ENABLED", raising=False)
    monkeypatch.setenv("VARUNA_CITY", "mumbai")

    res = client.post("/v1/onboard", json={"city": asked, "design_storm": "MUM-IDF-25yr"})

    assert res.status_code == 409, res.text
    error = res.json()["error"]
    assert error["code"] == "city_is_replay_city"
    # Section 6.8: it says what happened and what to do instead.
    assert "city/mumbai/" in error["message"]
    assert "city=chennai" in error["message"]
    assert started == [], "a refused request must not start a build"


def test_the_laptop_default_still_accepts_a_build(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    import varuna_api.routers.onboard as onboard_router

    class FakeState:
        def to_dict(self) -> dict[str, str]:
            return {"job_id": "onboard-test", "status": "running"}

    started: list[dict[str, object]] = []

    def fake_start(**kwargs: object) -> FakeState:
        started.append(kwargs)
        return FakeState()

    monkeypatch.setattr(onboard_router, "start_job", fake_start)
    monkeypatch.delenv("VARUNA_ONBOARD_ENABLED", raising=False)

    res = client.post("/v1/onboard", json={"city": "chennai"})

    assert res.status_code == 202, res.text
    assert res.json()["job_id"] == "onboard-test"
    assert len(started) == 1 and started[0]["city"] == "chennai"
