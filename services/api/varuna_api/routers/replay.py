"""Replay bundles and the shared simulation clock (SPEC.md 7.8, 10.2, 12; task P2.7).

``GET /v1/replay/bundles`` lists what is under ``bundles/``; the other routes drive the one
clock the process owns. Every control answers with the whole :class:`ReplayClock` state, so a
client never has to guess what its own request did, and the same state goes out on the bus as
``replay.clock`` for every other open tab.

The clock is opened lazily: the first request for it loads ``VARUNA_BUNDLE``'s manifest. When
that bundle is not on disk the answer is a 404 whose message names the make target rather than
an empty clock that pretends a replay exists.

The three ``radar`` routes are the storm-designer preview (task P2.10): a browser cannot open
a Zarr store, so the frames and the window accumulation are served as PNGs rendered by
:mod:`varuna_replay.preview`, with a small index telling the player how many there are and
when each one is. The index also carries the storm the frames were generated from - the
convective cells of a reconstructed replay, or the Chicago hyetograph of a design storm - in
the units the ``/replay`` cell table prints, so the console converts nothing. They read a
bundle without touching the clock - looking at a bundle card is not the same as pointing the
replay at it.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import Field
from pyproj import Transformer
from varuna_replay import preview
from varuna_replay.bundle import BundleLayout, BundleNotFoundError, load_manifest
from varuna_replay.clock import REPLAY_SPEEDS, ReplayClock
from varuna_replay.domain import WGS84
from varuna_schemas.models import (
    ErrorEnvelope,
    ReplayBundleSummary,
    ReplaySeekRequest,
    ReplaySpeedRequest,
    VarunaModel,
)
from varuna_schemas.models import (
    ReplayClock as ReplayClockState,
)
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.models.replay import (
    DesignStormBlocks,
    RadarAccumulation,
    RadarAoiPixels,
    RadarFrameRef,
    RadarPreviewIndex,
    RainRampBand,
    StormCellRow,
)
from varuna_schemas.paths import bundle_dir

from varuna_api.replay import bundle_hint, bundle_summaries
from varuna_api.routers.city import CACHE_CONTROL
from varuna_api.state import AppState, api_error, get_state

router = APIRouter(
    prefix="/v1/replay",
    tags=["replay"],
    responses={404: {"model": ErrorEnvelope, "description": "No such bundle under bundles/"}},
)

State = Annotated[AppState, Depends(get_state)]

BundleQ = Annotated[
    str | None,
    Query(description="Bundle id; default = VARUNA_BUNDLE, or the bundle already open."),
]


class ReplayBundleRequest(VarunaModel):
    """Body of ``POST /v1/replay/bundle``: which bundle the clock should walk."""

    bundle_id: str = Field(description="Bundle id, e.g. MUM-2019-07-02.")


async def _clock(state: AppState, bundle_id: str | None = None) -> ReplayClock:
    """The open clock, opening ``bundle_id`` (or the configured bundle) when there is none."""
    wanted = bundle_id or (
        state.replay.clock.bundle_id if state.replay.clock else state.settings.varuna_bundle
    )
    try:
        return await state.replay.open(
            wanted, bus=state.bus, registry=state.registry, settings=state.settings
        )
    except BundleNotFoundError as exc:
        raise api_error(
            404,
            "bundle_not_found",
            f"No replay bundle {wanted} under bundles/. {bundle_hint(wanted)}",
        ) from exc
    except ValueError as exc:  # an unreadable manifest
        raise api_error(
            422,
            "bundle_invalid",
            f"The manifest of {wanted} does not match the bundle contract: {exc}. "
            f"Run varuna bundle validate {wanted}.",
        ) from exc


def _bundle(bundle_id: str) -> tuple[BundleLayout, BundleManifest]:
    """A bundle's layout and manifest, without opening the clock on it.

    The preview is what a bundle card shows; selecting a bundle is ``POST /v1/replay/bundle``.
    A missing folder answers with the same 404 as :func:`_clock`, naming the make target.
    """
    try:
        layout = BundleLayout(root=bundle_dir(bundle_id).resolve())
        manifest = load_manifest(layout.root)
    # ValueError covers a bundle id that is not a path segment and an unreadable manifest;
    # either way there is no bundle here to preview.
    except (BundleNotFoundError, ValueError) as exc:
        raise api_error(
            404,
            "bundle_not_found",
            f"No replay bundle {bundle_id} under bundles/. {bundle_hint(bundle_id)}",
        ) from exc
    return layout, manifest


def _cube_etag(kind: str, cube: Path) -> str:
    """A weak ETag for one cube, as ``city.city_layer`` builds one for a layer file.

    A Zarr cube is a folder, so the stamp comes from the group's ``zarr.json``, which
    ``write_cube`` rewrites on every build. Deliberately not an immutable year-long cache:
    ``bundles/*/radar/`` is gitignored and ``make bundle`` rewrites it, so a stale frame must
    not outlive a rebuild on the demo laptop.
    """
    meta = cube / "zarr.json"
    stat = (meta if meta.is_file() else cube).stat()
    return f'W/"{kind}-{int(stat.st_mtime)}-{stat.st_size}"'


def _cube_missing(bundle_id: str, member: str) -> Exception:
    return api_error(
        404,
        "cube_not_built",
        f"Bundle {bundle_id} has no {member} yet. {bundle_hint(bundle_id)}",
    )


def _not_modified(request: Request, etag: str) -> Response | None:
    """A 304 when the browser already holds this render, so nothing is rendered twice."""
    if request.headers.get("if-none-match") != etag:
        return None
    return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})


@lru_cache(maxsize=4)
def _to_wgs84(crs: int) -> Transformer:
    """Metric CRS to lon/lat: the inverse of the transformer ``varuna_replay.domain`` caches."""
    return Transformer.from_crs(f"EPSG:{crs}", WGS84, always_xy=True)


def storm_cell_rows(manifest: BundleManifest) -> list[StormCellRow]:
    """The manifest's convective cells in the units the ``/replay`` cell table prints.

    Everything the table shows is converted here rather than in the browser, so one place owns
    the arithmetic: minutes from ``t0`` become an IST instant, the design CRS becomes lon/lat,
    the two velocity components become one speed, metres become kilometres, and the peak is
    multiplied by ``intensity_scale``. That last one matters: the manifest keeps the unscaled
    draw, and the field the bundle actually carries is the scaled one, so printing the raw
    number would put a rain rate on screen that no frame contains (rule 6).

    Empty on a design storm, which has no cells; see :func:`design_storm_blocks`.
    """
    storm = manifest.storm
    if storm is None:
        return []
    to_wgs84 = _to_wgs84(storm.crs)
    rows: list[StormCellRow] = []
    for cell in storm.cells:
        lon, lat = to_wgs84.transform(cell.start_x_m, cell.start_y_m)
        rows.append(
            StormCellRow(
                id=cell.id,
                birth=manifest.t0 + timedelta(minutes=cell.birth_min),
                lifetime_min=cell.lifetime_min,
                start_lat=float(lat),
                start_lon=float(lon),
                velocity_ms=math.hypot(cell.u_ms, cell.v_ms),
                sigma_km=cell.sigma_m / 1000.0,
                peak_mm_h=cell.peak_mm_h * storm.intensity_scale,
            )
        )
    return rows


def design_storm_blocks(manifest: BundleManifest) -> DesignStormBlocks | None:
    """The design storm's hyetograph blocks, or None on a reconstructed replay."""
    design = manifest.design_storm
    if design is None:
        return None
    return DesignStormBlocks(
        step_min=design.step_min,
        blocks_mm_h=list(design.hyetograph_mm_h),
        total_depth_mm=design.total_depth_mm,
        peak_position_r=design.peak_position_r,
    )


@router.get(
    "/bundles",
    response_model=list[ReplayBundleSummary],
    summary="Replay bundles on disk, with their build and bake status",
)
async def replay_bundles(state: State) -> list[ReplayBundleSummary]:
    """Every folder under ``bundles/`` with a manifest. An empty list means none is generated."""
    return bundle_summaries(state.registry)


@router.get("/clock", response_model=ReplayClockState, summary="The replay clock")
async def replay_clock(state: State, bundle: BundleQ = None) -> ReplayClockState:
    clock = await _clock(state, bundle)
    return clock.snapshot()


@router.post("/play", response_model=ReplayClockState, summary="Play")
async def replay_play(state: State, bundle: BundleQ = None) -> ReplayClockState:
    clock = await _clock(state, bundle)
    return await clock.play()


@router.post("/pause", response_model=ReplayClockState, summary="Pause")
async def replay_pause(state: State, bundle: BundleQ = None) -> ReplayClockState:
    clock = await _clock(state, bundle)
    return await clock.pause()


@router.post("/seek", response_model=ReplayClockState, summary="Seek to a simulated instant")
async def replay_seek(
    body: ReplaySeekRequest, state: State, bundle: BundleQ = None
) -> ReplayClockState:
    """Times outside the bundle window are clamped to it, not refused."""
    clock = await _clock(state, bundle)
    return await clock.seek(body.sim_time)


@router.post(
    "/speed",
    response_model=ReplayClockState,
    responses={422: {"model": ErrorEnvelope, "description": "Speed the time bar does not offer"}},
    summary="Set the replay speed",
)
async def replay_speed(
    body: ReplaySpeedRequest, state: State, bundle: BundleQ = None
) -> ReplayClockState:
    clock = await _clock(state, bundle)
    try:
        return await clock.set_speed(body.speed)
    except ValueError as exc:
        offered = ", ".join(f"{speed:g}" for speed in REPLAY_SPEEDS)
        raise api_error(422, "invalid_speed", f"{exc} The time bar offers {offered}.") from exc


@router.post(
    "/bundle",
    response_model=ReplayClockState,
    summary="Point the clock at another bundle",
)
async def replay_set_bundle(body: ReplayBundleRequest, state: State) -> ReplayClockState:
    """What a bundle card on ``/replay`` does: the clock reopens at the new bundle's ``t0``."""
    clock = await _clock(state, body.bundle_id)
    return clock.snapshot()


BundleId = Annotated[str, PathParam(description="Bundle id, e.g. MUM-2019-07-02.")]

PNG_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {"content": {"image/png": {}}, "description": "RGBA PNG on the shared rain ramp"},
    304: {"description": "The browser already holds this render"},
}


@router.get(
    "/bundles/{bundle_id}/radar",
    response_model=RadarPreviewIndex,
    summary="Radar frames of one bundle: how many, when, how big, and where to fetch them",
)
def replay_radar_index(request: Request, bundle_id: BundleId) -> RadarPreviewIndex:
    """The index behind the storm-designer player. Frame PNGs are separate requests."""
    layout, manifest = _bundle(bundle_id)
    try:
        index = preview.radar_index(manifest, layout)
    except BundleNotFoundError as exc:
        raise _cube_missing(bundle_id, "radar/frames.zarr") from exc

    app = request.app
    return RadarPreviewIndex(
        bundle_id=index["bundle"],
        label=index["label"],
        variable=index["variable"],
        n_frames=index["n_frames"],
        step_min=index["step_min"],
        t0=index["t0"],
        width=index["width"],
        height=index["height"],
        frames=[
            RadarFrameRef(
                index=frame["index"],
                ts=frame["ts"],
                url=str(
                    app.url_path_for(
                        "replay_radar_frame", bundle_id=bundle_id, index=frame["index"]
                    )
                ),
            )
            for frame in index["frames"]
        ],
        aoi_px=RadarAoiPixels(**index["aoi_px"]),
        ramp=[RainRampBand(**band) for band in index["bands"]],
        accumulation=RadarAccumulation(
            url=str(app.url_path_for("replay_radar_accumulation", bundle_id=bundle_id)),
            label=index["accumulation_label"],
            note=index["accumulation_note"],
        ),
        cells=storm_cell_rows(manifest),
        design_storm=design_storm_blocks(manifest),
    )


@router.get(
    "/bundles/{bundle_id}/radar/accumulation.png",
    responses=PNG_RESPONSE,
    response_class=Response,
    summary="Rainfall accumulated over the whole replay window, in mm",
)
def replay_radar_accumulation(request: Request, bundle_id: BundleId) -> Response:
    """Integrated from ``truth/rain.zarr``, the mm/h field, never the quantised radar frames."""
    layout, _ = _bundle(bundle_id)
    if not layout.truth.exists():
        raise _cube_missing(bundle_id, "truth/rain.zarr")

    etag = _cube_etag("accumulation", layout.truth)
    cached = _not_modified(request, etag)
    if cached is not None:
        return cached
    try:
        png = preview.accumulation_png(layout)
    except BundleNotFoundError as exc:
        raise _cube_missing(bundle_id, "truth/rain.zarr") from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": CACHE_CONTROL},
    )


@router.get(
    "/bundles/{bundle_id}/radar/{index}.png",
    responses=PNG_RESPONSE,
    response_class=Response,
    summary="One radar frame of a bundle, coloured by rain rate",
)
def replay_radar_frame(
    request: Request,
    bundle_id: BundleId,
    index: Annotated[int, PathParam(ge=0, description="Frame number, 0-based.")],
) -> Response:
    """The frame as the forecast will see it: dBZ inverted to mm/h, then the shared rain ramp."""
    layout, _ = _bundle(bundle_id)
    if not layout.radar.exists():
        raise _cube_missing(bundle_id, "radar/frames.zarr")

    etag = _cube_etag(f"radar-{index}", layout.radar)
    cached = _not_modified(request, etag)
    if cached is not None:
        return cached
    try:
        png = preview.radar_frame_png(layout, index)
    except BundleNotFoundError as exc:
        raise _cube_missing(bundle_id, "radar/frames.zarr") from exc
    except IndexError as exc:
        raise api_error(404, "frame_not_found", str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": CACHE_CONTROL},
    )


__all__ = ["ReplayBundleRequest", "design_storm_blocks", "router", "storm_cell_rows"]


@router.get(
    "/ground-truth",
    summary="The event's sourced ground-truth pins (SPEC.md 10.2, task P6.12)",
)
def replay_ground_truth(bundle: BundleQ = None) -> dict[str, Any]:
    """The curated, sourced pins for a bundle, oldest first.

    **Every pin carries the URL it was read from** (rule 7). These are the only observations in
    the replay that are not synthetic, which is what makes them worth dropping onto the map as the
    clock passes them: the claim is not "VARUNA says this street flooded", it is "a civic log said
    so, at this time, and here is the link".

    Pins outside the AOI are returned with `inside_aoi` false rather than dropped - the console
    shows them in the ticker and not on the map, because a report from a street the model does not
    cover is still part of the record of the morning.
    """
    from varuna_replay.bundle import bundle_dir
    from varuna_schemas.settings import get_settings

    bundle_id = bundle or get_settings().varuna_bundle
    path = bundle_dir(bundle_id) / "ground_truth.geojson"
    if not path.is_file():
        raise api_error(
            404,
            "no_ground_truth",
            f"No ground_truth.geojson in {bundle_id}. Run `make bundle BUNDLE={bundle_id}`.",
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    pins: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        pins.append(
            {
                "id": props.get("id"),
                "ts": props.get("ts"),
                "ts_uncertainty_min": props.get("ts_uncertainty_min"),
                "name": props.get("name"),
                "lon": props.get("lon"),
                "lat": props.get("lat"),
                "depth_cm": props.get("depth_cm"),
                "depth_phrase": props.get("depth_phrase"),
                "kind": props.get("kind"),
                "text": props.get("text"),
                "source_url": props.get("source_url"),
                "source_title": props.get("source_title"),
                "inside_aoi": bool(props.get("inside_aoi", True)),
            }
        )
    pins.sort(key=lambda p: str(p.get("ts") or ""))
    return {
        "bundle": bundle_id,
        "count": len(pins),
        "pins": pins,
        "notes": [
            "Sourced ground truth: every pin carries the URL it was read from. Nothing here is "
            "generated, unlike the gauges, traffic and citizen reports in the same bundle.",
        ],
    }
