"""The two ends of a live cycle run away from the API (ADR-0095).

The hosted API's container is too small to run a live cycle beside itself, so a scheduled GitHub
Actions job runs it (`.github/workflows/live.yml`). This script is that job's glue:

    uv run python tools/live_ci.py pull-reports --api https://...   # citizen reports -> inbox
    uv run python -m varuna_cycle.live --city mumbai --once          # the cycle itself
    uv run python tools/live_ci.py push --api https://...            # the run -> the API

``push`` sends the newest live run as a gzipped tar to ``POST /v1/runs/upload`` with the token in
``VARUNA_RUN_UPLOAD_TOKEN``. The rain member cube (``rain/cube.zarr``, about 15 MB) stays behind:
the quantiles the console reads travel, and the forcing that made the cube is re-fetchable.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CODES = {"mumbai": "MUM", "chennai": "CHN"}
LEFT_BEHIND = ("rain/cube.zarr",)


def pull_reports(api: str, city: str) -> int:
    """Write the API's public reports for ``city`` into ``data/reports/inbox.jsonl``."""
    with urllib.request.urlopen(f"{api}/v1/reports?city={city}&limit=500", timeout=60) as r:
        body = json.load(r)
    rows = [row for row in body.get("reports", []) if row.get("status") != "dismissed"]
    inbox = REPO / "data" / "reports" / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    print(f"{len(rows)} reports from {api} into {inbox.relative_to(REPO)}")
    return 0


def _archive(run_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as tar:
        for path in sorted(run_dir.rglob("*")):
            rel = path.relative_to(run_dir).as_posix()
            if any(rel == skip or rel.startswith(skip + "/") for skip in LEFT_BEHIND):
                continue
            tar.add(path, arcname=f"{run_dir.name}/{rel}", recursive=False)
    return buffer.getvalue()


def push(api: str, city: str) -> int:
    """Upload the newest live run of ``city`` to the API."""
    token = os.environ.get("VARUNA_RUN_UPLOAD_TOKEN", "")
    if not token:
        print("VARUNA_RUN_UPLOAD_TOKEN is not set; nothing uploaded.", file=sys.stderr)
        return 2
    runs = sorted((REPO / "data" / "runs").glob(f"{CODES[city]}-*-live"))
    if not runs:
        print("No live run to upload.", file=sys.stderr)
        return 1
    body = _archive(runs[-1])
    request = urllib.request.Request(
        f"{api}/v1/runs/upload",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/gzip"},
    )
    with urllib.request.urlopen(request, timeout=120) as r:
        print(f"{runs[-1].name}: {len(body):,} bytes, {r.status} {r.read().decode()[:200]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["pull-reports", "push"])
    parser.add_argument("--api", required=True)
    parser.add_argument("--city", default="mumbai")
    args = parser.parse_args()
    api = args.api.rstrip("/")
    return pull_reports(api, args.city) if args.command == "pull-reports" else push(api, args.city)


if __name__ == "__main__":
    raise SystemExit(main())
