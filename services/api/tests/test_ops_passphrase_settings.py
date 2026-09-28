"""The desk passphrase is read from ``.env`` as well as the environment (SPEC.md 4.4).

Before this, the write gate read only ``os.environ``. Settings reads ``.env`` but never exports
it, so a ``VARUNA_OPS_PASSPHRASE`` written into ``.env`` - where 4.4 and ``.env.example`` send
every other setting - did nothing, and the desk stayed read-only with a refusal telling the
officer to set a variable that was already set.

Every test here points Settings at a temporary ``.env`` of its own, so the developer's real one
never decides an outcome, and removes any exported passphrase first. The cache gets its own test:
``get_settings`` holds the environment of its first read, and a gate that trusted it would keep
a passphrase that had since been removed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import ops
from varuna_schemas.settings import Settings, get_settings, reload_settings

FROM_DOT_ENV = "tide gauge at Colaba"
"""A passphrase that lives only in the temporary ``.env``."""

EXPORTED = "monsoon desk 2026"
"""A passphrase that lives only in the process environment."""

DISABLED_MESSAGE = (
    "This API cannot accept authority edits: VARUNA_OPS_PASSPHRASE is not set in its "
    "environment, so there is nothing to check a request against. Set it where the API "
    "runs and restart it; the deployed API leaves it unset on purpose and is read-only."
)
"""The refusal the gate already gave before this change, pinned word for word."""

CLOSURE = {"segment_id": "S1-000", "reason": "Water main burst at Hindmata", "city": "mumbai"}


def _write_dot_env(path: Path, passphrase: str | None) -> None:
    """A ``.env`` in the shape ``make setup`` copies from ``.env.example``."""
    lines = ["VARUNA_CITY=mumbai"]
    lines.append(f"VARUNA_OPS_PASSPHRASE={passphrase}" if passphrase is not None else "")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _log_lines(root: Path) -> list[str]:
    path = root / "data" / "ops" / "mumbai.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


@pytest.fixture
def dot_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A writable data dir, no exported passphrase, and Settings reading a ``.env`` of our own.

    The file starts with an empty ``VARUNA_OPS_PASSPHRASE=``, exactly as ``.env.example`` ships
    it; a test that wants one configured rewrites it. ``get_settings`` is cleared on both sides
    so no cached copy from another test answers here, and none from here outlives the test.
    """
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.delenv(ops.PASSPHRASE_ENV, raising=False)
    env_file = tmp_path / ".env"
    _write_dot_env(env_file, "")
    monkeypatch.setitem(Settings.model_config, "env_file", str(env_file))
    get_settings.cache_clear()
    ops.reset_rate_limit()
    yield env_file
    ops.reset_rate_limit()
    get_settings.cache_clear()


def test_a_passphrase_written_into_dot_env_opens_the_gate(
    dot_env: Path, client: TestClient
) -> None:
    _write_dot_env(dot_env, FROM_DOT_ENV)

    assert get_settings().varuna_ops_passphrase == FROM_DOT_ENV
    assert get_settings().ops_writes_enabled

    res = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: FROM_DOT_ENV})

    assert res.status_code == 200, res.text
    assert len(_log_lines(dot_env.parent)) == 1, "the write was appended"
    log_body = client.get("/v1/ops/log", params={"city": "mumbai"}).json()
    assert log_body["writes_enabled"] is True
    assert any("Writes are enabled" in note for note in log_body["notes"])
    assert FROM_DOT_ENV not in res.text + json.dumps(log_body), "the passphrase is never echoed"


def test_a_wrong_passphrase_is_refused_when_dot_env_sets_one(
    dot_env: Path, client: TestClient
) -> None:
    _write_dot_env(dot_env, FROM_DOT_ENV)

    res = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: "guess"})

    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "ops_passphrase_rejected"
    assert FROM_DOT_ENV not in res.text, "a refusal never tells the caller what would work"
    assert not _log_lines(dot_env.parent), "a refused write appends nothing"


def test_no_passphrase_anywhere_refuses_with_the_existing_message(
    dot_env: Path, client: TestClient
) -> None:
    """The ``.env`` holds the empty line ``.env.example`` ships, and nothing is exported."""
    assert not get_settings().ops_writes_enabled

    res = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: "anything"})

    assert res.status_code == 503, res.text
    error = res.json()["error"]
    assert error["code"] == "ops_writes_disabled"
    assert error["message"] == DISABLED_MESSAGE
    assert not _log_lines(dot_env.parent)
    assert client.get("/v1/ops/log", params={"city": "mumbai"}).json()["writes_enabled"] is False


def test_the_environment_variable_still_opens_the_gate(
    dot_env: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ops.PASSPHRASE_ENV, EXPORTED)

    res = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: EXPORTED})

    assert res.status_code == 200, res.text
    assert len(_log_lines(dot_env.parent)) == 1


def test_an_exported_variable_wins_over_dot_env(
    dot_env: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same precedence ``notify.configured_sender`` gives a key set in both places."""
    _write_dot_env(dot_env, FROM_DOT_ENV)
    monkeypatch.setenv(ops.PASSPHRASE_ENV, EXPORTED)

    stale = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: FROM_DOT_ENV})
    current = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: EXPORTED})

    assert stale.status_code == 403, stale.text
    assert current.status_code == 200, current.text


def test_a_cached_settings_does_not_keep_a_removed_passphrase(
    dot_env: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_settings`` read while the variable was exported still holds it after it is gone."""
    monkeypatch.setenv(ops.PASSPHRASE_ENV, EXPORTED)
    assert reload_settings().varuna_ops_passphrase == EXPORTED, "the cache captured it"
    monkeypatch.delenv(ops.PASSPHRASE_ENV)

    res = client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: EXPORTED})

    assert res.status_code == 503, res.text
    assert res.json()["error"]["code"] == "ops_writes_disabled"
    assert not _log_lines(dot_env.parent)


def test_the_passphrase_is_never_logged_or_shown_in_a_repr(
    dot_env: Path,
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_dot_env(dot_env, FROM_DOT_ENV)

    assert FROM_DOT_ENV not in repr(get_settings())
    client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: FROM_DOT_ENV})
    client.post("/v1/ops/closures", json=CLOSURE, headers={ops.OPS_HEADER: "guess"})

    captured = capsys.readouterr()
    assert FROM_DOT_ENV not in captured.out + captured.err + caplog.text
