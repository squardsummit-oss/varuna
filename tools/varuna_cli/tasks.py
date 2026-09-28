"""Task commands behind every ``make`` target (SPEC.md section 4.3).

Each ``make <target>`` recipe is ``uv run varuna <target>``; the logic lives here so that
Windows machines without GNU make and CI runners behave identically. Long-running
services (``dev``, ``demo``) are launched through :mod:`varuna_cli.procs` so tests can
patch the launcher.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from varuna_cli import procs
from varuna_cli.procs import Service, console

# ---------------------------------------------------------------------------
# Repository facts
# ---------------------------------------------------------------------------

try:
    from varuna_schemas.paths import bundles_dir, city_dir, repo_root, runs_dir
except ImportError:  # pragma: no cover - schemas package is a hard dependency

    def repo_root() -> Path:  # type: ignore[misc]
        return Path(__file__).resolve().parents[2]

    def runs_dir() -> Path:  # type: ignore[misc]
        return repo_root() / "data" / "runs"

    def bundles_dir() -> Path:  # type: ignore[misc]
        return repo_root() / "bundles"

    def city_dir(city: str) -> Path:  # type: ignore[misc]
        return repo_root() / "city" / city


ENGINES: tuple[str, ...] = (
    "api",
    "cycle",
    "replay",
    "city",
    "sky",
    "twin",
    "pulse",
    "flash",
    "products",
    "route",
    "verify",
)

COMMAND_APP = "@varuna/command"
PYTEST_PATHS: tuple[str, ...] = ("packages", "services", "tests", "tools")
DEFAULT_CITY = "mumbai"
DEFAULT_BUNDLE = "MUM-2019-07-02"
DEFAULT_API_PORT = 8000
DEFAULT_UI_PORT = 3000

# Tasks whose engine does not exist yet. ``register_optional`` in ``main.py`` replaces a
# placeholder with the real engine sub-app of the same name once it becomes importable.
PLACEHOLDER_PHASES: dict[str, tuple[int, str]] = {
    "city": (1, "City-in-a-box (Mumbai)"),
    "bundle": (2, "Replay bundle and storm designer"),
    # `bake` left on 2026-09-13, three days after P5.6 was ticked on it: it runs cycles now.
    # `train` left on 2026-09-13 for the same reason `pack` did: it fits the emulator now.
    # `pack` was here until P10.6 implemented it (2026-09-11). Leaving it behind did not change
    # what the CLI does - the real command wins - but the phase-gate test drove every entry in
    # this dict expecting a refusal, so it invoked the real target and wrote an 830 MB package
    # on every full test run before failing on the exit code.
    # `demo-video` left on 2026-09-26 (P10.7): it records the take now.
}

NO_BUNDLE_MESSAGE = (
    "No bundle baked yet - run make bundle BUNDLE={bundle} then make bake BUNDLE={bundle}.\n"
    "Starting the console in its empty state."
)

_TRUTHY = {"1", "true", "yes", "on"}

COMPUTE_LIVE_ENV = "VARUNA_COMPUTE_LIVE"
"""The API's Compute live gate (`services/api/varuna_api/routers/cycle.py`), read from its process
environment only."""


def compute_live_flag() -> str:
    """``VARUNA_COMPUTE_LIVE`` for an API this task runner launches on this machine.

    On unless the shell or ``.env`` says otherwise: ``make dev`` and ``make demo`` run on the demo
    laptop, where the console's Compute live button is part of the demo (SPEC.md 7.2), and the
    API reads the flag from its process environment only, so without this it was off everywhere.
    A cycle takes a minute or more here (median about 104 s, 48-224 s over 8 runs, against section
    14's 15 s), which the button says before it is pressed. The deployed API never goes through
    this launcher and keeps it off.
    """
    explicit = os.environ.get(COMPUTE_LIVE_ENV, "").strip()
    if explicit:
        return explicit
    try:
        from dotenv import dotenv_values

        from_file = (dotenv_values(repo_root() / ".env").get(COMPUTE_LIVE_ENV) or "").strip()
    except Exception:  # a broken .env must not stop the task runner
        from_file = ""
    return from_file or "1"


def _compute_live_words(flag: str) -> str:
    return (
        "Compute live on"
        if flag.strip().lower() in _TRUTHY
        else f"Compute live off: {COMPUTE_LIVE_ENV}={flag}"
    )


@dataclass(slots=True)
class RuntimeConfig:
    """The few settings the task runner needs, read from ``.env`` when available."""

    city: str = DEFAULT_CITY
    bundle: str = DEFAULT_BUNDLE
    replay_speed: float = 30.0
    api_port: int = DEFAULT_API_PORT
    ui_port: int = DEFAULT_UI_PORT


def load_config() -> RuntimeConfig:
    """Read ``varuna_schemas.settings`` (which reads ``.env``); fall back to the defaults."""
    try:
        from varuna_schemas.settings import reload_settings

        settings = reload_settings()
    except Exception:  # a broken .env must not stop the task runner
        return RuntimeConfig()
    return RuntimeConfig(
        city=settings.varuna_city,
        bundle=settings.varuna_bundle,
        replay_speed=settings.varuna_replay_speed,
        api_port=settings.api_port,
        ui_port=settings.ui_port,
    )


def command_app_dir() -> Path:
    return repo_root() / "apps" / "command"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StepResult:
    name: str
    returncode: int
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _print_summary(title: str, results: list[StepResult]) -> None:
    table = Table(title=title, show_lines=False, title_justify="left")
    table.add_column("Step")
    table.add_column("Result")
    table.add_column("Time", justify="right")
    for result in results:
        status = (
            "[green]passed[/green]"
            if result.ok
            else f"[red]failed (exit {result.returncode})[/red]"
        )
        table.add_row(result.name, status, f"{result.duration_s:.1f} s")
    console.print(table)


def _run_steps(
    title: str,
    steps: list[tuple[str, list[str], Path | None]],
    *,
    stop_on_failure: bool = False,
) -> list[StepResult]:
    results: list[StepResult] = []
    for name, argv, cwd in steps:
        console.rule(f"[bold]{name}[/bold]")
        result = procs.run(argv, cwd=cwd)
        results.append(StepResult(name, result.returncode, result.duration_s))
        if stop_on_failure and not result.ok:
            break
    _print_summary(title, results)
    return results


def _exit_from(results: list[StepResult]) -> None:
    failed = [r for r in results if not r.ok]
    if failed:
        console.print(f"[red]{len(failed)} step(s) failed.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]All steps passed.[/green]")


def not_implemented(target: str) -> None:
    """Print the phase gate for a not-yet-built target and exit with code 2 (no traceback)."""
    phase, name = PLACEHOLDER_PHASES[target]
    console.print(
        f"[yellow]make {target} is not implemented until Phase {phase} ({name}).[/yellow]\n"
        f"See SPEC.md section 13 for the Phase {phase} checklist; "
        "'uv run varuna doctor' shows what exists today."
    )
    raise typer.Exit(code=2)


def engine_status(name: str) -> tuple[bool, bool]:
    """(package importable, ``varuna_<name>.cli`` exposes a Typer ``app``)."""
    try:
        importlib.import_module(f"varuna_{name}")
    except Exception:  # a broken engine must not break the doctor
        return False, False
    try:
        cli = importlib.import_module(f"varuna_{name}.cli")
    except Exception:
        return True, False
    return True, isinstance(getattr(cli, "app", None), typer.Typer)


def list_runs(bundle: str | None = None) -> list[dict[str, object]]:
    """Run folders under ``data/runs`` that carry a readable ``run.json``."""
    root = runs_dir()
    if not root.is_dir():
        return []
    runs: list[dict[str, object]] = []
    for folder in sorted(root.iterdir()):
        meta_path = folder / "run.json"
        if not folder.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        meta.setdefault("run_id", folder.name)
        if bundle is None or meta.get("bundle") == bundle:
            runs.append(meta)
    return runs


def baked_runs(bundle: str) -> list[dict[str, object]]:
    """Runs of ``bundle`` that were pre-computed by ``make bake`` (``mode == baked``)."""
    return [
        run
        for run in list_runs(bundle)
        if run.get("mode") == "baked" or str(run.get("run_id", "")).endswith("-baked")
    ]


def list_bundles() -> list[str]:
    root = bundles_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "manifest.json").is_file())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def setup(
    skip_web: Annotated[bool, typer.Option("--skip-web", help="Skip pnpm install.")] = False,
    skip_python: Annotated[bool, typer.Option("--skip-python", help="Skip uv sync.")] = False,
    skip_browsers: Annotated[
        bool, typer.Option("--skip-browsers", help="Skip the Playwright Chromium download.")
    ] = False,
) -> None:
    """Install both workspaces, hooks and browsers; copy .env.example to .env. Idempotent."""
    root = repo_root()
    steps: list[tuple[str, list[str], Path | None]] = []
    if not skip_web:
        steps.append(("pnpm install", procs.pnpm("install"), root))
    if not skip_python:
        steps.append(("uv sync --all-groups", procs.command("uv", "sync", "--all-groups"), root))
    if procs.resolve_executable("pre-commit") or importlib.util.find_spec("pre_commit"):
        steps.append(("pre-commit install", procs.python("-m", "pre_commit", "install"), root))
    else:
        console.print("pre-commit is not installed; skipping hook installation.", style="dim")
    if not skip_browsers:
        steps.append(
            (
                "playwright install chromium",
                procs.pnpm("exec", "playwright", "install", "chromium"),
                command_app_dir(),
            )
        )
    results = _run_steps("Setup", steps, stop_on_failure=True)

    env_file = root / ".env"
    example = root / ".env.example"
    if env_file.exists():
        console.print(".env already present; left unchanged.", style="dim")
    elif example.exists():
        shutil.copyfile(example, env_file)
        console.print("Copied .env.example to .env")
    results.append(StepResult(".env", 0, 0.0))
    _exit_from(results)


def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Check tool versions, .env, engines, city layers, bundles and runs."""
    root = repo_root()
    config = load_config()

    tools: dict[str, str | None] = {
        "node": _version(["node", "--version"]),
        "pnpm": _version(["pnpm", "--version"]),
        "uv": _version(["uv", "--version"]),
        "python": f"{platform.python_version()} ({sys.executable})",
        "make": _version(["make", "--version"]),
        "docker": _version(["docker", "--version"]),
    }
    engines = {name: engine_status(name) for name in ENGINES}
    reasons = engine_reasons()
    city_path = city_dir(config.city)
    city_files = sum(1 for p in city_path.rglob("*") if p.is_file()) if city_path.is_dir() else 0
    bundles = list_bundles()
    runs = list_runs()
    baked = [r for r in runs if r.get("mode") == "baked"]
    report = {
        "repo_root": str(root),
        "platform": platform.platform(),
        "tools": tools,
        "env_file": (root / ".env").is_file(),
        "engines": {
            name: {"package": pkg, "cli": cli, "reason": None if cli else reasons.get(name)}
            for name, (pkg, cli) in engines.items()
        },
        "city": {"name": config.city, "path": str(city_path), "files": city_files},
        "bundles": bundles,
        "runs": {"total": len(runs), "baked": len(baked), "live": len(runs) - len(baked)},
        "config": {
            "bundle": config.bundle,
            "api_port": config.api_port,
            "ui_port": config.ui_port,
            "replay_speed": config.replay_speed,
        },
    }
    if as_json:
        typer.echo(json.dumps(report, indent=2))
        return

    table = Table(title="varuna doctor", title_justify="left")
    table.add_column("Check")
    table.add_column("Status")
    for name, version in tools.items():
        table.add_row(name, f"[green]{version}[/green]" if version else "[red]missing[/red]")
    table.add_row(
        ".env",
        "[green]present[/green]"
        if report["env_file"]
        else "[yellow]missing (make setup copies .env.example)[/yellow]",
    )
    available = [name for name, (pkg, _cli) in engines.items() if pkg]
    with_cli = [name for name, (_pkg, cli) in engines.items() if cli]
    table.add_row("engines importable", ", ".join(available) or "[red]none[/red]")
    table.add_row(
        "engine sub-commands",
        ", ".join(with_cli)
        if with_cli
        else "[yellow]none yet (varuna_<engine>.cli arrives per phase)[/yellow]",
    )
    table.add_row(
        f"city/{config.city}",
        f"[green]{city_files} files[/green]"
        if city_files
        else "[yellow]not built (make city)[/yellow]",
    )
    table.add_row(
        "bundles",
        ", ".join(bundles) if bundles else "[yellow]none (make bundle)[/yellow]",
    )
    table.add_row(
        "runs",
        f"{len(runs)} total, {len(baked)} baked" if runs else "[yellow]none (make bake)[/yellow]",
    )
    table.add_row("default bundle", config.bundle)
    table.add_row("ports", f"API {config.api_port}, UI {config.ui_port}")
    console.print(table)
    broken = {
        name: reason
        for name, reason in reasons.items()
        if reason and not engines.get(name, (False, False))[1] and not _is_missing_cli(name, reason)
    }
    for name, reason in broken.items():
        console.print(f"[yellow]{name}[/yellow]: sub-command not loaded: {reason}", style="dim")


def engine_reasons() -> dict[str, str | None]:
    """Why each engine sub-command is absent, from the root app's registry.

    Imported lazily because ``varuna_cli.main`` imports this module.
    """
    try:
        from varuna_cli.main import registry
    except Exception:
        return {}
    return {name: item.reason for name, item in registry.engines.items()}


def _is_missing_cli(name: str, reason: str) -> bool:
    """True for the expected case: the engine has no ``cli`` module yet (arrives per phase)."""
    return reason.startswith("ModuleNotFoundError") and f"varuna_{name}.cli" in reason


def _version(argv: list[str]) -> str | None:
    exe = procs.resolve_executable(argv[0])
    if exe is None:
        return None
    output = procs.capture([exe, *argv[1:]])
    if not output:
        return None
    return output.splitlines()[0].strip()


def city(
    city: Annotated[
        str, typer.Option("--city", help="City slug: mumbai or chennai.")
    ] = DEFAULT_CITY,
    cache_only: Annotated[
        bool, typer.Option("--cache-only", help="Only download the open data into city/cache/.")
    ] = False,
) -> None:
    """Build the city-in-a-box layers (Phase 1)."""
    not_implemented("city")


def bundle(
    bundle: Annotated[str, typer.Option("--bundle", help="Replay bundle id.")] = DEFAULT_BUNDLE,
) -> None:
    """Generate a replay bundle: storm designer, synthetic streams, ground truth (Phase 2)."""
    not_implemented("bundle")


def bake(
    bundle: Annotated[str, typer.Option("--bundle", help="Replay bundle id.")] = DEFAULT_BUNDLE,
    every: Annotated[
        int | None,
        typer.Option("--every", help="Minutes between baked cycles; default every cycle."),
    ] = None,
    start: Annotated[
        str | None,
        typer.Option("--from", help="First cycle: HH:MM IST on the bundle's day, or ISO 8601."),
    ] = None,
    end: Annotated[
        str | None, typer.Option("--to", help="Last cycle, in the same forms as --from.")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Recompute cycles whose run already exists.")
    ] = False,
) -> None:
    """Pre-compute every 5-minute cycle of a bundle into data/runs/, oldest first (P5.6).

    A run that already exists is skipped, so an interrupted bake resumes where it stopped.
    ``make bake ARGS="--every 30 --from 06:10 --to 09:10"`` rebuilds the seven cycles that ship in
    ``demo/runs``; ``uv run varuna cycle plan`` lists what a bake would compute without running it.
    """
    try:
        from varuna_cycle import bake as engine
    except ImportError as error:
        console.print(
            f"The cycle engine is not importable ({error}); run make setup.",
            style="red",
            markup=False,
        )
        raise typer.Exit(code=1) from error

    try:
        plan = engine.plan_bundle(bundle, every_min=every, start=start, end=end)
    except Exception as error:  # planning reads the manifest and the radar cube; say which failed
        console.print(str(error), style="red", markup=False)
        raise typer.Exit(code=1) from error

    console.print(
        f"{plan.bundle} on {plan.city}: {len(plan.instants)} cycle(s), oldest first.", markup=False
    )
    report = engine.bake_cycles(
        plan,
        overwrite=overwrite,
        on_event=lambda *event: console.print(
            engine.describe_event(*event), markup=False, highlight=False
        ),
    )
    console.print(
        f"Baked {len(report.baked)}, skipped {len(report.skipped)} already baked, "
        f"failed {len(report.failed)}.",
        markup=False,
    )
    console.print(engine.PULSE_NOTE, style="dim", markup=False)
    if not report.ok:
        raise typer.Exit(code=1)


TRAIN_DIR = "data/train"
"""Where the Twin runs that fit Flash-lite live. Gitignored: 8 runs at ~24 MB each."""


def train(
    train_dir: Annotated[
        Path,
        typer.Option("--train-dir", help="Directory of Twin runs named train-*.npz."),
    ] = Path(TRAIN_DIR),
    city: Annotated[
        str, typer.Option("--city", help="City the runs were computed on: mumbai or chennai.")
    ] = DEFAULT_CITY,
    holdout: Annotated[
        int,
        typer.Option("--holdout", help="Heaviest storms held out of the fit and scored on."),
    ] = 2,
) -> None:
    """Fit Flash-lite from Twin runs (task P7.5); (P1) train the GNN.

    The fit itself is :func:`varuna_flash.train.train_from_runs`, which writes the model beside
    the corpus and the measured skill to ``docs/verification/flash_lite.json`` - the file `/verify`
    reads and the "Reduced-order emulator calibrated to VARUNA-Twin" badge is claiming. This
    target exists so that both are reproducible from a clean clone (SPEC.md 4.3) rather than
    only from the laptop that first produced them.

    The report path is resolved against the repository, not the working directory: a report
    written next to wherever somebody happened to stand is a second copy of the number `/verify`
    reads, and the two would drift.
    """
    from varuna_flash.train import train_from_runs

    files = sorted(train_dir.glob("train-*.npz")) if train_dir.is_dir() else []
    if len(files) <= holdout:
        console.print(
            f"[yellow]No training corpus to fit from: {len(files)} run(s) named train-*.npz "
            f"under {train_dir}; the fit needs more than the {holdout} it holds out.[/yellow]\n"
            "Nothing was written. docs/verification/flash_lite.json is the skill `/verify` "
            "reports and the what-if drawer's badge claims, so it is not written from an "
            "empty corpus (SPEC.md rule 6).\n"
            "The generator for that corpus - design storms x blockage draws, seeded, one "
            "train-*.npz per Twin run - is not in this repository yet; the eight runs P7.5 was "
            "fitted on were produced by hand. The model they produced ships as "
            "demo/flash_lite.npz, which is what the API loads.\n"
            "Point --train-dir at a directory of train-*.npz Twin runs to re-fit "
            f"(looked in {train_dir})."
        )
        raise typer.Exit(code=2)

    console.print(
        f"[bold]Fitting Flash-lite[/] from {len(files)} Twin run(s) in {train_dir} "
        f"({city}), holding out the {holdout} heaviest."
    )
    report = train_from_runs(
        train_dir,
        city=city,
        holdout=holdout,
        out_report=repo_root() / "docs" / "verification" / "flash_lite.json",
    )

    table = Table(show_edge=False)
    table.add_column("measure")
    table.add_column("value", justify="right")
    table.add_row("training runs", str(report["n_training_runs"]))
    table.add_row("held-out runs", str(report["n_holdout_runs"]))
    table.add_row("segments fitted", f"{report['fitted_segments']} of {report['n_segments']}")
    table.add_row("RMSE (held out)", f"{report['rmse_cm']} cm")
    table.add_row("CSI at 30 cm (held out)", str(report["csi_30cm"]))
    console.print(table)

    console.print(f"\nModel {report['model']}")
    console.print("Report docs/verification/flash_lite.json")
    console.print(str(report["note"]), style="dim")


# ---- the offline package (task P10.6) ---------------------------------------------------

PACK_DIR = "dist/varuna-offline"
"""Where `make pack` writes. Gitignored: it is a build output, and it is hundreds of megabytes."""


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


def pack() -> None:
    """Build the offline package: everything the finale needs with the venue's network off.

    SPEC.md 17 lists "no network at the venue" as a risk whose fallback is this target, and
    SPEC.md 10.4 wants Chennai reachable from cache. What actually has to travel:

    * **the baked runs** - the whole demo reads them, and a live cycle is 74 s of CPU;
    * **the city layers** for Mumbai and Chennai, which are what the map draws;
    * **the replay bundles**, including the radar the preview animates;
    * **the open-data cache**, so the onboarding wizard rebuilds Chennai without Overpass;
    * **the fitted emulator** and the committed verification numbers the landing falls back to.

    Not the basemap imagery. Esri's tiles are not redistributable, and the map is built to work
    without them - the note at the top of `CityMap` says why the city's own GIS is the ground
    truth here rather than a photograph. The package prints that plainly rather than leaving
    somebody to discover it on stage.

    Copies rather than archives: the finale runs *from* this directory, and a zip would only add a
    step to do under pressure.
    """
    import shutil

    root = repo_root()
    target = root / PACK_DIR
    console.print(f"[bold]Packing VARUNA for offline use[/] -> {target}")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    # (source, destination, what it is, whether the demo fails without it)
    parts: list[tuple[Path, str, str, bool]] = [
        (root / "data" / "runs", "data/runs", "baked runs", True),
        (root / "city", "city", "city layers and the open-data cache", True),
        (root / "bundles", "bundles", "replay bundles", True),
        (root / "demo", "demo", "seed runs and the fitted emulator", False),
        (root / "docs" / "research", "docs/research", "sourced registers and ground truth", True),
        (root / "docs" / "verification", "docs/verification", "measured skill", False),
    ]

    missing: list[str] = []
    table = Table(show_edge=False)
    table.add_column("part")
    table.add_column("size", justify="right")
    table.add_column("status")

    for source, relative, label, required in parts:
        destination = target / relative
        if not source.exists():
            missing.append(f"{label} ({source.relative_to(root)})" if required else "")
            table.add_row(label, "-", "[yellow]missing[/]" if required else "[dim]absent[/]")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        table.add_row(label, _mb(_dir_size(destination)), "[green]packed[/]")

    console.print(table)

    total = _dir_size(target)
    console.print(f"\n[bold]{_mb(total)}[/] at {target}")
    console.print(
        "\nThe basemap imagery is [bold]not[/] in this package: Esri's tiles are not "
        "redistributable. With the network off the map draws the city's own GIS - 39,259 building "
        "footprints and the 21,296-segment street network - which is what it was built to do."
    )
    console.print(
        "Verify it: turn the Wi-Fi off, then `make demo`. The console, the drain X-ray, the route "
        "planner and the onboarding wizard all read from here."
    )

    blocking = [m for m in missing if m]
    if blocking:
        console.print("\n[yellow]Not packed, and the demo needs it:[/]")
        for item in blocking:
            console.print(f"  - {item}")
        console.print(
            "Run `make city CITY=mumbai`, `make bundle BUNDLE=MUM-2019-07-02` and "
            "`make bake BUNDLE=MUM-2019-07-02`, then pack again."
        )
        raise SystemExit(1)


def default_video_path() -> Path:
    """Where a take goes by default: beside the repository, never inside it (P10.7)."""
    stamp = time.strftime("%Y%m%d-%H%M")
    return repo_root().parent / "varuna-demo-video" / f"varuna-demo-{stamp}.webm"


def demo_video(
    out: Annotated[
        Path | None,
        typer.Option(
            help="Where to write the take; default ../varuna-demo-video/ beside the repo."
        ),
    ] = None,
) -> None:
    """Record the eight-minute demo as one 1080p take, paced to SPEC.md 15's clock (P10.7).

    Runs `tests/e2e/demo-video.spec.ts` through the root Playwright config, which starts the API
    and the console or reuses a running `make demo`. The file is written outside the repository:
    a take is a fallback for the stage, copied to the demo laptops and USB sticks, not source.
    """
    root = repo_root()
    target = (out or default_video_path()).resolve()
    if target.is_relative_to(root):
        console.print(
            f"[red]{target} is inside the repository.[/red] A take is about 100 MB of video and "
            "does not belong in git; pass --out somewhere outside it."
        )
        raise typer.Exit(code=2)
    argv = procs.pnpm(
        "exec",
        "playwright",
        "test",
        "--config",
        str(root / "playwright.config.ts"),
        # A filter, not a path: Playwright reads its arguments as regular expressions, and a
        # Windows path is all backslashes.
        "demo-video.spec.ts",
    )
    env = {"VARUNA_DEMO_VIDEO": "1", "VARUNA_DEMO_VIDEO_OUT": str(target)}
    result = procs.run(argv, cwd=command_app_dir(), env=env)
    if target.is_file():
        console.print(f"[green]Demo take written to {target}[/green]")
    raise typer.Exit(code=result.returncode)


def _services(
    *,
    config: RuntimeConfig,
    env: dict[str, str],
    api_port: int,
    ui_port: int,
    reload: bool,
    no_api: bool,
    no_ui: bool,
) -> list[Service]:
    root = repo_root()
    services: list[Service] = []
    if not no_api:
        argv = procs.python(
            "-m",
            "uvicorn",
            "varuna_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        )
        if reload:
            argv += ["--reload", "--reload-dir", "services", "--reload-dir", "packages"]
        services.append(Service("api", argv, cwd=root, env=env, color="cyan"))
    if not no_ui:
        ui_env = {
            **env,
            "NEXT_PUBLIC_API_URL": env.get("NEXT_PUBLIC_API_URL", f"http://localhost:{api_port}"),
            "NEXT_PUBLIC_WS_URL": env.get(
                "NEXT_PUBLIC_WS_URL", f"ws://localhost:{api_port}/v1/live"
            ),
        }
        services.append(
            Service(
                "ui",
                procs.pnpm("--filter", COMMAND_APP, "dev", "--port", str(ui_port)),
                cwd=root,
                env=ui_env,
                color="magenta",
            )
        )
    return services


def dev(
    api_port: Annotated[
        int | None, typer.Option("--api-port", help="API port (default from .env).")
    ] = None,
    ui_port: Annotated[
        int | None, typer.Option("--ui-port", help="UI port (default from .env).")
    ] = None,
    no_api: Annotated[bool, typer.Option("--no-api", help="Only start the UI.")] = False,
    no_ui: Annotated[bool, typer.Option("--no-ui", help="Only start the API.")] = False,
    no_reload: Annotated[
        bool, typer.Option("--no-reload", help="Disable uvicorn auto-reload.")
    ] = False,
) -> None:
    """Start the API (:8000) and the console (:3000) side by side with the replay paused."""
    config = load_config()
    api_port = api_port or config.api_port
    ui_port = ui_port or config.ui_port
    env = {
        "VARUNA_MODE": os.environ.get("VARUNA_MODE", "replay"),
        "VARUNA_REPLAY_AUTOPLAY": "0",
        "VARUNA_API_PORT": str(api_port),
        "VARUNA_UI_PORT": str(ui_port),
        COMPUTE_LIVE_ENV: compute_live_flag(),
    }
    services = _services(
        config=config,
        env=env,
        api_port=api_port,
        ui_port=ui_port,
        reload=not no_reload,
        no_api=no_api,
        no_ui=no_ui,
    )
    console.print(
        f"Starting VARUNA dev: API http://localhost:{api_port}/docs, console http://localhost:{ui_port}/console "
        f"(replay paused, {_compute_live_words(env[COMPUTE_LIVE_ENV])}). Ctrl+C stops both."
    )
    raise typer.Exit(code=procs.run_concurrently(services))


def demo(
    bundle: Annotated[
        str | None, typer.Option("--bundle", help="Replay bundle (default from .env).")
    ] = None,
    speed: Annotated[float | None, typer.Option("--speed", help="Replay speed factor.")] = None,
    api_port: Annotated[int | None, typer.Option("--api-port")] = None,
    ui_port: Annotated[int | None, typer.Option("--ui-port")] = None,
) -> None:
    """Full demo: API + console replaying the baked runs of the default bundle at 30x."""
    config = load_config()
    bundle = bundle or config.bundle
    speed = speed or config.replay_speed
    api_port = api_port or config.api_port
    ui_port = ui_port or config.ui_port

    runs = baked_runs(bundle)
    env = {
        "VARUNA_MODE": "replay",
        "VARUNA_BUNDLE": bundle,
        "VARUNA_REPLAY_SPEED": f"{speed:g}",
        "VARUNA_API_PORT": str(api_port),
        "VARUNA_UI_PORT": str(ui_port),
        COMPUTE_LIVE_ENV: compute_live_flag(),
    }
    if runs:
        console.print(
            f"[green]{len(runs)} baked runs found for {bundle}.[/green] "
            f"Replaying at {speed:g}x from data/runs/."
        )
        # Autoplay flags are read by the replay clock from Phase 5 onwards (P2.7, P5.6).
        env["VARUNA_REPLAY_AUTOPLAY"] = "1"
        env["VARUNA_REPLAY_BAKED"] = "1"
    else:
        console.print(f"[yellow]{NO_BUNDLE_MESSAGE.format(bundle=bundle)}[/yellow]")
        env["VARUNA_REPLAY_AUTOPLAY"] = "0"

    services = _services(
        config=config,
        env=env,
        api_port=api_port,
        ui_port=ui_port,
        reload=False,
        no_api=False,
        no_ui=False,
    )
    console.print(
        f"Console: http://localhost:{ui_port}/console?bundle={bundle}  API: http://localhost:{api_port}/docs"
        f"  ({_compute_live_words(env[COMPUTE_LIVE_ENV])})"
    )
    raise typer.Exit(code=procs.run_concurrently(services))


def test(
    python_only: Annotated[
        bool, typer.Option("--python-only", help="Skip the web checks.")
    ] = False,
    web_only: Annotated[bool, typer.Option("--web-only", help="Skip pytest.")] = False,
    no_cov: Annotated[bool, typer.Option("--no-cov", help="Run pytest without coverage.")] = False,
    fail_under: Annotated[
        float | None,
        typer.Option("--fail-under", help="Coverage threshold in percent (SPEC.md 14: 70)."),
    ] = None,
) -> None:
    """Run pnpm lint, typecheck, test, lint:design, then pytest with coverage; summary table."""
    root = repo_root()
    steps: list[tuple[str, list[str], Path | None]] = []
    if not python_only:
        steps += [
            ("pnpm lint", procs.pnpm("lint"), root),
            ("pnpm typecheck", procs.pnpm("typecheck"), root),
            ("pnpm test", procs.pnpm("test"), root),
            ("pnpm lint:design", procs.pnpm("lint:design"), root),
        ]
    if not web_only:
        # Explicit paths: pyproject's testpaths plus the task runner's own tests under tools/.
        pytest_argv = procs.python("-m", "pytest", *PYTEST_PATHS)
        if not no_cov:
            pytest_argv += ["--cov", "--cov-report=term-missing:skip-covered"]
            if fail_under is not None:
                pytest_argv.append(f"--cov-fail-under={fail_under:g}")
        steps.append(("pytest", pytest_argv, root))
    results = _run_steps("Test summary", steps)
    _exit_from(results)


def e2e(
    ctx: typer.Context,
) -> None:
    """Run the Playwright demo test with the root playwright.config.ts (it starts API and UI)."""
    root = repo_root()
    argv = procs.pnpm(
        "exec",
        "playwright",
        "test",
        "--config",
        str(root / "playwright.config.ts"),
        *ctx.args,
    )
    result = procs.run(argv, cwd=command_app_dir())
    raise typer.Exit(code=result.returncode)


def openapi() -> None:
    """Write the OpenAPI document (apps/command/openapi.json) from the FastAPI app."""
    result = procs.run(procs.python("-m", "varuna_api.cli", "openapi"), cwd=repo_root())
    raise typer.Exit(code=result.returncode)


def typegen() -> None:
    """Export OpenAPI, then generate apps/command/lib/api/types.ts with openapi-typescript."""
    root = repo_root()
    results = _run_steps(
        "Typegen",
        [
            ("openapi export", procs.python("-m", "varuna_api.cli", "openapi"), root),
            ("openapi-typescript", procs.pnpm("--filter", COMMAND_APP, "typegen"), root),
        ],
        stop_on_failure=True,
    )
    _exit_from(results)


def lint(
    python_only: Annotated[bool, typer.Option("--python-only")] = False,
    web_only: Annotated[bool, typer.Option("--web-only")] = False,
) -> None:
    """ESLint + design lint for the app, Ruff for Python."""
    root = repo_root()
    steps: list[tuple[str, list[str], Path | None]] = []
    if not python_only:
        steps += [
            ("pnpm lint", procs.pnpm("lint"), root),
            ("pnpm lint:design", procs.pnpm("lint:design"), root),
        ]
    if not web_only:
        steps += [
            ("ruff check", procs.python("-m", "ruff", "check", "."), root),
            ("ruff format --check", procs.python("-m", "ruff", "format", "--check", "."), root),
        ]
    _exit_from(_run_steps("Lint", steps))


def typecheck(
    python_only: Annotated[bool, typer.Option("--python-only")] = False,
    web_only: Annotated[bool, typer.Option("--web-only")] = False,
) -> None:
    """tsc --noEmit for the app, mypy (basic) for the task runner and schemas."""
    root = repo_root()
    steps: list[tuple[str, list[str], Path | None]] = []
    if not python_only:
        steps.append(("pnpm typecheck", procs.pnpm("typecheck"), root))
    if not web_only:
        steps.append(
            (
                "mypy",
                procs.python("-m", "mypy", "tools/varuna_cli", "packages/schemas/varuna_schemas"),
                root,
            )
        )
    _exit_from(_run_steps("Typecheck", steps))


def format_code(
    check: Annotated[bool, typer.Option("--check", help="Report instead of rewriting.")] = False,
) -> None:
    """Prettier for the app, Ruff format (and import sorting) for Python."""
    root = repo_root()
    prettier_script = "format:check" if check else "format"
    ruff_format = procs.python("-m", "ruff", "format", ".")
    ruff_fix = procs.python("-m", "ruff", "check", ".", "--select", "I", "--fix")
    if check:
        ruff_format.append("--check")
        ruff_fix = procs.python("-m", "ruff", "check", ".", "--select", "I")
    steps = [
        ("prettier", procs.pnpm("--filter", COMMAND_APP, prettier_script), root),
        ("ruff format", ruff_format, root),
        ("ruff isort", ruff_fix, root),
    ]
    _exit_from(_run_steps("Format", steps))


CLEAN_DIRS: tuple[str, ...] = (
    "apps/command/.next",
    "apps/command/out",
    "apps/command/coverage",
    "apps/command/dist",
    "packages/tokens/dist",
    ".turbo",
    "playwright-report",
    "test-results",
    "htmlcov",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "node_modules/.cache",
)
CLEAN_FILES: tuple[str, ...] = (".coverage", "coverage.xml")
NEVER_CLEAN: tuple[str, ...] = ("data", "city", "bundles", "node_modules", ".venv", ".git")


def clean(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List what would be removed.")] = False,
) -> None:
    """Remove build outputs and caches (.next, dist, .turbo, pytest/ruff caches). Never data."""
    root = repo_root()
    removed = 0
    for rel in CLEAN_DIRS:
        path = root / rel
        if path.is_dir():
            console.print(f"remove {rel}/", style="dim")
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
            removed += 1
    for rel in CLEAN_FILES:
        path = root / rel
        if path.is_file():
            console.print(f"remove {rel}", style="dim")
            if not dry_run:
                path.unlink(missing_ok=True)
            removed += 1
    for pycache in _find_pycache(root):
        console.print(f"remove {pycache.relative_to(root).as_posix()}/", style="dim")
        if not dry_run:
            shutil.rmtree(pycache, ignore_errors=True)
        removed += 1
    verb = "would remove" if dry_run else "removed"
    console.print(f"clean: {verb} {removed} item(s); data/, city/ and bundles/ untouched.")


def _find_pycache(root: Path) -> list[Path]:
    found: list[Path] = []
    for base in ("tools", "packages", "services", "tests"):
        folder = root / base
        if not folder.is_dir():
            continue
        for path in folder.rglob("__pycache__"):
            if path.is_dir() and not any(
                part in NEVER_CLEAN for part in path.relative_to(root).parts
            ):
                found.append(path)
    return found


_TARGET_LINE = re.compile(r"^(?P<target>[A-Za-z0-9_.-]+):[^#=]*##\s*(?P<doc>.+)$")


def help_targets() -> None:
    """List every make target with its one-line description (parsed from the Makefile)."""
    makefile = repo_root() / "Makefile"
    if not makefile.is_file():
        console.print("[red]Makefile not found at the repository root.[/red]")
        raise typer.Exit(code=1)
    table = Table(
        title="VARUNA make targets (each is also 'uv run varuna <target>')", title_justify="left"
    )
    table.add_column("Target", style="bold")
    table.add_column("Does")
    count = 0
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = _TARGET_LINE.match(line)
        if match:
            table.add_row(match["target"], match["doc"].strip())
            count += 1
    console.print(table)
    console.print(
        'Variables: CITY=mumbai  BUNDLE=MUM-2019-07-02  ARGS="--flag"  '
        "(example: make city CITY=chennai ARGS=--cache-only)",
        style="dim",
    )
    if count == 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

TASKS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("setup", setup),
    ("doctor", doctor),
    ("city", city),
    ("bundle", bundle),
    ("bake", bake),
    ("train", train),
    ("dev", dev),
    ("demo", demo),
    ("test", test),
    ("e2e", e2e),
    ("pack", pack),
    ("demo-video", demo_video),
    ("typegen", typegen),
    ("openapi", openapi),
    ("lint", lint),
    ("typecheck", typecheck),
    ("format", format_code),
    ("clean", clean),
    ("help", help_targets),
)


def register(app: typer.Typer, *, skip: Iterable[str] = ()) -> None:
    """Attach every task command to ``app``, except the names in ``skip``.

    ``main.build_app`` passes the engine sub-apps it found so that a placeholder task (for
    example ``city``) yields to the real engine group of the same name.
    """
    skipped = set(skip)
    for name, fn in TASKS:
        if name in skipped:
            continue
        if name == "e2e":
            app.command(
                name,
                context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
            )(fn)
        else:
            app.command(name)(fn)


__all__ = [
    "ENGINES",
    "NO_BUNDLE_MESSAGE",
    "PLACEHOLDER_PHASES",
    "TASKS",
    "RuntimeConfig",
    "baked_runs",
    "engine_status",
    "list_bundles",
    "list_runs",
    "load_config",
    "not_implemented",
    "register",
]
