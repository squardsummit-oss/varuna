"""Run metadata (``run.json``) and run-id helpers (SPEC.md 10.3).

Run id format::

    <CITY>-<cycle_ts UTC compact YYYYMMDDTHHMMZ>-sky<v>-twin<v>-flash<v>-<live|baked>

Example: ``MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked`` is the 17:40 IST cycle
of 2 July 2019 for Mumbai, published from pre-computed (baked) artifacts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, computed_field, field_validator

from varuna_schemas.constants import CITY_CODES, CYCLE_STAGES, IST, N_STEPS, STEP_MIN
from varuna_schemas.models.common import BBox, Timestamp, VarunaModel

RunMode = Literal["baked", "live"]
"""Whether the run's artifacts were pre-computed by ``make bake`` or computed on demand."""

ReplayMode = Literal["replay", "live", "degraded"]
"""Ingestion mode of the cycle that produced the run (the mode banner)."""

_VERSION = r"[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9]+)?"
_RUN_ID_RE = re.compile(
    rf"^(?P<city>[A-Z]{{3}})-(?P<ts>\d{{8}}T\d{{4}}Z)"
    rf"-sky(?P<sky>{_VERSION})-twin(?P<twin>{_VERSION})-flash(?P<flash>{_VERSION})"
    r"-(?P<mode>live|baked)$"
)


class RunIdError(ValueError):
    """A string is not a valid VARUNA run id."""


@dataclass(frozen=True, slots=True)
class RunIdParts:
    """The parsed components of a run id. ``cycle_ts`` is timezone-aware (UTC)."""

    city_code: str
    cycle_ts: datetime
    sky_version: str
    twin_version: str
    flash_version: str
    mode: RunMode

    @property
    def cycle_ts_ist(self) -> datetime:
        return self.cycle_ts.astimezone(IST)

    @property
    def city(self) -> str:
        """City slug (``mumbai``) when the code is known, else the code in lower case."""
        for slug, code in CITY_CODES.items():
            if code == self.city_code:
                return slug
        return self.city_code.lower()


def city_code(city: str) -> str:
    """``"mumbai"`` -> ``"MUM"``; a three-letter code passes through upper-cased."""
    key = city.strip()
    if key.lower() in CITY_CODES:
        return CITY_CODES[key.lower()]
    if re.fullmatch(r"[A-Za-z]{3}", key):
        return key.upper()
    msg = f"Unknown city {city!r}; expected one of {sorted(CITY_CODES)} or a three-letter code"
    raise RunIdError(msg)


def _check_version(label: str, version: str) -> str:
    if not re.fullmatch(_VERSION, version):
        msg = f"{label} version must look like 1.0 or 0.3, got {version!r}"
        raise RunIdError(msg)
    return version


def build_run_id(
    city: str,
    cycle_ts: datetime,
    sky_v: str,
    twin_v: str,
    flash_v: str,
    mode: RunMode,
) -> str:
    """Compose a run id. ``cycle_ts`` must be timezone-aware; it is rendered in UTC to the minute."""
    if cycle_ts.tzinfo is None or cycle_ts.utcoffset() is None:
        msg = "cycle_ts must be timezone-aware (IST +05:30 or UTC)"
        raise RunIdError(msg)
    if mode not in ("live", "baked"):
        msg = f"mode must be 'live' or 'baked', got {mode!r}"
        raise RunIdError(msg)
    stamp = cycle_ts.astimezone(UTC).strftime("%Y%m%dT%H%MZ")
    return (
        f"{city_code(city)}-{stamp}"
        f"-sky{_check_version('sky', sky_v)}"
        f"-twin{_check_version('twin', twin_v)}"
        f"-flash{_check_version('flash', flash_v)}"
        f"-{mode}"
    )


