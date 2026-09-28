"""Finding the run an endpoint means when the caller did not name one.

**Every "latest run" is per city.** Runs from every city share `data/runs/`, their ids sort
chronologically, and `CHN-` sorts after `MUM-` for the same instant - so the moment Chennai was
onboarded, every endpoint that defaulted to "the newest run" began answering a Mumbai console with
Chennai water: the depth raster, the segment forecast, the drain health, the route. Nothing looked
broken, which is what made it worth a shared helper rather than four separate fixes.

**An unknown city is refused, never widened** (task D-09). :func:`run_prefix` has no code for a
name like ``atlantis`` and returns an empty prefix, and an empty prefix filters nothing - so
``?city=atlantis`` used to be answered with whichever city's run sorted newest, which is the very
leak the per-city filter exists to close. :func:`latest_run_for` now finds no run for such a city,
and :func:`resolve_city` turns the name into the section 12 envelope before anything is read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from varuna_schemas.constants import CITY_CODES, CITY_SLUGS
from varuna_schemas.models.run import RunIdError, city_code, parse_run_id
from varuna_schemas.paths import bundles_dir, runs_dir
from varuna_schemas.settings import get_settings

from varuna_api.state import api_error

__all__ = [
    "bake_hint",
    "bundle_for",
    "city_of_run",
    "known_cities",
    "latest_run_for",
    "no_run_hint",
    "onboarded_run_for",
    "resolve_city",
    "run_prefix",
]


def run_prefix(city: str | None) -> str:
    """The run-id prefix for a city, e.g. ``"MUM-"``. Empty when the city is unknown."""
    name = city or get_settings().varuna_city
    try:
        return f"{city_code(name)}-"
    except RunIdError:
        return ""


def known_cities() -> list[str]:
    """The city slugs the run-id scheme has a code for, alphabetically."""
    return sorted(CITY_CODES)


def _slug(name: str) -> str | None:
    """``"mumbai"``, ``"Mumbai"`` or ``"MUM"`` -> ``"mumbai"``; None for a city with no run code.

    A three-letter string is accepted only when it is one of the codes in ``CITY_CODES``.
    :func:`~varuna_schemas.models.run.city_code` passes any three letters through, which is right
    for composing an id and wrong here: ``?city=xyz`` names no city VARUNA has, whatever
    ``XYZ-`` would filter to.
    """
    key = name.strip()
    if key.lower() in CITY_CODES:
        return key.lower()
    return CITY_SLUGS.get(key.upper())


def _either(names: list[str]) -> str:
    """``["chennai", "mumbai"]`` -> ``"chennai or mumbai"``."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def resolve_city(city: str | None) -> str:
    """The city slug a request means: the one it named, or the configured one when it named none.

    A city the run-id scheme has no code for is refused as 404 ``unknown_city`` in the section 12
    envelope, naming the cities that do exist. Serving it anything would mean serving it another
    city's run, because there is no prefix to filter by. A blank ``?city=`` counts as omitted,
    as it always has.

    The refusal blames whichever of the two is wrong, and it tells them apart by comparing with
    the configured city rather than by whether ``city`` is empty: some callers (the ops routes)
    substitute the configured city before calling, so an empty argument is not the only way the
    setting arrives here. A name equal to ``VARUNA_CITY`` is the setting's fault, and the fix is
    the setting.
    """
    configured = (get_settings().varuna_city or "").strip()
    asked = (city or "").strip()
    name = asked or configured
    slug = _slug(name)
    if slug is not None:
        return slug
    known = _either(known_cities())
    if asked and asked.lower() != configured.lower():
        message = (
            f"VARUNA has no city called {asked!r}, so there is no run to serve for it. Ask for "
            f"{known}. A new city needs a run-id code in CITY_CODES "
            "(packages/schemas/varuna_schemas/constants.py) before it can be baked."
        )
    else:
        message = (
            f"VARUNA_CITY is set to {name!r}, which has no run-id code. Set it to {known}, or "
            "pass city= with one of those."
        )
    raise api_error(404, "unknown_city", message)


