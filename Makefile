# VARUNA - street-level urban flood nowcasting digital twin (SIH 2026, PS SIH26085)
#
# Every target is a one-line wrapper around `uv run varuna <task>`; the logic lives in
# tools/varuna_cli so Windows (Git Bash or cmd with the winget GNU make), Linux and macOS
# behave identically. `uv run varuna <task>` is the exact fallback when make is missing.
#
#   make                      prints this target table
#   make city CITY=chennai    variables: CITY, BUNDLE, ARGS (extra flags passed through)
#   make city-cache CITY=chennai   equivalent of `make city CITY=chennai --cache-only`

CITY   ?= mumbai
BUNDLE ?= MUM-2019-07-02
ARGS   ?=
UV     ?= uv
VARUNA  = $(UV) run varuna

# bash where it is the native shell; on Windows keep make's default so the recipes (plain
# `uv run ...` one-liners) also work from cmd.exe and PowerShell without a POSIX shell.
ifneq ($(OS),Windows_NT)
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
endif

.DEFAULT_GOAL := help
.PHONY: help setup doctor city city-cache city-terrain city-basemap workers route-rs bundle bake live train dev demo test e2e pack demo-video \
        lint typecheck typegen openapi format clean

help: ## List every target (each is also `uv run varuna <target>`)
	$(VARUNA) help

setup: ## Install pnpm + uv workspaces, pre-commit, Playwright Chromium; copy .env.example to .env
	$(VARUNA) setup $(ARGS)

doctor: ## Check tool versions, .env, engines, city layers, bundles and runs
	$(VARUNA) doctor $(ARGS)

city: ## Build city-in-a-box layers from cache (downloads on first run): city/$(CITY)/
	$(VARUNA) city --city $(CITY) $(ARGS)

city-cache: ## Download the open data for a city into city/cache/ only (Chennai pre-cache)
	$(VARUNA) city --city $(CITY) --cache-only $(ARGS)

city-terrain: ## Terrarium heightmap for the console's 3D mode, from the city's conditioned DEM (P6.15)
	uv run python -m varuna_city.terrain_export --city $(CITY) $(ARGS)

city-basemap: ## PMTiles vector basemap from the city's own OSM layers, for the offline map (P9.10)
	uv run python -m varuna_city.basemap_tiles --city $(CITY) $(ARGS)

workers: ## Copy the loaders.gl decoder workers into apps/command/public/workers/ (no CDN at run time)
	pnpm --filter @varuna/command run workers

route-rs: ## Build the Rust routing service and export the graph it reads (P8.12)
	cd services/route-rs && cargo build --release
	uv run python services/route-rs/tools/export_graph.py --city $(CITY) $(ARGS)

bundle: ## Generate a replay bundle (storm designer, synthetic streams, curated ground truth)
	$(VARUNA) bundle --bundle $(BUNDLE) $(ARGS)

bake: ## Pre-compute every 5-minute cycle of a bundle into data/runs/
	$(VARUNA) bake --bundle $(BUNDLE) $(ARGS)

live: ## One live cycle from today's rain ensemble and sea level (ARGS="--every 30" to keep running)
	uv run python -m varuna_cycle.live --city $(CITY) $(if $(ARGS),$(ARGS),--once)

train: ## Fit Flash-lite from the Twin runs in data/train/ (P1: train the GNN)
	$(VARUNA) train --city $(CITY) $(ARGS)

dev: ## API on :8000 and Next.js on :3000, replay paused, Compute live on (VARUNA_COMPUTE_LIVE=0 turns it off)
	$(VARUNA) dev $(ARGS)

demo: ## Full demo: API + UI + replay of the default bundle at 30x from baked runs, Compute live on
	$(VARUNA) demo --bundle $(BUNDLE) $(ARGS)

test: ## pnpm lint, typecheck, test, lint:design, then pytest with coverage
	$(VARUNA) test $(ARGS)

e2e: ## Playwright demo-script test (starts API and UI itself)
	$(VARUNA) e2e $(ARGS)

pack: ## Offline package: baked runs, tiles, Chennai cache, fonts, basemap
	$(VARUNA) pack $(ARGS)

demo-video: ## Record the demo path with Playwright video (fallback)
	$(VARUNA) demo-video $(ARGS)

lint: ## ESLint + design lint for the app, Ruff for Python
	$(VARUNA) lint $(ARGS)

typecheck: ## tsc --noEmit for the app, mypy for the task runner and schemas
	$(VARUNA) typecheck $(ARGS)

typegen: ## Export OpenAPI from FastAPI and generate apps/command/lib/api/types.ts
	$(VARUNA) typegen $(ARGS)

openapi: ## Write apps/command/openapi.json from the FastAPI app
	$(VARUNA) openapi $(ARGS)

format: ## Prettier for the app, Ruff format for Python
	$(VARUNA) format $(ARGS)

clean: ## Remove build outputs and caches (.next, dist, .turbo, pytest/ruff caches); never data
	$(VARUNA) clean $(ARGS)