def parse_run_id(run_id: str) -> RunIdParts:
    """Split a run id into :class:`RunIdParts`; raises :class:`RunIdError` on any deviation."""
    match = _RUN_ID_RE.match(run_id.strip())
    if match is None:
        msg = (
            f"Invalid run id {run_id!r}; expected "
            "<CITY>-<YYYYMMDDTHHMMZ>-sky<v>-twin<v>-flash<v>-<live|baked>"
        )
        raise RunIdError(msg)
    try:
        ts = datetime.strptime(match["ts"], "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
    except ValueError as exc:
        msg = f"Invalid timestamp in run id {run_id!r}: {exc}"
        raise RunIdError(msg) from exc
    return RunIdParts(
        city_code=match["city"],
        cycle_ts=ts,
        sky_version=match["sky"],
        twin_version=match["twin"],
        flash_version=match["flash"],
        mode=match["mode"],  # type: ignore[arg-type]
    )


def top_level_stage_ms(stage_ms: Mapping[str, int]) -> dict[str, int]:
    """The entries of ``stage_ms`` that are cycle stages, in cycle order.

    The single rule for what a stage total counts: a key is a stage when it is one of
    :data:`~varuna_schemas.constants.CYCLE_STAGES`, and everything else in ``stage_ms`` is
    provenance. The Twin writes ``twin`` (its wall clock) and also ``twin_total_ms``,
    ``twin_surface_ms``, ``twin_drain_ms``, ``twin_coupling_ms`` and ``twin_hydrology_ms``
    (time spent inside that wall clock), and a cycle result may add ``total``. Summing every key
    counted the Twin two and a half times: 189,704 ms for the 09:10 IST baked cycle whose
    stages took 76,851 ms. An allowlist rather than a denylist, so a sub-timing added
    later cannot inflate a total until someone deliberately names it a stage.
    """
    return {stage: int(stage_ms[stage]) for stage in CYCLE_STAGES if stage in stage_ms}


def stage_total_ms(stage_ms: Mapping[str, int]) -> int:
    """Wall-clock of a cycle in ms: each top-level stage counted once (:func:`top_level_stage_ms`)."""
    return sum(top_level_stage_ms(stage_ms).values())


def is_run_id(value: str) -> bool:
    """True when :func:`parse_run_id` would accept ``value`` (format and a real calendar time)."""
    try:
        parse_run_id(value)
    except RunIdError:
        return False
    return True


class EngineVersions(VarunaModel):
    """Engine versions that produced a run; the first three appear in the run id."""

    sky: str = Field(description="VARUNA-Sky version, e.g. 1.0")
    twin: str = Field(description="VARUNA-Twin version")
    flash: str = Field(description="VARUNA-Flash version (0.x = reduced-order emulator)")
    pulse: str = Field(description="VARUNA-Pulse version")
    products: str = Field(description="Products version")


class GridSpec(VarunaModel):
    """The computational grid of the 2D solver (SPEC.md 3.3)."""

    dx_m: float = Field(gt=0, description="Cell size in metres (30 for the city grid).")
    nx: int = Field(gt=0, description="Columns.")
    ny: int = Field(gt=0, description="Rows.")
    crs: str = Field(description="Computation CRS, e.g. EPSG:32643 (UTM 43N).")
    bounds: BBox = Field(description="WGS84 extent of the grid for the map BitmapLayer.")
    transform: tuple[float, float, float, float, float, float] | None = Field(
        default=None,
        description="Affine (a, b, c, d, e, f) in CRS units for raster writers; optional.",
    )

    @field_validator("crs", mode="before")
    @classmethod
    def _epsg_int(cls, value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            return f"EPSG:{value}"
        return value

    @property
    def n_cells(self) -> int:
        return self.nx * self.ny


_DIGEST = r"^[0-9a-f]{12}$"


class CityFingerprint(VarunaModel):
    """Which city files a run read: the first 12 hex of a sha256 of each, ``None`` when absent.

    The run id carries engine versions and nothing about the city, so a run baked before a city
    rebuild and one baked after share an id. This is how a reader tells them apart without
    reading the products. Each digest is of the file's content, not its bytes on disk: the
    segment ids in order; the decoded rasters with their shape and transform; the drain tables'
    ids and the columns that say how a node meets the sea; and ``condition.json``'s coast-wall
    fields. A rebuild that reproduces the city reproduces every digest.
    """

    segments: str | None = Field(
        default=None, pattern=_DIGEST, description="segments.parquet: segment ids in order."
    )
    sea_mask: str | None = Field(
        default=None, pattern=_DIGEST, description="sea_mask.tif: open sea and tidal creek codes."
    )
    intertidal_mask: str | None = Field(
        default=None,
        pattern=_DIGEST,
        description="intertidal_mask.tif, when the city build writes one.",
    )
    drain_nodes: str | None = Field(
        default=None,
        pattern=_DIGEST,
        description="drain_nodes.parquet: node ids, cells and outfall, tidal and flap flags.",
    )
    drain_edges: str | None = Field(
        default=None,
        pattern=_DIGEST,
        description="drain_edges.parquet: edge ids and the nodes each joins.",
    )
    coast_wall: str | None = Field(
        default=None,
        pattern=_DIGEST,
        description="condition.json: the coast wall's level, rule, ring, raise and basins.",
    )
    dem_conditioned: str | None = Field(
        default=None,
        pattern=_DIGEST,
        description="dem_conditioned.tif: the ground the Twin runs on, wall included.",
    )


class RunMeta(VarunaModel):
    """``run.json``: provenance for one cycle's artifacts (SPEC.md 10.3, blueprint 9.1 ``run``)."""

    run_id: str = Field(description="See build_run_id().")
    city: str = Field(description="City slug, e.g. mumbai.")
    cycle_ts: Timestamp = Field(description="Cycle time (IST).")
    radar_frame_ts: Timestamp | None = Field(
        default=None, description="Timestamp of the latest radar frame used, if any."
    )
    versions: EngineVersions
    mode: RunMode = Field(description="baked = pre-computed by make bake; live = computed now.")
    replay_mode: ReplayMode = Field(default="replay", description="Ingestion mode of the cycle.")
    ensemble_n: int = Field(ge=1, description="Street-forecast members (50 in the prototype).")
    stage_ms: dict[str, int] = Field(
        default_factory=dict, description="Wall-clock per stage in ms (decode, sky, twin, ...)."
    )
    mass_balance_err: float = Field(
        ge=0, description="Relative mass-balance error of the Twin run (fraction, target < 0.001)."
    )
    mass_balance_ledger: dict[str, float] | None = Field(
        default=None,
        description=(
            "Where the Twin's residual sits, in m3: the surface's own, the drain's own, and the "
            "two exchange gaps between them, which sum to the residual on a coupled run. A run "
            "baked before the ledger existed carries none."
        ),
    )
    bundle: str | None = Field(default=None, description="Replay bundle id, None when live.")
    created_at: Timestamp = Field(description="When the run directory was written (IST).")
    grid: GridSpec
    degraded_feeds: list[str] = Field(
        default_factory=list, description="Feeds missing in this cycle, e.g. ['radar', 'traffic']."
    )
    step_min: int = Field(default=STEP_MIN, gt=0, description="Forecast step in minutes.")
    n_steps: int = Field(default=N_STEPS, gt=0, description="Number of forecast steps.")
    notes: list[str] = Field(
        default_factory=list,
        description="Honesty notes shown in the run stamp, e.g. 'reconstructed replay'.",
    )
    aoi_depth_band: dict[str, list[float]] | None = Field(
        default=None,
        description=(
            "p10, p50 and p90 of mean street depth in cm at each forecast step, taken across the "
            "ensemble's members: the band the time bar draws (SPEC.md 7.2). None when the run "
            "has fewer than two members, so a deterministic run never draws a band of no width."
        ),
    )
    rain_aoi_mm_h: list[float] = Field(
        default_factory=list,
        description=(
            "AOI-mean rain rate in mm/h at each forecast step: the storm this run was given. "
            "What-if scales it, so a run without it can only answer 'what if' about pipes."
        ),
    )
    twin_revision: str | None = Field(
        default=None,
        description=(
            "The Twin revision that computed the run, finer than the version in the run id - "
            "for example 1.0+coast-2026-09-28, the Twin with the city's own coastline. None on "
            "a run baked before run.json carried it."
        ),
    )
    city_fingerprint: CityFingerprint | None = Field(
        default=None,
        description=(
            "Digests of the city files the run read, so a run baked on another build of the "
            "city can be told apart under the same id. None on a run baked before."
        ),
    )

    @field_validator("run_id")
    @classmethod
    def _valid_run_id(cls, value: str) -> str:
        parse_run_id(value)
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ms(self) -> int:
        """Sum of stage timings in ms."""
        # Top-level stages only: see stage_total_ms. The docstring above is the OpenAPI
        # description, so the rule is stated there rather than here.
        return stage_total_ms(self.stage_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lead_max_min(self) -> int:
        """Longest lead time in minutes (180 for 36 steps of 5 min)."""
        return self.step_min * self.n_steps

    @property
    def parts(self) -> RunIdParts:
        return parse_run_id(self.run_id)

    @property
    def is_degraded(self) -> bool:
        return self.replay_mode == "degraded" or bool(self.degraded_feeds)


__all__ = [
    "CityFingerprint",
    "EngineVersions",
    "GridSpec",
    "ReplayMode",
    "RunIdError",
    "RunIdParts",
    "RunMeta",
    "RunMode",
    "build_run_id",
    "city_code",
    "is_run_id",
    "parse_run_id",
    "stage_total_ms",
    "top_level_stage_ms",
]
