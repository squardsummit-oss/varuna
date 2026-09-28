from __future__ import annotations

import pytest
from varuna_schemas.settings import Settings, get_settings, reload_settings

_ENV_NAMES = (
    "VARUNA_CITY",
    "VARUNA_BUNDLE",
    "VARUNA_REPLAY_SPEED",
    "VARUNA_MODE",
    "VARUNA_OFFLINE",
    "VARUNA_API_PORT",
    "API_PORT",
    "VARUNA_UI_PORT",
    "UI_PORT",
    "VARUNA_CORS_ORIGINS",
    "CORS_ORIGINS",
    "MAPPLS_KEY",
    "TOMTOM_KEY",
    "OPENTOPO_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "WHATSAPP_CLOUD_TOKEN",
    "WHATSAPP_CLOUD_PHONE_ID",
    "POSTGRES_URL",
    "REDIS_URL",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env_file(clean_env: None) -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.varuna_city == "mumbai"
    assert s.varuna_bundle == "MUM-2019-07-02"
    assert s.varuna_replay_speed == 30.0
    assert s.varuna_mode == "replay"
    assert s.varuna_offline is False
    assert s.api_port == 8000
    assert s.ui_port == 3000
    assert s.cors_origins == ["http://localhost:3000"]
    assert s.mappls_key is None
    assert s.postgres_url is None
    assert s.is_replay and not s.is_live
    assert s.api_url == "http://localhost:8000"
    assert s.ws_url == "ws://localhost:8000/v1/live"
    assert not s.has_sms_sender and not s.has_whatsapp_sender


def test_env_overrides_and_csv(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VARUNA_MODE", "live")
    monkeypatch.setenv("VARUNA_CITY", " Chennai ")
    monkeypatch.setenv("VARUNA_OFFLINE", "1")
    monkeypatch.setenv("VARUNA_API_PORT", "8010")
    monkeypatch.setenv("VARUNA_CORS_ORIGINS", "http://localhost:3000, http://localhost:3001")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.varuna_mode == "live" and s.is_live
    assert s.varuna_city == "chennai"
    assert s.varuna_offline is True
    assert s.api_port == 8010
    assert s.cors_origins == ["http://localhost:3000", "http://localhost:3001"]
    assert s.twilio_account_sid is None


def test_sender_flags(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_CLOUD_TOKEN", "token")
    monkeypatch.setenv("WHATSAPP_CLOUD_PHONE_ID", "12345")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.has_whatsapp_sender and not s.has_sms_sender


def test_invalid_mode_rejected(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VARUNA_MODE", "demo")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached_and_reloadable(clean_env: None) -> None:
    reload_settings()
    first = get_settings()
    assert get_settings() is first
    assert reload_settings() is not first
