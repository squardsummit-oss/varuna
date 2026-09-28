"""``varuna city`` - the city-in-a-box CLI (SPEC.md 4.3, P1.1-P1.12).

``uv run varuna city --city mumbai`` runs every step of :mod:`varuna_city.pipeline` from the
open-data cache and prints a per-step timing table. Steps whose outputs are newer than their
inputs are loaded from ``city/<city>/`` instead of recomputed, so a second run is seconds.

Sub-commands cover the pieces on their own: ``varuna city cache`` checks the tiles,
``varuna city report`` re-renders ``REPORT.md`` from what is already on disk, and
``varuna city layers`` lists what the API can serve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

DEFAULT_CITY = "mumbai"

app = typer.Typer(
    name="city",
    help="Build the city-in-a-box layers (terrain, roads, drains, exports).",
    no_args_is_help=False,
    add_completion=False,
)


def _echo_table(text: str) -> None:
    for line in text.splitlines():
        typer.echo(line)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    city: Annotated[
        str, typer.Option("--city", help="City slug: mumbai or chennai.")
    ] = DEFAULT_CITY,
    cache_only: Annotated[
        bool, typer.Option("--cache-only", help="Only verify the open-data cache and stop.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Recompute every step, ignoring cached outputs.")
    ] = False,
    only: Annotated[
        str | None,
        typer.Option("--only", help="Comma-separated step names to rebuild (others load cached)."),
    ] = None,
    seed: Annotated[int, typer.Option("--seed", help="Seed for every random draw.")] = 2019,
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="Write to this folder instead of city/<city>/.")
    ] = None,
) -> None:
    """Run the whole pipeline (this is ``make city CITY=<city>``)."""
    if ctx.invoked_subcommand is not None:
        return
    from varuna_city.pipeline import run_city, timing_table

    result = run_city(
        city,
        cache_only=cache_only,
        force=force,
        out_dir=out_dir,
        only=[s.strip() for s in only.split(",")] if only else None,
        seed=seed,
    )
    typer.echo("")
    _echo_table(timing_table(result))
    typer.echo("")
    typer.echo(f"city/{result.city}: {result.out_dir}")
    failed = [s for s in result.steps if s.status == "failed"]
    for step in failed:
        typer.echo(f"failed: {step.name} - {step.detail}")
    if failed:
        raise typer.Exit(code=1)


@app.command("cache")
def cache(
    city: Annotated[str, typer.Option("--city", help="City slug.")] = DEFAULT_CITY,
) -> None:
    """Check that every DEM and land-cover tile the city needs is in city/cache/."""
    from varuna_city.cache import verify_cache
    from varuna_city.config import load_city_config

    rows = verify_cache(load_city_config(city))
    for row in rows:
        state = "ok" if row.ok else f"MISSING ({row.detail})"
        typer.echo(f"{row.key:70s} {state}")
    bad = [r for r in rows if not r.ok]
    typer.echo(f"{len(rows)} tile(s), {len(bad)} problem(s).")
    if bad:
        raise typer.Exit(code=1)


@app.command("report")
def report(
    city: Annotated[str, typer.Option("--city", help="City slug.")] = DEFAULT_CITY,
) -> None:
    """Re-render REPORT.md and its maps from the layers already in city/<city>/."""
    from varuna_city.pipeline import run_city, timing_table

    result = run_city(city, only=["report"])
    _echo_table(timing_table(result))
    step = result.step("report")
    if step is not None and step.status == "failed":
        typer.echo(f"failed: {step.detail}")
        raise typer.Exit(code=1)


@app.command("audit-gravity")
def audit_gravity_command(
    city: Annotated[str, typer.Option("--city", help="City slug.")] = DEFAULT_CITY,
    bundle: Annotated[
        str | None,
        typer.Option(
            "--bundle",
            help="Replay bundle whose tide.csv maximum the outfall inverts are compared with.",
        ),
    ] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the full audit to this JSON file.")
    ] = None,
) -> None:
    """Audit the drain graph in city/<city>/ for adverse beds and downstream sills. Read-only."""
    import json

    import geopandas as gpd
    import pandas as pd
    import rasterio
    from varuna_schemas.paths import bundle_dir, city_dir

    from varuna_city.config import load_city_config
    from varuna_city.gravity import MIN_SLOPE, audit_gravity, summary_lines

    root = city_dir(city)
    nodes_path, edges_path = root / "drain_nodes.parquet", root / "drain_edges.parquet"
    if not nodes_path.is_file() or not edges_path.is_file():
        typer.echo(f"No drain graph in {root}. Run `make city CITY={city}` first.")
        raise typer.Exit(code=1)
    nodes = pd.read_parquet(nodes_path)
    edges = pd.read_parquet(edges_path)

    transform = crs = hotspots = None
    dem_path, hotspots_path = root / "dem_conditioned.tif", root / "hotspots.geojson"
    if dem_path.is_file() and hotspots_path.is_file():
        with rasterio.open(dem_path) as ds:
            transform, crs = ds.transform, str(ds.crs)
        hotspots = gpd.read_file(hotspots_path)

    tide_max: float | None = None
    if bundle is not None:
        tide_path = bundle_dir(bundle) / "tide.csv"
        if not tide_path.is_file():
            typer.echo(f"No tide.csv in {tide_path.parent}. Run `make bundle BUNDLE={bundle}`.")
            raise typer.Exit(code=1)
        tide_max = float(pd.read_csv(tide_path)["stage_m"].max())

    min_slope = float(getattr(load_city_config(city), "min_drain_slope", MIN_SLOPE))
    audit = audit_gravity(
        nodes,
        edges,
        min_slope=min_slope,
        hotspots=hotspots,
        transform=transform,
        crs=crs,
        tide_max_m=tide_max,
    )
    for line in summary_lines(audit):
        typer.echo(line)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(audit, indent=1) + "\n", encoding="utf-8", newline="\n")
        typer.echo(f"audit written to {json_out}")


@app.command("layers")
def layers(
    city: Annotated[str, typer.Option("--city", help="City slug.")] = DEFAULT_CITY,
) -> None:
    """List the map layers ``GET /v1/city/{city}/layers/{name}`` can serve."""
    from varuna_schemas.paths import city_dir

    from varuna_city.export import MAP_LAYERS

    root = city_dir(city) / "map"
    for name in MAP_LAYERS:
        path = root / f"{name}.geojson"
        size = f"{path.stat().st_size / 1e6:.1f} MB" if path.is_file() else "not built"
        typer.echo(f"{name:14s} {size}")


__all__ = ["app"]
