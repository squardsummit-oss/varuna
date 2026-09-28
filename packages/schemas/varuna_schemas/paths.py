"""Repository paths used by every service.

All functions return :class:`pathlib.Path` objects and never create directories.
Environment overrides are read on every call (not cached) so tests and the offline
package can redirect data folders:

- ``VARUNA_REPO_ROOT`` overrides the walk-up detection of the repository root.
- ``VARUNA_DATA_DIR`` overrides ``<root>/data`` (run artifacts live under ``runs/``).
- ``VARUNA_CITY_DIR`` overrides ``<root>/city`` (static city layers and the cache).
- ``VARUNA_BUNDLES_DIR`` overrides ``<root>/bundles`` (replay bundles).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_WORKSPACE_MARKER = "[tool.uv.workspace]"


class RepoRootNotFoundError(RuntimeError):
    """Raised when no ``pyproject.toml`` with a uv workspace is found above this file."""


@lru_cache(maxsize=1)
def _detect_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8")
            except OSError:
                continue
            if _WORKSPACE_MARKER in text:
                return candidate
    msg = (
        "Could not find the VARUNA repository root: no pyproject.toml containing "
        f"'{_WORKSPACE_MARKER}' above {here}. Set VARUNA_REPO_ROOT to point at the clone."
    )
    raise RepoRootNotFoundError(msg)


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def repo_root() -> Path:
    """The repository root: the folder whose ``pyproject.toml`` declares the uv workspace."""
    return _env_path("VARUNA_REPO_ROOT") or _detect_repo_root()


def data_dir() -> Path:
    """``<root>/data`` (or ``VARUNA_DATA_DIR``)."""
    return _env_path("VARUNA_DATA_DIR") or repo_root() / "data"


def runs_dir() -> Path:
    """Run artifacts root: ``<data>/runs`` (SPEC.md 10.3)."""
    return data_dir() / "runs"


def run_dir(run_id: str) -> Path:
    """Folder for one run: ``<data>/runs/<run_id>``."""
    _guard_segment(run_id, "run_id")
    return runs_dir() / run_id


def city_root() -> Path:
    """Root of static city layers: ``<root>/city`` (or ``VARUNA_CITY_DIR``)."""
    return _env_path("VARUNA_CITY_DIR") or repo_root() / "city"


def city_dir(city: str) -> Path:
    """Static layers for one city: ``<city_root>/<city>`` (SPEC.md 10.1)."""
    _guard_segment(city, "city")
    return city_root() / city


def city_cache_dir(city: str) -> Path:
    """Raw download cache for one city: ``<city_root>/cache/<city>`` (SPEC.md 10.4)."""
    _guard_segment(city, "city")
    return city_root() / "cache" / city


def city_config_path(city: str) -> Path:
    """The city-in-a-box config: ``services/city/configs/<city>.yaml`` (SPEC.md 10.1)."""
    _guard_segment(city, "city")
    return repo_root() / "services" / "city" / "configs" / f"{city}.yaml"


def bundles_dir() -> Path:
    """Replay bundles root: ``<root>/bundles`` (or ``VARUNA_BUNDLES_DIR``)."""
    return _env_path("VARUNA_BUNDLES_DIR") or repo_root() / "bundles"


def bundle_dir(bundle_id: str) -> Path:
    """Folder for one replay bundle: ``<bundles>/<bundle_id>`` (SPEC.md 10.2)."""
    _guard_segment(bundle_id, "bundle_id")
    return bundles_dir() / bundle_id


def tokens_path() -> Path:
    """``packages/tokens/tokens.json``, the design-token source of truth (SPEC.md 6.2)."""
    return repo_root() / "packages" / "tokens" / "tokens.json"


def docs_dir() -> Path:
    """``<root>/docs``."""
    return repo_root() / "docs"


def schemas_json_dir() -> Path:
    """``packages/schemas/json``: exported JSON schemas (``python -m varuna_schemas.export_json``)."""
    return repo_root() / "packages" / "schemas" / "json"


def verification_public_path() -> Path:
    """Committed verification numbers for the offline landing page (SPEC.md 11.12)."""
    return repo_root() / "apps" / "command" / "public" / "verification.json"


def _guard_segment(value: str, what: str) -> None:
    """Refuse ids that would escape their folder (``..``, separators, empty)."""
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        msg = f"{what} must be a single path segment, got {value!r}"
        raise ValueError(msg)


__all__ = [
    "RepoRootNotFoundError",
    "bundle_dir",
    "bundles_dir",
    "city_cache_dir",
    "city_config_path",
    "city_dir",
    "city_root",
    "data_dir",
    "docs_dir",
    "repo_root",
    "run_dir",
    "runs_dir",
    "schemas_json_dir",
    "tokens_path",
    "verification_public_path",
]