def city_of_run(run_id: str) -> str | None:
    """The city slug a run id belongs to, or None for an id that does not parse or has no slug."""
    try:
        code = parse_run_id(run_id).city_code
    except RunIdError:
        return None
    return CITY_SLUGS.get(code)


def bundle_for(city: str) -> str | None:
    """The replay bundle that bakes runs for ``city``, or None when ``bundles/`` holds none.

    Read from each manifest's ``city`` rather than guessed from its id. The configured bundle is
    preferred when it belongs to this city, so a Mumbai hint names the demo's replay; otherwise
    the first of the city's bundles by id.
    """
    root = bundles_dir()
    found: list[str] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    except OSError:
        return None
    for folder in folders:
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and str(manifest.get("city", "")).lower() == city:
            found.append(str(manifest.get("id") or folder.name))
    if not found:
        return None
    configured = get_settings().varuna_bundle
    return configured if configured in found else found[0]


def bake_hint(city: str) -> str:
    """The command that gives ``city`` a baked run, as a sentence (SPEC.md 6.8).

    Names that city's own bundle: a Chennai console told to bake ``MUM-2019-07-02`` would only
    get more Mumbai runs.
    """
    bundle = bundle_for(city)
    if bundle is not None:
        return f"Run `make bake BUNDLE={bundle}`, or press Compute live on the replay panel."
    return (
        f"bundles/ holds no {city} bundle to bake yet. Build one with "
        "`make bundle BUNDLE=<id>`, then run `make bake BUNDLE=<id>`."
    )


def no_run_hint(city: str, carries: str) -> str:
    """Why ``city`` has no run carrying ``carries``, and the command that makes one."""
    return f"No baked run for {city} carries {carries} yet. {bake_hint(city)}"


def onboarded_run_for(
    city: str | None,
    requires: Callable[[Path], bool] | None = None,
) -> Path | None:
    """The run an onboarded city opens on: its last good onboarding build's first forecast.

    Only for a city other than the configured one. The configured city is the replay's city, and
    its newest bake is its default, as it always was - Mumbai is never onboarded through the
    wizard, and if it were, the design storm's run would not be the right thing to open the
    2 July replay on.

    For an onboarded city, name order is the wrong authority. Run ids sort by cycle time, and
    Chennai's superseded 2026-09-10 "peak frame" run (609 mm of rain from a 150 mm design storm)
    sorted after the wizard's own run, so the wizard's map, its finish card and the Chennai
    console all opened on it. The onboarding record (`varuna_api.onboard`) names the run the
    build made, and that is the one to open, as long as it is still on disk, belongs to this
    city and carries what the caller ``requires``. Otherwise None, and the caller falls back to
    the newest run.
    """
    slug = _slug(city or get_settings().varuna_city or "")
    configured = _slug(get_settings().varuna_city or "")
    if slug is None or slug == configured:
        return None
    from varuna_api.onboard import onboard_run_id

    run_id = onboard_run_id(slug)
    prefix = run_prefix(slug)
    if run_id is None or not prefix or not run_id.startswith(prefix):
        return None
    path = runs_dir() / run_id
    if not path.is_dir():
        return None
    if requires is not None and not requires(path):
        return None
    return path


def latest_run_for(
    city: str | None = None,
    requires: Callable[[Path], bool] | None = None,
) -> Path | None:
    """The default run directory for a city, optionally one satisfying ``requires``.

    For an onboarded city other than the configured one, the first forecast of its last good
    onboarding build (:func:`onboarded_run_for`); otherwise, and whenever that run is gone or
    lacks what ``requires`` asks for, the newest run by id.

    Returns None rather than raising: "no run yet" is an ordinary state on a cold console, and the
    callers each have their own message for it (SPEC.md 6.8). A city with no run-id code has no
    runs by definition, so it gets None too - never the newest run of every city.
    """
    root = runs_dir()
    if not root.is_dir():
        return None
    prefix = run_prefix(city)
    if not prefix:
        return None
    onboarded = onboarded_run_for(city, requires)
    if onboarded is not None:
        return onboarded
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or path.name.startswith("."):
            continue
        if not path.name.startswith(prefix):
            continue
        if requires is not None and not requires(path):
            continue
        return path
    return None
