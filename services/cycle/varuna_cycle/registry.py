"""Run registry: the file-backed index of ``data/runs/<run_id>/run.json`` (SPEC.md 4.2, 10.3).

P0 storage is files, so the registry is a directory scan with a small parse cache keyed on
each ``run.json``'s mtime. :meth:`RunRegistry.write_run_dir` is the only way a run directory
is created: the writer fills a temporary folder next to the destination and the folder is
renamed into place, so a reader never sees a half-written run.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import ValidationError
from varuna_schemas.models import RunList, RunMeta, RunSummary
from varuna_schemas.paths import runs_dir as default_runs_dir

log = structlog.get_logger("varuna.registry")

RUN_JSON: str = "run.json"


def summarize(meta: RunMeta) -> RunSummary:
    """The ``GET /v1/runs`` row for a run."""
    return RunSummary(
        run_id=meta.run_id,
        city=meta.city,
        cycle_ts=meta.cycle_ts,
        mode=meta.mode,
        replay_mode=meta.replay_mode,
        bundle=meta.bundle,
        created_at=meta.created_at,
        total_ms=meta.total_ms,
        mass_balance_err=meta.mass_balance_err,
        ensemble_n=meta.ensemble_n,
    )


class RunRegistry:
    """Index of run directories under ``runs_dir`` (default :func:`varuna_schemas.paths.runs_dir`)."""

    def __init__(self, runs_dir: Path | None = None):
        self._runs_dir = Path(runs_dir) if runs_dir is not None else None
        self._cache: dict[str, tuple[int, RunMeta]] = {}

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir if self._runs_dir is not None else default_runs_dir()

    # ---- reading ---------------------------------------------------------
    def _read(self, run_json: Path) -> RunMeta | None:
        try:
            mtime = run_json.stat().st_mtime_ns
        except OSError:
            return None
        run_id = run_json.parent.name
        cached = self._cache.get(run_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            meta = RunMeta.model_validate_json(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("registry.invalid_run", run_id=run_id, error=str(exc)[:200])
            return None
        if meta.run_id != run_id:
            log.warning("registry.run_id_mismatch", folder=run_id, run_id=meta.run_id)
            return None
        self._cache[run_id] = (mtime, meta)
        return meta

    def scan(self) -> list[RunMeta]:
        """Every valid run, newest ``cycle_ts`` first (then newest ``created_at``)."""
        root = self.runs_dir
        if not root.is_dir():
            return []
        runs: list[RunMeta] = []
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = self._read(child / RUN_JSON)
            if meta is not None:
                runs.append(meta)
        runs.sort(key=lambda m: (m.cycle_ts, m.created_at), reverse=True)
        return runs

    def get(self, run_id: str) -> RunMeta | None:
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            return None
        run_json = self.runs_dir / run_id / RUN_JSON
        return self._read(run_json) if run_json.is_file() else None

    def exists(self, run_id: str) -> bool:
        return self.get(run_id) is not None

    def latest(self, city: str | None = None, bundle: str | None = None) -> RunMeta | None:
        runs = self.list(city=city, bundle=bundle, limit=1)
        return runs[0] if runs else None

    def run_list(
        self, city: str | None = None, bundle: str | None = None, limit: int = 50
    ) -> RunList:
        """The ``GET /v1/runs`` payload."""
        runs = self.list(city=city, bundle=bundle, limit=limit)
        return RunList(
            runs=[summarize(m) for m in runs],
            count=len(runs),
            latest_run_id=runs[0].run_id if runs else None,
        )

    def list(
        self, city: str | None = None, bundle: str | None = None, limit: int = 50
    ) -> list[RunMeta]:
        """Runs filtered by city slug and bundle id, newest first, at most ``limit``."""
        runs = self.scan()
        if city:
            wanted = city.strip().lower()
            runs = [m for m in runs if m.city.lower() == wanted]
        if bundle:
            runs = [m for m in runs if m.bundle == bundle]
        return runs[: max(limit, 0)] if limit is not None else runs

    # ---- writing ---------------------------------------------------------
    def write_run_dir(
        self,
        run_id: str,
        writer: Callable[[Path], None],
        meta: RunMeta | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Create ``runs_dir/<run_id>`` atomically.

        ``writer(tmp_dir)`` fills a temporary folder; ``meta`` (if given) is written as
        ``run.json`` after the writer returns; then the folder is renamed into place. An
        existing run raises :class:`FileExistsError` unless ``overwrite`` is true.
        """
        if meta is not None and meta.run_id != run_id:
            msg = f"meta.run_id {meta.run_id!r} does not match run_id {run_id!r}"
            raise ValueError(msg)
        root = self.runs_dir
        root.mkdir(parents=True, exist_ok=True)
        final = root / run_id
        if final.exists() and not overwrite:
            msg = f"Run {run_id} already exists under {root}; pass overwrite=True to replace it"
            raise FileExistsError(msg)
        tmp = root / f".{run_id}.tmp-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        try:
            writer(tmp)
            if meta is not None:
                (tmp / RUN_JSON).write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
            if not (tmp / RUN_JSON).is_file():
                msg = f"writer did not produce {RUN_JSON} for {run_id} and no meta was given"
                raise FileNotFoundError(msg)
            if final.exists():
                shutil.rmtree(final)
            tmp.rename(final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        self._cache.pop(run_id, None)
        log.info("registry.run_written", run_id=run_id, path=str(final))
        return final

    def write_meta(self, meta: RunMeta, overwrite: bool = False) -> Path:
        """Write a run directory that contains only ``run.json`` (tests, placeholders)."""
        return self.write_run_dir(meta.run_id, lambda _p: None, meta=meta, overwrite=overwrite)

    @staticmethod
    def read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def newest_created_at(self) -> datetime | None:
        runs = self.scan()
        return max((m.created_at for m in runs), default=None)


__all__ = ["RUN_JSON", "RunRegistry", "summarize"]
