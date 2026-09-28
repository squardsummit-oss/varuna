"""The open-data tile cache (SPEC.md 10.1, 10.4, ADR-0006).

Phase 1 never downloads. ``tools/prefetch_city_cache.py`` has already put every DEM and
land-cover tile under ``city/cache/<kind>/`` together with ``city/cache/MANIFEST.json``
(url, bytes, sha256 per file). Everything here is a lookup into that folder, and every
failure names the prefetch tool so the fix is obvious on stage.

Never read a tile through GDAL's ``/vsicurl/``: TLS is intercepted on the build machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import structlog
from varuna_schemas.models.city import CityConfig
from varuna_schemas.paths import city_root

log = structlog.get_logger(__name__)

TileKind = Literal["dem", "worldcover", "osmnx"]
"""Sub-folders of ``city/cache/``. ``osmnx`` is a folder cache, not a tile."""

MANIFEST_NAME = "MANIFEST.json"

_PREFETCH_HINT = (
    "Run `uv run python tools/prefetch_city_cache.py` from the repository root to fill "
    "city/cache/ (it downloads Copernicus GLO-30 and ESA WorldCover tiles once)."
)


class CacheMissError(FileNotFoundError):
    """A tile the city config names is not in ``city/cache/``."""


class CacheCorruptError(RuntimeError):
    """A cached tile exists but does not match ``MANIFEST.json``."""


def cache_root() -> Path:
    """``city/cache`` (honours ``VARUNA_CITY_DIR``)."""
    return city_root() / "cache"


def manifest_path() -> Path:
    """``city/cache/MANIFEST.json``."""
    return cache_root() / MANIFEST_NAME


def read_manifest() -> dict[str, dict[str, object]]:
    """The cache manifest keyed by ``"<kind>/<name>"``. Missing manifest yields ``{}``."""
    path = manifest_path()
    if not path.is_file():
        log.warning("cache.manifest_missing", path=str(path), hint=_PREFETCH_HINT)
        return {}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def cached_tile(kind: TileKind, name: str) -> Path:
    """Path to one cached tile, raising :class:`CacheMissError` with the fix when it is absent.

    Args:
        kind: cache sub-folder, ``"dem"`` or ``"worldcover"``.
        name: file name exactly as it appears in the city config, e.g.
            ``Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif``.
    """
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        msg = f"tile name must be a bare file name, got {name!r}"
        raise ValueError(msg)
    path = cache_root() / kind / name
    if not path.is_file():
        available = (
            sorted(p.name for p in (cache_root() / kind).glob("*")) if path.parent.is_dir() else []
        )
        msg = (
            f"Cached {kind} tile {name!r} is missing at {path}. "
            f"Present in that folder: {available or 'nothing'}. {_PREFETCH_HINT}"
        )
        raise CacheMissError(msg)
    return path


@dataclass(frozen=True, slots=True)
class TileCheck:
    """One row of :func:`verify_cache`."""

    key: str
    path: Path
    present: bool
    size_ok: bool
    expected_bytes: int | None
    actual_bytes: int | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.present and self.size_ok


def verify_cache(city: CityConfig, *, strict: bool = False) -> list[TileCheck]:
    """Check every tile ``city`` names: present on disk and the right size per the manifest.

    Args:
        city: the loaded city config.
        strict: raise instead of returning rows when something is wrong.

    Returns:
        One :class:`TileCheck` per tile, DEM tiles first.
    """
    manifest = read_manifest()
    rows: list[TileCheck] = []
    wanted: list[tuple[TileKind, str]] = [("dem", n) for n in city.dem_tiles]
    wanted += [("worldcover", n) for n in city.landcover_tiles]

    for kind, name in wanted:
        key = f"{kind}/{name}"
        path = cache_root() / kind / name
        entry = manifest.get(key) or {}
        expected = entry.get("bytes")
        expected_bytes = int(expected) if isinstance(expected, int | float) else None
        if not path.is_file():
            rows.append(TileCheck(key, path, False, False, expected_bytes, None, "missing on disk"))
            continue
        actual_bytes = path.stat().st_size
        if expected_bytes is None:
            rows.append(
                TileCheck(key, path, True, True, None, actual_bytes, "not in MANIFEST.json")
            )
            continue
        size_ok = actual_bytes == expected_bytes
        rows.append(
            TileCheck(
                key,
                path,
                True,
                size_ok,
                expected_bytes,
                actual_bytes,
                "" if size_ok else f"expected {expected_bytes} bytes, found {actual_bytes}",
            )
        )

    bad = [row for row in rows if not row.ok]
    log.info(
        "cache.verified",
        city=city.id,
        tiles=len(rows),
        bad=len(bad),
    )
    if bad and strict:
        detail = "; ".join(f"{row.key}: {row.detail}" for row in bad)
        msg = f"Cache check failed for {city.id}: {detail}. {_PREFETCH_HINT}"
        if any(not row.present for row in bad):
            raise CacheMissError(msg)
        raise CacheCorruptError(msg)
    return rows


@lru_cache(maxsize=8)
def cached_osmnx_folder() -> Path:
    """``city/cache/osmnx``: the warm OSMnx response cache (set ``ox.settings.cache_folder``)."""
    return cache_root() / "osmnx"


__all__ = [
    "MANIFEST_NAME",
    "CacheCorruptError",
    "CacheMissError",
    "TileCheck",
    "TileKind",
    "cache_root",
    "cached_osmnx_folder",
    "cached_tile",
    "manifest_path",
    "read_manifest",
    "verify_cache",
]
