"""``varuna whatif prewarm``: compute full-city Twin what-ifs before anyone presses the button.

A tide scenario is one full-city coupled Twin at the cycle's inputs - 45-75 s on the demo laptop -
so the answers the demo will ask for can be computed ahead of time and served from the cache the
API reads (``POST /v1/whatif/twin``). By default they go to this machine's ``data/whatif/``;
``--shipped`` writes the read-only copy that travels with the repository (``demo/whatif/``), each
answer stamped with when and on what it was computed. A cached answer is keyed on the run's
``run.json`` bytes, the Twin's code and the city build, so a re-baked run, new Twin code or a
rebuilt city is recomputed rather than served stale. Each answer is compared with the same Twin
with nothing changed, computed once per run and stored beside the answers (``--shipped`` ships it
too).

Examples::

    uv run varuna whatif prewarm --run MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked \\
        --tide 0.5 --tide 1.0
    uv run varuna whatif prewarm --run <id> --rain 1.3
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(
    help="What-if scenarios on the full-city Twin: compute them ahead of the demo.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("prewarm")
def prewarm(
    run: Annotated[str, typer.Option("--run", help="Run id under data/runs.")],
    tide: Annotated[
        list[float] | None,
        typer.Option("--tide", help="Tide offset in metres; repeat for several. Default 0."),
    ] = None,
    rain: Annotated[
        list[float] | None,
        typer.Option("--rain", help="Rain scale; repeat for several. Default 1.0."),
    ] = None,
    clean: Annotated[
        list[str] | None,
        typer.Option("--clean", help="Road-segment id whose pipes are cleaned; repeatable."),
    ] = None,
    shipped: Annotated[
        bool,
        typer.Option("--shipped", help="Write the read-only copy in demo/whatif, not data/whatif."),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Recompute even when cached.")] = False,
) -> None:
    """Compute every rain x tide combination asked for and store each answer."""
    from fastapi import HTTPException
    from varuna_api.routers.whatif import prewarm_scenario

    tides = tide or [0.0]
    rains = rain or [1.0]
    cleaned = set(clean or ())
    failed = 0
    for rain_scale in rains:
        for offset in tides:
            label = f"rain {rain_scale:.1f}x, tide {offset:+.1f} m"
            try:
                result, where = prewarm_scenario(
                    run,
                    rain_scale=rain_scale,
                    tide_offset_m=offset,
                    cleaned_segments=cleaned,
                    shipped=shipped,
                    force=force,
                )
            except HTTPException as error:
                failed += 1
                detail = error.detail if isinstance(error.detail, dict) else {}
                typer.echo(f"{label}: refused. {detail.get('message', error.detail)}", err=True)
                continue
            if where["source"] == "computed":
                sea = result["sea"]
                drift = (result.get("baseline") or {}).get("drift") or {}
                typer.echo(
                    f"{label}: computed in {result['ms'] / 1000:.1f} s, "
                    f"{result['n_changed']:,} streets changed, {result['hotspots_moved']} "
                    f"hotspots moved, sea onto land {sea['sea_to_land_m3'] / 1e6:.2f} Mm3 "
                    f"({sea.get('sea_to_land_change_m3', 0.0) / 1e6:+.2f} Mm3 against the tide "
                    f"as forecast). Stored at {where.get('path')}."
                )
                if drift.get("n_changed"):
                    typer.echo(
                        f"  The run's own map differs from this Twin with nothing changed on "
                        f"{drift['n_changed']:,} streets, by up to {drift['max_abs_delta_cm']} cm: "
                        "re-bake the run before quoting its map beside this answer."
                    )
            else:
                typer.echo(f"{label}: already cached ({where['source']}). {where['label']}")
    if failed:
        raise typer.Exit(code=1)
