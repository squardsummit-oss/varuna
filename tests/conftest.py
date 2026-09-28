"""Shared fixtures for the cross-service integration tests.

``VARUNA_OFFLINE=1`` (SPEC.md section 4.4 and P0.6) must block every outbound network
call during tests. The guard below patches the two socket entry points that every HTTP
client ends up in (``socket.socket.connect`` and ``socket.create_connection``) so that any
address outside the loopback range raises ``OSError``. Loopback stays open because the API
contract tests talk to a local uvicorn or an in-process ASGI transport.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest

_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def offline_requested() -> bool:
    return os.environ.get("VARUNA_OFFLINE", "0").strip().lower() in _TRUTHY


def is_loopback(address: Any) -> bool:
    """True when ``address`` (a ``(host, port, ...)`` tuple or a path) stays on this machine."""
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.endswith(".localhost")


class OfflineViolation(OSError):
    """Raised when a test tries to reach a non-loopback address under VARUNA_OFFLINE=1."""


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "offline: the test must pass with VARUNA_OFFLINE=1 (no outbound network).",
    )


@pytest.fixture(autouse=True, scope="session")
def block_outbound_network() -> Iterator[None]:
    """Patch the socket layer for the whole session when VARUNA_OFFLINE=1."""
    if not offline_requested():
        yield
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if not is_loopback(address):
            raise OfflineViolation(f"VARUNA_OFFLINE=1 blocks outbound connections: {address!r}")
        original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if not is_loopback(address):
            raise OfflineViolation(f"VARUNA_OFFLINE=1 blocks outbound connections: {address!r}")
        return original_connect_ex(self, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if not is_loopback(address):
            raise OfflineViolation(f"VARUNA_OFFLINE=1 blocks outbound connections: {address!r}")
        return original_create_connection(address, *args, **kwargs)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
