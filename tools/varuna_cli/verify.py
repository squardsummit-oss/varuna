"""``varuna verify``: score an event from its artifacts and write the landing page's copy.

SPEC.md Appendix B lists ``uv run varuna verify --event MUM-2019-07-02`` and 11.12 says a
committed copy of the scores lives in ``apps/command/public/verification.json`` so the landing
page, the top bar's verification chip and ``/verify`` have real numbers when the API is
unreachable. Until this command existed nothing wrote that copy: it was pasted from an API
response and went stale (scored over seven runs where eight exist, and quoting an ensemble size
the runs no longer have).

What it writes is exactly what the API serves, from the same functions, plus one key:

* everything ``GET /v1/verification`` returns - :func:`varuna_verify.event.sweep`, unchanged, so
  every field the landing page, the chip and the end-to-end test read is there;
* ``rain_skill`` - the headline of :func:`varuna_verify.rain_event.event_rain_skill` (the useful
  skill horizon, the CSI of the ensemble mean, median and persistence at 20 and 40 mm/h at every
  lead, the cycles it pooled), not its ~205 KB payload, which only ``/verify`` reads.

It also writes that full payload, for the deployed site. A deployment seeds ``demo/runs``, which
omit the runs' rain cubes (``rain/cube.zarr`` and ``rain/quantiles.zarr``, about 20 MB a cycle), so
its ``GET /v1/verification/rain-skill`` has nothing to score. The payload scored here, with a record
of which runs it scored (ids, cycle times, a digest of each ``run.json``), the scorer's version and
the truth field, goes to ``demo/verification/<event>.rain-skill.json`` - gzipped past 200 KB -
and the API serves it only while it holds exactly those runs
(:func:`varuna_verify.rain_event.shipped_rain_skill`, ``varuna_api.routers.verify``).

**Deterministic (rule 8).** Keys are sorted, floats print as Python's shortest round-trip form
with exponents written the way Prettier writes them, the file ends in one ``\\n`` and uses LF on
every platform, and nothing in it is a wall-clock time: the only times are the runs' own window,
the pins' and the truth field's. The layout is the one Prettier gives JSON at a print width of
100, so the pre-commit hook that runs ``prettier --check`` over ``apps/command`` leaves it alone.

``--check`` scores the event again and exits 1 when the committed copy, or the shipped rain copy,
differs from what would be written, so a stale copy is a failed check rather than a number on a
slide. A shipped rain copy is compared by its JSON, so a zlib that packs differently is not stale.
"""

from __future__ import annotations

import json
import math
import numbers
from pathlib import Path
from typing import Annotated, Any

import typer

__all__ = [
    "COMMITTED_COPY",
    "DEFAULT_EVENT",
    "PRINT_WIDTH",
    "SHIPPED_RAIN_DIR",
    "app",
    "landing_copy",
    "rain_headline",
    "render",
    "scored",
]

DEFAULT_EVENT = "MUM-2019-07-02"
"""The event the replay demo scores itself on, and the only one the landing copy holds."""

COMMITTED_COPY = Path("apps") / "command" / "public" / "verification.json"
"""Where the landing copy lives, relative to the repository root."""

SHIPPED_RAIN_DIR = Path("demo") / "verification"
"""Where the full rain-skill payload ships for a deployment, relative to the repository root."""

PRINT_WIDTH = 100
"""Prettier's ``printWidth`` in ``.prettierrc``; the renderer lays lines out against it."""

RAIN_THRESHOLDS_MM_H: tuple[float, ...] = (20.0, 40.0)
"""The rain rates the per-lead CSI summary keeps: SPEC.md 11.12's two."""

RAIN_SCOPE = "aoi"
"""The scope the headline is read from: the Sky pixels over the city's own grid."""

INDENT = "  "

app = typer.Typer(
    help="Score an event from its artifacts and write the landing page's committed copy.",
    add_completion=False,
)


# ============================================================================ rendering
def _number(value: float) -> str:
    """Python's shortest round-trip float, with the exponent as Prettier normalises it.

    ``float.__repr__`` rather than ``repr``: under numpy 2 ``repr(np.float64(0.5))`` is
    ``"np.float64(0.5)"``, and ``np.float64`` is a ``float`` subclass the scorers can return.
    """
    if not math.isfinite(value):
        msg = f"{value!r} is not a JSON number; a score that cannot be computed is null"
        raise ValueError(msg)
    text = float.__repr__(float(value))
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    sign = "-" if exponent.startswith("-") else ""
    digits = exponent.lstrip("+-").lstrip("0") or "0"
    return f"{mantissa}e{sign}{digits}"


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        return _number(float(value))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    msg = f"{type(value).__name__} is not a JSON scalar"
    raise TypeError(msg)


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, dict | list | tuple)


