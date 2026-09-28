#!/usr/bin/env bash
# VARUNA API entrypoint (Railway). See docs/DEPLOY.md.
#
# City layers and replay bundles are generated artifacts, not committed, so on a fresh volume
# this builds them once and then keeps them. The build runs in the background: the API starts
# at once so the platform health check on /healthz passes, and the layer endpoints answer 404
# naming the command to run until the files land, which is the honest state. The router looks
# the files up per request, so nothing needs a restart when the build finishes. If a build
# step fails the API stays up and the 404s stay honest; a redeploy retries from the cache.
set -uo pipefail

CITY="${VARUNA_CITY:-mumbai}"
PORT="${PORT:-8000}"
BUILD_ON_BOOT="${VARUNA_BUILD_ON_BOOT:-1}"
CITY_DIR="${VARUNA_CITY_DIR:-/data/city}"

log() { printf '{"event":"entrypoint","msg":"%s","ts":"%s"}\n' "$1" "$(date -Is)"; }

# A city is "built" only when every step passed, which is what pipeline.json's `ok` records.
# Testing for one output file is not enough: varuna city does not stop at a failed step, it
# records the failure and carries on, so a run whose hotspots step failed still writes
# segments.geojson, drains.geojson and the rest. That is exactly what happened on this volume -
# the first boot had no docs/research to read the hotspot register from, failed that one step,
# and left a city complete enough to fool a single-file check. Every later boot then skipped the
# rebuild and the hotspots layer stayed 404 for good.
city_is_complete() {
  record="${CITY_DIR}/${CITY}/pipeline.json"
  [ -f "${record}" ] || return 1
  python -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('ok') else 1)" \
    "${record}" 2>/dev/null
}

build_city() {
  if city_is_complete; then
    log "city ${CITY} already built and every step passed, skipping"
    return 0
  fi
  if [ -f "${CITY_DIR}/${CITY}/pipeline.json" ]; then
    log "city ${CITY} is present but incomplete; rebuilding the steps that failed"
  fi
  log "building city ${CITY} into ${CITY_DIR} in the background (first boot; a few minutes)"
  # The open data is public and fetched through the OS trust store (ADR-0006). Both steps
  # are resumable, so a restart mid-way continues from what the volume already holds.
  uv run python tools/prefetch_city_cache.py --city "${CITY}" \
    || log "prefetch failed; the city build will report what is missing"
  if uv run varuna city --city "${CITY}"; then
    log "city ${CITY} built; layer endpoints are live"
    drop_download_cache
  else
    log "city build failed; the API keeps serving 404s that name the command"
    return 1
  fi
}

# The download cache is 339 MB of Copernicus and WorldCover tiles against a 500 MB volume that
# also has to hold the 116 MB the build produces - 94 % full, and a volume that fills mid-build
# leaves a half-written city. The cache only exists so a *rebuild* need not re-download, and
# once segments.geojson is on disk build_city skips the rebuild entirely, so after a success it
# is dead weight. It is dropped only on success: a boot that failed part-way still finds the
# cache where it left it and resumes, which is what makes the build restartable.
#
# The cost is honest - if the city ever has to be rebuilt from scratch, it re-downloads. That is
# rare, it is a few minutes, and it beats a build that dies at 94 % full. A volume larger than
# 500 MB (Railway ties volume size to the plan) would make this unnecessary.
drop_download_cache() {
  cache="${CITY_DIR}/cache"
  [ -d "${cache}" ] || return 0
  freed="$(du -sh "${cache}" 2>/dev/null | cut -f1)"
  rm -rf "${cache}" && log "dropped the ${freed:-?} download cache; the built city is what persists"
}

