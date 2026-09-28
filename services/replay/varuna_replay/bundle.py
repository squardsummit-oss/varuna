"""The replay-bundle contract: what a bundle is made of, how to read it, how to write it.

SPEC.md 10.2 fixes the layout, and this module is the only place that knows it::

    bundles/<ID>/
      manifest.json           BundleManifest
      radar/frames.zarr       dBZ[t, y, x] on the storm domain, every 10 min
      truth/rain.zarr         mm/h[t, y, x] on the same grid, every 5 min (synthetic bundles)
      gauges.csv              ts, station_id, lat, lon, mm_5min, synthetic
      tide.csv                ts, stage_m, source; from t0 to t1 + 3 h
      traffic/speeds.parquet  ts, segment_id, kmh, baseline_kmh, synthetic
      reports.jsonl           one JSON object per line
      ground_truth.geojson    real, sourced pins; properties follow GroundTruthPin

One extension in time: ``tide.csv`` runs on :data:`TIDE_LOOKAHEAD_MIN` past ``t1``, because a
cycle at the end of the window forecasts that far ahead and the tide is its boundary.

Three extensions to the table in SPEC.md 10.2: ``gauges.csv`` and
``traffic/speeds.parquet`` carry an explicit ``synthetic`` column, so a stream cannot reach
the console without saying what it is (rule 7); ``gauges.csv`` also carries the station
``name`` beside its id, because the site is the sourced part of that stream; and
``ground_truth.geojson`` may carry the provenance keys in :data:`GROUND_TRUTH_EXTRA_KEYS`
beside the contract properties.

Writers here take rows and write files; they never invent data. Everything they write is
deterministic: the same inputs produce byte-identical files (rule 8).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_schemas.constants import IST, LEAD_MAX_MIN
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.paths import bundle_dir, bundles_dir

from varuna_replay.domain import StormDomain

if TYPE_CHECKING:  # pandas is imported lazily: the root CLI must not pay for it
    import pandas as pd

log = structlog.get_logger("varuna.replay.bundle")

# ----------------------------------------------------------------------- member names
MANIFEST_NAME = "manifest.json"
RADAR_ZARR = "radar/frames.zarr"
TRUTH_ZARR = "truth/rain.zarr"
GAUGES_CSV = "gauges.csv"
TIDE_CSV = "tide.csv"
TRAFFIC_PARQUET = "traffic/speeds.parquet"
REPORTS_JSONL = "reports.jsonl"
GROUND_TRUTH_GEOJSON = "ground_truth.geojson"

RADAR_VARIABLE = "dbz"
TRUTH_VARIABLE = "rain"

GAUGES_COLUMNS: tuple[str, ...] = (
    "ts",
    "station_id",
    "name",
    "lat",
    "lon",
    "mm_5min",
    "synthetic",
)
"""``name`` is a third extension to SPEC.md 10.2's table, for the same reason as
``synthetic``: a gauge site is the one part of the stream that is real and sourced, and a bare
station id would send the reader back to the research file to find out whose gauge it is."""
TIDE_COLUMNS: tuple[str, ...] = ("ts", "stage_m", "source")
TIDE_LOOKAHEAD_MIN: int = LEAD_MAX_MIN
"""How far ``tide.csv`` runs past ``t1``: the forecast horizon, 3 h (SPEC.md 10.3).

