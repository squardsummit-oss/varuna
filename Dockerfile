# VARUNA API image (Railway). See docs/DEPLOY.md and ADR-0014.
#
# The console is a separate Vercel deployment; this image is only the FastAPI service
# and the engines it calls. It carries the scientific stack (numba, GDAL through rasterio,
# geopandas, pysteps, zarr), so it is large by nature - about 1.5 GB. That is the cost of
# running real physics rather than a mock.

FROM python:3.12-slim-bookworm AS base

# GDAL/PROJ come from the rasterio and pyproj wheels, so only the C runtime bits and curl
# (for the healthcheck) are needed here. git is required by uv for any VCS dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git libexpat1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependency layer: copy only what resolves the lock, so a code change does not re-resolve
# the whole scientific stack (which takes minutes).
COPY pyproject.toml uv.lock .python-version ./
COPY packages/schemas/pyproject.toml packages/schemas/
COPY services/api/pyproject.toml services/api/
COPY services/city/pyproject.toml services/city/
COPY services/cycle/pyproject.toml services/cycle/
COPY services/flash/pyproject.toml services/flash/
COPY services/products/pyproject.toml services/products/
COPY services/pulse/pyproject.toml services/pulse/
COPY services/replay/pyproject.toml services/replay/
COPY services/route/pyproject.toml services/route/
# The Rust routing service (P8.12) is a Rust crate, but `members = ["services/*"]` makes it a
# uv workspace member too, so it carries a virtual pyproject.toml. Without this line the
# dependency layer's `uv sync --frozen` fails on a member the lockfile names and the image
# does not have. The crate's source is not built here; the image serves the Python router.
COPY services/route-rs/pyproject.toml services/route-rs/
COPY services/sky/pyproject.toml services/sky/
COPY services/twin/pyproject.toml services/twin/
COPY services/verify/pyproject.toml services/verify/
COPY tools/ tools/

# pysteps publishes no Linux wheel, so uv builds it from source and its Cython extensions
# (`_proesmans`, `_vet`) compile with `gcc ... -fopenmp`. Without a toolchain the build dies on
# `error: [Errno 2] No such file or directory: 'gcc'`, which is what every deploy of this image
# did until 2026-09-09 - the failure is in the dependency layer, so it is not something a
# working local checkout would ever show you.
#
# The toolchain is installed, used and purged in ONE layer, because a purge in a later layer
# would leave the ~250 MB in the image anyway. `libgomp1` is installed on its own line first so
# apt marks it manually installed: the compiled extension links against OpenMP at *runtime*, and
# `--auto-remove` would otherwise take it away with the compiler that pulled it in.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && apt-get install -y --no-install-recommends build-essential \
 && uv sync --frozen --no-dev --no-install-workspace \
 && apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

# Source layer. README.md is not documentation here: the root pyproject declares
# `readme = "README.md"`, so hatchling reads it while validating the root package's metadata
# and this `uv sync` dies with `OSError: Readme file does not exist` without it. The dependency
# layer above passes because `--no-install-workspace` never builds the root package.
COPY packages/ packages/
COPY services/ services/
COPY Makefile README.md ./

# docs/research holds curated DATA the pipelines read at runtime, not prose: the chronic-hotspot
# register (SPEC.md 10.1 step 9), the sourced ground-truth pins, the gauge station list. Without
# it `varuna city` fails its hotspots step with "No hotspot register for mumbai", which is how the
# first Railway boot failed. `.dockerignore` drops docs/research/_raw, the 19 MB of evidence
# screenshots, so this copies about 2 MB.
COPY docs/ docs/

# Seven baked cycles, 10.9 MB. `varuna_api.seed` copies these onto the volume the first time the
# API starts against an empty run directory, so a fresh container serves a console with a flood
# in it instead of an honest 404 - a real cycle takes about three minutes of CPU, and the
# container would otherwise spend its first quarter hour with nothing to draw. They are ordinary
# run artifacts, minus the 19 MB-per-cycle `segment_forecast.parquet` the console never reads.
# The escalation matrix (SPEC.md 11.10): who an alert reaches at each level. `GET
# /v1/alerts/escalation` and the /alerts screen read it, and both 404 without it.
COPY config/ config/

COPY demo/ demo/

RUN uv sync --frozen --no-dev

# The environment is complete; do not let the two `uv run` processes at boot (API and the
# background city build) re-check the lock against it.
ENV UV_NO_SYNC=1

# Static city layers and replay bundles are generated, not committed (see .gitignore), so the
# image ships without them and the entrypoint builds them into the mounted volume on first
# boot. Every layer endpoint answers 404 with "Run make city CITY=<city>" until it has, which
# is the honest state rather than a crash.
#
# City-in-a-box is off in this image. A public visitor can press Start on /onboard, and a Chennai
# build needs about 320 MB beside the Mumbai city on a 500 MB volume: the one started on
# 13 September 2026 failed with ENOSPC and the API did not come back. The wizard runs on the demo
# laptop, where the variable is unset and the endpoint accepts builds.
#
# Citizen report photos are off here for the same volume, and because the team decided photo
# uploads are for the demo laptop: a report posted to this image is kept without its photo and
# the response says so.
ENV VARUNA_DATA_DIR=/data \
    VARUNA_CITY_DIR=/data/city \
    VARUNA_BUNDLES_DIR=/data/bundles \
    VARUNA_MODE=replay \
    VARUNA_CITY=mumbai \
    VARUNA_ONBOARD_ENABLED=0 \
    VARUNA_REPORT_PHOTOS=0 \
    VARUNA_WHATIF_TWIN=0 \
    PORT=8000

COPY docker/entrypoint.sh /usr/local/bin/varuna-entrypoint
RUN chmod +x /usr/local/bin/varuna-entrypoint

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

ENTRYPOINT ["varuna-entrypoint"]
