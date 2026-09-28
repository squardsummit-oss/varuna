"""The console's 3D heightmap (SPEC.md 6.7, task P6.15).

``GET /v1/city/{city}/terrain`` answers what the console needs to build a deck.gl
``TerrainLayer``: the Terrarium decoder, the lon/lat bounds and the height range. The image
itself is ``GET /v1/city/{city}/terrain.png``.

Both come from ``varuna_city.terrain_export``, which writes the city's own conditioned 30 m DEM
on the same grid and the same bounds as every run's depth rasters, so a depth PNG drapes on the
mesh texel for vertex. The export is a second of work and is made on the first request for a
city whose DEM is newer than its heightmap, which is what keeps it off the city pipeline's step
list while still never serving a stale surface.

A city with no DEM answers 404 with the command that builds it, never a flat plane: a flat
terrain would look like a 3D mode that works and a city that has no relief.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from varuna_schemas.models import ErrorEnvelope

from varuna_api.state import api_error

router = APIRouter(prefix="/v1", tags=["city"])

CACHE_CONTROL = "public, max-age=3600"
"""The heightmap changes only when ``make city`` rebuilds the DEM, as the city layers do."""

NOT_FOUND = {404: {"model": ErrorEnvelope, "description": "City has no conditioned DEM yet"}}


def _terrain(city: str) -> Any:
    from varuna_city.terrain_export import ensure_terrain

    try:
        made = ensure_terrain(city)
    except ValueError:
        # `city_dir` refuses anything that is not one path segment.
        raise api_error(400, "bad_city", f"{city!r} is not a city id.") from None
    if made is None:
        raise api_error(
            404,
            "terrain_not_built",
            f"{city} has no conditioned DEM, so there is no terrain to draw in 3D. Run "
            f"`make city CITY={city}` to build city/{city}/dem_conditioned.tif.",
        )
    return made


@router.get(
    "/city/{city}/terrain",
    responses=NOT_FOUND,
    summary="Heightmap metadata for 3D mode: Terrarium decoder, bounds, height range",
)
def terrain_meta(city: str, request: Request) -> JSONResponse:
    made = _terrain(city)
    meta = dict(made.meta_json)
    meta["png_url"] = str(request.url_for("terrain_png", city=city))
    return JSONResponse(meta, headers={"Cache-Control": CACHE_CONTROL})


@router.get(
    "/city/{city}/terrain.png",
    name="terrain_png",
    responses={**NOT_FOUND, 200: {"content": {"image/png": {}}}},
    response_class=Response,
    summary="The conditioned DEM as a Terrarium-encoded PNG, on the depth rasters' grid",
)
def terrain_png(city: str, request: Request) -> Response:
    made = _terrain(city)
    body = made.png.read_bytes()
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="image/png", headers=headers)


__all__ = ["router"]
