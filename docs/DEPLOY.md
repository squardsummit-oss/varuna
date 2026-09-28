# Deploying VARUNA

> **The finale does not depend on any of this.** SPEC.md rule 15 and section 5 are explicit: the
> demo runs from the laptop, offline, from baked runs. `make pack` is the artifact that matters on
> stage. Everything below exists so mentors, the jury and the idea submission can open a link
> before the event, and so continuous integration has somewhere to run. If the venue has no
> network, nothing here is missed.

## The split, and why

| Piece | Where | Why |
|---|---|---|
| Console and landing (`apps/command`) | **Vercel** | It is a Next.js App Router app; Vercel is its native target and the spec names it. Static pages prerender, so the site is useful even when the API is asleep. |
| API and engines (`services/*`) | **Railway** | The API is a long-lived Python process carrying numba, GDAL through rasterio, geopandas, pysteps and zarr, holding a WebSocket open and writing run artifacts to a disk. Railway runs an arbitrary container with a persistent volume, which is what that needs. |
| PostGIS and Timescale sink | **Supabase, later** | Not yet built. Spec section 4.2 makes the file store the P0 decision and the database sink a P1 upgrade. |

### Why not Supabase for the backend

Supabase is Postgres with PostgREST, auth, object storage and Deno edge functions. It is an
excellent database platform and a poor host for this particular service, because VARUNA's backend
is not a database with a thin API over it. It is a scientific compute process:

- The Twin solver is a Numba-compiled shallow-water kernel that runs for seconds per cycle. Deno
  edge functions cannot run it, and neither can a Postgres function.
- The engines need GDAL, PROJ, numba, pysteps and zarr. Those install into a container, not into a
  managed Postgres.
- `WS /v1/live` (spec section 11.11) needs a process that stays up between requests.
- Spec section 4.2 already decided the P0 store: run products are **files** under
  `data/runs/<run_id>/`, chosen for "zero infrastructure on stage, byte-reproducible bakes,
  trivial offline packaging". A database is not required to serve them, and adding one would work
  against the offline packaging the finale depends on.

Supabase becomes the right tool the moment we build the P1 sink in `services/api/sinks/postgis.py`,
which mirrors the blueprint's section 9.1 tables. At that point it hosts PostGIS and TimescaleDB
and the pitch can show the schema. Until then it would be an empty dependency.

## Vercel (console)

Project `varuna` on the `dhrishta` team, built from GitHub on every push to `main`. Production:
<https://varuna-dhrishta.vercel.app>.

| Setting | Value |
|---|---|
| Root directory | `apps/command` |
| Build command | `pnpm --filter @varuna/tokens build && pnpm --filter @varuna/command build` — the tokens package is built before the app that imports it |
| Install | pnpm, detected from the workspace lockfile at the repository root |

Environment:

| Variable | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | the Railway URL | **Not set as of 2026-09-09** (`vercel env ls production` lists only `NEXT_PUBLIC_SITE_URL`), because Railway is not deployed yet. Without it the client falls back to `http://localhost:8000`, so every data screen shows its honest "The VARUNA API is unreachable" state rather than breaking — and a visitor running the API locally on the same machine gets a working console, which is a coincidence of the fallback, not a feature to rely on. |
| `NEXT_PUBLIC_SITE_URL` | `https://varuna-dhrishta.vercel.app` | Resolves the Open Graph image. Set. |

The production alias is `varuna-dhrishta.vercel.app`; the same deployment also answers on
`command-mu-lime.vercel.app` and `varuna-git-main-dhrishta.vercel.app`. A second, abandoned
project called `command` exists on the team with two failed deployments and no alias pointing at
anything live — it is left over from the first attempt at the root directory setting (commits
`0f89bd5`, `f1f4612`) and can be deleted whenever someone is in the dashboard.

**Deployment protection.** A new Vercel project protects every deployment with Vercel
Authentication, so the production URL answers 200 but serves Vercel's own sign-in page
(`<title>Login – Vercel</title>`) to anyone outside the team. A team member logged into Vercel
sees the real site; a judge or a mentor following the link does not. The state is readable:

```bash
vercel project protection varuna --json
```

`"ssoProtection": {"deploymentType": "all_except_custom_domains"}` means it is on, and
`"ssoProtection": null` means it is off. To lift it, either the dashboard (project *Settings →
Deployment Protection → Vercel Authentication → Only Preview Deployments*, or *Disabled*, then
*Save*) or, from CLI 58.4.4 onwards:

```bash
vercel project protection disable varuna --sso
```

Earlier revisions of this file said the CLI had no command for it; `vercel project protection`
exists and that sentence was stale. **This is a publish**: it makes every production deployment
readable by anyone with the URL, so it is the team's call, not a step to run because a build
went green.

## Railway (API)

Deploy from the `Dockerfile` at the repository root. Attach a volume mounted at `/data`.

