# Changelog

All notable changes to the VARUNA prototype. Task IDs refer to SPEC.md section 13.

## Unreleased

### Phase 0
- Repository initialised at `C:\dev\varuna` (ADR-0001); Python uv workspace with 12 members; pnpm workspace with the Next.js 16 command app; design tokens JSON.
- [P0.6/P0.10/P0.11] Offline network guard in `tests/conftest.py` (`VARUNA_OFFLINE=1`), repository layout test, Playwright config with both web servers and a Phase 0 smoke spec, GitHub Actions CI (web, python, e2e, Lighthouse), `lighthouserc.json`, `docker-compose.yml` (P1 profile), `.pre-commit-config.yaml`, README with make targets and the cycle diagram, `docs/API.md`.
- [P0 verification] Every Phase 0 gate verified on 2026-09-06: TypeScript, ESLint and design lint clean; 126 vitest and 335 pytest tests pass at 90 % Python coverage; the Next.js production build prerenders 21 routes; all 14 screens render with zero console errors; the Playwright smoke suite passes with both servers auto-starting; and `varuna demo` prints the "No bundle baked yet" message, starts the API and console, and answers `/healthz`, `/v1/runs`, a 404 envelope and a 501 stub exactly as the contract specifies.
- [P0.10, deploy] The repository is public at `github.com/shahul-royalss/varuna` and CI is green there (2026-09-07) after fixing the pnpm version pin conflict, applying ruff format and installing the missing turbo. The console deploys to Vercel from `apps/command` (`varuna-dhrishta.vercel.app`); the API ships as a Railway Docker image with a `/data` volume and a background first-boot city build (ADR-0014, `docs/DEPLOY.md`). Supabase is reserved for the P1 PostGIS sink.
- [P2.6, ADR-0007] The demo replay window moves to 05:40-09:40 IST on 2 July 2019, opening at 06:40, on the evidence of 29 sourced ground-truth pins.
- [P2.7] The replay clock: `services/replay/varuna_replay/clock.py` walks a bundle's window in simulated time and publishes its streams on the blueprint's bus topics; `/v1/replay/{bundles,clock,play,pause,seek,speed,bundle}` drive the one clock the API process owns; the console's replay panel, time bar and bundle cards read the API and follow `replay.clock` over the WebSocket (ADR-0013).