# A replay bundle is only half committed. The manifest, gauges, tide, reports and the sourced
# ground truth are in git; radar/frames.zarr, truth/rain.zarr and traffic/speeds.parquet are
# gitignored because they are generated (.gitignore lines 31-33), so the image ships a bundle
# the API can describe but not run a cycle from. They regenerate in about seven seconds and are
# byte-identical every time - the storm designer is seeded with 2019 (rule 8) - so rebuilding
# them on the volume is cheaper and more honest than committing 4 MB of derived Zarr.
BUNDLE="${VARUNA_BUNDLE:-MUM-2019-07-02}"
BUNDLES_DIR="${VARUNA_BUNDLES_DIR:-/data/bundles}"

build_bundle() {
  if [ -d "${BUNDLES_DIR}/${BUNDLE}/radar" ]; then
    log "bundle ${BUNDLE} already generated, skipping"
    return 0
  fi
  # The generator is self-sufficient: from an empty directory it writes the whole bundle,
  # manifest and curated ground truth included, reading the sourced pins from docs/research.
  # Verified locally that manifest.json, ground_truth.geojson, gauges.csv, tide.csv and
  # reports.jsonl come back byte-identical to what git carries, so nothing is seeded from the
  # image and there is no second copy to drift.
  mkdir -p "${BUNDLES_DIR}"
  log "generating bundle ${BUNDLE} into ${BUNDLES_DIR}"
  if uv run varuna bundle --bundle "${BUNDLE}"; then
    log "bundle ${BUNDLE} ready"
  else
    log "bundle ${BUNDLE} generation failed; replay endpoints will say so"
    return 1
  fi
}

# What the volume holds, and the regenerable part of it reclaimed, before anything else writes.
# On 13 September 2026 an onboarding build left Chennai's downloaded tiles and OSM responses in
# city/cache after failing with ENOSPC, the volume stood at 495 of 500 MB, and the API crashed on
# its next restart and stayed down. Every entry is logged with its size first, so what was on
# the volume is on record rather than inferred. Only two things are removed, both regenerable:
#   - the download cache, and only once the default city is complete, which is the same rule
#     drop_download_cache already follows after a successful build;
#   - run directories an atomic write abandoned (`.<run_id>.tmp-*`), which no reader ever opens.
# A built city, the bundle and every published or seeded run are left alone.
DATA_DIR="${VARUNA_DATA_DIR:-/data}"

volume_usage() {
  df -h "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {print $3 " used, " $4 " free of " $2}'
}