def _items(value: dict[str, Any]) -> list[tuple[str, Any]]:
    for key in value:
        if not isinstance(key, str):
            msg = f"JSON object keys are strings; got {key!r}"
            raise TypeError(msg)
    return sorted(value.items())


def _forced_break(value: list[Any]) -> bool:
    """Prettier breaks an array of two or more objects, or arrays, that each hold two or more."""
    if len(value) < 2:
        return False
    if all(isinstance(v, dict) and len(v) > 1 for v in value):
        return True
    return all(isinstance(v, list) and len(v) > 1 for v in value)


def _inline(value: Any, *, in_list: bool) -> str | None:
    """``value`` on one line, or ``None`` when it has to break whatever the width.

    An object is written on one line only as an array element whose values are all scalars (a
    table row). Everywhere else it is expanded, which Prettier preserves: it keeps an object
    expanded when the source has a newline after its opening brace.
    """
    if isinstance(value, dict):
        if not value:
            return "{}"
        if not in_list or not all(_is_scalar(v) for v in value.values()):
            return None
        body = ", ".join(f"{_scalar(k)}: {_scalar(v)}" for k, v in _items(value))
        return "{ " + body + " }"
    if isinstance(value, list | tuple):
        items = list(value)
        if not items:
            return "[]"
        if _forced_break(items):
            return None
        parts = [_inline(v, in_list=True) for v in items]
        if any(p is None for p in parts):
            return None
        return "[" + ", ".join(p for p in parts if p is not None) + "]"
    return _scalar(value)


def _lines(value: Any, depth: int, head: str, tail: str, *, in_list: bool) -> list[str]:
    """The lines ``value`` prints as: the first starts with ``head``, the last ends with ``tail``.

    ``tail`` is the comma that follows a value inside its parent, which Prettier counts when it
    decides whether a group fits on the line.
    """
    pad = INDENT * depth
    flat = _inline(value, in_list=in_list)
    if flat is not None and (
        _is_scalar(value) or len(pad) + len(head) + len(flat) + len(tail) <= PRINT_WIDTH
    ):
        return [pad + head + flat + tail]
    if isinstance(value, dict):
        entries = _items(value)
        out = [pad + head + "{"]
        for i, (key, child) in enumerate(entries):
            comma = "," if i < len(entries) - 1 else ""
            out += _lines(child, depth + 1, f"{_scalar(key)}: ", comma, in_list=False)
        out.append(pad + "}" + tail)
        return out
    items = list(value)
    out = [pad + head + "["]
    numeric = (
        all(isinstance(v, numbers.Real) and not isinstance(v, bool) for v in items)
        and len(items) > 1
    )
    if numeric:
        # Prettier fills an array of plain numbers: as many to a line as fit.
        inner = INDENT * (depth + 1)
        line = ""
        for i, child in enumerate(items):
            text = _scalar(child) + ("," if i < len(items) - 1 else "")
            if not line:
                line = inner + text
            elif len(line) + 1 + len(text) <= PRINT_WIDTH:
                line += " " + text
            else:
                out.append(line)
                line = inner + text
        out.append(line)
    else:
        for i, child in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            out += _lines(child, depth + 1, "", comma, in_list=True)
    out.append(pad + "]" + tail)
    return out


def render(body: Any) -> str:
    """``body`` as the committed file's text: sorted keys, Prettier's layout, one trailing LF."""
    return "\n".join(_lines(body, 0, "", "", in_list=False)) + "\n"