| Variable | Value |
|---|---|
| `PORT` | set by Railway |
| `VARUNA_DATA_DIR` | `/data` |
| `VARUNA_CITY_DIR` | `/data/city` |
| `VARUNA_BUNDLES_DIR` | `/data/bundles` |
| `VARUNA_CITY` | `mumbai` |
| `VARUNA_MODE` | `replay` |
| `VARUNA_BUILD_ON_BOOT` | `1` on the first deploy, then `0` |
| `VARUNA_CORS_ORIGINS` | `https://varuna-dhrishta.vercel.app` (`CORS_ORIGINS` is accepted too) |

### What is deployed, and how it was done

Deployed 2026-09-09. Project `varuna-api` (`bbb05788-6d11-4757-a1ab-2dac3fd08748`), service
`fce11314-62a8-4b4f-b403-101ce8fa167d`, production environment
`4628844f-aa32-4957-8124-db5d43e1ca55`, volume `varuna-data` (500 MB) at `/data`, public at
<https://varuna-api-production-f577.up.railway.app>.

**Attach the GitHub repo; do not `railway up`.** `railway up` uploads the working directory and
lets Railway's Railpack auto-builder guess how to build it, which ignores the `Dockerfile` this
repo ships and fails on the scientific stack. Attaching `shahul-royalss/varuna` as the service
source makes Railway read `railway.json` and build the Dockerfile. Order matters: set the
variables and attach the volume *first*, so the first build comes up already configured rather
than deploying once without `/data` and again with it.

**Railway's GitHub webhook does not fire on push for this service.** A `git push` alone will not
redeploy; re-attach the source (Railway MCP `connect-service-source`, or the dashboard's
Deploy button) to start a build. `redeploy` is not a substitute - it re-runs the *existing*
build and will not pick up a new commit.

### Three things that made this image unbuildable

All three were invisible locally, because a local checkout resolves dependencies from a warm
`uv` cache and never rebuilds them. Railway was the first clean build this image ever had.

1. **No C compiler.** pysteps publishes no Linux wheel, so `uv` builds it from its sdist and its
   Cython extensions compile with `gcc ... -fopenmp`. Fixed in the Dockerfile (ADR-0018), not
   with Railway's suggested `RAILPACK_BUILD_APT_PACKAGES` - that patches one platform's
   auto-builder and leaves `docker build` broken everywhere else, `make pack` included.
2. **`README.md` was never copied.** The root `pyproject.toml` declares `readme = "README.md"`,
   so hatchling opens it while validating the root package during the second `uv sync`. The
   dependency layer survives because `--no-install-workspace` never builds the root package.
3. **The volume would have filled.** The Copernicus/WorldCover download cache is 339 MB and
   lands on the volume beside the 116 MB the build produces: 469 MB of a 500 MB volume, and a
   volume that fills mid-build leaves a half-written city. `docker/entrypoint.sh` now drops the
   cache after a *successful* build, settling the volume near 120 MB. A failed boot keeps it, so
   the build stays restartable. Railway ties volume size to the plan; a larger volume would make
   this unnecessary.

### The console side

`NEXT_PUBLIC_API_URL` had never been set, which is why every deployed screen fell back to
`http://localhost:8000` and showed its honest "API unreachable" state. It is a build-time inline,
so setting it needs a redeploy:

```bash
printf 'https://varuna-api-production-f577.up.railway.app' | vercel env add NEXT_PUBLIC_API_URL production
```

```bash
vercel --prod
```

Run both from the **repository root**, not from `apps/command`: the Vercel project's root
directory is already `apps/command`, and running from inside it makes the CLI look for
`apps/command/apps/command`.

### The data question

`city/` and `data/runs/` are generated and gitignored, so the image ships without them. On first
boot the entrypoint downloads the public terrain and land-cover tiles and runs the city pipeline
into the volume, which takes a few minutes and about 400 MB. After that it is skipped.

The build runs in the background: the API starts at once, so Railway's health check on
`/healthz` passes within seconds rather than waiting on the pipeline (the timeout in
`railway.json` is 600 s in case the platform is slow, but it is not needed for a healthy boot).
Until the files land, every layer endpoint answers 404 with the command that fixes it, and the
router looks the files up per request, so the layers appear without a restart. If the build fails
the API stays up and the 404s stay honest — a visitor seeing "not built yet" is better served
than one seeing a container that will not boot. Both steps are resumable from the volume.

The pipeline's peak memory has not been measured on Railway. It runs numba and geopandas over
about 116 MB of Mumbai layers, so if the service is sized small and the build is killed mid-way,
raise the memory in the service settings and redeploy: both steps resume from what the volume
already holds.

### What the deployed API can serve today

The eight city layers, the run registry, the cycle status and the live socket. Forecast endpoints
answer 501 with the phase that implements them, because no run has been baked yet — Phase 5 does
that. The deployed console therefore shows the real Mumbai terrain, roads, assets, hotspots and the
inferred drain graph, with honest empty states where the forecast will go.

## Cost and shape

The API image is roughly 1.5 GB and the service is small but always on; Railway's starter credit
covers a demo-scale deployment but not a busy one. Vercel's hobby tier covers the console. Neither
is on the critical path for the finale.
