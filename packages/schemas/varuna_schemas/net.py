"""Outbound HTTP for the Python services (ADR-0006; task D-04).

ADR-0006 decided on 2026-09-04 that *every* HTTP call in the Python services goes through
``varuna_schemas.net``, because Norton's "Web/Mail Shield" re-signs TLS on the demo laptop and
only the operating-system trust store has its root. The module it names was never written: the
city pipeline injects ``truststore`` itself in ``varuna_city.osm`` and ``tools/prefetch_city_cache``
carries its own downloader. This is that module, starting with the one shape the new surfaces
need - a small JSON GET - and the download helper moves behind it in a later task.

Three rules it exists to keep:

**The trust store is the operating system's.** :func:`use_system_trust_store` calls
``truststore.inject_into_ssl()`` once per process, so ``urllib`` and anything else built on
``ssl`` verify against the OS store on Windows, macOS and Linux. ``GDAL_HTTP_UNSAFESSL`` is never
set and verification is never disabled.

**Offline means no socket, not a failed socket.** Under ``VARUNA_OFFLINE=1`` (SPEC.md 4.4,
P0.6) :func:`get_json` raises :class:`OfflineRefused` *before* the URL is resolved, so nothing
opens a connection and nothing waits for a timeout. The caller gets a refusal that names the
variable, which is what the UI prints.

**A call that fails says so.** Every attempt is logged as one structured event with the host, the
path, the status and the elapsed milliseconds. Query values are never logged - only their names -
because a query string is where an API key ends up.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import structlog

log = structlog.get_logger("varuna.net")

USER_AGENT = "VARUNA/0.1 (SIH 2026 PS SIH26085; flood nowcasting prototype)"
"""Sent on every request. Public data services ask to be able to identify a client."""

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_RETRIES = 2
RETRY_BACKOFF_S = 0.4
"""Sleep before retry ``n`` is ``RETRY_BACKOFF_S * n`` - a demo cycle cannot afford more."""

MAX_BYTES = 8 << 20
"""Refuse a body larger than 8 MB: every JSON service this module talks to answers in kilobytes."""

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_inject_lock = threading.Lock()
_injected = False


class NetworkError(RuntimeError):
    """An outbound call did not produce a usable answer. Carries what to tell the user."""


class OfflineRefused(NetworkError):
    """``VARUNA_OFFLINE=1``: refused by name, with no socket opened."""


class HttpStatusError(NetworkError):
    """The server answered, with a status this call cannot use."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} from {_host_of(url)}{_path_of(url)}")


def offline() -> bool:
    """True when outbound network is blocked.

    Reads ``VARUNA_OFFLINE`` from the environment on every call, so a test or the offline
    package can flip it after settings were first read; falls back to :class:`Settings` when the
    variable is unset, which is where ``.env`` lands.
    """
    raw = os.environ.get("VARUNA_OFFLINE")
    if raw is not None and raw.strip():
        return raw.strip().lower() in _TRUTHY
    from varuna_schemas.settings import get_settings

    return bool(get_settings().varuna_offline)


def use_system_trust_store() -> bool:
    """Inject the operating-system trust store into ``ssl``, once per process (ADR-0006)."""
    global _injected
    with _inject_lock:
        if _injected:
            return True
        try:
            import truststore
        except ImportError:  # pragma: no cover - truststore is a workspace dependency
            log.warning("net.truststore_missing", detail="falling back to bundled certificates")
            _injected = True
            return False
        truststore.inject_into_ssl()
        _injected = True
        log.debug("net.truststore_injected")
        return True


def _host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