Every other stream stops at ``t1`` because it is observed, and nothing observed exists after the
window. The tide is not observed: it is the sea boundary the Twin forecasts against, and a cycle
at ``t1`` forecasts to ``t1 + 3 h``. :meth:`varuna_twin.types.TideSeries.at` holds the stage
flat past the last row, so a series that stopped at ``t1`` froze the sea for the last cycle's
whole horizon - on MUM-2019-07-02 at the 09:40 stage, 0.98 m below the 11:30 high water the
series is anchored to. An astronomical tide is known before the day, which is what makes running
it past the window a boundary rather than a look at the future. The replay clock still stops at
``t1``, so the stages after it are read by the Twin and never published on the bus."""
TRAFFIC_COLUMNS: tuple[str, ...] = ("ts", "segment_id", "kmh", "baseline_kmh", "synthetic")
REPORT_REQUIRED_KEYS: tuple[str, ...] = ("ts", "lat", "lon", "depth_hint", "synthetic")

GROUND_TRUTH_REQUIRED_KEYS: tuple[str, ...] = ("id", "ts", "name", "kind", "source_url")
GROUND_TRUTH_EXTRA_KEYS: frozenset[str] = frozenset(
    {
        "depth_phrase",
        "source_title",
        "cached_path",
        "geocode",
        "geocode_precision",
        "inside_aoi",
        "note",
    }
)
"""Provenance keys a curated pin may carry beside the ``GroundTruthPin`` contract fields."""

GENERATOR = "varuna_replay.storm"
"""Written into every cube so a run can say which code produced it."""

LF = "\n"
"""Every text member is written with LF endings on every platform (docs/CONVENTIONS.md)."""


class BundleNotFoundError(FileNotFoundError):
    """No bundle folder, or no ``manifest.json`` inside it."""


# ============================================================================ layout
@dataclass(frozen=True, slots=True)
class BundleLayout:
    """Where every member of one bundle lives on disk."""

    root: Path

    @classmethod
    def for_bundle(cls, bundle: str | Path) -> BundleLayout:
        """Resolve a bundle id (``MUM-2019-07-02``) or a path to its folder."""
        text = str(bundle)
        path = Path(bundle) if isinstance(bundle, Path) or "/" in text or "\\" in text else None
        return cls(root=(path or bundle_dir(text)).resolve())

    @property
    def bundle_id(self) -> str:
        return self.root.name

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def radar(self) -> Path:
        return self.root / RADAR_ZARR

    @property
    def truth(self) -> Path:
        return self.root / TRUTH_ZARR

    @property
    def gauges(self) -> Path:
        return self.root / GAUGES_CSV

    @property
    def tide(self) -> Path:
        return self.root / TIDE_CSV

    @property
    def traffic(self) -> Path:
        return self.root / TRAFFIC_PARQUET

    @property
    def reports(self) -> Path:
        return self.root / REPORTS_JSONL

    @property
    def ground_truth(self) -> Path:
        return self.root / GROUND_TRUTH_GEOJSON

    def members(self) -> dict[str, Path]:
        """Member name (as it appears in SPEC.md 10.2) to its path."""
        return {
            MANIFEST_NAME: self.manifest,
            RADAR_ZARR: self.radar,
            TRUTH_ZARR: self.truth,
            GAUGES_CSV: self.gauges,
            TIDE_CSV: self.tide,
            TRAFFIC_PARQUET: self.traffic,
            REPORTS_JSONL: self.reports,
            GROUND_TRUTH_GEOJSON: self.ground_truth,
        }

    def relative(self, path: Path) -> str:
        """``path`` as it should be named in a validation finding."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


# ============================================================================ cubes
@dataclass(frozen=True, slots=True)
class CubeInfo:
    """The grid and time axis of one Zarr cube, read from its attributes."""

    path: Path
    variable: str
    shape: tuple[int, ...]
    units: str
    crs: str
    res_m: float
    n_px: int
    transform: tuple[float, ...]
    t0: datetime
    step_min: float
    times_min: np.ndarray
    attrs: dict[str, Any]

    @property
    def n_times(self) -> int:
        return int(self.shape[0])

    def grid_key(self) -> tuple[str, float, int, tuple[float, ...]]:
        """What two cubes must agree on: CRS, resolution, size and affine transform."""
        return (self.crs, self.res_m, self.n_px, tuple(round(v, 6) for v in self.transform))

    def timestamps(self) -> list[datetime]:
        """The cube's instants as IST datetimes."""
        return [
            self.t0 + _minutes(float(minutes)) for minutes in np.asarray(self.times_min).tolist()
        ]


def _minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


def write_cube(
    path: Path,
    data: np.ndarray,
    *,
    variable: str,
    times_min: Sequence[float] | np.ndarray,
    domain: StormDomain,
    t0: datetime,
    step_min: float,
    units: str,
    attrs: Mapping[str, Any] | None = None,
) -> Path:
    """Write a ``(t, y, x)`` cube as a Zarr 3 group with its grid and time axis.

    The group carries the CRS, the affine transform and ``t0`` so the validator can check
    that the radar and truth cubes describe the same grid, and so Sky can resample without
    guessing. Writing the same array twice produces byte-identical stores.
    """
    import zarr

    array = np.asarray(data)
    times = np.asarray(times_min, dtype=np.float64)
    if array.ndim != 3:
        msg = f"a cube must be (t, y, x), got shape {array.shape}"
        raise ValueError(msg)
    if array.shape[0] != times.size:
        msg = f"{array.shape[0]} frames but {times.size} instants"
        raise ValueError(msg)
    if array.shape[1:] != domain.shape:
        msg = f"frame shape {array.shape[1:]} does not match the domain {domain.shape}"
        raise ValueError(msg)

    path.parent.mkdir(parents=True, exist_ok=True)
    group = zarr.open_group(str(path), mode="w")
    cube = group.create_array(
        variable,
        shape=array.shape,
        dtype="float32",
        chunks=(1, domain.n_px, domain.n_px),
        dimension_names=("time", "y", "x"),
    )
    cube[:] = array.astype(np.float32)
    cube.attrs["units"] = units
    for name, values in (
        ("time_min", times),
        ("x", domain.x_coords()),
        ("y", domain.y_coords()),
    ):
        axis = group.create_array(
            name, shape=values.shape, dtype="float64", dimension_names=(name.split("_")[0],)
        )
        axis[:] = values
    group.attrs["variable"] = variable
    group.attrs["units"] = units
    group.attrs["t0"] = t0.astimezone(IST).isoformat()
    group.attrs["step_min"] = float(step_min)
    group.attrs["generator"] = GENERATOR
    for key, value in domain.to_dict().items():
        group.attrs[key] = value
    for key, value in (attrs or {}).items():
        group.attrs[key] = value
    log.info("bundle.cube_written", path=str(path), variable=variable, shape=list(array.shape))
    return path


