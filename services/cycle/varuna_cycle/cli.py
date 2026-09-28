"""``varuna cycle`` - one cycle from the command line, and the plan of a bake (SPEC.md 11.11).

Appendix B's cheat sheet lists ``uv run varuna cycle --bundle MUM-2019-07-02 --t
2019-07-02T06:40+05:30 --live``, and it did not run: ``varuna_cycle`` had no ``cli`` module, so the
root task runner never attached a ``cycle`` command. The root app finds this module by name
(``varuna_cli.main.build_app``), so nothing else had to change for the command to appear.

``varuna cycle plan`` prints the cycles ``make bake`` would compute for the same options and
computes none of them. A bake of a whole bundle is hours of CPU; seeing the list first is worth
a command.

Imports are deferred into the commands, as in ``varuna_replay.cli``: the root app imports this
module on every ``varuna`` invocation, and ``run_cycle`` reaches pySTEPS and Numba.
"""

from __future__ import annotations

from typing import Annotated

import typer

DEFAULT_BUNDLE = "MUM-2019-07-02"

app = typer.Typer(
    name="cycle",
    help="Cycle orchestrator: run one cycle, or list the cycles a bake would compute.",
    no_args_is_help=False,
    add_completion=False,
)


def _fail(error: BaseException) -> typer.Exit:
    """Print what went wrong without a traceback; the engines' messages already name the fix."""
    typer.echo(str(error), err=True)
    return typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def cycle_main(
    ctx: typer.Context,
    bundle: Annotated[str, typer.Option("--bundle", help="Replay bundle id.")] = DEFAULT_BUNDLE,
    t: Annotated[
        str | None,
        typer.Option(
            "--t",
            help=(
                "Cycle instant: ISO 8601 with an offset, or HH:MM IST on the bundle's day. "
                "Snapped onto the cycle ladder; default the first cycle the bundle can forecast."
            ),
        ),
    ] = None,
    live: Annotated[
        bool, typer.Option("--live", help="Stamp the run live, as Compute live does.")
    ] = False,
    city: Annotated[
        str | None, typer.Option("--city", help="Built city to run on; default the bundle's own.")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing run of the same id.")
    ] = False,
) -> None:
    """Run one cycle end to end - Sky, Twin, Flash, products, Pulse - and publish its run."""
    if ctx.invoked_subcommand is not None:
        return

    from varuna_replay.bundle import BundleLayout, BundleNotFoundError, load_manifest

    from varuna_cycle.bake import describe_result, parse_instant
    from varuna_cycle.twin_cycle import run_cycle

    try:
        manifest = load_manifest(BundleLayout.for_bundle(bundle).root)
        when = parse_instant(t, on=manifest.t0) if t else None
        result = run_cycle(
            manifest.id,
            when,
            city=city or manifest.city,
            mode="live" if live else "baked",
            overwrite=overwrite,
        )
    except (BundleNotFoundError, OSError, ValueError) as error:
        raise _fail(error) from error

    typer.echo(describe_result(result))
    for note in result.notes:
        typer.echo(f"  {note}")


@app.command("plan")
def plan(
    bundle: Annotated[str, typer.Option("--bundle", help="Replay bundle id.")] = DEFAULT_BUNDLE,
    every: Annotated[
        int | None,
        typer.Option("--every", help="Minutes between cycles; default every cycle of the bundle."),
    ] = None,
    start: Annotated[
        str | None,
        typer.Option("--from", help="First cycle: HH:MM IST on the bundle's day, or ISO 8601."),
    ] = None,
    end: Annotated[
        str | None, typer.Option("--to", help="Last cycle, in the same forms as --from.")
    ] = None,
) -> None:
    """List the cycles ``make bake`` would compute with these options, and which already exist."""
    from varuna_replay.bundle import BundleNotFoundError

    from varuna_cycle.bake import describe_plan, plan_bundle

    try:
        chosen = plan_bundle(bundle, every_min=every, start=start, end=end)
    except (BundleNotFoundError, OSError, ValueError) as error:
        raise _fail(error) from error
    for line in describe_plan(chosen):
        typer.echo(line)