def _path_of(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "/"


def build_url(url: str, params: dict[str, Any] | None = None) -> str:
    """``url`` with ``params`` appended, list values repeated, ``None`` values dropped."""
    if not params:
        return url
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    if not pairs:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.urlencode(pairs)
    merged = f"{parts.query}&{query}" if parts.query else query
    return urllib.parse.urlunsplit(parts._replace(query=merged))


def _read(url: str, timeout: float, headers: dict[str, str]) -> tuple[int, bytes]:
    """One request. Kept separate so a test can watch exactly what would leave the process."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES + 1)
        status = int(getattr(response, "status", 200) or 200)
    if len(body) > MAX_BYTES:
        msg = f"{_host_of(url)} sent more than {MAX_BYTES} bytes; refusing to buffer it"
        raise NetworkError(msg)
    return status, body


def _fetch(
    url: str,
    params: dict[str, Any] | None,
    timeout: float,
    retries: int,
    headers: dict[str, str] | None,
    accept: str,
    event: str,
) -> tuple[str, int, bytes]:
    """The offline guard, the retry loop and the log line both readers share.

    Returns ``(full_url, status, body)``; raises exactly what :func:`get_json` documents.
    """
    if offline():
        log.info("net.refused_offline", host=_host_of(url), path=_path_of(url))
        msg = (
            f"VARUNA_OFFLINE=1 blocks outbound requests, so {_host_of(url)} was not contacted. "
            "Unset VARUNA_OFFLINE to allow it, or use the cached copy."
        )
        raise OfflineRefused(msg)

    use_system_trust_store()
    full = build_url(url, params)
    sent = {"User-Agent": USER_AGENT, "Accept": accept}
    sent.update(headers or {})
    attempts = max(1, retries + 1)
    names = sorted(params or {})
    last: Exception | None = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            status, body = _read(full, timeout, sent)
        except urllib.error.HTTPError as error:
            ms = (time.perf_counter() - started) * 1000
            detail = _peek(error)
            log.warning(
                event,
                host=_host_of(full),
                path=_path_of(full),
                params=names,
                status=error.code,
                ms=round(ms, 1),
                attempt=attempt,
                of=attempts,
            )
            last = HttpStatusError(error.code, full, detail)
            if error.code in RETRY_STATUS and attempt < attempts:
                time.sleep(RETRY_BACKOFF_S * attempt)
                continue
            raise last from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            ms = (time.perf_counter() - started) * 1000
            log.warning(
                event,
                host=_host_of(full),
                path=_path_of(full),
                params=names,
                error=repr(error),
                ms=round(ms, 1),
                attempt=attempt,
                of=attempts,
            )
            last = NetworkError(f"Could not reach {_host_of(full)}: {error}")
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF_S * attempt)
                continue
            raise last from error

        ms = (time.perf_counter() - started) * 1000
        log.info(
            event,
            host=_host_of(full),
            path=_path_of(full),
            params=names,
            status=status,
            bytes=len(body),
            ms=round(ms, 1),
            attempt=attempt,
            of=attempts,
        )
        return full, status, body

    raise last or NetworkError(f"Could not reach {_host_of(full)}")  # pragma: no cover


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET ``url`` and parse the JSON body.

    Args:
        url: absolute ``https://`` (or ``http://``) URL.
        params: query parameters; lists repeat the key, ``None`` values are dropped.
        timeout: seconds per attempt.
        retries: extra attempts after the first, for a timeout, a connection failure or a
            status in :data:`RETRY_STATUS`. A 4xx that is not 408/425/429 is not retried:
            the request is wrong and repeating it will not fix it.
        headers: added to (and able to override) the default ``User-Agent`` and ``Accept``.

    Raises:
        OfflineRefused: ``VARUNA_OFFLINE=1``. Nothing is resolved and no socket is opened.
        HttpStatusError: the server answered with a status this call cannot use.
        NetworkError: connection failure, timeout, oversized body or unparseable JSON.
    """
    full, status, body = _fetch(
        url, params, timeout, retries, headers, "application/json", "net.get_json"
    )
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        msg = f"{_host_of(full)} answered {status} with a body that is not JSON: {error}"
        raise NetworkError(msg) from error


def get_bytes(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET ``url`` and return the raw body: a radar tile, an image. Same guard and retries as
    :func:`get_json`, and the same errors apart from the JSON one."""
    _, _, body = _fetch(url, params, timeout, retries, headers, "*/*", "net.get_bytes")
    return body


def _peek(error: urllib.error.HTTPError, limit: int = 400) -> str:
    """The first few hundred characters of an error body, for the log and the message."""
    try:
        return error.read(limit).decode("utf-8", "replace")
    except Exception:  # pragma: no cover - the body may already be closed
        return ""


__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "MAX_BYTES",
    "RETRY_STATUS",
    "USER_AGENT",
    "HttpStatusError",
    "NetworkError",
    "OfflineRefused",
    "build_url",
    "get_bytes",
    "get_json",
    "offline",
    "use_system_trust_store",
]
