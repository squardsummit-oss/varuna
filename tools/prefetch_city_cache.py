"""Pre-download the open-data tiles the city-in-a-box pipeline needs (SPEC.md 10.1, 10.4).

Run once per machine::

    uv run python tools/prefetch_city_cache.py            # Mumbai and Chennai
    uv run python tools/prefetch_city_cache.py --city mumbai

Why this exists: on the demo laptop Norton re-signs every TLS connection, so ``requests``
(certifi) and GDAL's ``vsicurl`` both fail certificate verification (ADR-0006). Everything
here goes through ``truststore``, which uses the operating-system trust store, writes to
``city/cache/`` and is resumable, so ``make city`` and the Chennai pre-cache never depend on
the network at demo time.

Cache layout (the convention the pipeline reads; ``city/`` is ``$VARUNA_CITY_DIR`` when set):

    city/cache/dem/<Copernicus tile name>.tif
    city/cache/worldcover/<ESA WorldCover tile name>.tif
    city/cache/MANIFEST.json     # url, bytes, sha256 and fetch time per file
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def cache_root() -> Path:
    """The directory the city pipeline reads its cache from.

    ``varuna_city.cache`` resolves it as ``city_root() / "cache"``, where ``city_root`` honours
    ``VARUNA_CITY_DIR`` (in the Railway image that is the ``/data`` volume, not the checkout).
    Using the same resolver keeps the two in step; the fallback covers a bare ``python`` run
    outside the workspace environment.
    """
    try:
        from varuna_schemas.paths import city_root
    except ImportError:
        return REPO_ROOT / "city" / "cache"
    return city_root() / "cache"


CACHE = cache_root()
USER_AGENT = "VARUNA-SIH2026/0.1 (city-in-a-box prefetch)"

COPERNICUS = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
WORLDCOVER = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)


@dataclass(frozen=True)
class City:
    """The tiles one area of interest needs. Copernicus tiles are 1 degree, WorldCover 3."""

    name: str
    dem_tiles: tuple[str, ...]
    worldcover_tiles: tuple[str, ...]


CITIES: dict[str, City] = {
    # MUM-CENTRAL: lon 72.815-72.905, lat 18.995-19.135 -> N18/N19 at E072; WorldCover N18E072.
    "mumbai": City(
        name="mumbai",
        dem_tiles=(
            "Copernicus_DSM_COG_10_N18_00_E072_00_DEM",
            "Copernicus_DSM_COG_10_N19_00_E072_00_DEM",
        ),
        worldcover_tiles=("N18E072",),
    ),
    # CHN-SOUTH: lon 80.20-80.28, lat 12.96-13.05 -> N12/N13 at E080; WorldCover N12E078.
    "chennai": City(
        name="chennai",
        dem_tiles=(
            "Copernicus_DSM_COG_10_N12_00_E080_00_DEM",
            "Copernicus_DSM_COG_10_N13_00_E080_00_DEM",
        ),
        worldcover_tiles=("N12E078",),
    ),
}


def ssl_context() -> ssl.SSLContext:
    """An SSL context that trusts the operating-system store (ADR-0006)."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover - truststore is a dependency of the workspace
        print(
            "truststore is not installed; falling back to the default trust store", file=sys.stderr
        )
        return ssl.create_default_context()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, context: ssl.SSLContext) -> tuple[bool, int]:
    """Download ``url`` to ``target`` unless the sizes already agree. Returns (fetched, bytes)."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60, context=context) as response:
        remote_bytes = int(response.headers.get("Content-Length", "0"))

    if target.is_file() and remote_bytes and target.stat().st_size == remote_bytes:
        return False, remote_bytes

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Carriage-return progress is for a terminal; a redirected log gets one line per 25 %.
    interactive = sys.stdout.isatty()
    with (
        urllib.request.urlopen(request, timeout=300, context=context) as response,
        partial.open("wb") as out,
    ):
        done = 0
        next_mark = 0.25
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if not remote_bytes:
                continue
            if interactive:
                print(f"\r  {target.name}: {done / 1e6:6.1f} / {remote_bytes / 1e6:.1f} MB", end="")
            elif done / remote_bytes >= next_mark:
                print(f"  {target.name}: {done / remote_bytes:.0%} of {remote_bytes / 1e6:.0f} MB")
                next_mark += 0.25
    if interactive:
        print()
    partial.replace(target)
    return True, target.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--city", choices=[*CITIES, "all"], default="all")
    args = parser.parse_args()

    cities = list(CITIES.values()) if args.city == "all" else [CITIES[args.city]]
    context = ssl_context()
    manifest_path = CACHE / "MANIFEST.json"
    manifest: dict[str, dict[str, object]] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}

    failures: list[str] = []
    for city in cities:
        print(f"\n{city.name}:")
        targets: list[tuple[str, Path]] = [
            (COPERNICUS.format(name=tile), CACHE / "dem" / f"{tile}.tif") for tile in city.dem_tiles
        ]
        targets += [
            (
                WORLDCOVER.format(tile=tile),
                CACHE / "worldcover" / f"ESA_WorldCover_10m_2021_v200_{tile}_Map.tif",
            )
            for tile in city.worldcover_tiles
        ]
        for url, target in targets:
            key = str(target.relative_to(CACHE)).replace("\\", "/")
            try:
                fetched, size = download(url, target, context)
            except Exception as exc:
                print(f"  {target.name}: FAILED {type(exc).__name__}: {exc}")
                failures.append(f"{key}: {exc}")
                continue
            if not fetched:
                print(f"  {target.name}: cached ({size / 1e6:.1f} MB)")
                if key in manifest:
                    continue
            manifest[key] = {
                "url": url,
                "bytes": target.stat().st_size,
                "sha256": sha256_of(target),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "city": city.name,
            }

    CACHE.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    total = sum(int(entry["bytes"]) for entry in manifest.values())
    print(f"\n{len(manifest)} files, {total / 1e6:.0f} MB in {CACHE}")
    print(f"manifest: {manifest_path}")
    if failures:
        print(f"\n{len(failures)} download(s) failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
