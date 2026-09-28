"""Runtime settings read from the environment and the repository ``.env`` file.

Variable names follow ``.env.example`` (SPEC.md 4.4). Every provider key is optional
and an empty string in ``.env`` is read as "not configured" so the UI can hide
"Send to my phone" and similar controls honestly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from varuna_schemas.paths import repo_root

RunMode = Literal["replay", "live"]


def _split_csv(value: object) -> object:
    """Accept ``a,b`` as well as a JSON list for list-valued settings."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    """Process-wide configuration. Use :func:`get_settings` rather than instantiating."""

    model_config = SettingsConfigDict(
        env_file=str(repo_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ---- runtime ---------------------------------------------------------
    varuna_city: str = Field(default="mumbai", description="City slug, e.g. mumbai or chennai.")
    varuna_bundle: str = Field(
        default="MUM-2019-07-02", description="Replay bundle id loaded by default."
    )
    varuna_replay_speed: float = Field(
        default=30.0, gt=0, description="Replay acceleration factor (30 = 30x real time)."
    )
    varuna_mode: RunMode = Field(default="replay", description="replay or live ingestion.")
    varuna_offline: bool = Field(
        default=False, description="Block all outbound network (offline package, tests)."
    )
    varuna_onboard_enabled: bool = Field(
        default=True,
        description=(
            "Accept POST /v1/onboard. Off on the public deployment, where one build filled the "
            "500 MB volume and took the API down; on the demo laptop it stays on."
        ),
    )
    varuna_ops_passphrase: str | None = Field(
        default=None,
        repr=False,
        description=(
            "Shared passphrase for the authority desk's writes, sent in the X-Varuna-Ops "
            "header. Unset means every write is refused and says why, which is what the "
            "deployed API does. A gate, not authentication; never logged, never echoed."
        ),
    )
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("VARUNA_API_PORT", "API_PORT"),
        description="FastAPI port.",
    )
    ui_port: int = Field(
        default=3000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("VARUNA_UI_PORT", "UI_PORT"),
        description="Next.js port.",
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        validation_alias=AliasChoices("VARUNA_CORS_ORIGINS", "CORS_ORIGINS"),
        description="Allowed browser origins for the API (comma-separated or JSON list).",
    )

    # ---- optional providers (all None unless configured) -----------------
    mappls_key: str | None = None
    tomtom_key: str | None = None
    opentopo_key: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None
    whatsapp_cloud_token: str | None = None
    whatsapp_cloud_phone_id: str | None = None
    postgres_url: str | None = None
    redis_url: str | None = None

    # ---- validators ------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator(
        "varuna_ops_passphrase",
        "mappls_key",
        "tomtom_key",
        "opentopo_key",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from",
        "whatsapp_cloud_token",
        "whatsapp_cloud_phone_id",
        "postgres_url",
        "redis_url",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("varuna_city", mode="before")
    @classmethod
    def _lower_city(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    # ---- derived ---------------------------------------------------------
    @property
    def is_replay(self) -> bool:
        return self.varuna_mode == "replay"

    @property
    def is_live(self) -> bool:
        return self.varuna_mode == "live"

    @property
    def api_url(self) -> str:
        return f"http://localhost:{self.api_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://localhost:{self.api_port}/v1/live"

    @property
    def ops_writes_enabled(self) -> bool:
        """True when a desk passphrase is configured, so the authority desk can write."""
        return bool(self.varuna_ops_passphrase)

    @property
    def has_sms_sender(self) -> bool:
        """True when Twilio is fully configured (real SMS is P2; otherwise the phone mock)."""
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from)

    @property
    def has_whatsapp_sender(self) -> bool:
        """True when the WhatsApp Cloud API is configured (real send is P2)."""
        return bool(self.whatsapp_cloud_token and self.whatsapp_cloud_phone_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide :class:`Settings`, read once and cached."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache and re-read the environment (tests, hot config changes)."""
    get_settings.cache_clear()
    return get_settings()


__all__ = ["RunMode", "Settings", "get_settings", "reload_settings"]
