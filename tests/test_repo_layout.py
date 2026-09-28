"""Repository layout guard: SPEC.md sections 4.1 (folders), 4.3 (make targets), 4.4 (env)."""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def offline_requested() -> bool:
    return os.environ.get("VARUNA_OFFLINE", "0").strip().lower() in {"1", "true", "yes", "on"}


# SPEC.md section 4.1. ``city/`` and ``data/runs/`` are gitignored but keep a .gitkeep.
DIRECTORIES = [
    "apps/command/app",
    "apps/command/components",
    "apps/command/lib",
    "apps/command/public",
    "services/api",
    "services/city",
    "services/replay",
    "services/sky",
    "services/twin",
    "services/pulse",
    "services/flash",
    "services/products",
    "services/route",
    "services/cycle",
    "services/verify",
    "packages/schemas",
    "packages/tokens",
    "city",
    "bundles",
    "data/runs",
    "docs",
    "tests",
]

ROOT_FILES = [
    "SPEC.md",
    "Makefile",
    "docker-compose.yml",
    "pnpm-workspace.yaml",
    "turbo.json",
    "package.json",
    "pyproject.toml",
    "uv.lock",
    ".env.example",
    "playwright.config.ts",
]

# SPEC.md section 4.3.
MAKE_TARGETS = [
    "setup",
    "city",
    "bundle",
    "bake",
    "train",
    "dev",
    "demo",
    "test",
    "e2e",
    "pack",
    "demo-video",
]

# SPEC.md section 4.4. ``TWILIO_*`` and ``WHATSAPP_CLOUD_*`` are prefixes.
ENV_VARIABLES = [
    "VARUNA_CITY",
    "VARUNA_BUNDLE",
    "VARUNA_REPLAY_SPEED",
    "VARUNA_MODE",
    "VARUNA_OFFLINE",
    "NEXT_PUBLIC_API_URL",
    "NEXT_PUBLIC_BASEMAP_STYLE",
    "MAPPLS_KEY",
    "TOMTOM_KEY",
    "OPENTOPO_KEY",
    "POSTGRES_URL",
    "REDIS_URL",
]
ENV_PREFIXES = ["TWILIO_", "WHATSAPP_CLOUD_"]

DOCS = ["DECISIONS.md", "SIMPLIFICATIONS.md", "CHANGELOG.md", "QA.md"]


@pytest.mark.parametrize("relative", DIRECTORIES)
def test_directory_exists(relative: str) -> None:
    assert (ROOT / relative).is_dir(), f"missing directory {relative} (SPEC.md 4.1)"


@pytest.mark.parametrize("relative", ROOT_FILES)
def test_root_file_exists(relative: str) -> None:
    assert (ROOT / relative).is_file(), f"missing file {relative} (SPEC.md 4.1)"


def _env_keys() -> set[str]:
    keys: set[str] = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


@pytest.mark.parametrize("name", ENV_VARIABLES)
def test_env_example_lists_variable(name: str) -> None:
    assert name in _env_keys(), f".env.example lacks {name} (SPEC.md 4.4)"


@pytest.mark.parametrize("prefix", ENV_PREFIXES)
def test_env_example_lists_prefixed_group(prefix: str) -> None:
    matches = [k for k in _env_keys() if k.startswith(prefix)]
    assert matches, f".env.example has no {prefix}* variable (SPEC.md 4.4)"


def _make_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", text, flags=re.MULTILINE))


@pytest.mark.parametrize("target", MAKE_TARGETS)
def test_make_target_exists(target: str) -> None:
    assert target in _make_targets(), f"Makefile lacks target {target} (SPEC.md 4.3)"


def test_make_targets_are_phony() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    phony = re.search(r"^\.PHONY:(.*?)(?=^\S|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert phony is not None, "Makefile has no .PHONY line"
    declared = set(phony.group(1).replace("\\", " ").split())
    missing = [t for t in MAKE_TARGETS if t not in declared]
    assert not missing, f"targets not declared .PHONY: {missing}"


@pytest.mark.parametrize("name", DOCS)
def test_doc_exists(name: str) -> None:
    path = ROOT / "docs" / name
    assert path.is_file(), f"docs/{name} is missing (P0.11)"
    assert path.read_text(encoding="utf-8").strip(), f"docs/{name} is empty"


def test_blueprint_pdf_copied() -> None:
    assert (ROOT / "docs" / "VARUNA_SIH2026_Blueprint.pdf").is_file()


@pytest.mark.offline
def test_offline_guard_blocks_public_addresses() -> None:
    """With VARUNA_OFFLINE=1 a public address is refused before any packet leaves."""
    if not offline_requested():
        pytest.skip("run with VARUNA_OFFLINE=1 to exercise the network guard")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            sock.connect(("93.184.216.34", 80))
        with pytest.raises(OSError):
            socket.create_connection(("example.com", 443), timeout=1)
    finally:
        sock.close()


@pytest.mark.offline
def test_offline_guard_keeps_loopback_open() -> None:
    """Loopback must stay reachable so local API tests keep working."""
    if not offline_requested():
        pytest.skip("run with VARUNA_OFFLINE=1 to exercise the network guard")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    client.close()
    server.close()