def read_cube_info(path: Path, variable: str) -> CubeInfo:
    """Read one cube's grid and time axis without loading the frames."""
    import zarr

    if not path.exists():
        msg = f"No cube at {path}"
        raise BundleNotFoundError(msg)
    group = zarr.open_group(str(path), mode="r")
    if variable not in list(group.array_keys()):
        msg = f"{path} has no array {variable!r}; found {sorted(group.array_keys())}"
        raise KeyError(msg)
    cube = group[variable]
    attrs = dict(group.attrs)
    return CubeInfo(
        path=path,
        variable=variable,
        shape=tuple(int(n) for n in cube.shape),
        units=str(attrs.get("units", "")),
        crs=str(attrs.get("crs", "")),
        res_m=float(attrs.get("res_m", 0.0)),
        n_px=int(attrs.get("n_px", 0)),
        transform=tuple(float(v) for v in attrs.get("transform", ())),
        t0=datetime.fromisoformat(str(attrs["t0"])).astimezone(IST),
        step_min=float(attrs.get("step_min", 0.0)),
        times_min=np.asarray(group["time_min"][:], dtype=np.float64),
        attrs=attrs,
    )


def read_cube(path: Path, variable: str) -> np.ndarray:
    """The whole cube as a numpy array (``(t, y, x)``, float32)."""
    import zarr

    group = zarr.open_group(str(path), mode="r")
    return np.asarray(group[variable][:], dtype=np.float32)


