# Engineering conventions (read before writing any code)

This file complements `SPEC.md` (the build spec). It records how this repository is actually set up on
this machine and the library versions in use, so that code is written against reality, not memory.

## Repository and shell

- The repository is `C:\dev\varuna` (see `docs/DECISIONS.md` ADR-0001). In a Bash tool call the path is
  `/c/dev/varuna`. Always start commands with `cd /c/dev/varuna` (or a subfolder); the shell's working
  directory can drift between calls.
- Windows. Prefer the Bash tool (Git Bash) with forward slashes. Long heredocs containing quotes and
  backslashes are fragile; write files with the Write tool instead.
- Contributors do not run `pnpm add`, `pnpm install`, `uv add` or `uv sync`. Every needed package is already
  installed (see the lists below). If something is missing, say so in your report and keep going.
- Contributors do not commit. The maintainer commits with the `[<task id>] <what changed>` convention.
- Line endings are LF (`.gitattributes`, `.editorconfig`). Do not reformat files you did not change.
- Data folders are gitignored: `city/` (static city layers), `data/runs/` (run artifacts), heavy bundle
  members (`bundles/*/radar`, `truth`, `traffic`). Never commit rasters, Zarr stores or GeoPackages.

## Python

- Python 3.12.14 managed by uv. Run everything as `uv run <cmd>` from the repo root; tests with
  `uv run pytest` (config in `pyproject.toml`, `--import-mode=importlib`, testpaths `packages`, `services`,
  `tests`). Downloads need `UV_SYSTEM_CERTS=1` (already set in `pyproject.toml` via `system-certs = true`).
- uv workspace members: `packages/schemas` (import `varuna_schemas`) and `services/<name>` (import
  `varuna_<name>`). The root package `varuna` lives in `tools/varuna_cli` and provides the `varuna` CLI
  (Typer). `uv run varuna <task>` mirrors every `make` target.
- Every engine exposes a pure `run(inputs) -> outputs` function, a Typer sub-CLI, writes only to its run
  directory, records `stage_ms`, and has unit tests under `services/<name>/tests/`.
- Determinism: every synthetic generator takes `seed: int`; use `numpy.random.default_rng(seed)`; never
  `np.random.seed` globally. The demo bundle uses `seed=2019`.
- Shared helpers live in `varuna_schemas`: Pydantic v2 models, `settings.py` (pydantic-settings reading
  `.env`), `paths.py` (repo root, `city_dir()`, `bundles_dir()`, `runs_dir()`), `tokens.py` (design
  tokens loaded from `packages/tokens/tokens.json`), `ramps.py` (depth and drain colour ramps).
- Logging: `structlog` JSON. Errors returned by the API follow
  `{ "error": { "code", "message", "run_id" } }`.
- Style: Ruff (line length 100, rules E F I B UP NPY RUF), type hints everywhere,
  `from __future__ import annotations`, pathlib not os.path.
- Numba kernels: `@njit(cache=True, fastmath=True, parallel=True)` where safe; keep kernels in their own
  module so import does not trigger compilation of everything.
- Installed versions (2026-09-04): numpy 2.5.2, scipy 1.18.1, numba 0.67.0, pysteps 1.21.5, zarr 3.3.0,
  xarray 2026.7.0, geopandas 1.1.4, shapely 2.1.2, pyproj 3.x, rasterio 1.5.1, rioxarray, osmnx 2.1.1,
  networkx 3.6.1, pyflwdir 0.5.12, whitebox 2.3.6, opencv-python-headless 5.0, scikit-learn 1.9.0,
  pyarrow 25.0.1, pandas 2.x, fastapi 0.141.1, pydantic 2.13.5, pydantic-settings 2.x, structlog, httpx,
  uvicorn, websockets 17, lxml, xmlschema 4.3, matplotlib 3.9+, pillow, typer, rich, pytest 8, hypothesis.
- zarr is version 3 (new API: `zarr.open_group`, `zarr.create_array`; xarray `to_zarr` works). pysteps has no
  `__version__` attribute; `pysteps.motion.get_method("LK")` and `pysteps.nowcasts.get_method("steps")` exist.
  osmnx 2.x uses `ox.graph_from_bbox(bbox=(left, bottom, right, top))` and `ox.features_from_bbox`.

## TypeScript, Next.js 16 and the UI toolkit

- `apps/command` is Next.js 16.3.4 (App Router, Turbopack by default) with React 19.2.8, TypeScript strict,
  Tailwind CSS v4 (`@import "tailwindcss"` in `app/globals.css`, theme via `@theme`; there is no
  `tailwind.config.js`). Package name `@varuna/command`; run scripts with `pnpm --filter @varuna/command <s>`.
- This Next.js differs from training data. The docs for exactly this version are in
  `apps/command/node_modules/next/dist/docs/01-app/`. Known breaking changes:
  `params` and `searchParams` are Promises (`const { id } = await props.params`); use the global helper types
  `PageProps<'/route'>` and `LayoutProps<'/'>`; `middleware.ts` is now `proxy.ts` with `export function proxy`;
  `next lint` is gone (run `eslint` directly, flat config in `eslint.config.mjs`); Open Graph image functions
  receive async `params`; `next build` does not lint.
- shadcn/ui is version 4 with the `base-nova` style: primitives come from `@base-ui/react/*` (not Radix),
  `import { cn } from "cn"` (an npm package, re-exported from `@/lib/utils`). Generated primitives live in
  `components/ui/*` and are treated as vendor code; VARUNA components live in `components/varuna/*` and wrap
  or compose them. Components already added: button, dialog, dropdown-menu, tabs, tooltip, popover, command,
  sheet, slider, switch, select, input, input-group, table, badge, separator, scroll-area, skeleton, toggle,
  toggle-group, sonner, label, checkbox, radio-group, progress, textarea, hover-card, collapsible.
