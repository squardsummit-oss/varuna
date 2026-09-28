"""``python -m varuna_api.cli``: ``serve`` (uvicorn) and ``openapi`` (write the contract).

``uv run varuna openapi`` and ``uv run varuna typegen`` call this module; the OpenAPI JSON is
written with sorted keys and a trailing newline so two exports are byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from varuna_schemas.paths import repo_root
from varuna_schemas.settings import get_settings

app = typer.Typer(
    name="varuna-api",
    help="VARUNA API: serve it or export its OpenAPI document.",
    no_args_is_help=True,
    add_completion=False,
)


def default_openapi_path() -> Path:
    return repo_root() / "apps" / "command" / "openapi.json"


def openapi_document() -> dict[str, object]:
    from varuna_api.main import app as fastapi_app

    return fastapi_app.openapi()


def write_openapi(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(openapi_document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


@app.command()
def serve(
    port: Annotated[int | None, typer.Option(help="Port; default VARUNA_API_PORT or 8000")] = None,
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes")] = False,
) -> None:
    """Run the API with uvicorn."""
    import uvicorn

    settings = get_settings()
    root = repo_root()
    uvicorn.run(
        "varuna_api.main:app",
        host=host,
        port=port or settings.api_port,
        reload=reload,
        reload_dirs=[str(root / "services"), str(root / "packages")] if reload else None,
        log_level="info",
    )


@app.command()
def openapi(
    out: Annotated[
        Path | None, typer.Option(help="Output path; default apps/command/openapi.json")
    ] = None,
) -> None:
    """Write the OpenAPI 3.1 document (sorted keys, trailing newline)."""
    path = write_openapi(out or default_openapi_path())
    doc = openapi_document()
    n_paths = len(doc.get("paths", {}))  # type: ignore[arg-type]
    typer.echo(f"Wrote {path} ({n_paths} paths)")


if __name__ == "__main__":
    app()

__all__ = ["app", "default_openapi_path", "openapi_document", "write_openapi"]
