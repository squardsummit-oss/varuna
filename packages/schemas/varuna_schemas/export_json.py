"""Export every model's JSON Schema (draft 2020-12) to ``packages/schemas/json/``.

Usage::

    uv run python -m varuna_schemas.export_json            # write <ModelName>.schema.json files
    uv run python -m varuna_schemas.export_json --check    # exit 1 when the committed files are stale
    uv run python -m varuna_schemas.export_json --out DIR  # write elsewhere

Output is deterministic: keys are sorted, indentation is two spaces, files end with a single
newline, and stale ``*.schema.json`` files for renamed models are removed. The schemas are the
validation (input) contracts; ``computed_field`` values appear in serialised artifacts but are
accepted and ignored on input (see :class:`varuna_schemas.models.common.VarunaModel`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from varuna_schemas.models import MODEL_REGISTRY
from varuna_schemas.paths import schemas_json_dir

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SUFFIX = ".schema.json"


def schema_for(name: str) -> dict[str, Any]:
    """The JSON Schema of one registered model with ``$schema`` and ``$id`` set."""
    model = MODEL_REGISTRY[name]
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = f"urn:varuna:schema:{name}"
    return schema


def render(schema: dict[str, Any]) -> str:
    """Canonical text form of a schema (sorted keys, two-space indent, trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected_files(out_dir: Path) -> dict[Path, str]:
    """``{path: text}`` for every registered model, in registry order."""
    return {out_dir / f"{name}{SUFFIX}": render(schema_for(name)) for name in MODEL_REGISTRY}


def export(out_dir: Path | None = None, *, check: bool = False) -> tuple[list[Path], list[Path]]:
    """Write (or, with ``check``, compare) every schema file.

    Returns ``(changed, stale)``: files written or differing, and orphan files not backed by a
    registered model. In check mode nothing is written.
    """
    target = out_dir or schemas_json_dir()
    wanted = expected_files(target)
    changed: list[Path] = []
    for path, text in wanted.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    stale = (
        sorted(p for p in target.glob(f"*{SUFFIX}") if p.is_file() and p not in wanted)
        if target.is_dir()
        else []
    )
    if not check:
        for path in stale:
            path.unlink()
    return changed, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export VARUNA JSON schemas.")
    parser.add_argument(
        "--out", type=Path, default=None, help="Output folder (default: packages/schemas/json)."
    )
    parser.add_argument(
        "--check", action="store_true", help="Do not write; exit 1 if any file is missing or stale."
    )
    args = parser.parse_args(argv)
    target = args.out or schemas_json_dir()
    changed, stale = export(target, check=args.check)
    if args.check:
        if changed or stale:
            for path in changed:
                print(f"stale or missing: {path}", file=sys.stderr)
            for path in stale:
                print(f"orphan: {path}", file=sys.stderr)
            print("Run: uv run python -m varuna_schemas.export_json", file=sys.stderr)
            return 1
        print(f"{len(MODEL_REGISTRY)} schemas up to date in {target}")
        return 0
    print(
        f"wrote {len(changed)} of {len(MODEL_REGISTRY)} schemas to {target}"
        + (f"; removed {len(stale)} orphan(s)" if stale else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