- Design tokens: `packages/tokens/tokens.json` is the law. The tokens build emits CSS variables (`--ink`,
  `--deep`, `--tide`, `--depth-1` ...), Tailwind `@theme` colour entries (`--color-ink` ...) so utility classes
  such as `bg-ink`, `bg-deep`, `text-text-2`, `border-line`, `text-tide`, `bg-depth-3` work, and a typed
  `tokens.ts`. Never write a hex colour in a component. shadcn's semantic variables (`--background`,
  `--primary`, `--border`, `--ring` ...) are mapped onto the tokens in `globals.css` so vendor primitives look
  right without edits. The console is dark only: `<html class="dark">` always.
- Fonts: `font-display` = Bricolage Grotesque (`next/font/google`), `font-sans` = Geist Sans and `font-mono` =
  Geist Mono (from the `geist` package). Every element that shows a number carries the `num` class
  (`font-variant-numeric: tabular-nums`). Icons: `lucide-react` only, 16 px in rows, 20 px in nav, stroke 1.75.
- Motion: `import { motion, useReducedMotion } from "motion/react"` (motion v13). Only motions listed in
  `SPEC.md` section 8; every one has a reduced-motion branch. Numbers roll with `@number-flow/react`.
- Maps: `maplibre-gl` 6, `react-map-gl` 8 (`import { Map } from "react-map-gl/maplibre"`), deck.gl 9.3
  (`MapboxOverlay` from `@deck.gl/mapbox`, layers from `@deck.gl/layers`, `@deck.gl/geo-layers`,
  `@deck.gl/extensions`). Map and deck components are client-only (`"use client"`, and dynamic import with
  `ssr: false` from a client wrapper where the module touches `window`).
- State: `zustand` 5 stores in `lib/stores/`; server data: `@tanstack/react-query` 5 in `lib/api/`;
  WebSocket hook in `lib/api/live.ts`. API types are generated into `lib/api/types.ts` by `pnpm typegen`
  (openapi-typescript against the FastAPI OpenAPI document).
- Other installed packages: `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`, `cmdk`, `sonner`,
  `recharts` 3, `zod` 4, `clsx`, `tailwind-merge`, `class-variance-authority`, `tw-animate-css`.
  Dev: `vitest` 5 (jsdom, `@testing-library/react`), `@playwright/test` 1.62 (Chromium installed),
  `prettier` + `prettier-plugin-tailwindcss`, `openapi-typescript`, `@lhci/cli`, `concurrently`.
- Copy rules (`SPEC.md` section 6.8) apply to every string: sentence case, no ALL-CAPS labels, no emoji,
  no lorem ipsum, buttons name the action, empty states say what to do, honesty labels are UI copy.
- `pnpm lint:design` (`tools/lint-design.mjs`) fails on raw hex, non-token `font-family`, `transition-all`
  or emoji inside `apps/command/{app,components/varuna,components/v0,components/map,lib}`.

## Open-data cache (Phase 1 and 2 read this, they do not download)

`tools/prefetch_city_cache.py` has already downloaded the raster tiles both areas of interest need, so the
city pipeline must look in the cache before it reaches for the network:

```
city/cache/dem/Copernicus_DSM_COG_10_N18_00_E072_00_DEM.tif     # Mumbai, 1-degree tiles
city/cache/dem/Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif
city/cache/dem/Copernicus_DSM_COG_10_N12_00_E080_00_DEM.tif     # Chennai
city/cache/dem/Copernicus_DSM_COG_10_N13_00_E080_00_DEM.tif
city/cache/worldcover/ESA_WorldCover_10m_2021_v200_N18E072_Map.tif   # 3-degree tiles
city/cache/worldcover/ESA_WorldCover_10m_2021_v200_N12E078_Map.tif
city/cache/MANIFEST.json    # url, bytes, sha256 and fetch time per file
```

The OSMnx cache (`city/cache/osmnx/`, 36 MB) is warm for the whole Mumbai area of interest. Measured on this
laptop, so use these as sanity checks:

| OSM layer | Features | First fetch |
|---|---|---|
| `drive_service` graph | 15,546 nodes / 34,539 edges | 23 s |
| buildings | 39,259 | 20 s |
| waterways (drain, canal, stream, river) | 219 | 112 s |
| assets (hospital, fire station, school, community centre, station) | 706 | 5 s |
| culverts and bridges | 1,045 | 62 s |

Rules: read the tile from the cache and never through GDAL's `vsicurl` (ADR-0006 explains why remote reads
fail on this machine). Any new download goes through `truststore.inject_into_ssl()` or the helper in
`tools/prefetch_city_cache.py`, writes into `city/cache/`, and records itself in `MANIFEST.json`. OSMnx also
needs truststore injected before its first Overpass call. `city/` is gitignored, so the cache never lands in
a commit; `make pack` copies it into the offline package.

Cached research evidence lives in `docs/research/_raw/` (about 140 files: Nominatim geocodes, Overpass
responses, IMD and BMC pages, 25 dated news articles about 1-2 July 2019). Read it before fetching anything.

## Ports, env and services

- UI :3000, API :8000 (`/docs`, `WS /v1/live`). `.env.example` lists every variable; copy to `.env`.
- FastAPI app: `services/api/varuna_api/main.py` (`uv run uvicorn varuna_api.main:app --port 8000`).
- The in-process asyncio bus (`services/cycle/varuna_cycle/bus.py`) carries topics `radar.frames`,
  `gauges.obs`, `traffic.speeds`, `reports.raw`, `tide.stage`, `runs.published`, `cycle.stage`, `alerts`.
