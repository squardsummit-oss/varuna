"""`varuna_schemas.net`: the outbound-HTTP helper ADR-0006 mandates (task D-04).

The load-bearing test is :func:`test_offline_opens_no_socket`: "offline" has to mean that nothing
leaves the process, not that something left and failed. It proves that by making every socket entry
point a tripwire - ``getaddrinfo`` included, since a refusal that still resolved the name has
already told a DNS server what we were about to ask.
"""

from __future__ import annotations

import io
import socket
import urllib.error
from typing import Any

import pytest
from varuna_schemas import net


@pytest.fixture(autouse=True)
def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to "network allowed"; the offline tests set it themselves."""
    monkeypatch.setenv("VARUNA_OFFLINE", "0")


class _Response(io.BytesIO):
    """The subset of ``http.client.HTTPResponse`` that :func:`net._read` touches."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _urlopen(*responses: Any, seen: list[str] | None = None):
    """A fake ``urlopen`` that plays ``responses`` in order; an exception instance is raised."""
    queue = list(responses)

    def fake(request: Any, timeout: float | None = None) -> Any:
        if seen is not None:
            seen.append(request.full_url)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return fake


# ---- offline ---------------------------------------------------------------


def test_offline_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """VARUNA_OFFLINE=1 refuses by name before anything is resolved or connected."""
    monkeypatch.setenv("VARUNA_OFFLINE", "1")
    touched: list[str] = []

    def tripwire(name: str):
        def guard(*args: object, **kwargs: object) -> None:
            touched.append(name)
            raise AssertionError(f"offline must not call socket.{name}")

        return guard

    monkeypatch.setattr(socket, "getaddrinfo", tripwire("getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", tripwire("create_connection"))
    monkeypatch.setattr(socket.socket, "connect", tripwire("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", tripwire("connect_ex"))
    monkeypatch.setattr(
        net.urllib.request, "urlopen", tripwire("urlopen (via urllib)"), raising=True
    )

    with pytest.raises(net.OfflineRefused) as caught:
        net.get_json("https://api.open-meteo.com/v1/forecast", {"latitude": 19.0})

    assert touched == []
    message = str(caught.value)
    assert "VARUNA_OFFLINE" in message
    assert "api.open-meteo.com" in message


def test_offline_is_a_network_error() -> None:
    """Callers that only care that the call failed can catch one class."""
    assert issubclass(net.OfflineRefused, net.NetworkError)
    assert issubclass(net.HttpStatusError, net.NetworkError)


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_offline_truthy_spellings(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VARUNA_OFFLINE", value)
    assert net.offline() is True


def test_offline_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable means "ask .env", not "assume online"."""
    from varuna_schemas import settings as settings_module

    monkeypatch.delenv("VARUNA_OFFLINE", raising=False)
    monkeypatch.setattr(
        settings_module, "get_settings", lambda: settings_module.Settings(varuna_offline=True)
    )
    assert net.offline() is True


# ---- URL building ----------------------------------------------------------


def test_build_url_encodes_repeats_and_drops_none() -> None:
    url = net.build_url(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": 19.065,
            "hourly": ["precipitation", "precipitation_probability"],
            "cell": None,
        },
    )
    assert url == (
        "https://api.open-meteo.com/v1/forecast?latitude=19.065"
        "&hourly=precipitation&hourly=precipitation_probability"
    )


def test_build_url_keeps_an_existing_query() -> None:
    assert net.build_url("https://x.test/a?b=1", {"c": "2"}) == "https://x.test/a?b=1&c=2"


def test_build_url_without_params_is_unchanged() -> None:
    assert net.build_url("https://x.test/a") == "https://x.test/a"


# ---- the happy path and its failures ---------------------------------------


def test_get_json_parses_and_sends_the_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake(request: Any, timeout: float | None = None) -> Any:
        captured["url"] = request.full_url
        captured["agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(net.urllib.request, "urlopen", fake)
    assert net.get_json("https://x.test/j", {"a": 1}, timeout=3.0) == {"ok": True}
    assert captured["url"] == "https://x.test/j?a=1"
    assert captured["agent"] == net.USER_AGENT
    assert captured["timeout"] == 3.0


def test_retries_a_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError("https://x.test/j", 500, "boom", {}, io.BytesIO(b"upstream"))
    seen: list[str] = []
    monkeypatch.setattr(net, "RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr(
        net.urllib.request, "urlopen", _urlopen(error, _Response(b'{"n": 2}'), seen=seen)
    )
    assert net.get_json("https://x.test/j", retries=1) == {"n": 2}
    assert len(seen) == 2


def test_a_404_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating a request the server rejected on its merits only wastes the cycle budget."""
    error = urllib.error.HTTPError("https://x.test/j", 404, "nope", {}, io.BytesIO(b"missing"))
    seen: list[str] = []
    monkeypatch.setattr(net, "RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr(net.urllib.request, "urlopen", _urlopen(error, seen=seen))
    with pytest.raises(net.HttpStatusError) as caught:
        net.get_json("https://x.test/j", retries=2)
    assert caught.value.status == 404
    assert len(seen) == 1


def test_a_timeout_exhausts_its_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(net, "RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr(
        net.urllib.request, "urlopen", _urlopen(TimeoutError("timed out"), seen=seen)
    )
    with pytest.raises(net.NetworkError) as caught:
        net.get_json("https://x.test/j", retries=2)
    assert len(seen) == 3
    assert "x.test" in str(caught.value)


def test_a_body_that_is_not_json_is_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net.urllib.request, "urlopen", _urlopen(_Response(b"<html>maintenance")))
    with pytest.raises(net.NetworkError, match="not JSON"):
        net.get_json("https://x.test/j", retries=0)


def test_an_oversized_body_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "MAX_BYTES", 16)
    monkeypatch.setattr(net.urllib.request, "urlopen", _urlopen(_Response(b"x" * 64)))
    with pytest.raises(net.NetworkError, match="refusing to buffer"):
        net.get_json("https://x.test/j", retries=0)


def test_trust_store_is_injected_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0006: the OS store, injected once per process, never a disabled check."""
    calls: list[int] = []
    monkeypatch.setattr(net, "_injected", False)
    monkeypatch.setattr(
        net.urllib.request, "urlopen", lambda request, timeout=None: _Response(b"1")
    )

    import truststore

    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: calls.append(1))
    net.get_json("https://x.test/j", retries=0)
    net.get_json("https://x.test/j", retries=0)
    assert calls == [1]
