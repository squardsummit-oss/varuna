"""Write the landing page's offline copy of ``GET /v1/cycle/status`` (SPEC.md 7.1 item 4, 17).

The landing page's cycle diagram prints each stage's measured time under its node. It asks the
API first; when the API is unreachable - a venue's network, the morning of the finale - it reads
``apps/command/public/cycle-status.json`` instead. That file must not be typed by hand (rule 6),
so this script builds it the way the endpoint does: the newest baked run's ``run.json`` through
the same :class:`varuna_schemas.models.CycleStatus` model, with the run it came from recorded
beside it.

    uv run python tools/write_cycle_status.py                  # newest run in demo/runs
    uv run python tools/write_cycle_status.py --run MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked

"Newest" means what ``varuna_api.runs_util.latest_run_for`` means: the last directory name in
sorted order for the city, which for run ids is the latest cycle. Re-run this whenever
``demo/runs`` is re-baked; ``components/landing/__tests__/cycle-status-file.test.ts`` fails when
the committed copy no longer matches the run it names.

The output is deterministic: no wall clock, keys in model order, two-space indent, trailing newline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from varuna_schemas.models import CycleStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = REPO_ROOT / "demo" / "runs"
DEFAULT_OUT = REPO_ROOT / "apps" / "command" / "public" / "cycle-status.json"
CITY_PREFIX = {"mumbai": "MUM-", "chennai": "CHN-"}


def newest_run(runs: Path, city: str) -> Path:
    prefix = CITY_PREFIX[city]
    candidates = sorted(
        p
        for p in runs.iterdir()
        if p.is_dir() and p.name.startswith(prefix) and (p / "run.json").is_file()
    )
    if not candidates:
        msg = f"No {city} run with a run.json under {runs}. Bake one with make bake first."
        raise SystemExit(msg)
    return candidates[-1]


def build(run_json: Path) -> dict[str, object]:
    run = json.loads(run_json.read_text(encoding="utf-8"))
    status = CycleStatus(
        run_id=run["run_id"],
        stage="idle",
        stage_ms=dict(run.get("stage_ms") or {}),
        cycle_ts=run.get("cycle_ts"),
        mode=run.get("mode"),
        replay_mode=run.get("replay_mode"),
        bundle=run.get("bundle"),
        degraded_feeds=list(run.get("degraded_feeds") or []),
    )
    body: dict[str, object] = status.model_dump(mode="json")
    try:
        source = run_json.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        source = run_json.name
    body["fallback"] = {
        "written_by": "tools/write_cycle_status.py",
        "from_run_json": source,
    }
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--run", help="run id; defaults to the newest run for the city")
    parser.add_argument("--city", default="mumbai", choices=sorted(CITY_PREFIX))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    run_dir = args.runs / args.run if args.run else newest_run(args.runs, args.city)
    body = build(run_dir / "run.json")
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(text.encode("utf-8"))
    print(f"Wrote {args.out} from {run_dir.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