# ============================================================================ streams
def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line endings on every platform, so bakes are byte-identical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline=LF)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(IST).isoformat()
    return str(value)


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> Path:
    import pandas as pd

    frame = pd.DataFrame(list(rows), columns=list(columns))
    if "ts" in frame.columns:
        frame["ts"] = [_iso(value) for value in frame["ts"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    log.info("bundle.stream_written", path=str(path), rows=len(frame))
    return path


def write_gauges(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """``gauges.csv``: ``ts, station_id, lat, lon, mm_5min, synthetic``.

    Synthetic readings sampled from the truth field at real station locations must carry
    ``synthetic=True``; the station coordinates themselves are sourced (SPEC.md 10.2).
    """
    return _write_csv(path, GAUGES_COLUMNS, rows)


def write_tide(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """``tide.csv``: ``ts, stage_m, source``. ``source`` is a tide-table URL or
    ``illustrative`` - never blank (SPEC.md 3.2, 10.2)."""
    return _write_csv(path, TIDE_COLUMNS, rows)


def write_traffic(path: Path, rows: Iterable[Mapping[str, Any]] | pd.DataFrame) -> Path:
    """``traffic/speeds.parquet``: ``ts, segment_id, kmh, baseline_kmh, synthetic``.

    A ready-made ``pandas.DataFrame`` is accepted as well as rows. The traffic feed is the one
    stream with hundreds of thousands of rows - every covered segment at every snapshot - and
    building that frame from dictionaries costs more than the rest of a bundle build together.
    """
    import pandas as pd

    if isinstance(rows, pd.DataFrame):
        frame = rows.loc[:, list(TRAFFIC_COLUMNS)].reset_index(drop=True)
    else:
        frame = pd.DataFrame(list(rows), columns=list(TRAFFIC_COLUMNS))
    if "ts" in frame.columns:
        frame["ts"] = [_iso(value) for value in frame["ts"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    log.info("bundle.stream_written", path=str(path), rows=len(frame))
    return path


def write_reports(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """``reports.jsonl``: one compact JSON object per line, keys sorted for determinism."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for row in rows:
        payload = {key: (_iso(value) if key == "ts" else value) for key, value in row.items()}
        lines.append(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    _write_text(path, "".join(f"{line}{LF}" for line in lines))
    log.info("bundle.stream_written", path=str(path), rows=len(lines))
    return path


def write_ground_truth(
    path: Path,
    features: Sequence[Mapping[str, Any]],
    *,
    name: str,
    description: str,
) -> Path:
    """``ground_truth.geojson``: real, sourced pins only (SPEC.md 0.7).

    ``features`` are complete GeoJSON features; this writer checks nothing but the envelope,
    because the bundle validator is what enforces the sourcing rule.
    """
    payload = {
        "type": "FeatureCollection",
        "name": name,
        "description": description,
        "features": list(features),
    }
    _write_text(path, json.dumps(payload, indent=1, ensure_ascii=False) + LF)
    log.info("bundle.ground_truth_written", path=str(path), features=len(payload["features"]))
    return path


def write_manifest(path: Path, manifest: BundleManifest) -> Path:
    """``manifest.json``, two-space indent, trailing newline, model field order."""
    _write_text(path, manifest.model_dump_json(indent=2) + LF)
    log.info("bundle.manifest_written", path=str(path), bundle=manifest.id)
    return path


# ============================================================================ loading
@dataclass(frozen=True, slots=True)
class Bundle:
    """A loaded bundle: its manifest plus readers for every member."""

    manifest: BundleManifest
    layout: BundleLayout

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def root(self) -> Path:
        return self.layout.root

    def truth_info(self) -> CubeInfo:
        return read_cube_info(self.layout.truth, TRUTH_VARIABLE)

    def radar_info(self) -> CubeInfo:
        return read_cube_info(self.layout.radar, RADAR_VARIABLE)

    def read_truth(self) -> np.ndarray:
        return read_cube(self.layout.truth, TRUTH_VARIABLE)

    def read_radar(self) -> np.ndarray:
        return read_cube(self.layout.radar, RADAR_VARIABLE)

    def read_gauges(self) -> pd.DataFrame:
        import pandas as pd

        return pd.read_csv(self.layout.gauges)

    def read_tide(self) -> pd.DataFrame:
        import pandas as pd

        return pd.read_csv(self.layout.tide)

    def read_traffic(self) -> pd.DataFrame:
        import pandas as pd

        return pd.read_parquet(self.layout.traffic)

    def read_reports(self) -> list[dict[str, Any]]:
        text = self.layout.reports.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def read_ground_truth(self) -> dict[str, Any]:
        """The raw FeatureCollection. Validation happens in :mod:`varuna_replay.validate`."""
        return json.loads(self.layout.ground_truth.read_text(encoding="utf-8"))


def load_manifest(bundle: str | Path) -> BundleManifest:
    """Read and validate ``manifest.json`` of a bundle id or folder."""
    layout = BundleLayout.for_bundle(bundle)
    if not layout.root.is_dir():
        msg = (
            f"No bundle folder at {layout.root}. "
            f"Known bundles: {', '.join(list_bundle_ids()) or 'none'}."
        )
        raise BundleNotFoundError(msg)
    if not layout.manifest.is_file():
        msg = f"{layout.root} has no {MANIFEST_NAME}; it is not a bundle."
        raise BundleNotFoundError(msg)
    return BundleManifest.model_validate_json(layout.manifest.read_text(encoding="utf-8"))


def load_bundle(bundle: str | Path) -> Bundle:
    """Load a bundle by id (``MUM-2019-07-02``) or by path."""
    layout = BundleLayout.for_bundle(bundle)
    return Bundle(manifest=load_manifest(layout.root), layout=layout)


def list_bundle_ids() -> list[str]:
    """Every folder under ``bundles/`` that carries a ``manifest.json``, sorted."""
    root = bundles_dir()
    if not root.is_dir():
        return []
    return sorted(
        path.name for path in root.iterdir() if path.is_dir() and (path / MANIFEST_NAME).is_file()
    )


__all__ = [
    "GAUGES_COLUMNS",
    "GAUGES_CSV",
    "GENERATOR",
    "GROUND_TRUTH_EXTRA_KEYS",
    "GROUND_TRUTH_GEOJSON",
    "GROUND_TRUTH_REQUIRED_KEYS",
    "MANIFEST_NAME",
    "RADAR_VARIABLE",
    "RADAR_ZARR",
    "REPORTS_JSONL",
    "REPORT_REQUIRED_KEYS",
    "TIDE_COLUMNS",
    "TIDE_CSV",
    "TIDE_LOOKAHEAD_MIN",
    "TRAFFIC_COLUMNS",
    "TRAFFIC_PARQUET",
    "TRUTH_VARIABLE",
    "TRUTH_ZARR",
    "Bundle",
    "BundleLayout",
    "BundleNotFoundError",
    "CubeInfo",
    "list_bundle_ids",
    "load_bundle",
    "load_manifest",
    "read_cube",
    "read_cube_info",
    "write_cube",
    "write_gauges",
    "write_ground_truth",
    "write_manifest",
    "write_reports",
    "write_tide",
    "write_traffic",
]