reclaim_volume() {
  [ -d "${DATA_DIR}" ] || return 0
  log "volume before reclaim: $(volume_usage)"
  for entry in "${DATA_DIR}"/* "${CITY_DIR}"/*; do
    [ -e "${entry}" ] || continue
    log "volume holds $(du -sh "${entry}" 2>/dev/null | cut -f1) in ${entry}"
  done
  if [ -d "${CITY_DIR}/cache" ] && city_is_complete; then
    drop_download_cache
  fi
  if [ -d "${DATA_DIR}/runs" ]; then
    find "${DATA_DIR}/runs" -mindepth 1 -maxdepth 1 -type d -name '.*.tmp-*' -print \
      | while read -r stale; do
          rm -rf "${stale}" && log "removed an abandoned atomic write: ${stale}"
        done
  fi
  log "volume after reclaim: $(volume_usage)"
}

reclaim_volume

# The two map products a built city does not produce on its own: the Terrarium heightmap the
# console's 3D mode drapes the depth raster over (P6.15), and the PMTiles vector basemap the
# public map draws when it is offline (P9.10). Both are derived from the city the build just
# finished, both are cheap - 6 s and 62 s on a developer laptop, 278 KB and 4.2 MB - and both are
# skipped when their file is already on the volume. Without them `/v1/city/<city>/terrain` and
# `/v1/city/<city>/basemap.pmtiles` answer 404 on the deployed API and two shipped features are
# dark in production, which is exactly the kind of gap that is only ever found on stage.
# The map layers are generated artifacts on a persistent volume, and build_city only rebuilds a
# city whose pipeline.json records a failure. So when the exporter's output *shape* changes -
# a column added to MAP_KEEP_COLUMNS, a different simplification tolerance - the new code ships
# in the image, the city on the volume is still "complete", the export is skipped, and the API
# serves the old columns from the new build. Nothing errors; the feature that needed the column
# is just quietly dark.
#
# That is not hypothetical. On 2026-09-23 the drain invert elevations were added to the drains
# layer so the console's X-ray could draw a pipe at the depth it sits at. The image built, the
# deployment reported SUCCESS, and /v1/city/mumbai/layers/drains went on returning the same
# twelve properties it had before, without them.
#
# map_export_fingerprint() digests everything that decides that shape, and export_city writes it
# into map/EXPORT.json. A mismatch re-runs step 12 alone, which loads the cached upstream steps
# and costs seconds rather than a city rebuild.
map_export_is_current() {
  marker="${CITY_DIR}/${CITY}/map/EXPORT.json"
  [ -f "${marker}" ] || return 1
  # `uv run`, not bare `python`: this one imports varuna_city, where city_is_complete above only
  # needs the standard library. A check that cannot run reports "stale", so the worst case is a
  # re-export that costs seconds, never a silently stale layer.
  uv run python -c "import json,sys;from varuna_city.export import map_export_fingerprint as f;sys.exit(0 if json.load(open(sys.argv[1])).get('fingerprint')==f() else 1)" "${marker}" 2>/dev/null
}

build_map_products() {
  if ! map_export_is_current; then
    log "map layers on the volume predate this build's export schema; re-exporting"
    # Straight to export_city, not through `varuna city --only export`. The step graph loads every
    # upstream step and runs `report` after this one, and on this volume it failed in four seconds
    # on 2026-09-23 for a reason the log then threw away. export_city reads each layer off disk
    # through find_layer_source, which is what a built city folder is made of: 17.7 s locally for
    # all eleven layers, and nothing between a stale file and a fresh one.
    if map_out=$(uv run python -m varuna_city.export --city "${CITY}" 2>&1); then
      log "map layers re-exported"
      printf '%s\n' "${map_out}" | tail -n 3
    else
      log "map re-export failed; the API keeps the layers already on the volume"
      # The reason, not just the fact. A silent failure here is how the drain inverts sat
      # undelivered through two green deployments.
      printf '%s\n' "${map_out}" | tail -n 25
    fi
  fi
  if [ ! -f "${CITY_DIR}/${CITY}/map/terrain.png" ]; then
    uv run python -m varuna_city.terrain_export --city "${CITY}"       && log "terrain heightmap written; 3D mode can load it"       || log "terrain export failed; /v1/city/${CITY}/terrain keeps its 404"
  fi
  if [ ! -f "${CITY_DIR}/${CITY}/map/basemap.pmtiles" ]; then
    uv run python -m varuna_city.basemap_tiles --city "${CITY}"       && log "offline basemap written; the public map can cache it"       || log "basemap build failed; the offline map falls back to no basemap"
  fi
}

if [ "${BUILD_ON_BOOT}" = "1" ]; then
  # Sequential, and in that order: the bundle is built ON the city. Its reconstruction reads
  # the city's own segments.parquet - the traffic feed is synthesised on real road segments -
  # and its hotspots.geojson. Backgrounding the two independently raced them, and the bundle
  # lost: it failed at 10:03:45 on a city that finished exporting at 10:05:01. The pair still
  # runs in the background as a whole, so the API is up for the platform health check either
  # way, and a city that fails short-circuits the bundle rather than letting it fail confusingly.
  ( build_city && build_map_products && build_bundle ) &
fi

log "starting the API on :${PORT}"
# Score the event once in the background after the API is up, so the first visitor's top bar and
# the landing page read a kept score instead of starting the sweep themselves (routers/verify.py).
(
  sleep 45
  uv run python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/v1/verification', timeout=900).read()" >/dev/null 2>&1 \
    && log "verification scored and kept" || log "verification warm-up did not finish; the first request will score it"
) &
exec uv run uvicorn varuna_api.main:app --host 0.0.0.0 --port "${PORT}" --proxy-headers
