"""``varuna`` CLI: every ``make`` target is also ``uv run varuna <task>`` (SPEC.md 4.3).

Two kinds of commands hang off the root app:

* **Tasks** from :mod:`varuna_cli.tasks` (setup, doctor, dev, demo, test, ...). These exist
  from Phase 0 and never depend on an engine being finished.
* **Engine sub-apps**: each engine package exposes a Typer ``app`` in ``varuna_<name>.cli``
  from the phase that builds it (``varuna api serve``, ``varuna sky nowcast`` ...). They are
  attached with :func:`register_optional`; an engine whose CLI is missing or fails to import
  is simply absent from ``varuna --help``. ``varuna doctor`` lists which ones are available.

When an engine sub-app shares a name with a placeholder task (``city``), the engine wins:
the placeholder only exists to print the phase gate until the engine arrives. A task that
becomes a whole command *group* (``bundle``) is listed in :data:`GROUP_SUB_APPS`.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field

import typer

from varuna_cli import tasks

__version__ = "0.1.0"

app = typer.Typer(
    help="VARUNA - street-level urban flood nowcasting digital twin. Every street. Three hours early.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


@dataclass(slots=True)
class OptionalRegistration:
    """Outcome of one optional registration, kept for ``doctor`` and tests."""

    name: str
    module: str
    attr: str
    registered: bool
    reason: str | None = None


@dataclass(slots=True)
class Registry:
    """What the root app ended up with; tests inspect it instead of parsing ``--help``."""

    engines: dict[str, OptionalRegistration] = field(default_factory=dict)

    @property
    def available(self) -> list[str]:
        return [name for name, item in self.engines.items() if item.registered]

    @property
    def missing(self) -> list[str]:
        return [name for name, item in self.engines.items() if not item.registered]


registry = Registry()


def load_sub_app(module: str, attr: str = "app") -> tuple[typer.Typer | None, str | None]:
    """Import ``<module>.<attr>`` and return ``(typer_app, None)`` or ``(None, reason)``.

    Every failure is caught: a missing package, a missing ``cli`` module, an exception raised
    while importing the engine, or an attribute that is not a :class:`typer.Typer`.
    """
    try:
        engine_module = importlib.import_module(module)
    except Exception as exc:  # a broken engine must never break the task runner
        return None, f"{type(exc).__name__}: {exc}"
    sub_app = getattr(engine_module, attr, None)
    if not isinstance(sub_app, typer.Typer):
        return None, f"{module}.{attr} is not a typer.Typer"
    return sub_app, None


def register_optional(
    target: typer.Typer,
    *,
    module: str,
    attr: str = "app",
    name: str,
    help_text: str | None = None,
) -> bool:
    """Attach ``<module>.<attr>`` as the ``name`` sub-command when it is importable.

    Import failures are silent (the sub-command is simply not present); the reason is
    recorded in :data:`registry` so ``varuna doctor`` can show it. Returns ``True`` when the
    sub-app was attached.
    """
    sub_app, reason = load_sub_app(module, attr)
    if sub_app is None:
        registry.engines[name] = OptionalRegistration(name, module, attr, False, reason)
        return False
    target.add_typer(sub_app, name=name, help=help_text or f"{name} engine commands.")
    registry.engines[name] = OptionalRegistration(name, module, attr, True)
    return True


GROUP_SUB_APPS: tuple[tuple[str, str, str, str], ...] = (
    (
        "bundle",
        "varuna_replay.cli",
        "bundle_app",
        "Replay bundles: build, validate, list and inspect.",
    ),
)
"""Task names an engine turns into a command *group* rather than a single command.

``varuna bundle`` is the placeholder task until the replay service ships ``bundle_app``, and
then it becomes the group holding ``build``, ``validate``, ``list``, ``show`` and ``design``
(SPEC.md P2.1-P2.8). The group's own callback accepts ``--bundle <id>``, which is how
``make bundle BUNDLE=<id>`` reaches the build.
"""


def build_app(target: typer.Typer | None = None) -> typer.Typer:
    """Register the tasks and every importable engine sub-app on ``target`` (default :data:`app`).

    Engines and group sub-apps are probed first so that a placeholder task with the same name
    (``city``, ``bundle``) is skipped in favour of the real group.
    """
    target = target if target is not None else app
    probed: dict[str, typer.Typer] = {}
    for engine in tasks.ENGINES:
        module = f"varuna_{engine}.cli"
        sub_app, reason = load_sub_app(module)
        if sub_app is None:
            registry.engines[engine] = OptionalRegistration(engine, module, "app", False, reason)
        else:
            probed[engine] = sub_app
    groups: dict[str, tuple[str, str, str]] = {}
    for name, module, attr, help_text in GROUP_SUB_APPS:
        sub_app, reason = load_sub_app(module, attr)
        if sub_app is None:
            registry.engines[name] = OptionalRegistration(name, module, attr, False, reason)
        else:
            groups[name] = (module, attr, help_text)
    tasks.register(target, skip=set(probed) | set(groups))
    for engine in probed:
        register_optional(target, module=f"varuna_{engine}.cli", name=engine)
    for name, (module, attr, help_text) in groups.items():
        register_optional(target, module=module, attr=attr, name=name, help_text=help_text)
    # `varuna whatif prewarm`: full-city Twin what-ifs computed ahead of the demo. It lives here
    # rather than in an engine because it drives the API's own scenario code and cache.
    register_optional(
        target,
        module="varuna_cli.whatif",
        name="whatif",
        help_text="What-if scenarios on the full-city Twin: compute them ahead of the demo.",
    )
    # `varuna verify --event <id>` (Appendix B): the scores /v1/verification serves, written to
    # the landing page's committed copy. It lives here because `varuna_verify` has no CLI of its
    # own; if one ever ships, the engine's group wins and this one is not attached.
    if "verify" not in probed:
        verify_app, _reason = load_sub_app("varuna_cli.verify")
        if verify_app is not None:
            target.add_typer(
                verify_app,
                name="verify",
                help="Score an event from its artifacts and write the landing page's copy.",
            )
    return target


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"varuna {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print the CLI version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """VARUNA task runner. Run 'varuna help' for the make-target table."""


build_app(app)


def main() -> None:
    """Console-script entry point (``uv run varuna``)."""
    app()


if __name__ == "__main__":
    main()