# ============================================================================ the payload
def rain_headline(payload: dict[str, Any]) -> dict[str, Any]:
    """What the landing copy keeps of :func:`varuna_verify.rain_event.event_rain_skill`.

    An event the scorer could not score keeps its reason, what is missing and the command that
    supplies it, exactly as served. A scored one keeps the headline horizon, the CSI of the
    ensemble mean, the ensemble median and persistence at 20 and 40 mm/h at every scored lead of
    the city scope with the cycles behind each, which runs were pooled and which were skipped.
    """
    if not payload.get("available"):
        return {
            key: payload.get(key)
            for key in ("available", "reason", "missing", "command", "version")
        }
    scope = (payload.get("by_scope") or {}).get(RAIN_SCOPE) or {}
    by_lead: dict[str, list[dict[str, Any]]] = {}
    for threshold in RAIN_THRESHOLDS_MM_H:
        key = f"{int(threshold)}"
        rows: list[dict[str, Any]] = []
        for row in scope.get("by_lead") or []:
            cell = (row.get("thresholds") or {}).get(key)
            if cell is None:
                continue
            rows.append(
                {
                    "lead_min": row["lead_min"],
                    "n_cycles": row["n_cycles"],
                    "mean": cell["mean"]["csi"],
                    "p50": cell["p50"]["csi"],
                    "persistence": cell["persistence"]["csi"],
                }
            )
        by_lead[key] = rows
    truth = payload.get("truth") or {}
    return {
        "available": True,
        "version": payload.get("version"),
        "label": payload.get("label"),
        "truth": {name: truth.get(name) for name in ("source", "note", "t0", "t1", "n_frames")},
        "scope": RAIN_SCOPE,
        "scope_label": scope.get("label"),
        "n_cycles": payload.get("n_cycles"),
        "run_ids": [cycle["run_id"] for cycle in payload.get("cycles") or []],
        "skipped_runs": payload.get("skipped_runs") or [],
        "headline_threshold_mm_h": payload.get("headline_threshold_mm_h"),
        "csi_floor": payload.get("csi_floor"),
        "horizon": payload.get("horizon"),
        "csi_by_lead": by_lead,
        "unavailable": payload.get("unavailable") or {},
    }


