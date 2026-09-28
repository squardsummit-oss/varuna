"""``varuna bundle`` and ``varuna replay`` - the replay service's command line (P2.1-P2.8).

``varuna bundle build <id>`` is the one command that writes a whole bundle and then checks it:
``MUM-2019-07-02`` is the reconstruction of tasks P2.3-P2.6, ``MUM-IDF-25yr`` and
``CHN-IDF-25yr`` the design storms of P2.8. ``varuna bundle validate <id>`` is what SPEC.md
P2.1 asks for: it checks a folder against the bundle contract and prints a line per rule,
naming the file and the rule behind every complaint.

``varuna bundle`` with neither a sub-command nor ``--bundle`` prints what the commands are and
exits 2, because a build has to be told what to build.

Imports are deferred into the commands, as in ``varuna_city.cli``: the root task runner must
stay fast, and importing numpy, zarr and pandas to print ``--help`` would not be.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

DEFAULT_CITY = "mumbai"
DEFAULT_BUNDLE = "MUM-2019-07-02"

USAGE = (
    "Name the bundle to build: 'varuna bundle build MUM-2019-07-02' writes the reconstructed "
    "replay of 2 July 2019, and 'varuna bundle build MUM-IDF-25yr' (or CHN-IDF-25yr) writes a "
    "design storm.\n"
    "Also here: 'varuna bundle validate <id>' checks a bundle against the contract, "
    "'varuna bundle list' shows what is on disk, 'varuna bundle show <id>' prints what a "
    "bundle claims about itself, and 'varuna bundle storm <id>' prints its cell table."
)

bundle_app = typer.Typer(
    name="bundle",
    help="Replay bundles: build, validate, list and inspect.",
    no_args_is_help=False,
    add_completion=False,
)

app = typer.Typer(
    name="replay",
    help="Replay service: bundle contract, storm designer, clock.",
    no_args_is_help=True,
    add_completion=False,
)


def _echo(text: str) -> None:
    for line in text.splitlines():
        typer.echo(line)


@bundle_app.callback(invoke_without_command=True)
def bundle_main(
    ctx: typer.Context,
    bundle: Annotated[
        str | None, typer.Option("--bundle", help="Replay bundle id to generate.")
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Directory to create the bundle folder in."),
    ] = None,
) -> None:
    """Generate a replay bundle (storm designer, synthetic streams, curated ground truth).

    ``make bundle BUNDLE=<id>`` arrives here as ``--bundle <id>``, which is the same build
    ``varuna bundle build <id>`` runs.
    """
    if ctx.invoked_subcommand is not None:
        return
    if bundle is None:
        _echo(USAGE)
        raise typer.Exit(code=2)
    _build(bundle, out_dir)


def _build(bundle: str, out_dir: Path | None) -> None:
    """Build one bundle by id and validate what was written, exiting non-zero if it is invalid."""
    from varuna_replay.build import build_bundle
    from varuna_replay.validate import validate_bundle

    result = build_bundle(bundle, bundles_root=out_dir)
    _echo(result.summary())
    typer.echo("")
    report = validate_bundle(result.root)
    _echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@bundle_app.command("build")
def build(
    bundle: Annotated[
        str, typer.Argument(help="Bundle id: MUM-2019-07-02, MUM-IDF-25yr or CHN-IDF-25yr.")
    ] = DEFAULT_BUNDLE,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Directory to create the bundle folder in."),
    ] = None,
) -> None:
    """Write a bundle end to end, then validate it against the contract.

    ``MUM-2019-07-02`` is the reconstruction: the storm is fitted to the sourced ground-truth
    pins and calibrated to a stated share of IMD's 24-hour Santacruz total, and the gauges,
    tide, traffic and reports are generated from that same field. The design storms need only
    a city config. Nothing downloads.
    """
    _build(bundle, out_dir)


@bundle_app.command("validate")
def validate(
    bundle: Annotated[str, typer.Argument(help="Bundle id (MUM-2019-07-02) or a path to one.")],
) -> None:
    """Check a bundle against the contract in SPEC.md 10.2 and print a line per rule."""
    from varuna_replay.validate import validate_bundle

    report = validate_bundle(bundle)
    _echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@bundle_app.command("list")
def list_bundles() -> None:
    """List the bundles under bundles/ with their label and window."""
    from varuna_replay.bundle import list_bundle_ids, load_manifest

    ids = list_bundle_ids()
    if not ids:
        typer.echo("No bundles yet - run 'varuna bundle design --city mumbai' to write one.")
        return
    for bundle_id in ids:
        try:
            manifest = load_manifest(bundle_id)
        except Exception as exc:  # a broken bundle must still be listed
            typer.echo(f"{bundle_id:20s} unreadable manifest: {exc}")
            continue
        typer.echo(
            f"{manifest.id:20s} {manifest.label:22s} {manifest.t0:%Y-%m-%d %H:%M} -> "
            f"{manifest.t1:%H:%M} IST  {manifest.n_cycles} cycles  "
            f"{manifest.ground_truth_n} pin(s)"
        )


@bundle_app.command("show")
def show(
    bundle: Annotated[str, typer.Argument(help="Bundle id or path.")],
) -> None:
    """Print what a bundle claims about itself: window, sources, storm and honesty labels."""
    from varuna_replay.bundle import BundleLayout, load_manifest

    manifest = load_manifest(bundle)
    layout = BundleLayout.for_bundle(bundle)
    typer.echo(f"{manifest.id}  {manifest.label}")
    typer.echo(f"  city         {manifest.city}  aoi {manifest.aoi.as_tuple()}")
    typer.echo(
        f"  window       {manifest.t0.isoformat()} -> {manifest.t1.isoformat()} "
        f"({manifest.duration_min} min, {manifest.n_cycles} cycles)"
    )
    typer.echo(f"  cadences     {manifest.cadences}")
    typer.echo(f"  seed         {manifest.seed}")
    typer.echo(f"  ground truth {manifest.ground_truth_n} sourced pin(s) inside the area")
    for note in manifest.synthetic_notes:
        typer.echo(f"  synthetic    {note}")
    for source in manifest.sources:
        typer.echo(f"  source       {source.name} - {source.url}")
    if manifest.calibration:
        typer.echo(f"  calibration  {manifest.calibration}")
        typer.echo(f"  basis        {manifest.calibration_basis}")
    if manifest.storm is not None:
        storm = manifest.storm
        typer.echo(
            f"  storm        {len(storm.cells)} cell(s), background "
            f"{storm.background_mm_h} mm/h, wind from {storm.wind_from_deg:.0f} deg at "
            f"{storm.wind_speed_ms} m/s, intensity scale {storm.intensity_scale}"
        )
    if manifest.design_storm is not None:
        design = manifest.design_storm
        typer.echo(
            f"  design storm {design.intensity_mm_h:.0f} mm/h over {design.duration_min} min "
            f"= {design.total_depth_mm:.0f} mm, peak at "
            f"{design.peak_position_r * 100:.0f} %"
        )
        typer.echo(f"  basis        {design.basis}")
    for name, path in layout.members().items():
        state = "ok" if path.exists() else "absent"
        typer.echo(f"  {state:<12} {name}")


@bundle_app.command("design")
def design(
    city: Annotated[
        str, typer.Option("--city", help="City slug: mumbai or chennai.")
    ] = DEFAULT_CITY,
    intensity: Annotated[
        str, typer.Option("--intensity", help="Design intensity to use: upgraded or legacy.")
    ] = "upgraded",
    duration_min: Annotated[
        int, typer.Option("--duration-min", help="Storm duration in minutes.")
    ] = 180,
    out_dir: Annotated[
        Path | None,
        typer.Option(
            "--out-dir",
            help="Directory to create the bundle folder in, instead of bundles/.",
        ),
    ] = None,
) -> None:
    """Write the design-storm bundle for a city (MUM-IDF-25yr, CHN-IDF-25yr) and validate it."""
    from varuna_replay.build import build_design_bundle, load_city
    from varuna_replay.validate import validate_bundle

    result = build_design_bundle(
        load_city(city),
        intensity_key=intensity,
        duration_min=duration_min,
        bundles_root=out_dir,
    )
    _echo(result.summary())
    typer.echo("")
    report = validate_bundle(result.root)
    _echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("validate")
def replay_validate(
    bundle: Annotated[str, typer.Argument(help="Bundle id or path.")],
) -> None:
    """Alias of 'varuna bundle validate'."""
    validate(bundle)


@app.command("bundles")
def replay_bundles() -> None:
    """Alias of 'varuna bundle list'."""
    list_bundles()


@app.command("storm")
def storm(
    bundle: Annotated[str, typer.Argument(help="Bundle id or path.")],
) -> None:
    """Print the storm designer's cell table for a bundle (the /replay panel's table)."""
    from varuna_replay.bundle import load_manifest

    manifest = load_manifest(bundle)
    if manifest.storm is None:
        typer.echo(f"{manifest.id} records no storm design.")
        if manifest.design_storm is not None:
            typer.echo(f"It is a design storm: {manifest.design_storm.basis}")
        return
    design_spec = manifest.storm
    typer.echo(
        f"{manifest.id}: {len(design_spec.cells)} cell(s), seed {design_spec.seed}, "
        f"background {design_spec.background_mm_h} mm/h, intensity scale "
        f"{design_spec.intensity_scale}"
    )
    typer.echo(
        f"{'cell':10s} {'birth':>8s} {'life':>7s} {'peak':>8s} {'sigma':>8s} "
        f"{'u m/s':>7s} {'v m/s':>7s}"
    )
    for cell in design_spec.cells:
        typer.echo(
            f"{cell.id:10s} {cell.birth_min:8.1f} {cell.lifetime_min:7.1f} "
            f"{cell.peak_mm_h * design_spec.intensity_scale:8.1f} {cell.sigma_m:8.0f} "
            f"{cell.u_ms:7.2f} {cell.v_ms:7.2f}"
        )
    for note in design_spec.notes:
        typer.echo(f"  {note}")


__all__ = ["app", "bundle_app"]