def scored(event: str = DEFAULT_EVENT) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(landing copy, full rain payload)``, scoring the rain once for both.

    Raises:
        FileNotFoundError: the event has no ground truth (``make bundle``).
    """
    from varuna_verify.event import sweep
    from varuna_verify.rain_event import event_rain_skill

    body = sweep(event)
    if "rain_skill" in body:  # pragma: no cover - a guard for the day the sweep grows the key
        msg = "the sweep now returns rain_skill itself; the landing copy would overwrite it"
        raise RuntimeError(msg)
    rain = event_rain_skill(event)
    return {**body, "rain_skill": rain_headline(rain)}, rain


def landing_copy(event: str = DEFAULT_EVENT) -> dict[str, Any]:
    """The sweep ``GET /v1/verification`` serves, plus the rain-skill headline under ``rain_skill``.

    Raises:
        FileNotFoundError: the event has no ground truth (``make bundle``).
    """
    return scored(event)[0]


def _shipped_rain_text(rain: dict[str, Any]) -> str:
    """The shipped rain copy's JSON for ``rain``.

    Raises:
        FileNotFoundError: a scored run has no ``run.json`` to record.
    """
    from varuna_verify.rain_event import shipped_rain_skill, shipped_text

    return shipped_text(shipped_rain_skill(rain))


def _check_rain_copy(event: str, text: str, folder: Path) -> str | None:
    """Why the shipped rain copy in ``folder`` differs from ``text``, or None when it matches."""
    from varuna_verify.rain_event import read_shipped_text, shipped_bytes, shipped_names

    plain, packed = shipped_names(event)
    gzipped, _ = shipped_bytes(text)
    expected, other = (packed, plain) if gzipped else (plain, packed)
    if (folder / other).is_file():
        return f"{folder / other} should not exist: the copy is {folder / expected}."
    if not (folder / expected).is_file():
        return f"{folder / expected} does not exist."
    if read_shipped_text(folder / expected) != text:
        return f"{folder / expected} is stale: it differs from the rain scores the runs give today."
    return None


def _repo_root() -> Path:
    from varuna_cli.tasks import repo_root

    return repo_root()


def _summary(body: dict[str, Any]) -> list[str]:
    scores = body.get("scores") or {}
    truth = body.get("ground_truth") or {}
    lines = [
        f"{body.get('event')}: {len(body.get('run_ids') or [])} runs scored against "
        f"{truth.get('n_in_window')} of {truth.get('n_pins')} sourced pins in the window, "
        f"at {body.get('headline_threshold_cm', body.get('threshold_cm')):g} cm.",
        f"  CSI {scores.get('csi')}, POD {scores.get('pod')}, FAR {scores.get('far')}, median lead "
        f"{scores.get('median_lead_min')} min ({scores.get('n_hits_early')} hits early, "
        f"{scores.get('n_hits_after')} after the pin).",
    ]
    rows = body.get("by_threshold") or {}
    for key in sorted(rows, key=float):
        s = rows[key].get("scores") or {}
        lines.append(
            f"  at {rows[key].get('threshold_cm'):g} cm: CSI {s.get('csi')}, POD {s.get('pod')}, "
            f"FAR {s.get('far')}, median lead {s.get('median_lead_min')} min"
        )
    rain = body.get("rain_skill") or {}
    if rain.get("available"):
        horizon = rain.get("horizon") or {}
        lines.append(
            f"  Rain skill over {rain.get('n_cycles')} cycles: useful horizon "
            f"{horizon.get('lead_min')} min at {horizon.get('threshold_mm_h'):g} mm/h "
            f"({horizon.get('status')})."
        )
    else:
        lines.append(f"  Rain skill not scored: {rain.get('reason')}")
    return lines


@app.callback(invoke_without_command=True)
def verify(
    event: Annotated[
        str, typer.Option("--event", help="Bundle id to score, e.g. MUM-2019-07-02.")
    ] = DEFAULT_EVENT,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Where to write the scores (default: the landing copy, "
            "apps/command/public/verification.json, which holds MUM-2019-07-02 only).",
        ),
    ] = None,
    rain_dir: Annotated[
        Path | None,
        typer.Option(
            "--rain-dir",
            help="Folder for the full rain-skill payload a deployment serves (default: "
            "demo/verification with the landing copy; not written with --out unless given).",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Write nothing; exit 1 when a file differs from what would be written.",
        ),
    ] = False,
) -> None:
    """Score EVENT from its baked runs and sourced pins, exactly as GET /v1/verification does."""
    if out is None:
        if event != DEFAULT_EVENT:
            typer.echo(
                f"The landing copy holds {DEFAULT_EVENT} only, and the landing page refuses any "
                f"other event. Pass --out <path> to score {event}.",
                err=True,
            )
            raise typer.Exit(code=2)
        target = _repo_root() / COMMITTED_COPY
        rain_folder: Path | None = rain_dir or _repo_root() / SHIPPED_RAIN_DIR
    else:
        target = out
        rain_folder = rain_dir

    try:
        body, rain = scored(event)
    except FileNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    if not body.get("run_ids"):
        typer.echo(
            f"No baked runs of {event} to score, so nothing was written. "
            f"Run make bake BUNDLE={event}, then score again.",
            err=True,
        )
        raise typer.Exit(code=1)

    text = render(body)
    rain_text: str | None = None
    if rain_folder is not None and rain.get("available"):
        try:
            rain_text = _shipped_rain_text(rain)
        except (FileNotFoundError, ValueError) as error:
            typer.echo(
                f"The rain skill was scored but cannot ship: {error}. Nothing was written.",
                err=True,
            )
            raise typer.Exit(code=1) from error

    if check:
        failed = False
        try:
            current = target.read_bytes().decode("utf-8")
        except FileNotFoundError:
            typer.echo(
                f"{target} does not exist. Run uv run varuna verify --event {event} to write it.",
                err=True,
            )
            raise typer.Exit(code=1) from None
        if current != text:
            typer.echo(
                f"{target} is stale: it differs from the scores the runs give today. "
                f"Run uv run varuna verify --event {event} to rewrite it.",
                err=True,
            )
            for line in _summary(body):
                typer.echo(line, err=True)
            failed = True
        else:
            typer.echo(f"{target} matches the scores the runs give today.")
        if rain_folder is not None and rain_text is not None:
            problem = _check_rain_copy(event, rain_text, rain_folder)
            if problem is None:
                typer.echo(f"The shipped rain skill in {rain_folder} matches the runs' rain today.")
            else:
                typer.echo(
                    f"{problem} Run uv run varuna verify --event {event} to rewrite it.", err=True
                )
                failed = True
        elif rain_folder is not None:
            typer.echo(
                f"Rain skill not scored here ({rain.get('reason')}), so the shipped copy in "
                f"{rain_folder} was not checked.",
                err=True,
            )
        if failed:
            raise typer.Exit(code=1)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text: text mode on Windows would write CRLF and the file would differ by
    # platform (rule 8).
    target.write_bytes(text.encode("utf-8"))
    typer.echo(f"Wrote {target} ({len(text.encode('utf-8')):,} bytes).")
    if rain_folder is not None and rain_text is not None:
        from varuna_verify.rain_event import shipped_rain_skill, write_shipped_rain_skill

        written = write_shipped_rain_skill(shipped_rain_skill(rain), rain_folder)
        typer.echo(
            f"Wrote {written} ({written.stat().st_size:,} bytes, "
            f"{len(rain_text.encode('utf-8')):,} as JSON), the rain skill a deployment serves."
        )
    elif rain_folder is not None:
        typer.echo(
            f"Rain skill not scored ({rain.get('reason')}), so {rain_folder} was left as it is."
        )
    for line in _summary(body):
        typer.echo(line)
