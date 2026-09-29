# VARUNA — Prototype Build Spec

**Street-level urban flood nowcasting digital twin · SIH 2026 · PS SIH26085 (Ministry of Earth Sciences)**
**Team: VIT · Spec version 1.0 · 4 September 2026 · Source: `docs/VARUNA_SIH2026_Blueprint.pdf` (blueprint v1.0, 2 Sep 2026)**

> "Every street. Three hours early."

This file is the single source of truth for building the VARUNA prototype. The blueprint PDF is the *science* reference; this file is the *build* reference. It scopes the blueprint's "V1" into something that can actually be built, demoed on a laptop, and judged — and it tells you exactly how every screen must look, move and behave. When the two disagree on scope, this file wins.

---

## 0. Operating rules (read before touching code)

1. **Read this whole file first.** Then copy the blueprint PDF to `docs/VARUNA_SIH2026_Blueprint.pdf` and read Sections 5, 6, 9, 10, 13 of it when you implement the matching engine or screen.
2. **Work phase by phase (Section 13).** Do not start a phase until the previous phase's exit criteria are all checked. Inside a phase, tasks may be done in any order.
3. **Checklist protocol.** `- [ ]` = not done · `- [x]` = done (append `(YYYY-MM-DD, <short commit hash>)`) · `- [-]` = deliberately skipped (append the reason). Never delete a task. After every working session update the STATUS BOARD (Section 1).
4. **Definition of done** for any task: code written → tests pass (where testable) → wired into the demo path → checkbox updated → `make demo` still green → committed with message `[<task id>] <what changed>`.
5. **The demo path is sacred.** `make demo` must always bring up the full replay demo without errors. If a change breaks it, fixing it is the next task.
6. **No fake numbers.** Every number on screen comes from run artifacts produced by the engines in Section 11. Simplified physics is fine; hard-coded results are not. Every simplification versus the blueprint is listed in `docs/SIMPLIFICATIONS.md` and, where the user can see it, labelled in the UI (e.g., "reduced-order emulator", "reconstructed replay").
7. **No fabricated ground truth.** Every ground-truth pin, gauge total or event fact carries a `source_url`. If you cannot find a public source, leave it out. Synthetic data is allowed and must be labelled `synthetic` in the data and in the UI.
8. **Determinism.** Every synthetic generator takes a `seed`. The demo bundle is generated with `seed=2019`. Two runs of `make bake` on the same inputs produce byte-identical products.
9. **The design system (Section 6) is law.** No raw hex values in components, no fonts outside the token set, no motion outside the catalogue (Section 8) without adding it there first. Run `pnpm lint:design` before marking a UI task done.
10. **Copy rules (Section 6.8) apply to every string** — buttons, empty states, tooltips, toasts, alerts.
11. **When blocked** (missing API key, dataset not downloadable, library won't install), implement the fallback listed in Section 17, note it in `docs/DECISIONS.md`, and keep moving. Ask the human only for API keys, paid infrastructure, or real-device WhatsApp setup.
12. **Decisions are documented**, not debated: `docs/DECISIONS.md`, ADR style, five lines each (context, decision, alternatives, consequence, date).
13. **Performance budgets (Section 14) are acceptance criteria**, not aspirations.
14. **Before marking anything done:** `pnpm lint && pnpm typecheck && pnpm test && uv run pytest`.
15. **Never open a terminal on stage.** Everything the demo needs is a button in the UI or a `make` target run before the judges arrive.

**Starting sequence:** read this file → `make setup` → fill the STATUS BOARD date → start Phase 0.

---

## 1. STATUS BOARD (updated after every working session)

> **Progress is the share of section 13's tasks that are ticked or deliberately skipped, nothing more.** A phase at 100 % can still miss acceptance criteria and budgets - phases 6, 8 and 9 all do - and the notes name every one. Read the notes, not the number.

| Phase | Name | Progress | Last updated | Blockers / notes |
|---|---|---|---|---|
| 0 | Foundation, shell, design tokens | 100 % | 2026-09-09 | All gates green: typecheck, ESLint, design lint, 146 vitest, 1058 pytest, Next build 21 routes. **Both halves are deployed and talking to each other**: console at `varuna-dhrishta.vercel.app` (public), API at `varuna-api-production-f577.up.railway.app` (Railway, Dockerfile build from GitHub, 500 MB volume at /data). All eight Mumbai city layers serve 200, the replay bundle generates on boot (`built: true`, 49 cycles), and `/v1/nowcast/rain` computes a real 20 x 36 Sky cycle in ~15 s. Six defects had to be fixed before the image would build at all - see ADR-0018 and `docs/DEPLOY.md`. **Deployed through Phase 7 on 2026-09-10** (commit 709f2e7): every Phase 5-8 endpoint serves 200 from Railway - raster, segments, hotspots, surcharge, alerts + CAP, pumps, drains/health, drains/health.csv, observations and whatif - against the seven demo cycles seeded from `demo/runs`. Two deployment defects found and fixed on the way: Railway auto-deploy never fired because the service had no branch bound (re-binding the source is what triggers a build), and the Vercel build had no `NEXT_PUBLIC_API_URL`, so the deployed console was calling `http://localhost:8000` on the *visitor* machine - invisible on a developer laptop where one is listening (ADR-0024). **Down and redeployed 2026-09-16 (13f5aab):** every Railway deployment had been `REMOVED` since 15 Sep 19:06 UTC - a clean stop, not a crash - so the API domain answered 404 "Application not found" and the deployed console had no backend. `railway up` brought it back: `/healthz` 200 in 0.53 s, the seven demo cycles served, and `/v1/verification` answering in 0.52 s from the score kept since start (ADR-0058). |
| 1 | City-in-a-box (Mumbai) | 100 % | 2026-09-12 | **Two of the ten hotspots section 3.3 names could never flood** (ADR-0039). Khar Subway and Parel / Bharat Mata Cinema sit on an OSM building footprint, and the register was resolved *after* the burn, so both were raised 5 m and — because the building mask is also the solver's blocked mask — given no flux at all. Nothing failed: a hotspot that cannot flood looks exactly like one that did not. Fixed by resolving the register before the burn; 11 sink cells cleared, **0 register points blocked or raised**, down from 2 each. **Every number in the previous row was also stale** (it predated ADR-0019). Re-measured: 198.7 s from cache against the 10-minute budget, 21,296 segments, 10,466 units (100 % inside the 0.5-2 ha cap), 49,897 drain nodes, 49,770 edges, **1,716.34 km** inferred pipe, depressions explain **82.1 %** of 28 register points (76.9 % of the 13 sourced) against 60 %, connectivity 100 %, 1,498 spurious pits breached and 2,493 kept as larger. All eight layers served and rendering. **Baked runs predate the fix and need re-baking before any depth at Parel or Khar is quoted.** |
| 2 | Replay bundle & storm designer | 100 % | 2026-09-22 | All three bundles build and validate (**13 contract rules** on the reconstruction, 11 on a design storm, 0 warnings) and rebuild byte-identically across 96 files (rule 8). The validator was checking 10 rules and **none of them read section 10.2's column lists**, so a bundle missing `mm_5min`, `stage_m` or `baseline_kmh`, or a radar cube labelled `mm/h`, passed; B11-B13 close that. MUM-2019-07-02 calibrated to the documented gauge totals for 05:40-09:40 IST; 29 sourced ground-truth pins; synthetic gauges, tide, traffic and reports all labelled. Replay clock drives play/pause/seek/speed; the replay screen animates the radar preview (M25). P2.9 (IMD PNG decoder) stays P1. **2026-09-15: the tide is read in the DEM's frame** (ADR-0055, 8293f99). `tide.csv` stays in chart datum as sourced; `manifest.tide_datum` puts mean sea level 2.70 m above chart datum (PSMSL station 43, range 2.66-2.73 m) and the Twin subtracts it once, so the window's stage is -2.655 to +1.236 m rather than 0.045 to 3.936 m. Regenerating the bundle changed only `manifest.json` (95 of 96 files identical). The EGM2008-to-local-MSL residual is not quantified, `varuna city audit-gravity` does not convert yet, and every baked run predates the change. **2026-09-22: P2.9 landed, so the phase is 10/10** (ADR-0064). The IMD decoder reads the colour bar out of the image rather than carrying a palette, fits the range rings for its georeference, and refuses a layout it does not know. Two things the task's wording got wrong and the code does not: IMD serves **GIFs**, and the Colaba legend has **4** dBZ classes, not 5 - the decoder outputs the legend's own. Measured on one archived IMD image (cached, never committed - it is IMD's): 7.3 % of Sky cells unobserved because every pixel in them was a spoke, ring or coastline. **It is not wired into Sky**: a nowcast needs three frames ten minutes apart, and `qc.coverage_mask` still assumes the radar sits at the domain centre while Colaba is 19 km from it. |
| 3 | VARUNA-Sky | 100 % | 2026-09-26 | **All eight tasks done, and the stage budget is met in steady state** (ADR-0075). Sky's 20 members run across four worker processes, each group seeded from pySTEPS' own per-member chain so the split cube equals the single call element for element, and `make bake` warms the pool before its first cycle. Steady state measured **2.30 s** in the verification pass and **2.4-5.6 s** across two bakes of the seven demo cycles on 2026-09-26, against 5 s; a single-cycle re-bake straight after the laptop woke read 6.0 s. The first cycle in a fresh process runs sequentially at **8.8 s** while the pool warms, which is what a cold "Compute live" pays. Section 11.1's third test passes at a median **5.4 %** over five storms (15 % allowed), after correcting the lead times pySTEPS interpolates in dBR - a geometric mean in rain, which put every odd lead 10-13 points low. **That pass is close to true by construction**: lead 0 is a half step whose lower neighbour is the analysis the test compares against. The whole-step deficit (6-13 % at ten minutes, past 30 % by two hours) is the STEPS cascade's own, untouched, and has its own test. The timing test was loosened during the work and restored before commit; it now also asserts the pooled cycle beats the sequential one. P2.9's IMD decoder is still not wired into Sky. |
| 4 | VARUNA-Twin + drains + coupling | 100 % | 2026-09-26 | **The mass balance is inside budget on every shipped cycle: 0.00012-0.00066 %** against 0.1 % (1.2e-06 to 6.6e-06 over the seven demo cycles re-baked 2026-09-26; 08:40 is bit-identical to an independent re-run). The 2026-09-23 "regression" was a reveal (ADR-0072): ADR-0055's tide stopped admitting 5.35 Mm3 of sea the city should never have received, which had been hiding two exchange leaks - surcharge the surface received that the drain never emitted, and surcharge onto building cells destroyed before it was tallied. Both are fixed, and `run.json` now carries the ledger that located them (each solver's own residual and both exchange gaps). A cache keyed on `id()` could hand one drain network another's outfalls and say nothing (ADR-0073). **P4.6 stays missed**: the coloured drain scatter is 3.5x faster on its own step (ADR-0074), and a 3-hour run is still **34-60 s** on a quiet machine against 8 s - the shipped runs record up to 192 s, because they were baked with test suites running beside them - because the drain is now 42 % of the run and Amdahl caps what colouring can give at 1.72x. **P4.8**: 5 m nests at Hindmata and King's Circle conserve to 1e-15 and impose the parent's depth, not its level, at their edge - level put 18 Mm3 into a 1 km2 window (ADR-0078). A nest is quoted for pattern only: the bicubic and bilinear DEMs disagree about the sign at Hindmata, and nothing calls a nest yet. **P4.9**: the SWMM adapter measures the reduced model - flows agree to 1e-16, depths are 4.5x low from its full-bore hydraulic radius - and SWMM itself misses the 0.1 % budget at -1.9 % on a cold start, so it does not replace `drain1d` (ADR-0079). **R3's data is on disk**: every cycle stores 500 reversed edges with 0 lacking geometry, 6 of 7 put reversed flow at the tidal outfall, and 2,762-8,932 nodes surcharge. |
| 5 | Products, cycle, API | 100 % | 2026-09-13 | Depth rasters, segment forecast, hotspot ranking, surcharge, alerts and the pump plan all written atomically per run. A run's console-facing payload is 1.7 MB, down from 20 MB: the 19 MB parquet stays the product of record and the cycle also writes the compact `segments_wet.json` the map actually draws. **`make bake` was a phase-gate stub until 2026-09-13 (7b941f5)** while P5.6 was ticked; it bakes now, oldest first and resumable, and `varuna cycle` runs one cycle. |
| 6 | Command console (core UI) | 100 % | 2026-09-22 | **The map has a basemap and labels now.** Esri aerial imagery under everything, treated rather than shown raw - drawn dim under an `--ink` scrim, so the city is a ground the water sits on rather than a photograph the water is drawn over. Two label layers over it: Esri's localities and arterials, and VARUNA's own street names, hospitals, fire stations and chronic junctions, zoom-gated with the cap applied to what is *in view* (ADR-0034). Also landed: the replay's cycle log, which was wired to an empty array over seven baked cycles, and a mass-balance formatter - `formatPct` rounds to whole percent, so every row read "0 %" and the column said nothing about the number it exists to report. That immediately surfaced the 06:40 cycle at **0.18 %**, over the 0.1 % budget (**0.215 %** after the 2026-09-13 ensemble re-bake; the other six 0.014-0.091 %). P6.15 (3D) is outstanding; P6.5, P6.9 and P6.12 were ticked after this row was first written. **2026-09-15: the surcharge pulse (M8) and reversed-flow dash (M9) run on deck's animation clock** (ADR-0053): no React render per frame, byte-still under reduced motion. The dash has nothing to draw until the runs are re-baked with reversed-edge geometry (ADR-0052), and the layer panel says so. Idle fps on the Iris Xe with the pulse on measured 43.7-58.3 on a dev build under contention, against 59.7-60.0 with it off: marginal against 55, so neither section 14's frame-rate budget nor 7.2's frame-rate AC is ticked. **2026-09-22: every task is done (15/15), and four of section 7.2's acceptance criteria are still not.** P6.15 3D drapes the depth raster over a Terrarium heightmap exported from the city's own DEM (ADR-0065); R, I, 3 and W are wired through the shortcut registry and the `?` overlay no longer calls them "coming in pilot"; the segment popover's "why" link reaches the drawer; and "Compute live" runs a real cycle with per-stage milliseconds over the socket. **Measured and missed:** the live cycle takes **141 s** (274 s contended) against 15 s, so it is gated by `VARUNA_COMPUTE_LIVE` and off where it is deployed; map frame rate **57.8 fps flat, 54.2 contended, 50.4 in 3D** against 55; scrub restyle p95 **26.1 ms** against 16 ms; no reversed-flow edge can draw because every run predates the geometry (ADR-0052) and the panel says 20,270 pipes run backwards with none drawn; and the drawer still refuses attribution, which is P7.7's to fix (ADR-0042). What did pass: 0 network requests during a 36-step scrub, 0 console errors across a full replay, and the ground-truth pins dropping on the clock with their sources. **2026-09-23: the 3D ground is no longer ours.** P6.15's Terrarium heightmap is replaced by Google's Photorealistic 3D Tiles (ADR-0069): `layers/terrain.ts` is deleted, every VARUNA layer drapes onto the photographed city through the same extension, and the depth raster is now draped rather than dropped. Mumbai has coverage to a **2.006 m geometric error** at Hindmata, traversed from the global root through six nested sub-tilesets. It is **slower than what it replaced**: **25.2-26.1 fps at street level and 11.4-11.9 fps at the AOI fit** against 44.9 flat and a budget of 55, so 3D stays off by default. It also needs the network and a billed Google project, where the heightmap needed neither. The **drain X-ray** (ADR-0070) is what the ground exists for: the surface fades to 20 % (M28) and the 49,770 inferred pipes draw at their own invert elevations, with `depthTest: false` because otherwise Google's opaque mesh hides them as it refines. |
| 7 | Pulse, Flash-lite, drain X-ray, what-if | 100 % | 2026-09-26 | **Eleven of twelve tasks done; P7.12 (GNN) is not built** - it needs torch and PyTorch Geometric and at least 500 Twin runs, 5-8 hours of compute on this laptop. **P7.6**: the street ensemble is 50 members with the Sky axis covered rather than resampled (resampling moved the band 29 % between seeds), blockage drawn from this cycle's posterior - Pulse now runs before Flash - and every run prices its own axes: at 08:40 the band is 16.3 cm, 9.9 from the weather and 8.9 from the parameters, and switching the blockage draw off moves it -0.14 cm (ADR-0076). **P7.7/P7.11**: attribution runs on `drain1d` on each junction's own catchment and is baked into the drawer, ranked by magnitude with the sign kept (ADR-0071). Hindmata refuses at every cycle - the best pipe moves it 0.015-0.027 cm, because its inlets and not its pipes limit it - and at 08:40 cleaning Sion Subway 1's fourteen pipes makes it **2 cm deeper** (9.6 to 11.6 cm), because it is already surcharging. Section 7.2's "cleaning these 14 pipes: 55 → 20 cm" exists at no cycle and is not claimed. Attribution costs 24-55 s per storm cycle against the products stage's 2 s. **P7.8**: the physics check runs the coupled Twin twice on a 990 m crop and compares changes, not levels, in 2.4-4.7 s warm and ~20 s on a process's first call (ADR-0077); `/whatif` and the console drawer print it. At rain +30 % on 08:40 it read 5.07 cm against a 5 cm tolerance - outside, and said so. Still open: Pulse's reduced observation operator predicts 54 m of water at one street every cycle, and P7.3's posterior is still re-analysed from the prior each cycle rather than carried. |
| 8 | Route, reachability, alerts, pumps | 100 % | 2026-09-22 | Route, reachability and the road-conditions feed landed on 2026-09-10 (ADR-0028); this pass finished the screens. **The route planner could not be scrolled** - its right column was a plain flex column in an `overflow-hidden` page, so a result with an avoided list and two alternates grew past the viewport with no way to reach it. Avoided streets now come back with their own geometry and are drawn in `--depth-5`, so the detour visibly goes around something, and the VARUNA route draws itself along the road over 1.2 s (M14). **Alerts and pumps were showing the quietest cycle of the storm** - both loaded the newest run, 09:10 IST, where one pump is worth sending; at 08:40 the same optimiser assigns all twelve and avoids 925 minutes above 45 cm. Both take the console's cycle picker now. **Drag-to-assign** (P8.10) existed only as a comment saying it did not; dnd-kit with `layoutId` so the card flies to its column (M17), and the board says plainly that a moved pump invalidates the optimiser's benefit figure until the emulator can recompute one. The console's Alerts and Pumps tabs mirror their pages (P8.11). **Outstanding:** the real WhatsApp sender (P8.8, P2) and the isochrone morph (M15). **2026-09-22: 12/12 tasks.** P8.12 is a Rust Axum service that is a line-for-line port, held to the Python router by a parity test - **72 of 72 trips identical, no tolerance** - at 2-3 ms p95 on the demo trip against Python's 62-73 ms, with contraction hierarchies serving only the dry-weather search, where they are exact (ADR-0063). P8.8 finishes the alert centre: cross-cycle hysteresis as section 11.10 actually words it, `config/escalation.yaml`, a delivery log, acknowledge and escalate that survive a reload, and real Twilio and WhatsApp Cloud adapters that activate only when their keys are set (ADR-0068). Pumps re-price a hand-moved plan through the emulator. **Measured and missed:** reachability p95 **3,432 ms** against 2 s; Optimise's first call after a restart 1,669 ms against 1 s (157-554 ms warm); **KEM to Sion avoids nothing for an ambulance on any 2 July cycle**, so R6 stays a comparison rather than a diversion; the pedestrian's `h * v` half cannot fire because no product carries a flow speed, and both the router and the response say so rather than assuming one; and no real WhatsApp or SMS has been sent, because there are no keys here. |
| 9 | Landing, public map, report, onboarding, verify | 100 % | 2026-09-22 | **City-in-a-box did not work on the deployed site, and said the wrong thing about why** (ADR-0038). The live job reported `failed` at `build_graph`, 83 %, when what failed was `cache` - the first of fourteen steps. Two defects: `run_city` does not stop at a failed step, so the remaining thirteen each failed and each advanced the bar, and the job named the last step rather than the broken one; and the Copernicus and WorldCover tiles it wanted were deleted on purpose by the entrypoint's `drop_download_cache`, because a 500 MB volume cannot hold the 339 MB cache beside the city it builds. `from_cache_only` was decorative - stored, logged, never read - and passing it to `run_city(cache_only=...)` would have been worse, since that parameter means the opposite ('run only the fetch steps'). The job now warms its own cache: warm means no network at all (the stage path), cold means either an honest refusal naming the prefetch or a fetch of just that city's tiles, with a free-space check first and a release after. `VARUNA_OFFLINE=1` overrides either way. **Chennai onboards end to end in 65.4 s locally, 15,468 wet segments - the same count R8 recorded, so the build is still deterministic.** Earlier in the phase: the landing page (P9.1), its performance (P9.2, LCP 1.6 s), verification (P9.7), the public map (P9.3) and the report flow (P9.4). **Outstanding:** the API explorer (P9.8, P1), i18n (P9.9, P1) and the PWA (P9.10, P1). **The deployed API still needs a redeploy for this fix to reach the live site.** **2026-09-22: 10/10 tasks.** `/api` lists all 60 paths with three presets pinned to the 08:40 cycle and never sends a write with a passphrase (P9.8); the public map and report flow run in English, Hindi and Marathi, with Devanagari loaded only when it is chosen and 0 axe violations at 390 x 844 in all three - **the Hindi and Marathi are drafts awaiting a native speaker** (P9.9, ADR-0067); and the public map is installable and works with no network, drawing the last forecast it loaded over a PMTiles basemap built from VARUNA's own OSM layers, queueing an offline report in IndexedDB until the connection returns (P9.10, ADR-0066). Esri's imagery is never cached: it is not ours to redistribute. The basemap and the terrain heightmap are built by `make city-basemap` and `make city-terrain`, which a deploy or an offline package has to run. |
| 10 | Polish, rehearsal, packaging | 80 % | 2026-09-26 | **Eight of ten. P10.7's take is recorded and only its USB copies remain; P10.10 needs people.** **P10.2** is a test: `tests/e2e/design-qa.spec.ts` walks all 17 routes, writes `docs/screens/`, and asserts no horizontal scroll at three sizes, a visible first focus ring, no placeholder copy and zero console errors; its first pass fixed six defects, and 6.11's four judgement lines are annotated for a person on every run (ADR-0080). `/dashboard` still logs Google's `InvalidKeyMapError`, which is the key (D-26), not the screen. **P10.4**: every section 14 browser and API budget has a verdict from a repeatable run (ADR-0081). Met: LCP 160-212 ms, map 57-60 fps at 1440 x 900 on the Iris Xe, tab memory 676-959 MB over a full replay, route 64-82 ms, what-if 301-581 ms, physics check 4.1-7.4 s. Missed: the console's first render (run stamp at 1.0-1.8 s, then 3.6-5.7 s of blocked main thread), the scrub (the key handler alone 19-21 ms against 16) and `GET /v1/nowcast/segments` (507-553 ms). Fixed since: the segments route honours `bbox`, gzip runs at level 6, and Google's 3D Tiles loader loads only when 3D is switched on. **P10.7**: `make demo-video` records one 1080p take paced to section 15's clock in a headed browser and writes it beside the repository, never into it; the 8 min 36 s take of 2026-09-26 has every beat. **The seeder that deleted two complete re-bakes is fixed** (ADR-0082): every API start copied the shipped runs over any fresh bake whose alert files differed. |

| C | Citizen dashboard, authority desk, rural advisory (`PRD.md`, `TASKS.md`) | 95 % | 2026-09-19 | **`/dashboard` exists and works, and the Google basemap it was built for is dormant.** Measured: the supplied browser key carries an HTTP-referrer restriction listing neither development origin - `RefererNotAllowedMapError` from Maps JS v3.66.4d at both `127.0.0.1:8899` and `localhost:3000`, `google.maps` loaded, zero tiles. Two defects came out of opening the screen rather than reading the code: an auth failure reaches an application through **neither** the provider's `onError` nor the bootstrap timeout, only through `window.gm_authFailure`, which nothing installed - so the reader got Google's grey "Oops! Something went wrong" where the map belongs; and even with that fixed Google paints that surface before the callback fires, so its map is now covered by the app's own background until `onTilesLoaded` says it drew something. The screen falls back to VARUNA's own Esri-and-deck map, labelled, with identical water and routes (ADR-0059); D-25 is the human step that wakes Google up. **The router got six times slower and nobody noticed**: making `P(h > threshold)` real (D-01) put a dict resolution and a `timedelta` inside a loop that runs twenty thousand times a route, taking the KEM-to-Sion demo trip from 85 ms to 531-562 ms against a 300 ms budget. cProfile named it; hoisting the tables and answering the step by integer division gives **67.6-102.5 ms**, with the response identical field for field apart from `ms`. **The spreading policy gave its largest share to the slowest road** - 0.4038 to a 23.9-minute corridor against 0.2458 to an equally safe 13.5-minute one - because `TECH_SPEC` 3.3 summed capacity over edges and a sum rewards length; it is the bottleneck over the legs divided by travel time now, and the order inverted (ADR-0060). Live weather serves from the deployed API (29.0 C, light drizzle, 2026-09-19 13:30 IST) and degrades to a stamped copy with its age (ADR-0061). **`/authority` and `/rural` landed.** The desk's loop was driven in a browser by the team as well as its builder: a closure typed into the screen took the KEM-to-Sion ambulance route from 5.6 min with nothing avoided to 6.1 min around Dr Babasaheb Ambedkar Marg, carrying the officer's own words. `/rural` is **4,281 bytes** in its worst state against a 30 KB budget, with no client JavaScript at all. `/dashboard` and `/rural` were then read on the deployed site. **Still open:** 24 of TASKS.md's 25 items are done and the twenty-fifth is D-25, a human step in the Google Cloud Console; M27 measures **41.2-42.1 fps against section 14's 55** on a harness with nothing else on screen; `GET /v1/alerts` does not yet reflect an acknowledgement; the deployed `/authority` could not be read in my own browser, which blocked 54 of its resources while curl gets 200 and the page renders locally. Six parallel chunks merged with zero conflicts against agreed component seams; the contract went 46 -> 52 paths in two regenerations. **2026-09-23: D-25 is closed, and closing it was a finding.** The key this build carried was **MCGM's**, scraped into `.env.local` - `docs/research/_raw/mcgm_main.js:17` holds it byte for byte - so the 2026-09-19 `RefererNotAllowedMapError` was somebody else's referrer list refusing us, D-25 was impossible for this team to do, and every call spent MCGM's quota. The team issued a key on their own project with billing linked and both APIs on; it took three projects, because an unbilled project answers every Map Tiles method with a bare `404 NOT_FOUND` and only Static Maps says "You must enable Billing" out loud. **D-26 replaces it and blocks a deploy:** the new key carries no referrer restriction and answers Static Maps, Geocoding and Directions as well as Map Tiles, and it is a `NEXT_PUBLIC_*` value inlined into every visitor's JavaScript. |

**2026-09-28 (a98b8a5, ADR-0085 to ADR-0092).**
- Every screen is named in Sanskrit with its English gloss: Drishti, Nadi, Marga, Sanket, Jalayantra, Kalpana, Smriti, Pramana, Pravesh.
- No screen prints "Unnamed road". Streets without an OSM name read "off X" or "near Y".
- Every map opens on the flood. The console has a Full view (F).
- Jalayantra draws the dispatch as lorries on their roads (M33-M35).
- The team's logo is the brand.
- The coastline is a sea held at the tide, with a 2.72 m wall behind 674 intertidal cells that no street reads. Assumption: 54 cells are trapped behind the wall.
- Sky's motion no longer anchors on the radar's range edge. It moves the storm at 0.71-1.15 of its true speed, up from 0.17-0.36.
- Mumbai was rebuilt, Flash-lite refit, and the seven demo cycles re-baked at 0.000 % mass balance, with land peaks of 19-163 cm.
- Chennai onboards in 206 s from cache.
- **The pin scores fell and are reported as they are:** CSI 0.103 / POD 0.176 at 15 cm, against 0.22 / 0.41 before. At 5 cm POD is 1.0 with a 73-minute median lead.
- Rain CSI at 20 mm/h loses to persistence from +5 min, so Pramana says "No useful lead" and the 90-minute line is not claimed.
- The deployed Pramana rain tab shows its "not scored" state, because the shipped runs omit the 15 MB rain cube.

**2026-09-29 finishing pass.**
- **Every screen was checked in headless Chromium at 1440 x 900 and 1366 x 768** (/map and /report at 390 x 844 too): axe WCAG A/AA 0 violations, no horizontal overflow, no console errors, a visible first focus ring and no "Unnamed" text.
- **Fixes from that pass:**
  - The ward desk and Marga open on the storm cycle.
  - The public map and the landing hero frame the flood.
  - Pravesh says "Loading" instead of "Press Start" over a finished build, and has its legend.
  - The landing and Pramana POD notes no longer claim early warning the scores do not show.
  - The report flow drops a placeholder map that invited the reader to adjust a point it never drew.
  - Jalayantra's review defects are closed with tests, including the timeline drawn 5 minutes early and the gauges running ahead of the lorries after a cycle round trip.
  - /design has stories for 114 of 119 components, with a test that fails on a missing one.
  - Pramana's rain tab works on the deployed site from `demo/verification/MUM-2019-07-02.rain-skill.json.gz`, which `varuna verify` writes and the API serves only when its run ids and run.json digests match the seeded runs.
- **Gates:** vitest 1,806/1,806, ESLint clean, design lint clean, Playwright design-QA 34/34.

**2026-09-30 (ADR-0093).**
- Pravesh draws Chennai's streets coloured by depth instead of 30 m squares, from a shipped copy of the street layer when the server's is older. Two layer switches, no helper text, and a "Deepest streets" list.
- Drishti's layer panel drops the depth raster, isochrones and routes rows and every count. R and I still work from the keyboard.
- Citizen reports reach Drishti within one 30 s poll, as pins, a list and a toast.
- A "right now" card shows live weather and live sea level from Open-Meteo, labelled live, not the replay.
- Light mode is monsoon mist, a blue-grey tint rather than white, with every text pair at 4.5:1 or better.
- **Gates:** vitest 1,925/1,925, tokens 41/41, ESLint clean, design lint clean.

**2026-09-30 later (ADR-0094): the flood map is live.**
- A full cycle runs on today's weather: DWD ICON-EPS rain through Open-Meteo (20 members, NWP, no radar), live sea level, and the citizen reports in the inbox, through the same Twin, Flash-lite, Pulse and products as the replay.
- Measured on the laptop at 44 s per cycle; the hosted time is recorded per run.
- Drishti, the public map and the citizen dashboard open on the live run while it is under 3 hours old.
- A dry forecast says "No street is forecast above 15 cm in the next 3 hours" and links to 2 July 2019 at its peak (08:40, scrubbed to the most flooded step).
- `/console?bundle=MUM-2019-07-02` still opens the demo's 06:40.
- **Where it runs (ADR-0095):** the hosted API's trial container cannot fit a 1.5 GB cycle beside the API, and the first attempt restarted it. The cycle runs on GitHub Actions (`.github/workflows/live.yml`, scheduled every 15 minutes, which GitHub runs late or skips) and is uploaded to `POST /v1/runs/upload` behind a token.

**Demo readiness (the eight things that must be true when judges arrive — mirrors blueprint §11.3):**

- [ ] R1 A Mumbai replay streams through the same pipeline as live data; the mode banner reads "Replay 30×" and the run stamp says "baked" or "live".
- [ ] R2 Sky produces a 20-member, 3-hour rain ensemble every cycle; the skill-versus-lead-time chart exists on `/verify`.
- [ ] R3 Twin runs coupled 1D–2D on the city grid with visible surcharge at manholes and reversed flow at a tide-locked outfall. (**The data exists**, re-baked 2026-09-26: every cycle stores 500 reversed edges with **0 lacking geometry**, 6 of the 7 put reversed flow **at the tidal outfall**, with 18,382 to 22,082 reversed edges and 2,762 to 8,932 surcharging nodes per cycle, and the coupled mass balance is 0.00012-0.00066 %. Still unticked: "visible" means seen on screen, and it has not been checked on the new runs.)
- [ ] R4 Flash-lite returns a 50-member street forecast in under a second; the probability layer and what-if work; "Physics check" agrees within the stated tolerance.
- [ ] R5 Pulse assimilates two observation types on replay and visibly changes a hotspot forecast; the drain-health map renders.
- [x] R6 Route returns ambulance/bus/car routes that avoid predicted impassable segments; hospital reachability isochrones shrink along the time slider. (2026-09-10 - the detour is exercised in the test suite and on the deployed API; on the 2 July runs the KEM-to-Sion corridor stays under an ambulance's 60 cm, so the demo trip is a comparison rather than a diversion. Say that out loud rather than picking a trip that diverts.)
- [ ] R7 Command shows alerts (CAP + WhatsApp mock), pump dispatch with expected benefit, attribution, and a verification score for the event.
- [x] R8 The Chennai onboarding wizard runs on stage in minutes (from cache) and produces a first, uncalibrated forecast. (2026-09-10 - 36 s from cache; the first forecast wets 15,468 of 18,622 segments with a median street peak of 26 cm. Say "uncalibrated" out loud: Chennai has no curated hotspot register and no observations, so every pipe is still at its prior.)

---

## 2. What we are building

### 2.1 The one-liner

VARUNA is a self-correcting digital twin of a city's water — sky, surface and sewer — that turns Doppler-radar rainfall into street-by-street depth forecasts for the next three hours, learns its hidden drains from every flood it sees, and tells emergency services which street will be impassable, when, and how to get around it.

### 2.2 The four deliverables the ministry asked for (name modules after them so the jury can tick boxes)

| PS clause | VARUNA module | What the prototype actually does |
|---|---|---|
| Fuse real-time rainfall nowcasts with a high-resolution DEM and a graph-based drainage model | **Sky + Twin** | Radar frames → calibrated rain ensemble → 2D local-inertial routing on a hydro-conditioned 30 m DEM, coupled to a 1D drain graph |
| Route the rain volume across a 2D surface terrain model, instantly | **Twin** | Numba-compiled shallow-water solver; a 3-hour city run in seconds on CPU |
| Directed drainage graph: capacity, surcharge, backflow, blockages | **Drain graph + Pulse** | Synthetic drain graph inferred from roads + DEM; Manning capacity; head-driven 1D routing with surcharge and backflow; blockage factors learned by an ensemble Kalman filter from traffic anomalies and citizen reports |
| Dynamic web GIS dashboard, street-by-street, depth in cm, 0–3 h | **Command** | Next.js + MapLibre + deck.gl console with time slider, probability mode, hotspots, alerts, pumps, what-if |
| API for flood-safe routing usable by navigation apps, transit and commuters | **Route** | FastAPI time-dependent routing with vehicle profiles, reachability isochrones and a road-condition feed |

### 2.3 Who judges this, and what they reward

- **MoES/IMD/NCCR scientists**: physical correctness, honesty about uncertainty and data, knowledge of Indian sources (IMD DWR, BMC AWS, INSAT-3DS, CartoDEM, IFLOWS). They punish hand-waving.
- **General SIH jury**: does it work end to end, is it beautiful, is it obviously useful, is the team credible.
- **Both**: no terminals, no placeholders, no "we would…" — everything they touch runs.

### 2.4 The prototype thesis (say this in the first 30 seconds)

> Everything you see runs live: real Mumbai terrain and roads from open data, a reconstructed replay of 2 July 2019, simplified-but-real physics for the surface and the drains, a learning drain map, sub-second what-if, and routes for an ambulance. Every simplification is labelled on screen.

---

## 3. Scope, tiers, and what is real versus simulated

### 3.1 Tiers

- **P0** — must exist for the demo. If it is not P0, it is not built until every P0 task is done.
- **P1** — should exist; each one raises the ceiling. Build after P0 is green.
- **P2** — stretch; only if time remains. Interfaces for P2 items are designed now (schemas, endpoints, buttons that say "coming in pilot") so the pitch can point at them.

### 3.2 Capability matrix (be able to say each row's third column out loud)

| Capability | Tier | Real / reduced / simulated in the prototype |
|---|---|---|
| Terrain, roads, buildings, land cover for Mumbai AOI | P0 | **Real** (Copernicus GLO-30, OSM, ESA WorldCover) |
| Radar reflectivity frames | P0 | **Simulated**: storm designer calibrated to public gauge totals for the event; IMD PNG decoder is P1 |
| Rain gauges | P0 | **Simulated** from the truth field at real station locations, with noise |
| Rainfall nowcast (Z–R, gauge merge, optical flow, STEPS ensemble) | P0 | **Real** algorithms (pySTEPS) on simulated radar |
| 2D surface hydraulics | P0 | **Real**, local-inertial shallow water at 30 m (5 m nests P1) |
| Drain graph | P0 | **Inferred** from roads + DEM (blueprint §7.1); labelled "inferred" everywhere |
| 1D drain hydraulics | P0 | **Reduced**: head-driven Manning ("diffusive-wave-lite") with surcharge and backflow; SWMM dynamic wave is P1 |
| 1D–2D coupling (inlet capture, surcharge) | P0 | **Real** formulas from blueprint §6.5 |
| Tide boundary | P0 | **Real** boundary logic; stage series from public tide tables if obtainable, else labelled illustrative |
| Traffic speeds | P0 | **Simulated** baseline + anomalies (labelled); Mappls/TomTom adapter P2 |
| Citizen reports | P0 | **Simulated** stream + a curated set of **real, sourced** ground-truth pins |
| Pulse (EnKF over blockage β) | P0 | **Real** EnKF, reduced model as the observation operator |
| Flash surrogate | P0 | **Reduced-order emulator** (reservoir cascade calibrated to Twin runs); GNN surrogate P1 |
| Probabilistic products (50 members) | P0 | **Real**, from Sky members × Pulse parameter draws through the emulator |
| Routing, reachability | P0 | **Real** time-dependent Dijkstra on the OSM graph (Python); Rust service P2 |
| Alerts, CAP 1.2 | P0 | **Real** CAP XML; WhatsApp is an on-screen phone mock (real send P2) |
| Pump dispatch | P0 | **Real** greedy optimiser; MILP P1; pump inventory is synthetic (labelled) |
| Verification scores | P0 | **Real** computation against the sourced ground-truth pins and the truth rain field |
| Chennai onboarding | P0 | **Real** pipeline from pre-cached open data; first forecast from a design storm |
| 3D water rendering | P1 | deck.gl terrain + depth texture |
| PWA / offline public map | P1 | PMTiles basemap cached |
| Multilingual public map (EN/HI/MR) | P1 | i18n JSON |
| PostGIS + Timescale sink | P1 | File artifacts are the P0 store (see §4) |

### 3.3 Demo geography

**Primary city — Mumbai central AOI (`MUM-CENTRAL`)**
- Bounding box (WGS84): lon 72.815 → 72.905, lat 18.995 → 19.135 (≈ 9.5 km × 15.5 km). CRS for computation: EPSG:32643 (UTM 43N). Grid: 30 m (≈ 316 × 517 cells ≈ 163k cells). Nests (P1): 5 m at Hindmata and King's Circle, 1 km² each.
- Radar/Sky domain: 60 km × 60 km centred on the AOI at 500 m (120 × 120 px), so pySTEPS has enough pixels for its scale cascade; rain is resampled to the 30 m AOI grid.
- Chronic hotspots to register (coordinates are approximate — **verify each with Nominatim/OSM before use** and store the verified point + `source_url`): Hindmata junction (Dadar East, ≈19.012, 72.841), Dadar TT (≈19.019, 72.845), Parel/Bharatmata (≈19.007, 72.838), King's Circle / Maheshwari Udyan (≈19.027, 72.857), Gandhi Market (≈19.032, 72.858), Sion Circle (≈19.039, 72.862), Kurla LBS Marg (≈19.066, 72.879), Khar subway (≈19.070, 72.838), Milan subway (≈19.079, 72.840), Andheri subway (≈19.119, 72.844).
- Assets: KEM Hospital, Parel (≈19.003, 72.841); LTMG Sion Hospital (≈19.041, 72.862); fire stations and railway stations from OSM; pumping stations and the Hindmata holding tanks from public BMC material (verify, source). Mobile pumps: a **synthetic** inventory of 12 pumps at plausible depots, labelled synthetic.
- Ambulance demo trip: KEM Hospital → Sion Hospital at 08:40 IST on the replay day; the naive route runs through Hindmata/Dr Ambedkar Road.

**Second city — Chennai onboarding AOI (`CHN-SOUTH`)**: Velachery–Adyar–T. Nagar, lon 80.20 → 80.28, lat 12.96 → 13.05, EPSG:32644. Pre-cache all open data so the wizard runs offline in minutes.

### 3.4 Screen inventory

| Route | Screen | Tier |
|---|---|---|
| `/` | Landing page | P0 |
| `/console` | Command console (map, time bar, hotspot rail, layers, replay panel, attribution) | P0 |
| `/drains` | Drain X-ray (drain-health map + table + assimilation timeline) | P0 |
| `/route` | Route planner (naive vs VARUNA, profiles, safe-until) | P0 |
| `/console?tab=reachability` | Reachability clocks for facilities | P0 |
| `/alerts` | Alert centre, CAP viewer, WhatsApp phone mock | P0 |
| `/pumps` | Pump dispatch board | P0 |
| `/whatif` | What-if lab (also a drawer inside the console) | P0 |
| `/replay` | Replay control and storm designer | P0 (designer editing P1) |
| `/onboard` | City-in-a-box wizard (Chennai on stage) | P0 |
| `/verify` | Verification dashboard | P0 |
| `/map` | Public map (mobile-first) | P0 |
| `/report` | Citizen report flow | P0 |
| `/api` | API explorer (Scalar/Swagger) | P1 |
| `/design` | Internal design-system page (tokens, components, states) | P0 (internal) |
| `/dashboard` | Citizen dashboard: Google basemap, VARUNA water and routes over it, plain-language reasons, live weather (`PRD.md` 3.1) | P0 |
| `/authority` | Ward officer's desk: closures, pump status, dispatch, alert actions, the citizen inbox (`PRD.md` 3.2) | P0 |
| `/rural` | Low-bandwidth text advisory, server-rendered, no client JavaScript (`PRD.md` 3.3) | P0 |


---

## 4. Repository layout and tooling

### 4.1 Monorepo

```
varuna/
├── SPEC.md                      # this file
├── Makefile                       # every workflow is a make target (see §4.3)
├── docker-compose.yml             # optional: PostGIS/Timescale, Redis, MinIO (P1)
├── pnpm-workspace.yaml · turbo.json · package.json
├── pyproject.toml · uv.lock       # one uv workspace for all Python services
├── .env.example
├── apps/
│   └── command/                   # Next.js (App Router, TS): landing, console, public map, all screens
│       ├── app/                   # routes listed in §3.4
│       ├── components/            # ui/ (shadcn), varuna/ (custom), v0/ (generated then adapted), map/
│       ├── lib/                   # api client (generated types), stores (zustand), ramps, i18n, motion
│       └── public/                # fonts, OG image, PMTiles (P1), icons
├── services/
│   ├── api/                       # FastAPI: products, routing, alerts, replay controls, WS
│   ├── city/                      # city-in-a-box pipeline (DEM, OSM, conditioning, drain synthesis)
│   ├── replay/                    # bundle loader, storm designer, synthetic streams, clock
│   ├── sky/                       # QPE, gauge merge, optical flow, STEPS ensemble
│   ├── twin/                      # 2D solver (Numba), 1D drain solver, coupling, boundaries
│   ├── pulse/                     # traffic anomaly detector, report ingestion, EnKF
│   ├── flash/                     # reduced-order emulator (P0) + GNN surrogate (P1)
│   ├── products/                  # segment/node forecasts, rasters, hotspots, attribution, alerts, pumps
│   ├── route/                     # time-dependent routing, reachability, provider feeds
│   ├── cycle/                     # orchestrator (stages, timings, run registry, bake)
│   └── verify/                    # verification scores per event
├── packages/
│   ├── schemas/                   # Pydantic models + JSON schemas; design-tokens.json shared with the UI
│   └── tokens/                    # tokens.json → CSS variables + Tailwind theme + Python ramps
├── city/                          # per-city static layers (gitignored, cached by make city)
├── bundles/                       # replay bundles (MUM-2019-07-02, MUM-IDF-25yr, CHN-IDF-25yr)
├── data/runs/                     # run artifacts (gitignored)
├── docs/                          # blueprint PDF, DECISIONS.md, SIMPLIFICATIONS.md, CHANGELOG.md, QA.md, screens/
└── tests/                         # cross-service integration + Playwright demo test
```

### 4.2 Storage decision (P0 = files, P1 = PostGIS)

Run products are written as files under `data/runs/<run_id>/` (GeoParquet, GeoJSON, PNG + world file, Zarr, JSON) and served by FastAPI. Reasons: zero infrastructure on stage, byte-reproducible bakes, trivial offline packaging. The PostGIS/Timescale sink (`services/api/sinks/postgis.py`) is P1 and mirrors the same tables as blueprint §9.1 so the pitch can show the schema. Redis Streams / Redpanda are replaced by an in-process asyncio bus (`services/cycle/bus.py`) with the same topic names as the blueprint; swapping to Redpanda is a config change (P2).

### 4.3 Make targets (each must work from a clean clone)

| Target | Does |
|---|---|
| `make setup` | installs pnpm + uv workspaces, pre-commit, Playwright browsers; copies `.env.example` |
| `make city CITY=mumbai` | runs city-in-a-box from cache (downloads on first run), writes `city/mumbai/` |
| `make bundle BUNDLE=MUM-2019-07-02` | generates the replay bundle (storm designer + synthetic streams + curated ground truth) |
| `make bake BUNDLE=MUM-2019-07-02` | pre-computes every 5-minute cycle of the bundle into `data/runs/` |
| `make train` | fits Flash-lite from Twin runs; (P1) trains the GNN |
| `make dev` | API on :8000, Next.js on :3000, replay paused |
| `make demo` | full demo: API + UI + replay of the default bundle at 30× from baked runs |
| `make test` | Python + TS unit tests, API contract tests |
| `make e2e` | Playwright demo-script test |
| `make pack` | offline package: baked runs, tiles, Chennai cache, fonts, basemap; verified with network disabled |
| `make demo-video` | records the demo path with Playwright video as the fallback |

### 4.4 Environment variables (`.env.example`)

`VARUNA_CITY=mumbai` · `VARUNA_BUNDLE=MUM-2019-07-02` · `VARUNA_REPLAY_SPEED=30` · `VARUNA_MODE=replay|live` · `VARUNA_OFFLINE=1` (block all external network) · `NEXT_PUBLIC_API_URL` · `NEXT_PUBLIC_BASEMAP_STYLE` (default CARTO dark matter, no labels) · optional: `MAPPLS_KEY`, `TOMTOM_KEY`, `TWILIO_*`, `WHATSAPP_CLOUD_*`, `OPENTOPO_KEY`, `POSTGRES_URL`, `REDIS_URL`.

### 4.5 Ports

3000 UI · 8000 API (+ `/docs`, `/v1/live` WebSocket) · 5432 PostGIS (P1) · 6379 Redis (P1).

---

## 5. Technology stack (say a reason for every choice)

| Layer | Choice | Why |
|---|---|---|
| Web app | Next.js (App Router) + React 19 + TypeScript strict | One app for landing, console and public map; server components for the landing; client components for the map |
| Styling | Tailwind CSS v4 + CSS variables from `packages/tokens` | Tokens are the single source of truth for TS and Python |
| Components | shadcn/ui as the base kit; custom `components/varuna/*`; Magic UI and Aceternity components copied in only where §8 lists them; v0 for first drafts (§9) | Owned code, no black-box UI library |
| Motion | `motion` (framer-motion) for UI; CSS keyframes for pulses; deck.gl transitions for map; `@number-flow/react` for numbers | Interaction-driven motion; GPU-side for the map |
| Maps | MapLibre GL JS + deck.gl (`MapboxOverlay` on MapLibre) via `react-map-gl/maplibre` | Free, WebGL, layers for 160k-cell rasters and 30k segments at 60 fps |
| Basemap | CARTO "dark matter, no labels" style (free with attribution) for P0; Protomaps PMTiles self-hosted for offline (P1) | Dark, quiet, lets water be the hero |
| Charts | Recharts for fan charts, sparklines, reliability diagrams; visx only if Recharts cannot do it | Small bundle, SSR-friendly |
| State/data | Zustand (UI state), TanStack Query (artifacts), native WebSocket hook | Instant scrubbing from a preloaded run |
| Drag and drop | `@dnd-kit/core` | Pump board |
| Command palette / toasts | `cmdk` (shadcn Command) · `sonner` | Keyboard-first control room |
| i18n | `next-intl` with EN/HI/MR JSON (P1) | Public map |
| API | FastAPI + Pydantic v2 + uvicorn; OpenAPI → `openapi-typescript` for TS types | One contract, typed on both sides |
| Science | numpy, numba, xarray, zarr, rasterio, rioxarray, geopandas, shapely, pyproj, osmnx, networkx, whitebox (WhiteboxTools), richdem, pysteps, opencv-python-headless, scipy, scikit-learn | The blueprint's stack minus the GPU |
| Optional science | pyswmm (P1 dynamic wave), torch + torch_geometric (P1 GNN), ortools (P1 MILP), wradlib/xarray for NetCDF (P1 real radar) | Upgrade paths, same interfaces |
| Data formats | GeoParquet, GeoJSON, COG/PNG + world file, Zarr, JSON; CAP 1.2 XML; GTFS-RT protobuf (P1) | Blueprint-compatible, standards-native |
| Packaging | pnpm + Turborepo; uv workspace; Makefile; Docker for optional infra | Reproducible from a clean clone |
| Testing | pytest (+ hypothesis for solvers), vitest, Playwright (demo path + video), Lighthouse CI | Demo insurance |
| Observability | structlog JSON logs; stage timings in every run; `/v1/cycle/status` | The cycle budget bar is a product feature |
| Deploy | Vercel for the landing (and console in demo mode pointing at a hosted API, optional); everything else on the demo laptop | The finale runs offline |

---

## 6. Design system (this is DESIGN.md — implement it exactly)

### 6.1 Concept: the control room at 3 a.m. during a cloudburst

The subject is water arriving in a city faster than people can see it. The interface must make **water rising on real streets** the one memorable thing on every screen and keep everything else quiet, disciplined and legible from across a room. The palette is a monsoon night — deep indigo, not black — with water rendered in luminous, semantically fixed colours. The type is technical and confident. Motion exists to show change, never to decorate.

**Spend the boldness in one place per screen**: on the console it is the map; on the landing page it is the live map hero scrubbing three hours in eight seconds; on the drain X-ray it is the pipes glowing with learned blockage; on the route planner it is the ambulance route drawing itself around the underpass.

### 6.2 Colour tokens (`packages/tokens/tokens.json` → `globals.css` → Tailwind theme)

Base (dark, always):

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#0A1020` | app background |
| `--deep` | `#111A2E` | panels, rails, cards |
| `--well` | `#17233B` | inputs, hover rows, selected tabs |
| `--line` | `#24314F` | borders, dividers |
| `--line-strong` | `#33436A` | focused/emphasised borders |
| `--text` | `#E3EAF6` | primary text |
| `--text-2` | `#A7B4CC` | secondary text |
| `--text-3` | `#7E8DA9` | muted text, placeholders (was `#6E7E9E`, which failed 6.10's 4.5:1 floor on `--deep` and `--well`; ADR-0036) |
| `--tide` | `#2DD4BF` | brand accent: primary buttons, links, safe routes, "VARUNA" route, focus rings |
| `--tide-soft` | `#0F3A3A` | accent backgrounds |

Depth ramp — **fixed meaning everywhere** (map segments, rasters, chips, charts, alerts):

| Token | Hex | Meaning |
|---|---|---|
| `--depth-dry` | `#2B3A55` | < 5 cm (barely visible on the map) |
| `--depth-1` | `#3B82F6` | 5–15 cm — two-wheelers slow |
| `--depth-2` | `#F59E0B` | 15–30 cm — two-wheelers impassable |
| `--depth-3` | `#F97316` | 30–45 cm — cars impassable |
| `--depth-4` | `#EF4444` | 45–60 cm — buses/trucks impassable |
| `--depth-5` | `#B91C1C` | > 60 cm — rescue vehicles only |

Other semantic ramps:

| Token(s) | Hex | Use |
|---|---|---|
| `--drain-0` → `--drain-3` | `#3E4C6E`, `#7C3AED`, `#C026D3`, `#E879F9` | posterior blockage β 0–0.25, 0.25–0.5, 0.5–0.75, > 0.75 (magenta = blocked) |
| `--surcharge` | `#EF4444` | manhole surcharge markers, reversed-flow edges (pulsing / animated dash) |
| `--naive` | `#64748B` | naive route (dashed), pre-update drain state, "before" states |
| `--reach-5`, `--reach-10`, `--reach-15` | `#2DD4BF` at 45 %, 28 %, 14 % opacity | reachability isochrones (5/10/15 min) |
| `--obs-traffic`, `--obs-report`, `--obs-sensor`, `--obs-cctv`, `--obs-sar` | `#FB7185`, `#FDE68A`, `#34D399`, `#C4B5FD`, `#93C5FD` | observation types in Pulse |
| `--truth` | `#FFFFFF` with `--tide` ring | ground-truth pins |
| `--status-live`, `--status-replay`, `--status-baked`, `--status-degraded` | `#22C55E`, `#38BDF8`, `#A78BFA`, `#FBBF24` | mode banner |
| `--danger` | `#F87171` | destructive UI actions only (never for depth) |
| `--chart-1..5` | `#2DD4BF`, `#60A5FA`, `#F472B6`, `#FBBF24`, `#A3E635` | charts, in order |

Probability mode: colour stays the depth ramp at the p50 depth; **opacity = P(> selected threshold)** (min 0.15 so a segment never vanishes). Uncertainty on charts: p10–p90 band at 20 % opacity of the line colour.

Rules: never use the depth ramp for anything that is not water depth; never use `--tide` for a warning; `--danger` never appears on the map.

### 6.3 Typography

- **Display: Bricolage Grotesque** (Google Fonts, variable) — landing headlines, page titles, the big depth number in the hotspot drawer. Weights 500–700, tracking −0.02em, line-height 1.02–1.1.
- **UI and body: Geist Sans** (`geist` npm package) — everything else. Weights 400/500/600. `font-variant-numeric: tabular-nums` on every element that shows a number (`.num` utility).
- **Mono: Geist Mono** — only run IDs, CAP XML, API explorer, log streams. Not for labels, not for small data.
- **Indic (P1)**: Noto Sans Devanagari and Noto Sans Tamil loaded only when the locale switches.
- Scale (px / line-height): 12/1.3 micro · 13/1.4 small · 15/1.5 body · 18/1.35 h3 · 24/1.2 h2 · 32/1.15 h1 · 48/1.05 display · 72/1.0 hero. Body line length ≤ 72 characters.
- Numbers always carry units and context: "45 cm", "+40 min", "82 %", "08:20 (+40 min)". Times are IST 24-hour.

### 6.4 Shape, surface, elevation

- Radius: panels 12 px, controls 8 px, chips 999 px, map popovers 10 px, phone mock 40 px.
- No drop shadows in the dark UI. Depth is expressed with `--deep` on `--ink` and 1 px `--line` borders. The only glow in the product is the active hotspot ring on the map and the focus ring (`--tide`, 2 px).
- Glass (backdrop blur) is allowed on exactly one element: the console time bar (`bg: --ink @ 72 %`, blur 12 px).
- Density: control-room density. Rows 40 px in lists, 32 px in dense tables; panel padding 16 px; section gap 24 px; 4-pt grid.
- Icons: Lucide, 16 px in rows, 20 px in nav, stroke 1.75. No emoji anywhere.

### 6.5 Layout

Console shell (1440 × 900 reference; must also work at 1366 × 768 and on a 4K wall at 150 % zoom):

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ◈ VARUNA  Mumbai ▾   ● REPLAY 30× · 2 Jul 2019 06:40 IST    run MUM-…-baked │ 52 px top bar
├──┬───────────────────────────────────────────────────────────┬───────────────┤
│  │                                                           │ Hotspots      │
│ ▤│                        MAP CANVAS                         │ Alerts  Pumps │
│ ⌇│              (depth on streets, surcharge,                │ Reach         │
│ ⇄│               drains, routes, isochrones)                 │───────────────│
│ ! │                                                           │ 1 Hindmata    │
│ ⚙│  ┌ Layers ┐                          ┌ Legend ┐           │   55 cm 08:20 │
│ ~│  │        │                          │        │           │ 2 King's Cir. │
│ ▶│  └────────┘                          └────────┘           │ 3 Sion Circle │
│ ✓│                                                           │ …             │
├──┴───────────────────────────────────────────────────────────┴───────────────┤
│ ◀ ▶  −60 ───────●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ +180   08:20 ▸ │ 96 px time bar
└──────────────────────────────────────────────────────────────────────────────┘
   56 px icon rail                                               360 px right rail
```

Landing: left-aligned text column (max 72 ch) on a 12-column grid; full-bleed hero; section padding 120 px desktop / 72 px mobile; no centred paragraphs. Public map: single column, bottom sheet, 44 px touch targets.

### 6.6 Component inventory (build these in `components/varuna/`; shadcn provides the primitives)

`AppShell`, `TopBar`, `IconRail`, `ModeBanner`, `RunStamp`, `VerificationChip`, `CityMap` (MapLibre + deck.gl host), `LayerPanel`, `Legend`, `TimeBar` (scrub, play, speed, ensemble band), `DepthChip` (colour + value + unit), `ProbabilityToggle`, `HotspotRow`, `HotspotDrawer` (big depth number, fan chart, safe-until table, attribution list), `SegmentPopover`, `FanChart`, `Sparkline`, `ObservationCard`, `DrainHealthTable`, `PipeRow`, `AlertCard`, `AlertLevelChip`, `CapViewer`, `PhoneMock` (WhatsApp card), `PumpCard`, `PumpBoard`, `DispatchOrder`, `WhatIfDrawer`, `DeltaTable`, `RouteCompare`, `ReachabilityClock`, `CycleBudgetBar`, `ReplayPanel`, `StormDesigner` (P1 editing), `OnboardingSteps`, `LogStream`, `VerificationGrid`, `ReliabilityDiagram`, `SkillByLeadChart`, `ContingencyTable`, `EmptyState`, `Skeleton`, `Kbd`, `ShortcutsOverlay`, `CommandPalette`, `LanguageToggle`, `ReportWizard`, `DepthChips` (ankle/knee/waist).

Every component ships with: typed props, loading state, empty state, error state, reduced-motion behaviour, and a story on `/design`.

### 6.7 Map style specification

- Basemap: CARTO dark matter **no labels**; add a thin custom label layer (road names at z ≥ 15, locality names at z ≥ 12) in `--text-3` so labels never fight with water.
- Layer order (bottom → top): basemap · buildings (fill `--deep`, 40 %, only at z ≥ 14) · depth raster (`BitmapLayer`, per-step PNG, 55 % opacity, fixed ramp) · drains (`PathLayer`, off by default; width by diameter 1–4 px; colour by β) · road segments (`PathLayer`, colour by p50 depth, width by road class 2–6 px; dry segments 1 px `--depth-dry`) · reversed-flow edges (`PathLayer` + dash extension, animated) · surcharge markers (`ScatterplotLayer` + CSS pulse via `IconLayer` sprite) · isochrones (`PolygonLayer`, `--reach-*`) · routes (`PathLayer`: naive dashed `--naive` 3 px; VARUNA `--tide` 5 px with a 1 px `--ink` casing) · assets (`IconLayer`: hospital, fire, pump, station) · hotspot rings (`ScatterplotLayer` stroked, radius 120 m) · ground-truth pins (`IconLayer`) · hover/selection highlight.
- Depth raster ramp is generated in Python from the same `tokens.json` so map pixels and UI chips match exactly.
- Scrubbing swaps the bitmap and re-styles segments from a preloaded run; no network calls during a scrub.
- 3D mode (P1): deck.gl `TerrainLayer` with the depth PNG as texture, exaggeration 2×, pitch 55°.
- Hover: segment tooltip within 80 ms. Click: opens `SegmentPopover` (mini fan chart, safe-until per vehicle, "why" link).
- Always-visible `Legend` (depth ramp with thresholds; toggles to probability legend in probability mode).

### 6.8 Copy and microcopy rules

- Sentence case everywhere. No ALL-CAPS labels, no tracked-out eyebrows, no "A · B · C" meta strings, no arrows appended to buttons.
- Buttons name the action and the result keeps the name: "Dispatch pumps" → toast "Pumps dispatched"; "Acknowledge" → "Acknowledged"; "Run what-if" → "What-if ready"; "Compute live" → "Live run published".
- Consistent vocabulary: **scrub** (time slider), **hotspot**, **segment**, **surcharge**, **blockage** (β), **clogging** (κ), **safe until**, **passable**, **reachability**, **dispatch**, **what-if**, **replay**, **baked/live**, **inferred** (drain graph), **reconstructed** (replay).
- Depth is "cm"; time is "08:20 (+40 min)"; probability is "82 %"; lead time "3 h".
- Empty states tell the user what to do: "No runs yet — press Play on the replay, or Compute live." Errors say what happened and the fix: "Traffic feed offline. Pulse is using reports only." Never "Something went wrong".
- Honesty labels are UI copy, not fine print: "Reconstructed replay", "Inferred drain graph", "Reduced-order emulator", "Synthetic pump inventory".
- No lorem ipsum, ever. Every sample string is about Mumbai, Chennai, monsoon, drains.

### 6.9 Anti-patterns (explicitly banned)

Identical card grids with the same radius and shadow · gradient washes as decoration · ambient particles/blobs on the console · fade-and-slide-up on every section · spinners (use skeleton shimmer) · monospace for small data labels · numbered markers where the content is not a sequence · placeholder avatars/logos · "AI" sparkle icons · modal-on-modal · light theme on the console.

### 6.10 Accessibility floor

Contrast ≥ 4.5:1 for text (checked on `/design`); every map control reachable by keyboard; time bar operable with ←/→ (15 min), Shift+←/→ (60 min), Space (play/pause); focus rings visible; `prefers-reduced-motion` respected in every motion (§8); ARIA labels on icon buttons; colour never the only carrier of meaning (depth chips also show the number; alert levels also show text).

### 6.11 Design QA checklist (run per screen before marking the screen done)

- [ ] Uses only tokens; `pnpm lint:design` passes (no raw hex, no non-token fonts)
- [ ] One memorable element; nothing competes with it
- [ ] All states implemented: loading skeleton, empty, error, degraded (where relevant)
- [ ] Copy follows §6.8; no placeholder text
- [ ] Works at 1366 × 768, 1440 × 900, 4K wall at 150 %, and (public map) 390 × 844
- [ ] Keyboard path works; focus visible; reduced motion verified
- [ ] Screenshot saved to `docs/screens/<route>.png` and compared with the previous one


---

## 7. Screens — specification and acceptance criteria

Each screen lists: purpose · layout · components · data · interactions · states · signature motion · acceptance criteria (AC). Tick the AC boxes only when verified in the browser.

### 7.1 Landing page — `/`

**Purpose.** Convince a judge, a ministry officer and a mentor in 60 seconds that VARUNA is real, rigorous and different. It is also the entry to the console.

**Layout (top to bottom, left-aligned text column, full-bleed hero):**

1. **Hero** — a full-viewport, read-only embed of the console map (`CityMap` in `mode="hero"`) auto-scrubbing −60 → +180 min in an 8-second loop over Hindmata/King's Circle/Sion, with a small live readout "06:40 · +0 min … 09:40 · +180 min" and the depth legend. Over it, in the left third: the wordmark, headline **"Every street. Three hours early."**, one sentence ("VARUNA turns Doppler radar into street-by-street flood depth for the next three hours, learns the city's hidden drains from every flood, and routes emergency services around what is coming."), two buttons: "Open the console" (primary) and "Watch the 2 July 2019 replay" (secondary, opens `/console?bundle=MUM-2019-07-02&autoplay=1`). A small line under the buttons: "SIH 2026 · PS SIH26085 · Ministry of Earth Sciences". No stats in the hero.
2. **The gap** — "Forecasts stop at 12 km. Streets flood at 30 m." A two-panel diagram (SVG): an NWP grid cell over Mumbai on the left; a 30 m street with a 40 cm dip under a rail bridge on the right. Three sentences from blueprint §2.1.
3. **Four ways a street floods** — pluvial, fluvial, tidal lock, invisible drainage. A two-column list (diagram left, text right), each with one Indian example; deliberately not four identical cards.
4. **The five-minute cycle** — the pipeline diagram (ingest → Sky → Twin ∥ Flash → Pulse → products → Command/Route/Public) drawn with Magic UI `AnimatedBeam` between nodes and the live stage timings of the latest run under each node (fetched from `/v1/cycle/status`; static fallback). This section is a real sequence, so numbering is allowed.
5. **Six engines** — Magic UI `BentoGrid` with varied tile sizes: Pulse is the largest ("The city reveals its own drains"), Twin and Flash medium, Sky/Route/Command small. Each tile: name, one-line job, a small inline SVG micro-diagram, and the "why didn't we think of that" sentence from blueprint §4 on hover/focus.
6. **Proof** — three numbers with `@number-flow/react` counting on scroll-in: CSI at chronic spots, depth MAE at pins, median lead time gained; each pulled from `/v1/verification?event=MUM-2019-07-02`. Footnote in plain text: "Computed on a reconstructed replay with sourced ground truth. See how we score ourselves." → `/verify`.
7. **Where VARUNA sits** — the landscape table (IFLOWS-Mumbai, C-FLOWS, IIT-B Mumbai Flood, IMD nowcasts, Google Flood Hub, VARUNA) with the positioning sentence from blueprint §3 as a pull quote. Dense, real, honest.
8. **Data we use** — a compact list of sources with a status pill each: public / requested (MoES SPOC) / synthetic in prototype. Honesty as design.
9. **Roadmap** — V1 → V10 → V100 as a vertical timeline with Aceternity `TracingBeam` (a real sequence).
10. **Team and footer** — six roles (names, one line each), PS number, MoES, VIT, links to `/console`, `/verify`, `/api`, GitHub.

**Signature motion (the only orchestrated moment):** on load, the hero copy enters with `BlurFade` (stagger 60 ms), then the map begins its scrub loop. Sections below reveal without animation except the cycle beams, the number counters and the roadmap beam. Hover on bento tiles reveals text (no scale bounce).

**States.** If the API is unreachable, the hero plays a pre-rendered image sequence from `public/hero/` (36 frames) and the numbers show the last committed verification values from `public/verification.json`. Reduced motion: hero shows the +120 min frame, static.

**AC**
- [ ] LCP < 2.5 s, CLS < 0.1, Lighthouse performance ≥ 90 and accessibility ≥ 95 on desktop and mobile
- [ ] Hero map loop runs at ≥ 55 fps on an integrated GPU laptop; pauses on hover; static under reduced motion
- [ ] Every number is fetched; fallback JSON is committed and used when offline
- [ ] Table, list and roadmap render on a 390 px phone without horizontal scroll
- [ ] Open Graph image (`/opengraph-image`) shows the hero frame with the headline
- [ ] No section uses the banned patterns (§6.9)

### 7.2 Command console — `/console`

**Purpose.** The operator's screen and 70 % of the demo. Layout is §6.5.

**Top bar.** Wordmark; city switcher (Mumbai, Chennai once onboarded); `ModeBanner` ("Replay 30× · 2 Jul 2019 · 06:40 IST" / "Live" / "Degraded: radar offline, using gauges + satellite"); `RunStamp` ("run MUM-20190702T0640-sky1.0-twin1.0-flash0.3 · baked · 3.9 s") with a click-to-copy; `VerificationChip` ("CSI 0.71 on this event"); command palette button (⌘K); shortcuts (?).

**Icon rail (left).** Console, Drains, Route, Alerts, Pumps, What-if, Replay, Verify, Onboard. Labels appear on hover/focus.

**Map canvas.** Per §6.7. Floating `LayerPanel` (top-left, collapsible): Streets (depth) · Probability mode + threshold (15/30/45/60 cm) · Depth raster · Drains (health) · Surcharge & backflow · Isochrones (facility picker) · Routes · Ground truth · Buildings · 3D (P1). Floating `Legend` (bottom-right of map). Scale bar. Attribution.

**Time bar (bottom).** Play/pause (Space); speed (1×/10×/30×/60×); scrub handle across −60 → +180 min in 15-min ticks (5-min fine steps with Shift); the observed half is tinted `--text-3`, the forecast half `--tide-soft`; the ensemble spread band (p10–p90 of AOI-mean depth) is drawn under the track; the current sim time and lead ("08:20 · +40 min") sit at the right; "Compute live" button re-runs the current cycle with real computation and shows the `CycleBudgetBar` (decode / Sky / Twin ∥ Flash / Pulse / products) filling stage by stage with milliseconds.

**Right rail tabs.**
- **Hotspots** — ranked `HotspotRow`s: rank, name, `DepthChip` (p50 at the selected time), time-to-peak ("08:20"), a 3-hour `Sparkline` (p50 with band), exposure icon set (hospital, station, transit). Click → map fly-to + ring + `HotspotDrawer`.
- **Alerts** — the live queue (`AlertCard`: level chip, headline, area, trigger time, acknowledge/escalate) — mirrors `/alerts`.
- **Pumps** — compact dispatch board mirror with "Optimise" and the current plan's expected benefit.
- **Reachability** — facility list (hospitals, fire stations) with `ReachabilityClock`: a 15-minute catchment area as a ring gauge that shrinks over the scrub; "collapse" state when below 40 % of dry baseline.

**HotspotDrawer (slides in over the rail).** Big depth number in display type (e.g., "55 cm" with "p50 at 08:20"), `FanChart` (p10/p50/p90 over −60 → +180 with the observed part solid), safe-until table per vehicle (two-wheeler, car, bus, ambulance, pedestrian), exposure (traffic volume proxy, nearest hospital, station), **"Why this junction floods"**: ranked responsible pipes with their β and the depth they explain ("cleaning these 14 pipes: 55 → 20 cm"), buttons "Clean in what-if", "Dispatch pumps here", "Show drains".

**Replay panel (opens from the rail or `/replay`).** Bundle selector, clock, play/pause/seek/speed, baked/live toggle, cycle log table (time, stages, ms, mass-balance error), storm summary.

**Ground-truth pins.** As the replay clock passes a pin's timestamp, it drops onto the map with a ripple and appears in a small "As it happened" ticker at the bottom of the hotspot rail ("08:47 · Gandhi Market · BMC log: waterlogging, traffic diverted · source"). This is the 2:40 moment of the demo — the pins land where VARUNA was already red.

**Keyboard.** Space play/pause · ←/→ scrub · P probability · D drains · S surcharge · R routes · I isochrones · G ground truth · 3 3D · W what-if · ⌘K palette · ? overlay.

**States.** No runs: `EmptyState` on the map ("No runs yet — press Play on the replay, or Compute live") with the replay panel open. Loading: skeleton rail + faint map. Degraded: banner + a "missing feeds" popover. Live-compute in progress: the budget bar animates; the map keeps the last run until the new one is published, then swaps atomically.

**Signature motion.** Scrubbing: instant restyle (< 16 ms). Surcharge markers pulse; reversed-flow edges animate their dash; ground-truth pins drop with a ripple; the hotspot fly-to is a 900 ms `flyTo`.

**AC**
- [ ] Preloads a run's 36 depth PNGs + segment table on `run.published`; scrub restyle ≤ 16 ms; no network during scrub
- [ ] Map ≥ 55 fps with streets + raster + surcharge + pins at 1440 × 900
- [ ] Probability mode changes opacity by P(> threshold); legend switches accordingly
- [ ] Surcharge markers and reversed-flow edges appear at the tide-locked outfall and Hindmata in the demo run
- [ ] Hotspot drawer shows fan chart, safe-until per vehicle, attribution with ≥ 5 pipes
- [ ] Ground-truth pins drop at their timestamps during replay; ticker shows source links
- [ ] "Compute live" runs a real cycle ≤ 15 s and shows per-stage ms
- [ ] All keyboard shortcuts work; `?` overlay lists them
- [ ] Empty, loading, degraded states implemented; zero console errors during a full replay

### 7.3 Drain X-ray — `/drains` (also the "Drains" layer mode in the console)

**Purpose.** Show the invisible drain becoming visible: the learned blockage map, the observations that taught it, and the desilting priority list.

**Layout.** Map (left 62 %) with drains coloured by posterior β (magenta ramp), width by diameter, dashed where confidence = inferred (all of them, in the prototype — say so), inlets as small squares coloured by κ, surcharge nodes as red rings; hovering a pipe shows β mean ± sd, capacity, last update. Right panel (38 %): `DrainHealthTable` (top 25 pipes by β: id, street, β ± sd, capacity reduction %, hotspots it explains, observations count, "last updated 06:35"), an **assimilation timeline** (a vertical list of `ObservationCard`s — traffic anomaly / citizen report / sensor — each with time, place, inferred depth, and the β change it caused), a **before/after toggle** that cross-fades the prior map and the posterior map, and "Export desilting priority (CSV)".

**Signature motion.** Toggling before/after cross-fades the pipe colours (300 ms) while the depth number on the affected hotspot rolls with `NumberFlow` ("55 → 48 cm").

**AC**
- [ ] Posterior β renders per pipe with the magenta ramp; inferred pipes are dashed and the panel says "Drain graph inferred from roads and terrain"
- [ ] Assimilation timeline lists every observation assimilated in the current replay with its β effect
- [ ] Before/after toggle works; hotspot depth delta shown
- [ ] CSV export contains id, street, β, sd, capacity reduction, hotspots explained
- [ ] Table sorts by β, sd, capacity reduction

### 7.4 Route planner — `/route`

**Purpose.** Prediction turned into an ambulance route.

**Layout.** Map (70 %) + left panel (30 %): origin/destination (map click or search over assets: "KEM Hospital" → "Sion Hospital"), departure time (defaults to the scrub time), vehicle profile (ambulance, fire tender, bus, car, two-wheeler, pedestrian), risk tolerance slider (0.2 default for ambulance), "Find route". Result: `RouteCompare` — two columns, "Naive (shortest)" vs "VARUNA": ETA, distance, max expected depth on route, safe-until, then "Avoided" list (segment name, P(> threshold) at the time it would be reached) and "Alternates" (up to two). Buttons: "Send to dispatch" (creates a dispatch note), "Copy as GeoJSON".

**Signature motion.** The naive route draws first in dashed grey, then the VARUNA route draws itself over 1.2 s in `--tide` around the red segments; avoided segments flash once.

**AC**
- [ ] KEM → Sion at 08:40 avoids Hindmata/Sion underpass when they are predicted impassable for the profile; the naive route crosses them
- [ ] Route API p95 < 300 ms; response includes `run_id`, `avoided`, `alternates`, `safe_until`
- [ ] Changing departure time or profile re-routes; a pedestrian profile uses the h·v hazard rule
- [ ] Reachability tab isochrones (5/10/15 min) for a chosen facility update when scrubbing; a "collapse" flag appears when the 15-min catchment < 40 % of dry baseline

### 7.5 Alerts centre — `/alerts`

**Layout.** Left: queue grouped by level (Severe / Moderate / Watch) with `AlertCard`s (headline "Hindmata junction: depth likely above 45 cm from 08:20 to 10:00", area, trigger P, hysteresis state "raised 06:45 · persists 2 cycles", channels sent, acknowledge/escalate). Centre: `CapViewer` (CAP 1.2 XML, syntax-highlighted in Geist Mono, "Copy CAP", "Download .xml"). Right: `PhoneMock` — a phone frame showing the WhatsApp message card exactly as the ward officer would receive it (map snapshot PNG, text, "Pumps P-12, P-15 dispatched"), plus a delivery log (dashboard / WhatsApp mock / SMS mock; real WhatsApp when configured — P2). Escalation matrix table at the bottom (ward officer → control room → police/traffic → transit → public).

**Signature motion.** A new alert slides into the queue and the phone mock pops the card with a short shake (300 ms) and a soft sound (muted by default; toggle in settings — on for the demo).

**AC**
- [ ] Alerts raise at P(> threshold) ≥ 0.6 for two consecutive cycles and clear at ≤ 0.3 (hysteresis visible in the card)
- [x] CAP XML validates against the CAP 1.2 schema (test) (2026-09-14, 78f6c0b - the OASIS XSD is vendored with its source URL, retrieval date and sha256 in `services/products/varuna_products/schemas/SOURCE.txt`; `services/products/tests/test_cap_schema.py` validates generated CAP in replay and live modes plus all 272 committed demo documents (272 valid, all Exercise), and proves the validator rejects a missing `<event>` and an undefined status. One generator defect fixed: a cycle time with microseconds or no offset wrote `sent`/`onset`/`expires` values the schema rejects; times are now whole seconds and a naive time is refused. No shipped document changed. ADR-0045)
- [ ] Phone mock renders the demo alert with map snapshot; "Send to my phone" appears only when a sender is configured
- [ ] Acknowledge/escalate change state and are logged with user + time

### 7.6 Pump dispatch — `/pumps`

**Layout.** `PumpBoard`: left column "Available pumps" (`PumpCard`: id, capacity m³/h, depot, status, ETA to selected hotspot); hotspot columns (Hindmata, King's Circle, Sion Circle…) each with a `Sparkline` of predicted excess inflow volume and "minutes above 45 cm: 95 → 55 with plan". Drag a pump to a column (dnd-kit) or press "Optimise" (greedy P0 / MILP P1) to auto-assign; `DispatchOrder` panel renders the plain-language order ("Move P-12 from Parel depot to Hindmata now; ETA 25 min; prevents about 40 min above 45 cm") with "Dispatch pumps" → alert + WhatsApp mock + toast "Pumps dispatched".

**AC**
- [ ] Optimise returns an assignment in < 1 s with expected benefit per hotspot
- [ ] Drag-assign updates benefit numbers live (emulator call)
- [ ] Synthetic inventory is labelled in the panel header
- [ ] Dispatch produces an alert instruction and a phone-mock message

### 7.7 What-if lab — `/whatif` (and the console drawer)

**Layout.** Controls: rain scale (0.5× → 2.0×), tide offset (−0.5 → +1.0 m), "Clean pipes" (picked on the map or "Clean top 14 by β"), pump plan on/off. "Run what-if" → result in < 1 s from the emulator: the map switches to a **diff layer** (segments coloured by Δdepth: blue improved, red worse, grey unchanged) with a left-to-right wipe; `DeltaTable` lists hotspots with before → after depth and minutes-impassable. "Physics check" runs the Twin on the same scenario (≈ 5–8 s) and reports "Emulator vs physics: max difference 4 cm at Sion Circle" with a small bar. Badge on the panel: "Reduced-order emulator calibrated to VARUNA-Twin".

**AC**
- [ ] What-if p95 < 1 s; physics check < 10 s; disagreement is displayed, never hidden
- [ ] Diff layer and delta table agree
- [ ] "Clean top 14" reproduces the demo moment (Hindmata p50 drops substantially at 08:20; exact numbers from the run)

### 7.8 Replay and storm designer — `/replay`

**Layout.** Bundle cards (MUM-2019-07-02 "Reconstructed replay", MUM-IDF-25yr "Design storm", CHN-IDF-25yr) with source notes; clock and controls; cycle log with `CycleBudgetBar` per cycle; `StormDesigner`: a table of cells (birth time, lifetime, start point, velocity, size, peak intensity) with a preview animation of the rain field and the AOI 3-hour accumulation; "Generate bundle" (P1 for editing; P0 shows the demo storm read-only).

**AC**
- [ ] Play/pause/seek/speed control the same clock the console uses
- [ ] Cycle log shows stage timings and mass-balance error for baked and live runs
- [ ] Storm preview animates the radar frames of the selected bundle

### 7.9 City onboarding wizard — `/onboard`

**Purpose.** "City-in-a-box" on stage: Chennai in minutes.

**Layout.** Left: `OnboardingSteps` (Choose area → Fetch open data → Condition terrain → Infer drains → Build graph → First forecast), each with a progress bar, a `LogStream` of real pipeline log lines, and elapsed time. Right: the map building itself layer by layer as steps complete (DEM hillshade → roads → buildings → drains → depressions/hotspot candidates → first depth forecast under the design storm). Finish card: "First forecast, uncalibrated. VARUNA learns Chennai's drains from the next monsoon." with "Open Chennai console".

**Signature motion.** Each completed step stacks its layer with a 400 ms fade; the final depth layer fades in over the streets.

**AC**
- [ ] Runs fully offline from `city/cache/chennai/` in ≤ 5 min on the demo laptop; streams progress over the WebSocket
- [ ] Produces `city/chennai/` and a first run; the city switcher then lists Chennai
- [ ] Every log line is real (from the pipeline), not scripted

### 7.10 Verification — `/verify`

**Layout.** Event selector; `VerificationGrid` of headline scores (CSI/POD/FAR at 30 cm per chronic spot, depth MAE at pins, timing error, Brier, ROC AUC, frame-to-product latency, routing value); `ContingencyTable`; `ReliabilityDiagram`; `SkillByLeadChart` (rain CSI at 20/40 mm/h versus lead time — "confidence decays after 90 minutes" is visible); a "Where we are wrong" list (pins the model missed, with the likely reason); the limitations list from blueprint §15.1 in plain text.

**AC**
- [ ] All scores computed by `services/verify` from artifacts, not typed in
- [ ] Charts have axes, units, and the ground-truth count
- [ ] Limitations text present and linked from the landing footnote

### 7.11 Public map — `/map` and citizen report — `/report`

**Public map (mobile-first).** Three-colour street map (passable / caution / impassable for the selected vehicle), a bottom sheet listing nearby streets with "passable until 09:25", vehicle selector, saved-location alerts (local storage), language toggle EN/HI/MR (P1), a floating "Report water" button, and the honesty line "Forecast from the last VARUNA run at 06:40; updates every 5 minutes".

**Report flow.** Step 1 location (auto + adjust) → Step 2 photo (optional, camera) → Step 3 depth chips: ankle (≈ 10 cm), knee (≈ 45 cm), waist (≈ 90 cm) → Submit → confirmation "Thanks — your report improved the forecast for 3 streets" (the count comes from Pulse's feedback field). Reports go to `POST /v1/reports` and appear on the console as observations.

**AC**
- [ ] 390 × 844 layout; 44 px targets; bottom sheet drag works
- [ ] Report creates an observation visible in Drain X-ray and the console within one cycle
- [ ] Works with the API offline (last forecast cached; report queued) — P1

### 7.12 API explorer — `/api` (P1) and design page — `/design`

`/api` embeds the OpenAPI explorer with the demo `run_id` pre-filled and three "try it" presets (segments in bbox, ambulance route, what-if). `/design` renders every token, type size, component and its states; it is the visual regression baseline.

### 7.13 Global behaviours

- Command palette (⌘K): jump to hotspot, facility, pipe id, screen, run; actions ("Dispatch pumps at…", "Clean top pipes in what-if", "Compute live").
- Error boundary per panel (a broken panel never blanks the map).
- 404 page with the map faintly behind and "This street does not exist. Open the console."
- Settings drawer: sound on/off, units (cm only — locked, explain why), risk tolerance defaults per profile, replay speed default, theme (dark only).


---

## 8. Motion catalogue (the only motion allowed; add a row before adding a motion)

Global easing: `cubic-bezier(0.2, 0.8, 0.2, 1)` for UI; springs (`stiffness 400, damping 32`) for handles and pins; map flights use MapLibre `flyTo` (curve 1.4). Durations: 150–220 ms micro, 300 ms panel, 900 ms map flight, ≤ 1.2 s draw-on. Every row has a reduced-motion fallback. Nothing loops on the console except the surcharge pulse and the reversed-flow dash (both are data, not decoration).

| ID | Where | Motion | Implementation | Trigger | Reduced motion |
|---|---|---|---|---|---|
| M1 | Landing hero | Map auto-scrubs −60 → +180 in 8 s, loops, pauses on hover | `CityMap mode="hero"`, rAF timer swapping preloaded frames | page load | static +120 min frame |
| M2 | Landing hero copy | Blur-fade entrance, stagger 60 ms, once | Magic UI `BlurFade` | page load | instant |
| M3 | Landing cycle diagram | Beams travelling between pipeline nodes | Magic UI `AnimatedBeam` | in view | static arrows |
| M4 | Landing proof, hotspot drawer, delta tables | Numbers roll to new values | `@number-flow/react` | value change / in view | instant |
| M5 | Landing roadmap | Beam traces the timeline on scroll | Aceternity `TracingBeam` | scroll | static line |
| M6 | Console time bar | Scrub handle springs; layers restyle instantly | `motion` spring on the handle; deck.gl `updateTriggers` | drag / keys | same (no spring) |
| M7 | Console play mode | Depth colours tween between 5-min steps | deck.gl `transitions: { getColor: 120 }` only while playing | play | no tween |
| M8 | Surcharge markers | Expanding ring pulse, 1.6 s | CSS keyframes on `IconLayer` sprite / HTML marker | data | static ring |
| M9 | Reversed-flow edges | Dash offset animates in the flow direction | deck.gl `PathStyleExtension` dash + time uniform | data | static dashed red |
| M10 | Hotspot select | 900 ms fly-to + ring highlight fades in | MapLibre `flyTo`, `ScatterplotLayer` opacity | click | jump cut |
| M11 | Hotspot/attribution drawer | Slides in 220 ms; responsible pipes glow in sequence (40 ms stagger) | `motion` layout + deck.gl color updates | open | instant |
| M12 | Drain X-ray before/after | Pipe colours cross-fade 300 ms; hotspot depth rolls | deck.gl `transitions`, NumberFlow | toggle | instant |
| M13 | What-if result | Diff layer wipes left→right 500 ms; delta rows highlight 600 ms | CSS mask on a canvas overlay; `motion` | result | instant |
| M14 | Route planner | Naive route draws dashed grey, VARUNA route draws on over 1.2 s; avoided segments flash once | deck.gl `TripsLayer` or progressive `getPath` | result | both shown at once |
| M15 | Reachability | Isochrone polygons morph on scrub (300 ms) | deck.gl `transitions` on `PolygonLayer` | scrub | instant |
| M16 | Alerts | Card slides into the queue 180 ms; phone mock message pops + 300 ms shake + optional sound | `motion` `layout` on cards keyed by alert identity (scope, place, level); the shake as framer keyframes; the chime synthesised with Web Audio (`lib/sound.ts`), unlocked by the first gesture after sound is switched on | new alert | fade only, no sound |
| M17 | Pump board | Card flies to the hotspot column; benefit numbers roll | dnd-kit + `motion` `layout` | drag / optimise | instant move |
| M18 | Ground-truth pins | Pin drops (scale 0→1 spring) with a 600 ms ripple; ticker row slides in | `IconLayer` + HTML ripple overlay; `motion` | replay clock passes timestamp | pin appears, no ripple |
| M19 | Onboarding | Each completed step stacks a map layer with a 400 ms fade; final depth fade-in | layer opacity transitions | step complete | instant |
| M20 | Mode banner | Colour cross-fade 300 ms; degraded pulses once | CSS transition + one-shot keyframe | mode change | colour change only |
| M21 | Cycle budget bar | Stage segments fill as timings arrive | width tween 200 ms | WS `cycle.stage` | instant |
| M22 | Skeletons | Shimmer only | CSS gradient keyframe | loading | static blocks |
| M23 | Page navigation | None — instant | — | — | — |
| M24 | Public map bottom sheet | Drag with rubber-band, snap points | `motion` drag | drag | tap to expand |
| M25 | Replay radar preview | Radar frames loop at 4 fps (25 frames, 6.25 s), pauses on hover, focus and when the tab is hidden | preloaded images drawn to a canvas via rAF | bundle selected | static middle frame |
| M26 | Landing hero intro (added 2026-09-14; it shipped in P9.1 without a row) | Globe turns 1.4 s to face Mumbai and unrolls into a flat world map over 2.6 s, then cross-fades 900 ms into the M1 city map once that map's frames are decoded; once per load | interpolated d3 projection on an SVG via rAF (`globe-intro.tsx`); CSS opacity transition (`hero.tsx`) | page load | finished flat map, then a cut to M1's static +120 min frame |
| M27 | Citizen dashboard entry and ward officer's desk (added 2026-09-19 for `/dashboard`, `UI_SPEC.md` 2; `/authority` 2026-09-23) | The vector Earth turns 1.4 s to bring India to the meridian, the frame approaches India over 1.6 s as the sphere flattens, then narrows to the Mumbai AOI over 1.0 s and cross-fades 900 ms into the screen's own map, which is mounted and framed on the same bounds behind it; once per session, and skippable | the same interpolated d3 projection as M26 (`globe-intro.tsx`) with a third act; CSS opacity transition to the dashboard map | opening the dashboard or the desk | static Mumbai frame, then a cut to the framed map |
| M28 | Console 3D, drain X-ray (added 2026-09-23 for the photorealistic city) | Google's photorealistic surface fades to 20 % opacity over 300 ms while the inferred pipes beneath it fade up to full | deck.gl `transitions` on layer `opacity` | X-ray toggle | both layers at their final opacity, no fade |
| M29 | Alert centre (added 2026-09-26 for "See more") | An alert's details open under its row over 220 ms; one alert is open at a time | `motion` height and opacity on the details block | See more | details shown at once, no expand |
| M30 | Drain health before and after (added 2026-09-26) | A drag handle splits the prior on the left from the posterior on the right; pressing Before or After slides the split across in 300 ms | clip on the posterior pipe layer, `motion` tween on the handle | drag / Before or After | split jumps, no slide |
| M31 | What-if physics run (added 2026-09-26 for the tide lever) | A bar fills as each of the Twin's output steps arrives, and the physics answer replaces the emulator's with a 300 ms cross-fade | width tween 200 ms on the bar; opacity cross-fade on the result | Twin progress | bar jumps to each step, result swaps with no fade |
| M32 | Citizen report pins on the dashboard and the ward map (added 2026-09-26) | A new report's pin drops in (scale 0→1 spring) with a 600 ms ripple, as M18 does | the M18 pin layer on deck's animation clock | a report appears | pin appears, no ripple |
| M33 | Jalayantra dispatch map (added 2026-09-27) | Each dispatched pump travels its road route from depot to hotspot over 1.2 s, the route drawing behind it, staggered 150 ms per pump | deck.gl `PathLayer` draw-on with a moving marker on deck's animation clock | Optimise / dispatch | pumps at their hotspots and routes drawn, no travel |
| M34 | Jalayantra hotspot gauges (added 2026-09-27) | A hotspot's water gauge drains from the no-pump level to the planned level over 900 ms as its pump arrives, and its minutes above 45 cm roll down | CSS `transform: scaleY` on the gauge fill; NumberFlow | pump arrival | gauge and number at the final value, no drain |
| M35 | Jalayantra arrival timeline (added 2026-09-27) | Each pump's arrival marker slides onto the flood-window timeline in 300 ms when the plan lands | `motion` layout on the markers | Optimise | markers at their times, no slide |
| M36 | Pramana contingency by threshold (added 2026-09-29) | Each threshold's column fills from the bottom like rising water, hits then misses then false alarms (600 ms per band, 150 ms stagger across thresholds), under a wave crest whose amplitude decays to flat within 1.2 s; the counts and CSI/POD/FAR roll to their values; choosing another threshold drains and re-pours the columns | SVG clip rects tweened with `motion`; the crest an SVG path with a decaying sine on rAF, stopped when flat; NumberFlow | in view / threshold change | columns at their final heights, flat crest, final numbers |
| M37 | Theme switch (added 2026-09-29) | None: dark and light swap in one frame | CSS variables under `[data-theme]` | toggle | same |

---

## 9. v0 workflow — first drafts of components, then adapt

v0 (v0.app, or the v0 MCP server) generates shadcn + Tailwind + motion components quickly. Use it for **first drafts of the components below**, then adapt: move the file to `components/v0/<Name>.tsx`, replace every raw colour with tokens, replace fonts with the token families, add the reduced-motion branch, add typed props, delete sample data that does not fit the domain, and register the component on `/design`. Never ship a v0 file unmodified. Magic UI and Aceternity components are installed with the shadcn CLI registry commands from their sites and live in `components/ui/`.

**Paste this preamble into every v0 prompt:**

```
Stack: Next.js App Router, TypeScript, Tailwind v4, shadcn/ui, motion (framer-motion). Dark UI only.
Use ONLY these CSS variables for colour (no raw hex): --ink #0A1020 (background), --deep #111A2E (panels),
--well #17233B (inputs/hover), --line #24314F (borders), --text #E3EAF6, --text-2 #A7B4CC, --text-3 #7E8DA9,
--tide #2DD4BF (primary accent), depth ramp --depth-1 #3B82F6 (5–15 cm), --depth-2 #F59E0B (15–30),
--depth-3 #F97316 (30–45), --depth-4 #EF4444 (45–60), --depth-5 #B91C1C (>60), --danger #F87171.
Fonts: font-display (Bricolage Grotesque) for headings, font-sans (Geist) for UI, tabular numbers on all figures.
Radius 12px panels, 8px controls. No drop shadows; use 1px borders var(--line). Lucide icons only, no emoji.
Sentence case; no ALL-CAPS labels; no arrows on buttons; no lorem ipsum — sample data must be about urban
flooding in Mumbai (Hindmata junction, King's Circle, Sion Circle, Milan subway, KEM Hospital, Sion Hospital).
Respect prefers-reduced-motion. Export one default component with typed props and a small sample-data file.
```

**Prompts (one component each; keep the preamble):**

1. **Landing hero shell** — full-viewport section with a slot for a live map (`children`), left-third overlay with a wordmark "VARUNA", headline "Every street. Three hours early.", one sentence of copy, primary button "Open the console", secondary "Watch the 2 July 2019 replay", a small credit line, and a compact depth legend bottom-left. Copy enters with a blur-fade stagger once.
2. **Six-engines bento** — a bento grid of six tiles with varied sizes (one large, two medium, three small), each with a title, a one-line job, an inline SVG micro-diagram slot, and a hover/focus reveal of a second sentence. No numbering, no gradients, no scale bounce.
3. **Five-minute cycle pipeline** — horizontal pipeline with nodes (Ingest, Sky, Twin, Flash, Pulse, Products, Command/Route/Public) connected by animated beams, each node showing a stage time in ms passed as props; numbered because it is a sequence.
4. **Proof numbers** — three large numbers (CSI, depth MAE in cm, lead time in min) that count up when scrolled into view, with a plain-text footnote slot; no gradients.
5. **Hotspot rail** — a panel with tabs (Hotspots, Alerts, Pumps, Reachability) and a list of ranked rows: rank, name, depth chip (colour by the depth ramp thresholds), time-to-peak, a tiny sparkline slot, exposure icons; selected row state; keyboard navigable.
6. **Time bar** — a bottom bar with play/pause, speed menu (1×/10×/30×/60×), a scrub slider from −60 to +180 minutes with 15-minute ticks, observed vs forecast halves tinted differently, a spread band under the track (data via props), the current time and lead on the right, and a "Compute live" button with a segmented progress bar (decode, sky, twin, flash, pulse, products) showing milliseconds.
7. **Hotspot drawer** — slide-in panel: a big depth number with a sub-label, a fan chart slot, a safe-until table (two-wheeler, car, bus, ambulance, pedestrian), an exposure row, and a "Why this junction floods" list of pipes with a blockage value and the depth each explains; buttons "Clean in what-if" and "Dispatch pumps here".
8. **Alert card + CAP viewer + phone mock** — three columns: a queue of alert cards with level chips and acknowledge/escalate, a syntax-highlighted XML viewer with copy/download, and a phone frame showing a WhatsApp-style message card with a map image slot and text.
9. **Pump board** — kanban with an "Available pumps" column and hotspot columns; draggable pump cards (dnd-kit); each hotspot column shows a sparkline slot and "minutes above 45 cm: before → after"; an "Optimise" button and a dispatch-order panel with a "Dispatch pumps" button.
10. **What-if drawer** — sliders for rain scale and tide offset, a chips list of selected pipes with "Clean top 14", a pump-plan switch, "Run what-if", a result state with a delta table (hotspot, before, after, minutes impassable) and a "Physics check" button that shows a small agreement bar; a badge "Reduced-order emulator calibrated to VARUNA-Twin".
11. **Onboarding wizard** — vertical steps (Choose area, Fetch open data, Condition terrain, Infer drains, Build graph, First forecast) each with a progress bar, elapsed time and a streaming log area; a right-side slot for a map; a finish card with a button.
12. **Route comparison** — two-column card "Naive (shortest)" vs "VARUNA" with ETA, distance, max expected depth, safe-until; an "Avoided" list with segment name and probability; alternates list; "Send to dispatch".
13. **Global chrome** — top bar with wordmark, city switcher, a mode banner pill (live / replay / baked / degraded variants), a run stamp with copy-to-clipboard, a verification chip, and a command-palette button.
14. **Public map mobile screen** — full-height map slot, a draggable bottom sheet listing nearby streets with "passable until" times and a three-colour legend, a vehicle selector, a language toggle, a floating "Report water" button.
15. **Citizen report flow** — three steps with a progress indicator: location with an adjust map slot, optional photo, depth chips ankle/knee/waist with the centimetre hint; a confirmation screen with a count "improved the forecast for N streets".

Magic UI / Aceternity components to install (and where they are allowed): `BlurFade` (M2), `AnimatedBeam` (M3), `BentoGrid` (landing engines), `NumberTicker` is replaced by NumberFlow (M4), `TracingBeam` (M5), `BorderBeam` only on the landing "Open the console" card if it does not distract (optional), `ShimmerButton` never on the console.

---

## 10. Data layer

### 10.1 City-in-a-box pipeline (`services/city`) — `make city CITY=mumbai`

Config `services/city/configs/mumbai.yaml`: bbox, CRS, grid size, nests, hotspot register path, tidal boundary edges, design intensity (25 mm/h legacy, 50 mm/h upgraded corridors).

Steps (each a function with a cached output and a validation check):

1. **DEM** — Copernicus GLO-30 tiles from the public AWS bucket `copernicus-dem-30m` (no key) → mosaic → reproject to the metric CRS → 30 m grid (and 5 m nests by bicubic resampling, P1). Fallback: NASADEM/SRTM via OpenTopography with a key. Cache under `city/cache/`.
2. **OSM** — OSMnx: drivable + service roads (graph), buildings, `waterway=drain|canal|stream|river`, `tunnel=culvert`, `bridge=yes`, railway stations, `amenity=hospital|fire_station`, shelters (`amenity=school|community_centre` as proxies, labelled). Save GeoPackages.
3. **Land cover** — ESA WorldCover 10 m tile(s) (public) → imperviousness raster; plus building footprint burn; fallback: OSM landuse + building density.
4. **Hydro-conditioning** — burn buildings +5 m; carve road centrelines −0.15 m; keep underpasses/subways as sinks (OSM `tunnel`/`layer<0` + the hotspot register); breach culverts/bridges; priority-flood breaching for spurious pits (< 900 m²) with WhiteboxTools (`BreachDepressionsLeastCost`) or RichDEM fallback; roughness raster (Manning n by class: asphalt 0.016, open ground 0.04, vegetation 0.07, water 0.03, buildings blocked); CN raster (90–98 by land use); depression map (remaining pits with depth and area) → hotspot candidates.
5. **Road segments** — split OSM ways at intersections; attributes class, lanes, oneway, length, z_min/z_mean from the DEM, ward (from an OSM admin boundary if available), exposure weight = f(class, hospital/station within 300 m, building density).
6. **Surface units** — watersheds draining to each inlet node (D8 from the conditioned DEM) capped to 0.5–2 ha; fallback 150 m hexagons. Attributes: area, imperviousness, CN, mean n, depression depth, segment_id, cells.
7. **Synthetic drain graph** (blueprint §7.1) — nodes: inlets every 40 m along roads + every intersection + depression bottoms + hotspot register points; outfalls where road-following drains meet OSM waterways/coast; trunks along waterways; edges oriented downhill toward the nearest outfall via a shortest-path tree with an uphill penalty; minimum slope 0.3 %; sizing by the rational method (C from imperviousness, i = design intensity, A = contributing area) → Manning full-flow diameter snapped to 450/600/900/1200/1500 mm, box drains for trunks; invert depth 1.5 m (3 m trunks); β prior Beta(a, b) with mean from land use (markets/informal settlements 0.35, residential 0.2, arterial 0.15) and κ prior mean 0.25; `confidence = "inferred"` on every element; tidal flag on coastal outfalls.
8. **Assets** — hospitals, fire stations, stations, shelters from OSM; pumping stations and holding tanks from a hand-curated `assets/mumbai_infra.json` with `source_url`; synthetic mobile pumps (`synthetic: true`).
9. **Hotspot register** — `hotspots.geojson` from public BMC chronic waterlogging lists and news (≥ 10 points for the AOI, each with `source_url`, verified coordinates).
10. **Validation report** — `city/mumbai/REPORT.md`: DEM depressions vs hotspot register overlap (target ≥ 60 % within 150 m), drain graph connectivity (every node reaches an outfall), total inferred pipe length, PNG maps.
11. **Exports** — GeoParquet/GeoJSON for the API; simplified GeoJSON for the map (tolerance 2 m); graph tables for Flash (`nodes.parquet`, `edges.parquet`, `inlet_links.parquet`).

### 10.2 Replay bundle (`bundles/<ID>/`) — `make bundle BUNDLE=…`

```
manifest.json      # id, city, label ("Reconstructed replay" | "Design storm"), t0, t1 (IST), cadences,
                   # radar domain, sources[] with urls, seed, notes on what is synthetic
radar/frames.zarr  # dBZ[t, y, x] every 10 min over the 60 km domain (synthetic or decoded)
truth/rain.zarr    # mm/h[t, y, x] every 5 min (synthetic bundles only; used for verification)
gauges.csv         # ts, station_id, lat, lon, mm_5min   (synthetic from truth + noise, real station locations)
tide.csv           # ts, stage_m, source ("tide table <url>" | "illustrative")
traffic/speeds.parquet   # ts, segment_id, kmh, baseline_kmh (synthetic: baseline + anomalies at reported flooding)
reports.jsonl      # ts, lat, lon, depth_hint (ankle/knee/waist), text, photo?, synthetic:true|false, source_url?
ground_truth.geojson     # REAL, sourced pins: ts, name, depth_cm?, kind (log/news/social), source_url
```

**Storm designer (`services/replay/storm.py`).** N convective cells, each with birth time, lifetime, start point, velocity (wind from SW ~8 m/s for the demo), σ (2–6 km), peak intensity (40–120 mm/h) and a sine growth/decay envelope; background stratiform 2–5 mm/h; rain field `R(x,y,t) = Σ peak·g(t)·exp(−d²/2σ²)`; radar frames via Marshall–Palmer inverse plus speckle noise, a coverage circle and a 5-dBZ quantisation to mimic decoded images. Calibrated so the AOI 3-hour accumulation matches the public gauge totals documented in `manifest.sources` for the event window (document exactly which number and from where). Seed 2019.

**Synthetic streams.** Gauges: truth sampled at real IMD/BMC AWS locations (Santacruz, Colaba plus BMC stations — verify) with 10 % multiplicative noise and 15-min cadence. Traffic: weekday-hour baseline speeds by road class; anomalies (speed collapse to < 5 km/h) synthesised at the ground-truth pins' times ± 10 min with spatial spread along the segment and its neighbours; 3 % random unrelated slowdowns as confounders; labelled synthetic. Reports: 15–30 synthetic reports at hotspots with depth chips consistent with the truth model, plus the real ground-truth pins.

**Ground truth (real).** Curate from BMC/MCGM disaster-management logs, news archives (with timestamps), IIT-B Mumbai Flood platform if available, and geotagged posts referenced by news. Rules: `source_url` mandatory; keep the time uncertainty (`ts_uncertainty_min`); depth only when the source states or shows it; a minimum of 10 pins inside the AOI for MUM-2019-07-02; if the event does not yield 10, switch the demo bundle to another Mumbai event (5 Aug 2020, 1–2 Jul 2019 overnight, 26 Jul 2005) and document the choice.

**Replay service (`services/replay/clock.py`).** Publishes bundle streams to the in-process bus at real or accelerated speed, exposes play/pause/seek/speed, and triggers a cycle every 5 sim-minutes; in `baked` mode it publishes the pre-computed run for that cycle instead of computing it.

### 10.3 Run artifacts (`data/runs/<run_id>/`)

`run_id = <CITY>-<cycle_ts UTC compact>-sky<v>-twin<v>-flash<v>[-live|-baked]`

```
run.json                 # city, cycle_ts, radar_frame_ts, versions, mode, ensemble_n, stage_ms{}, mass_balance_err, bundle
rain/cube.zarr           # R[m, t, y, x] 20 members × 36 steps at 500 m; quantiles.zarr
depth/p50_<step>.png + .pgw, p90_<step>.png, prob30_<step>.png   # 36 steps × stats, fixed ramp, world files
depth/bounds.json        # lon/lat bounds for BitmapLayer
segment_forecast.parquet # run_id, segment_id, valid_ts, depth_p10/p50/p90, p_gt_15/30/45/60, safe_until{profile}
node_forecast.parquet    # node_id, valid_ts, head_p50, p_surcharge, q_surcharge_p50, responsible_edges[]
hotspots.json            # ranked list with time_to_peak, exposure, attribution
drain_health.geojson     # edges with beta_mean, beta_sd, kappa, confidence, last_update, explains[]
observations.parquet     # everything assimilated in this cycle with its effect
alerts/<id>.cap.xml + alerts.json
pump_plan.json
reachability.geojson     # per facility, per slice, 5/10/15-min polygons + collapse flag
verification.json        # only in verify runs (per event)
```

### 10.4 Chennai pre-cache

`make city CITY=chennai --cache-only` downloads DEM/OSM/WorldCover for `CHN-SOUTH` into `city/cache/chennai/` before the finale; the wizard then runs every step from cache with `VARUNA_OFFLINE=1`.


---

## 11. Engines — prototype implementations (science reference: blueprint §6 and Appendix A)

Every engine is a Python package under `services/`, exposes a pure function `run(inputs) -> outputs` plus a CLI, writes only to the run directory, records its stage time, and has unit tests. Simplifications go in `docs/SIMPLIFICATIONS.md`.

### 11.1 VARUNA-Sky (`services/sky`) — target ≤ 5 s per cycle

Inputs: last 3 radar frames (dBZ, 500 m, 60 km domain), gauge readings (last 60 min), NWP field (none in P0 → blend disabled and labelled). Steps:

1. **QC**: coverage mask; clutter = pixels with near-zero temporal variance over the last 6 frames; mark attenuation shadow behind cells > 50 dBZ (flag only).
2. **Z–R**: `Z = 10^(dBZ/10)`; adaptive `(a, b)` by least squares on `log R_gauge` vs `log Z` over co-located pairs from the last 60 min, clamped to a ∈ [100, 400], b ∈ [1.1, 1.8]; fallback Marshall–Palmer (200, 1.6) when < 8 pairs. Test: on synthetic data generated with MP, the fit recovers a, b within 10 %.
3. **Gauge merge**: mean-field bias `MFB = ΣG/ΣR` then residual interpolation by inverse-distance weighting (KED via `pykrige` is P1). Merged field honours gauges within 5 %.
4. **Motion**: pySTEPS Lucas–Kanade (`pysteps.motion.get_method("LK")`) on the last three frames.
5. **Nowcast**: pySTEPS STEPS (`pysteps.nowcasts.get_method("steps")`), 20 members, 5-min steps, 36 lead times, 6 cascade levels, AR(2), probability matching on. Fallback if pySTEPS is unavailable: own semi-Lagrangian advection + per-scale AR(2) decay + spatially correlated noise (`services/sky/fallback_steps.py`).
6. **Products**: rain cube resampled to the 30 m AOI grid (bilinear) and to the 500 m Sky grid; p10/p50/p90 and P(> 20, 40 mm/h) per pixel; AOI-mean hyetograph per member for the time-bar spread band; skill versus lead time computed later by `services/verify` against `truth/rain.zarr` (synthetic bundles) or gauges.

Tests: ensemble spread grows monotonically with lead; total rain of the ensemble mean over the domain is within 15 % of persistence at lead 0; a dry input yields a dry cube.

### 11.2 Terrain and effective rainfall (`services/twin/hydrology.py`)

`R_eff = I·max(0, R − d_s) + (1 − I)·max(0, R − f)` with depression storage `d_s = 1.5 mm` (applied once per event as a bucket) and infiltration by SCS-CN in incremental form (`S = 25400/CN − 254`); CN 90–98 (saturated monsoon soils). Imperviousness and CN rasters from §10.1.

### 11.3 VARUNA-Twin 2D (`services/twin/swe2d.py`) — target ≤ 8 s for a 3-hour AOI run on an 8-core CPU

Local-inertial shallow water (Bates et al. 2010), explicit, Numba `@njit(parallel=True, fastmath=True)`:

- Face flux: `q^{t+Δt} = [q^t − g·h_f·Δt·∂(h+z)/∂x] / [1 + g·Δt·n²·|q^t| / h_f^{10/3}]`, `h_f = max(h+z) − max(z)` at the two cells; `q = 0` when `h_f < 1 mm`.
- Continuity: `h^{t+Δt} = h^t + Δt·(Σq_in − Σq_out)/Δx + Δt·(R_eff − Q_inlet/A + Q_surch/A)`.
- CFL: `Δt = 0.7·Δx/√(g·h_max)`, clamped to [0.5, 10] s.
- Buildings: blocked cells (no flux). Domain edges: closed, except sea/creek boundary cells (from the city config) where `h + z = tide stage(t)` when stage > z.
- Outputs every 5 min: `h[t, y, x]` (36 steps); mass-balance error asserted every 100 steps: `|ΔV_stored − V_in + V_out| < 0.1 %` of `V_in`.
- Nests (P1): same kernel on 5 m crops at Hindmata and King's Circle with boundary heads interpolated from the 30 m run.

Tests: still water stays still; conservation on a closed basin under uniform rain; radial spread on a flat plane is symmetric; a tide rise floods low coastal cells only; runtime budget.

### 11.4 Drain graph 1D (`services/twin/drain1d.py`) — "diffusive-wave-lite" (P0); PySWMM dynamic wave (P1)

State: node heads `H_j`; edge flows `Q_e`. Explicit inner step 1 s.

- Effective area `A_eff = (1 − β_e)·A_e`; fill fraction from `min(H_up − z_inv, D)/D`; pressurised when both ends above the crown.
- Flow: `Q_e = sign(ΔH)·min(Q_full,e, (1/n)·A_eff·R_h^{2/3}·√(|ΔH|/L))` with `Q_full,e = (1/n)·A_eff·R_h^{2/3}·S^{1/2}` (Manning). Backflow is the negative sign when the downstream head is higher (tide-locked outfall).
- Node continuity: `dH_j/dt = (ΣQ_in + Q_inlet − ΣQ_out − Q_surch)/A_s,j`; `A_s` = manhole area, plus a Preissmann slot width when surcharged so heads stay bounded.
- Outfalls: fixed stage = tide(t) (with flap-gate option that blocks reverse flow); free outfalls at invert.
- Surcharge when `H_j > z_g,j` (§11.5). Pumps/tanks as controlled sinks with capacity curves (Hindmata tanks).

Tests: conservation; a single pipe at capacity gives Manning flow; raising the outfall stage above the trunk invert produces negative Q on the trunk and surcharge at the first upstream manhole (this is the demo's red reversed-flow edge).

### 11.5 Coupling (`services/twin/coupling.py`)

Per sync interval `Δt_sync = 5 s` with fluxes frozen and limited so no cell goes negative:

- Inlet capture on the inlet's cell depth `h`: `Q_weir = 1.66·L_i·h^{3/2}`, `Q_orifice = 0.6·A_o·√(2gh)`, `Q_inlet = (1 − κ)·min(Q_weir, Q_orifice, Q_avail)`, `Q_avail` = remaining node capacity given `H_j`.
- Surcharge when `H_j > z_g,j + h`: `Q_surch = 0.6·A_m·√(2g(H_j − z_g,j − h))`; reversed when the surface is higher (manhole drains the street).

### 11.6 VARUNA-Pulse (`services/pulse`) — target ≤ 3 s per cycle

- **Traffic anomaly detector**: per segment `z = (v − μ_wd,hr)/σ`; observation when `z < −2.5` for ≥ 2 consecutive snapshots during rain and not explained by network-wide congestion (neighbouring dry segments within 500 m have `z > −1`). Depth prior: `v < 5 km/h → h ≥ 20 cm (sd 8)`; `5–15 km/h → 10–20 cm`. Confounder handling: reject if the segment has an incident tag in the feed (synthetic feed includes 3 % confounders).
- **Reports**: depth chips ankle/knee/waist → 10/45/90 cm with sd 8/12/15; de-duplicate within 50 m and 10 min; reporter trust weight (synthetic 1.0).
- **EnKF** over `θ = logit(β_e)` for pipes within 3 hydraulic hops of any observation (plus `κ` at observed inlets), `N_e = 50` members from the current posterior; observation operator `H(θ)`: run `drain1d` with the surface inflows frozen from the last Twin run (milliseconds per member) and read depth at observation cells from the surcharge/inlet balance via the Flash-lite emulator; gain `K = P_θy (P_yy + R)^{-1}`; update with perturbed observations; localisation by hop distance; posterior persists across cycles and relaxes toward the prior with a 30-day time constant (desilting resets).
- Products: `drain_health.geojson` (posterior mean/sd per pipe, capacity reduction %, hotspots explained), `observations.parquet` with the β change each caused, model–observation disagreement list, and the citizen feedback count ("improved the forecast for N streets" = segments whose p50 changed by > 3 cm).

Tests: on a synthetic truth with two blocked pipes and 20 observations, the posterior mean ranks those two pipes in the top 5 with sd reduced by ≥ 40 %; no observation moves a pipe more than 3 hops away.

### 11.7 VARUNA-Flash (`services/flash`) — P0 reduced-order emulator; P1 GNN

**Flash-lite (P0).** Per surface unit a two-reservoir cascade (Nash) with storage coefficient `k_u` and a depth–storage curve from the DEM depression geometry, coupled to `drain1d` through the inlet capture rule; parameters `k_u` fitted by least squares to ≥ 200 Twin runs (design storms × β draws) generated by `make train`; runs 3 hours for the whole AOI in ≤ 300 ms. Reports RMSE and CSI at 30 cm versus Twin on held-out runs to `docs/verification/flash_lite.json` and on `/verify`. UI badge: "Reduced-order emulator calibrated to VARUNA-Twin".

**Ensemble products**: 50 members = 20 Sky members × parameter draws from the Pulse posterior (with replacement) → per-segment quantiles and exceedance probabilities; MC noise on `k_u` for structural spread.

**What-if**: rain scale, tide offset, cleaned pipes (β → 0.05), pump plan (extra outflow at hotspots) → emulator run → Δ products; "Physics check" re-runs Twin on the same scenario and reports max |Δ| at hotspots.

**Attribution**: for each hotspot, finite-difference sensitivity — clean each candidate pipe within 5 upstream hops one at a time in the emulator, rank by depth reduction at the peak; report the top pipes and the combined effect of cleaning the top 14.

**GNN surrogate (P1).** PyTorch Geometric Encode–Process–Decode (hidden 128, 12 message-passing rounds, residual, LayerNorm), 15-min autoregressive rollout, loss = MSE on `log(1+h)` and `H` + exceedance-weighted term + soft mass-balance penalty; trained on ≥ 500 Twin runs with noise injection; served behind the same `FlashModel` interface; gradient attribution replaces finite differences. Report RMSE ≤ 5 cm and CSI(30 cm) ≥ 0.85 versus Twin, or say what was achieved.

### 11.8 Products (`services/products`) — target ≤ 2 s

- **Segment depth**: for each road segment, the 90th percentile of cell depths within a 15 m buffer (documented choice); quantiles across members; `P(h > 15/30/45/60)`; safe-until per profile = first `valid_ts` where `P(h > θ_profile) > risk_tolerance` (defaults: two-wheeler 15 cm, car 30 cm, bus/truck 45 cm, rescue 60 cm, pedestrian `h ≥ 30 cm or h·v ≥ 0.5`; tolerance 0.5, ambulance 0.2).
- **Rasters**: PNG per step per stat with the fixed ramp from tokens + world file + `bounds.json`.
- **Hotspots**: `expected impact = P(impassable at peak) × exposure_weight`; time-to-peak; attribution from Flash.
- **Node surcharge**: from the Twin run; responsible edges from attribution.
- **Alert candidates** → Command (§11.10). **Reachability** → Route (§11.9).

### 11.9 VARUNA-Route (`services/route`) — target route ≤ 300 ms, reachability ≤ 2 s per facility

- Graph: OSMnx drive graph → `networkx.DiGraph` with `t_e` from length and class speeds; segment forecast joined by `segment_id`.
- Cost at departure `τ` for profile `v`: `c_e(τ, v) = t_e·φ(h_e(τ))` if `P(h_e(τ) > θ_v) < p_max` else ∞; `φ(h) = 1` below 5 cm, rising linearly to 3× at `θ_v`; `h_e(τ)` uses the 15-min slice reached at arrival time (FIFO time-dependent Dijkstra with a heap keyed on arrival time).
- Alternates: two more routes by penalising used edges (×3). Explanation: the avoided segments with their probabilities at the time they would have been reached.
- Reachability: forward time-dependent Dijkstra from each facility per slice → nodes within 5/10/15 min → `shapely.concave_hull(ratio=0.3)` polygons; `collapse = area_15 < 0.4 × dry baseline`.
- Feeds: `road-conditions` GeoJSON (impassable/degraded segments with validity windows); GTFS-RT service alerts (P1); Mappls/Google incident format adapter (P2). Rust Axum service with contraction hierarchies is P2 — keep the request/response contract identical.

Tests: with one impassable segment on the only short path, the route detours; FIFO property holds on a synthetic graph; isochrone areas are monotone non-increasing as depth rises.

### 11.10 VARUNA-Command (`services/products/alerts.py`, `pumps.py`)

- **Alert state machine** per segment/ward: raise when `P(h > θ) ≥ 0.6` for two consecutive cycles; clear when `≤ 0.3`; levels: Watch (15 cm), Moderate (30 cm), Severe (45 cm); CAP 1.2 XML (identifier, sender, sent, status Actual/Exercise — replay uses **Exercise**, live uses Actual — msgType, scope, info with category Met, event "Street flooding", urgency, severity, certainty, headline, instruction with the route hint and the pump plan, area polygon); WhatsApp/SMS message templates rendered from the same alert; escalation matrix from `config/escalation.yaml`.
- **Pump dispatch**: excess inflow volume per hotspot `V_i(τ)` from the emulator (inflow − drain outflow); pumps `p` with capacity `c_p` and travel time `d_p,i` from the Route API; greedy (P0): iterate pumps by capacity, assign to the hotspot with the largest weighted remaining excess; MILP (P1) with OR-Tools per blueprint §6.10. Benefit = minutes above 45 cm avoided (emulator with extra outflow). Re-solve every 15 sim-minutes and on demand.

### 11.11 Cycle orchestrator and bus (`services/cycle`)

`cycle.run(bundle, t)` executes decode/QC → Sky → Twin (ensemble mean) ∥ Flash-lite (50 members) → Pulse → Products → Route/reachability → alerts/pumps → publish, recording `stage_ms`, and writes the run directory atomically (temp dir then rename). `bake` runs every cycle of a bundle in order (Pulse posterior carried forward). The bus is in-process asyncio with topics `radar.frames`, `gauges.obs`, `traffic.speeds`, `reports.raw`, `tide.stage`, `runs.published`, `cycle.stage`, `alerts`; the WebSocket relays `runs.published`, `cycle.stage`, `alert.*`, `obs.assimilated`, `replay.clock`, `onboard.progress`.

Targets: baked publish ≤ 200 ms; live cycle ≤ 15 s on the demo laptop (Sky 5 + Twin 8 + rest 2, with Twin and Flash in parallel processes). Degraded modes: no radar → gauge/satellite-only Sky with wider spread and a banner; no traffic → reports only; the banner always says which.

### 11.12 Verification (`services/verify`)

Per event: CSI/POD/FAR for "> 30 cm within the window" at chronic spots and pins; depth MAE and bias at pins with depth; timing error at chronic spots; Brier score and reliability bins for P(> 30 cm); ROC AUC; rain CSI at 20/40 mm/h by lead versus truth/gauges; frame-to-product latency from `stage_ms`; routing value = share of naive emergency trips (100 random hospital-to-hotspot trips) that cross an observed-impassable segment versus VARUNA routes. Output `verification.json`; a committed copy in `apps/command/public/verification.json` for offline landing numbers.

---

## 12. API contract (`services/api`, OpenAPI 3.1; every response carries `run_id` and `valid_ts`)

| Method and path | Purpose | Tier |
|---|---|---|
| `GET /healthz` | liveness + mode + bundle + last run | P0 |
| `GET /v1/runs` · `GET /v1/runs/{run_id}` | run registry and provenance (versions, stage_ms, mass balance) | P0 |
| `GET /v1/nowcast/segments?run_id=&bbox=&t=&profile=` | segment quantiles, probabilities, safe-until (GeoJSON; `format=parquet` for the console preload) | P0 |
| `GET /v1/nowcast/raster?run_id=&t=&stat=p50,p90,prob30` | PNG + bounds (COG P1) | P0 |
| `GET /v1/nowcast/hotspots?run_id=&limit=` | ranked hotspots with attribution | P0 |
| `GET /v1/nowcast/segments/{id}/series?run_id=` | one segment's fan-chart series and safe-until table | P0 |
| `GET /v1/drains/health?run_id=&bbox=` · `GET /v1/drains/health.csv` | drain-health product; desilting CSV | P0 |
| `GET /v1/observations?run_id=` | assimilated observations with effects | P0 |
| `POST /v1/reports` | citizen/field observation ingestion (returns the feedback count after the next cycle via WS) | P0 |
| `POST /v1/route` | `{origin, destination, depart_at, profile, risk_tolerance}` → route, avoided, alternates, safe_until, explanation | P0 |
| `GET /v1/reachability?facility=&profile=&t=` | isochrone polygons + collapse flag | P0 |
| `GET /v1/feeds/road-conditions` | provider feed GeoJSON | P0 |
| `GET /v1/feeds/gtfs-rt/alerts` | GTFS-Realtime alerts | P1 |
| `GET /v1/alerts?since=` · `GET /v1/alerts/{id}.cap` · `POST /v1/alerts/{id}/ack` · `/escalate` | alert feed, CAP documents, state changes | P0 |
| `GET /v1/pumps` · `POST /v1/pumps/optimise` · `POST /v1/pumps/dispatch` | inventory, plan, dispatch order | P0 |
| `POST /v1/whatif` | `{rain_scale, tide_offset_m, cleaned_edges[], pump_plan}` → deltas (< 1 s) | P0 |
| `POST /v1/whatif/physics-check` | Twin re-run on the scenario → agreement report | P0 |
| `GET /v1/replay/bundles` · `POST /v1/replay/{play,pause,seek,speed}` · `GET /v1/replay/clock` | replay control | P0 |
| `POST /v1/cycle/compute` · `GET /v1/cycle/status` | live cycle + stage timings | P0 |
| `POST /v1/onboard` · `GET /v1/onboard/{job}` | city-in-a-box job with progress | P0 |
| `GET /v1/verification?event=` | scores and chart data | P0 |
| `GET /v1/city/{city}/layers/{name}` | static layers (segments, drains, assets, hotspots, buildings) simplified for the map | P0 |
| `WS /v1/live` | events listed in §11.11 | P0 |

Error format: `{ "error": { "code": "...", "message": "what happened and what to do", "run_id": ... } }`. All times ISO 8601 with offset (+05:30). TS types generated by `openapi-typescript` into `apps/command/lib/api/types.ts` in CI.


---

## 13. Build phases and checklists

Tick boxes per the protocol in §0. Task IDs are stable; reference them in commits. Each phase ends with exit criteria that must all be true.

### Phase 0 — Foundation, shell, design tokens

- [x] P0.1 Initialise the monorepo: pnpm workspace + Turborepo, uv workspace, `.editorconfig`, Prettier, ESLint (strict), Ruff, mypy (basic), pre-commit hooks (2026-09-06, c0613bb)
- [x] P0.2 `packages/tokens`: `tokens.json` (§6.2–6.4) → `globals.css` variables, Tailwind theme, `ramps.py`; a build script and a unit test that the three outputs agree (2026-09-06, 9ca5229)
- [x] P0.3 `apps/command`: Next.js App Router, TypeScript strict, Tailwind v4, shadcn init, fonts (Bricolage Grotesque via `next/font/google`, Geist Sans/Mono via `geist`), `.num` tabular utility, dark-only theme (2026-09-06, fc2636d)
- [x] P0.4 `services/api`: FastAPI skeleton with `/healthz`, `/v1/runs`, `WS /v1/live`, CORS, structured logging, OpenAPI at `/docs`; `openapi-typescript` generation wired into `pnpm typegen` (2026-09-06, 77e6b44)
- [x] P0.5 `packages/schemas`: Pydantic models for run, segment_forecast, node_forecast, observation, alert, pump, bundle manifest; JSON schema export; tests (2026-09-06, 77e6b44)
- [x] P0.6 `Makefile` with every target in §4.3 (stubs allowed for now), `.env.example`, `docker-compose.yml` (optional services), `VARUNA_OFFLINE` guard that blocks outbound network in tests (2026-09-06, 774be53)
- [x] P0.7 App shell: `AppShell`, `TopBar`, `IconRail`, `ModeBanner`, `RunStamp`, `VerificationChip`, `CommandPalette`, `ShortcutsOverlay`, `sonner` toasts, error boundaries per panel, 404 (2026-09-06, 77e6b44; **2026-09-15/16, 7c3ddb0 and 5cd7831:** the chip said "Not scored yet" beside a served CSI 0.22 and the palette said "No hotspots yet" beside 28 ranked spots. The chip now reads the served headline with its threshold ("CSI 0.22 at 15 cm on this event"), shimmers while loading and shows the committed `public/verification.json` as a labelled interim copy; the banner reads "Loading run" rather than "No runs yet" while runs load; the palette loads 28 hotspots, 368 facilities, the 25 worst pipes and the run registry on first open, and "Compute live" reads "Coming in pilot". Every top bar was asking the API for a sweep that takes 9-10 s warm and up to 67.6 s under load, so it is scored once per set of inputs now (ADR-0058). The 404 draws Mumbai's streets faintly behind its copy)
- [x] P0.8 `/design` page rendering every token, type size, and each component with loading/empty/error states as they are built (2026-09-06, a6a6818)
- [x] P0.9 `pnpm lint:design`: script that fails on raw hex, non-token fonts, `transition-all`, or emoji in `apps/command` (2026-09-06, 518a8ab)
- [x] P0.10 CI (GitHub Actions): lint, typecheck, unit tests, Python tests, Playwright smoke, Lighthouse CI on `/` (2026-09-06, 77e6b44 — workflow written and every job's command verified locally; green on GitHub from 2026-09-07 once 063d5e4 and af793ad removed the pnpm version pin conflict, applied ruff format and installed the missing turbo. **"Green" never included the Playwright job**: it `needs: [web]` and was skipped behind a red Web job from 2026-09-10, and the first three times it ran, on 2026-09-13, it sat 13-35 minutes until a newer push cancelled it - three `/route` tests clicking a "Find route" that cannot enable without `city/mumbai`, twelve minutes each. The job now has a 30-minute ceiling, runs every test a clean clone can pass, and skips the eight tagged `@needs-city` with `make city CITY=mumbai` as the reason, listed on the job summary. ADR-0043)
- [x] P0.11 `docs/`: blueprint PDF copied; `DECISIONS.md`, `SIMPLIFICATIONS.md`, `CHANGELOG.md`, `QA.md` created with headers (2026-09-06, 66993c5)
- [x] P0.12 Empty console renders with the mode banner "No runs yet" and the replay panel open (2026-09-06, 77e6b44)

**Exit:** `make dev` shows the shell; CI green; `/design` shows tokens; `make demo` prints a clear "no bundle baked yet" message instead of crashing.

### Phase 1 — City-in-a-box (Mumbai)

- [x] P1.1 `configs/mumbai.yaml` (bbox, CRS, grid, nests, tidal outfall edges, design intensities) (2026-09-07, 328a3d5)
- [x] P1.2 DEM fetch from the public Copernicus GLO-30 bucket → mosaic → 30 m grid; cache; fallback path documented (2026-09-07, 2424c05)
- [x] P1.3 OSM extraction (roads, buildings, waterways, culverts/bridges, stations, hospitals, fire stations, shelters) → GeoPackages (2026-09-07, 0412660)
- [x] P1.4 Land cover → imperviousness raster; CN raster (2026-09-07, 247e4aa)
- [x] P1.5 Hydro-conditioning (buildings burned, roads carved, underpasses kept, culverts breached, spurious pits breached, roughness raster, depression map) (2026-09-07, 247e4aa; **"underpasses kept" was only half true until 2026-09-12** — the register protected pits from breaching but not cells from the building burn, so Khar Subway and Parel were burned 5 m and blocked in the solver. ADR-0039; `sinks_cleared_of_building` now records it and a 30 m test pins it)
- [x] P1.6 Road segments split at intersections with attributes and exposure weights (2026-09-07, 247e4aa)
- [x] P1.7 Surface units (watersheds to inlets, hexagon fallback) with attributes (2026-09-07, 247e4aa)
- [x] P1.8 Synthetic drain graph per §10.1 step 7, including β/κ priors, tidal outfalls, `confidence=inferred` (2026-09-07, 247e4aa; **not gravity-consistent, audited 2026-09-14 (ADR-0048):** inverts sit at a fixed cover and are never deepened, so 18,994 of 49,770 edges (38.2 %) run uphill, the stored `slope` column is false on 26,622, and 27,974 of 49,897 nodes (56.1 %) reach an outfall only over an invert above their own street - Hindmata by 8.56 m, which is where its demo-run surcharge comes from. Topological connectivity is 100 %, hydraulic 43.9 %. `varuna city audit-gravity` measures it; the regrade, within the blueprint's 1-3 m depth bound with adverse edges flagged, is decided and not yet built)
- [x] P1.9 Hotspot register (≥ 10 verified, sourced points) and `assets/mumbai_infra.json` (sourced) + synthetic pump inventory (labelled) (2026-09-07, 247e4aa)
- [x] P1.10 `REPORT.md` with validation: depressions vs hotspots overlap ≥ 60 %, drain connectivity 100 %, maps (2026-09-07, 247e4aa; re-verified 2026-09-12 — all four targets met at 82.1 %, 100 %, 13 sourced points and 100 % of units in the cap. Two reporting defects fixed: "Pits kept" printed `pits_protected` (0) under a label that meant 2,493, hiding the whole effect of ADR-0019, and the rule text read `< 900 m2` where the code means `<= 900`)
- [x] P1.11 Exports: GeoParquet/GeoJSON for the API, simplified map GeoJSON, Flash graph tables (2026-09-07, 247e4aa)
- [x] P1.12 `/v1/city/mumbai/layers/*` served; layers render in the console (streets grey, drains off, assets, hotspots) (2026-09-07, 247e4aa — the eight layers are served and contract-tested, with gzip and a bbox filter. **Caveat cleared 2026-09-12:** it read "rendering waits on `CityMap` in P6.1/P6.2, so the console still shows the map placeholder", which stopped being true when P6.1/P6.2 landed. `apps/command/lib/api/city-layers.ts` fetches them and `components/map/city-map.tsx` draws segments, drains and hotspot rings)

**Exit:** `make city CITY=mumbai` completes from cache in < 10 min; validation targets met; layers visible.

### Phase 2 — Replay bundle and storm designer

- [x] P2.1 Bundle manifest schema, loader, validator (`varuna bundle validate`) (2026-09-07, 8045e63; **the validator enforced 10 rules and none of them read section 10.2's column lists** — a `gauges.csv` with no `mm_5min`, a `tide.csv` with no `stage_m`, a traffic feed with no `baseline_kmh` and a radar cube labelled `mm/h` all validated clean. B11 `columns.present`, B12 `cubes.units` and B13 `groundtruth.floor` added 2026-09-12 with a test per case; 13 rules on the reconstruction, 11 on a design storm)
- [x] P2.2 Storm designer (cells, wind, lifecycle, stratiform background, Marshall–Palmer inverse, noise, coverage, quantisation); seeded (2026-09-07, 8045e63)
- [x] P2.3 `MUM-2019-07-02` calibration to documented public gauge totals for the 05:40–09:40 IST window (ADR-0007); sources in the manifest (2026-09-07, 639b71a)
- [x] P2.4 Synthetic gauges at real station locations; tide series (public tide table if obtainable, else "illustrative" label) with the demo's high tide in the window (2026-09-07, 639b71a; **tide datum, 2026-09-15** (8293f99, ADR-0055): the illustrative stage is assumed to be chart datum, and the Twin now subtracts mean sea level at 2.70 m above it (PSMSL station 43, range 2.66-2.73 m), reading -2.655 to +1.236 m in the DEM's frame. The EGM2008-to-local-MSL residual is not quantified, and the baked runs predate the conversion)
- [x] P2.5 Synthetic traffic baseline + anomalies + confounders, labelled (2026-09-07, 639b71a)
- [x] P2.6 Curated **real** ground-truth pins (≥ 10, `source_url`, time uncertainty) + synthetic reports stream (2026-09-07, 639b71a)
- [x] P2.7 Replay clock service: play/pause/seek/speed, bus publishing, cycle triggering, baked mode (2026-09-07, 639b71a)
- [x] P2.8 Design-storm bundles `MUM-IDF-25yr` (Chicago hyetograph) and `CHN-IDF-25yr` (2026-09-07, 8045e63)
- [x] P2.9 IMD radar PNG decoder (`services/sky/decode_imd.py`): legend lookup, georeference by site and range rings, 5-dBZ classes; tested on one archived image — P1 (2026-09-22, decoder in `services/sky/varuna_sky/decode_imd.py`, ADR-0064. The legend is read from the image's own colour bar rather than hard-coded, the georeference is a concentric fit of the range rings, and an unknown layout is refused. **IMD serves GIFs, not PNGs**, and the Colaba legend has 4 dBZ classes where the task says 5 - the decoder outputs the legend's own. Tested on one archived IMD image, cached and never committed, plus a synthetic fixture drawn with IMD's legend: 1,053 of 14,400 Sky cells (7.3 %) unobserved because every pixel in them was a spoke, ring or coastline. **Not wired into Sky or the cycle**: Sky needs three frames ten minutes apart and `qc.coverage_mask` still assumes the radar sits at the domain centre, 19 km from Colaba.)
- [x] P2.10 Radar preview for the replay screen: bundle frames and AOI accumulation rendered server-side through the shared rain ramp, played at 4 fps (motion M25) (2026-09-08, 4a88b42)

**Exit:** `make bundle` builds all three bundles; the console's replay panel plays the radar animation from a bundle.

### Phase 3 — VARUNA-Sky

- [x] P3.1 QC (coverage, clutter, attenuation flag) (2026-09-09, e4cb94f)
- [x] P3.2 Adaptive Z–R with clamps and MP fallback; test recovers MP parameters on synthetic data (2026-09-09, f4252f6 — b is clamped first and the intercept re-solved at the clamped exponent, which is the constrained optimum; a second fallback fires when the gauges span less than a factor of two in rate, since an exponent fitted from rates that agree cannot be extrapolated over the three decades the relation is used across)
- [x] P3.3 Gauge merge (MFB + IDW residuals) (2026-09-09, f4252f6 — additive residuals, reasoned in the module docstring; Shepard's taper after a test caught that plain normalisation cancels it and leaves a hard ring at the search radius; the 5 % contract is measured into `max_gauge_error_pct`, not asserted). KED via pykrige stays P1
- [x] P3.4 Optical flow (pySTEPS LK) and STEPS ensemble (20 members × 36 steps); fallback implementation behind the same function (2026-09-09, e4cb94f — the fallback's AR(2) now degrades to AR(1) rather than ring, ADR-0015)
- [x] P3.5 Rain cube products (AOI 30 m resample, quantiles, exceedance, AOI-mean hyetographs); Zarr writer (2026-09-09, f4252f6 — the 30 m cube is a function the Twin calls rather than a 485 MB per-cycle artifact; the hyetographs come from a separable weight field, an identity that is tested against the literal per-member resample)
- [x] P3.6 Skill-versus-lead-time computation against truth for synthetic bundles (`services/verify/rain_skill.py`) (2026-09-09, e4cb94f)
- [x] P3.7 Tests: spread grows with lead; dry-in dry-out; stage time ≤ 5 s (2026-09-26, ADR-0075 - all three section 11.1 tests pass and the stage is inside 5 s in steady state: 2.30 s in the verification pass and 2.4-5.6 s across two 2026-09-26 bakes, with the members split over four worker processes that `make bake` warms first. The first cycle in a fresh process is sequential at 8.8 s, which a cold "Compute live" pays. The third test passes at a median 5.4 % after correcting pySTEPS' dBR interpolation of the half-step leads, and is close to true by construction because lead 0's lower neighbour is the analysis it is compared against; the whole-step deficit is untouched and has its own test. History of the miss: 6.32 s warm / 8.39 s cold on 2026-09-12, ADR-0040.)
- [x] P3.8 Console: radar animation layer and the fan chart at Hindmata read the cube (temporary panel until Phase 6 wires it properly) (2026-09-09, 3724182 — `GET /v1/nowcast/rain` and `/rain/series` under the SPEC.md 12 URL shape (ADR-0016); `FanChart` is the section 6.6 component Phase 6 keeps, `SkyPanel` is the scaffolding it deletes. Hindmata's position and its `source_url` come from the city register, never a literal. `run_id` is null on a computed cycle because minting one would claim Twin and Flash versions that never ran)

**Exit:** Sky stage ≤ 5 s on the laptop; ensemble fan chart shows divergence after ~90 min.

### Phase 4 — VARUNA-Twin, drain graph, coupling

- [x] P4.1 Numba local-inertial 2D kernel with CFL, blocked buildings, closed edges; unit tests (still water, conservation, radial symmetry) (2026-09-10, 954e968)
- [x] P4.2 Rain forcing and SCS-CN infiltration; tide boundary cells (2026-09-10, 954e968; 2026-09-15, 0535152, ADR-0056: the Twin also resumes from a `TwinState` and returns its final state. 6 steps plus a 6-step resume equal 12 bitwise, a checkpoint whose grid, counts, inverts or blocked mask differ refuses to load and names the difference, and the coupled mass balance divides by this run's inflow, so carried water never enlarges the denominator. **Not wired into the cycle**, and the surface stepper's own audit is still relaxed on a hot start. The tide boundary cells read the stage in the DEM's frame since 8293f99, ADR-0055)
- [x] P4.3 `drain1d` diffusive-wave-lite: capacity, fill fraction, pressurised flow, backflow, outfalls with flap option, Preissmann slot, pumps/tanks as sinks (2026-09-10, 954e968)
- [x] P4.4 Coupling (inlet capture with κ, surcharge, 5 s sync, flux limiter) (2026-09-10, 954e968)
- [x] P4.5 Mass balance assertion < 0.1 % over a 3-hour AOI run (2026-09-10, 954e968; re-established 2026-09-26, ADR-0072 - **0.00012-0.00066 %** on all seven shipped cycles. The 2026-09-23 re-bake had read 0.127-0.535 %; a single-variable bisect showed ADR-0055's tide had removed 5.35 Mm3 of phantom sea from the denominator, and a residual ledger then put the whole error in two exchange leaks - surcharge the surface got that the drain never emitted, and surcharge destroyed on building cells - both fixed. `run.json` carries the ledger as `mass_balance_ledger`.)
- [-] P4.6 Performance: 3-hour AOI run ≤ 8 s — **115 s to 74 s, still missed** (2026-09-11). The drain step and the coupling are Numba kernels now (106 s to 29 s, 25 s to 4.6 s), tested against the NumPy versions they replace. The remaining factor of seven needs the edge scatter parallelised by colouring, which is a piece of work rather than a tuning pass; ADR-0035. Machine: Intel i5-1155G7, 4 physical and 8 logical cores, where section 11.3 assumes an 8-core CPU. **2026-09-15 (ADR-0049):** the surface solver's per-call setup is hoisted into a run-long stepper (1.95-2.06 ms to 0.20-0.22 ms per call, outputs bitwise identical); a one-hour coupled run measures 27.1 s under contention, drain kernel 13.2 s and surface kernels 9.2 s, so the budget is still missed and the drain step is now the wall.
- [x] P4.7 Tide-lock test: reversed flow on the trunk and surcharge upstream when the outfall stage rises (2026-09-10, 954e968)
- [x] P4.8 5 m nests at Hindmata and King's Circle with boundary heads from the city run — P1 (2026-09-24, ADR-0078 - `varuna_twin.nests`: 34 x 34 parent cells snapped to 204 x 204 at 5 m, the same surface kernel, bound to verified register points, conserving to 2e-15 and 4e-15. The boundary imposes the parent's **depth**, because imposing its level put 17,994,252 m3 into a 1.04 km2 window where the two grids' DEMs disagree by 2.89 m. A nest is quoted for pattern and never for a junction's depth - the sign of the difference at Hindmata flips between bicubic and bilinear. 9.2-11.9 s per nest, so on demand only; nothing in the cycle or the console calls it yet.)
- [x] P4.9 PySWMM adapter and `.inp` export of the synthetic graph behind the same `Drain1D` interface — P1 (2026-09-24, ADR-0079 - `varuna_twin.swmm` exports all 49,897 nodes in 0.62 s, byte-identical on re-export, and drives SWMM 5.2.4 through `drain1d.simulate`'s call shape. Flows agree to 2.7e-16 m3/s; `drain1d`'s depths are 4.5x low (full-bore hydraulic radius, hand-checked); SWMM misses section 11.3's budget at -1.901 % on a cold start where `drain1d` reads 0.0000 %, so it is measured beside the P0 solver and does not replace it. `pyswmm` is a dev dependency.)
- [x] P4.10 `docs/SIMPLIFICATIONS.md` entries for the 1D scheme, DEM accuracy, no NWP blend (2026-09-12 — verified rather than written fresh: all four topics already carried substantive rows (1D hydraulics, 2D solver, NWP blend, DEM, plus a separate DEM vertical-accuracy row), each naming the blueprint's version, the prototype's, the UI label and the upgrade path)

**Exit:** rain cube → depth maps end to end; Hindmata surcharges in the demo storm; reversed-flow edge exists at a tide-locked outfall.

### Phase 5 — Products, cycle orchestrator, API

- [x] P5.1 Segment forecast (buffer sampling, quantiles, exceedance, safe-until per profile) → GeoParquet (2026-09-10, 954e968)
- [x] P5.2 Node forecast (head, P(surcharge), responsible edges placeholder until Phase 7) (2026-09-10, 954e968)
- [x] P5.3 Depth rasters: PNG per step and stat with the token ramp + world file + `bounds.json` (2026-09-10, 954e968)
- [x] P5.4 Hotspot ranking with exposure weights and time-to-peak (2026-09-10, 954e968)
- [x] P5.5 Run registry, `run_id` format, atomic writes, `run.json` with `stage_ms` (2026-09-10, 954e968; **stage totals double-counted until 2026-09-14** (e23f72f, ADR-0046): the Twin's sub-timings were summed on top of its own wall clock, so the deployed status read 189.7 s for a 76.9 s cycle. Totals now count each `CYCLE_STAGES` key once in the API and in the console run stamp; the seven baked cycles serve 64.8-147.2 s)
- [x] P5.6 Cycle orchestrator (stages in order, Twin ∥ Flash placeholder, timings) and `make bake` (2026-09-10, 954e968; **`make bake` was a phase-gate stub until 2026-09-13, 7b941f5** — `uv run varuna bake` printed "not implemented until Phase 5" and exited 2 while this box was ticked, because `varuna_cycle` had no `cli` module; the demo runs had been made by calling `run_cycle` by hand. It bakes now: oldest first from Sky's own forecastable window, resumable, `--every/--from/--to`, and `make bake ARGS="--every 30 --from 06:10 --to 09:10"` is the shipped demo set, pinned by a test. Appendix B's `varuna cycle` exists for the same reason, with `varuna cycle plan`. **Pulse's posterior is not carried between cycles** — see P7.3)
- [x] P5.7 API endpoints: runs, segments (+parquet), raster, hotspots, segment series, replay controls, cycle compute/status, city layers; WS events (2026-09-10, 954e968)
- [x] P5.8 `pnpm typegen` produces TS types; API contract tests (2026-09-12 — **the committed OpenAPI snapshot was five paths behind the app** and every one of the five was already being called from `apps/command/lib/api/`: surcharge, raster bounds, ground truth, route facilities and the Chennai onboard route. Regenerated (41 -> 46 paths) and guarded: `services/api/tests/test_contract.py` fails in both directions naming `uv run varuna openapi && pnpm typegen`, which is section 12's "generated in CI" made real, plus the invariants the prose states — the error envelope, a 404 that names the command that fixes it, content types, the `/v1` prefix. Verified the guard by drifting the snapshot on purpose. The regeneration also exposed that **22 of 46 routes declare no response model**, so 29 schemas vanished: recorded as ADR-0041 rather than papered over, because attaching the stale models would 500 the screens they serve)
- [x] P5.9 Idempotence test: baking the same cycle twice yields identical files (2026-09-12 — rule 8 and section 14 both demand byte-identical bakes and **nothing was checking it**. `services/cycle/tests/test_idempotence.py` bakes the demo bundle's first cycle twice and compares the sha256 of every product; they match. `run.json` is the single exclusion, because `stage_ms` and the wall clock are provenance and are *meant* to differ - a second test pins that the exclusion list stays one entry long, so a product that starts carrying a timestamp cannot be quietly added to it)

**Exit:** `make bake BUNDLE=MUM-2019-07-02` produces every cycle; `make demo` serves them; the console (Phase 6) has data to render.

### Phase 6 — Command console (core UI)

- [x] P6.1 `CityMap`: MapLibre + deck.gl overlay, basemap style, custom quiet labels, AOI fit, resize handling, WebGL context loss recovery (2026-09-10, 954e968)
- [x] P6.2 Layers per §6.7: streets (depth ramp, width by class), depth raster bitmap per step, buildings, drains (off), hotspot rings, assets, ground-truth pins (2026-09-10, 954e968)
- [x] P6.3 Run preload on `runs.published` (36 PNGs + segment parquet via `parquet-wasm` or JSON fallback); atomic swap (2026-09-10, 954e968)
- [x] P6.4 `TimeBar`: scrub, play, speed, ensemble band, keyboard; restyle ≤ 16 ms (2026-09-10, 954e968) **The ensemble band was not drawn until 2026-09-26**: the bar printed "Ensemble spread appears with the first run" over loaded runs. `run.json` now carries `aoi_depth_band` - quantiles across members of the mean street depth - and the bar draws it under the track; a run without members says it has no spread.
- [x] P6.5 Probability mode with threshold selector; legend switch (2026-09-11 — colour is the p50 depth, opacity is P(above the threshold) with the 15 % floor from tokens.json. On a one-member run every street is 0 % or 100 %, and the legend says so rather than drawing a ramp over a spread that does not exist)
- [x] P6.6 Surcharge markers (pulse) and reversed-flow edges (animated dash) (2026-09-10, 954e968; **the dash did not exist until 2026-09-15** (2cd5b3f, 940d6d7, ADR-0053): M8 and M9 now run on deck's animation clock with no React render per frame, and the surcharge product carries each reversed edge's line from the city export (04d178f, ADR-0052). Every committed and deployed run predates that join, so M9 draws nothing until the combined re-bake, and the layer panel says so. Idle fps with the pulse on measured 43.7-58.3 against 59.7-60.0 with it off (Iris Xe, dev build, 10-12 python processes): marginal against 55, and not ticked under section 14 or 7.2)
- [x] P6.7 Hotspot rail (ranked rows with chips, time-to-peak, sparklines, exposure icons), fly-to + ring (2026-09-10, 954e968)
- [x] P6.8 `HotspotDrawer` (big number, fan chart, safe-until table, exposure, attribution list — attribution data arrives in Phase 7, show a skeleton until then) (2026-09-10, 954e968)
- [x] P6.9 `SegmentPopover` on click; hover tooltip ≤ 80 ms (2026-09-11 — the tooltip is deck's own, so there is no React render in its path; the popover gives depth now, peak with its time, and safe-until for all five profiles against the same thresholds the router costs on. **The third thing 7.2 asks the popover for — the "why" link — was missing and the box was ticked anyway**; added 2026-09-13. It points at the hotspot drawer's attribution section rather than explaining the junction a second time, and it is drawn only when the run carries a ranking: no run does (ADR-0042), so on every baked cycle a street that belongs to a chronic spot shows that run's own reason instead of a link into an empty section. **Still to wire:** `console-screen.tsx` has to match the picked segment against each hotspot's `segmentIds` and pass `hotspot` and `onWhy` to the popover; until it does, the section does not appear)
- [x] P6.10 `LayerPanel`, `Legend`, scale bar, attribution (2026-09-10, 954e968)
- [x] P6.11 Replay panel with cycle log and `CycleBudgetBar` (2026-09-11 - the log read a hard-coded empty array over seven baked cycles; "Compute live" is still the outstanding half)
- [x] P6.12 Ground-truth pins drop on the replay clock with the "As it happened" ticker (2026-09-11 — 29 sourced pins had no endpoint serving them; `GET /v1/replay/ground-truth` does, the map drops each as the clock passes it (M18) and the ticker links the URL each was read from. **M18 as section 8 states it since 2026-09-15 (c07f86c):** the pin scales 0 to 1 on the catalogue spring with a 600 ms eased ripple, on deck's clock, so a drop costs 3 renders of the pin hook and none of `CityMap` - the old hook set state every frame for 600 ms. The ticker slides rows in and dates the six pins from 1 July, which read as future times beside a 2 July clock. Measured by the engineer who built it; an independent review is still to do)
- [x] P6.13 Empty, loading, degraded states; keyboard shortcuts; `?` overlay; zero console errors during a full replay (2026-09-12 — **four of the ten layer shortcuts were dead keys the overlay advertised as working.** `lib/shortcuts.ts` had a handler registry built for exactly this and nothing ever called it, so the console handled six keys through its own listener while the overlay listed ten. The console registers through the registry now, which makes the overlay's answer true by construction, and R, I, 3 and W read "Coming in pilot" with the task that wires each — section 17 applies to a key the same way it applies to a button. **One real console warning** also turned up under the AC's own words: closing the live WebSocket mid-handshake logged "closed before the connection is established" on every console mount, fixed by waiting for the handshake on a detached socket. 13 Playwright tests in `tests/e2e/console.spec.ts`)
- [x] P6.14 Responsive checks at 1366 × 768 and 4K/150 % (2026-09-12 — asserted rather than eyeballed: `/console` at 1366 x 768, at the 1440 x 900 reference and at 2560 x 1440 (a 4K wall at 150 % presents as that many CSS pixels) all carry no horizontal overflow and keep the map canvas visible, and `/map` fits 390 x 844 per 7.11. Section 6.5's "must never scroll horizontally" is the assertion)
- [x] P6.15 3D mode (TerrainLayer + depth texture) — P1

**Exit:** demo script 0:00–2:40 runs without a terminal; §7.2 AC all ticked.

### Phase 7 — Pulse, Flash-lite, drain X-ray, what-if
 (2026-09-22, ADR-0065 - deck.gl `TerrainLayer` over a Terrarium heightmap exported from the city's own conditioned DEM by `make city-terrain` (byte-identical re-export, 0.00195 m round-trip error), the step's depth PNG draped over it at 2x exaggeration and 55 degrees. **50.4 fps against section 14's 55** (31.3 under contention), so it is off by default; and section 8 has no row for a camera tilt, so the toggle cuts rather than flies.)
- [x] P7.1 Traffic anomaly detector with baselines and confounder rejection; tests (2026-09-10, 90efeea; 2026-09-15, 98b80b8 and d207510, ADR-0054: vectorised and identical to the loop it replaced at all seven demo cycles, with a windowed `start=`/`end=` form the cycle does not call yet, and the three static city joins cached in process. `run_pulse` at 09:10 is 1.47-1.65 s warm with 10 python processes, and 3.56-4.97 s warm and 6.18 s cold with 19-21; at 08:40 it is 2.15-2.16 s warm and 6.51 s cold with 15-17. The 3 s budget is met warm and missed cold)
- [x] P7.2 Report ingestion (`POST /v1/reports`), dedupe, depth chips → observations (2026-09-10, 90efeea)
- [x] P7.3 EnKF over logit β, localisation, posterior persistence; synthetic-truth recovery test (2026-09-10, 90efeea). **The tick used to claim κ was assimilated too, and it is not** — `grep kappa services/pulse` returns nothing, κ is never in the state vector and `drain_health.geojson` carries no κ property, so 11.6's "plus κ at observed inlets" is unbuilt. The priors exist and go unread: `city/mumbai/drain_nodes.parquet` holds `kappa_mean` 0.25 on all 49,897 nodes. 7.3's "inlets as small squares coloured by κ" is not drawn either, because `CityMap` has no point layer to carry it; `/drains` now says both in words beside the map (2026-09-13) rather than drawing a legend for squares that are not there. 11.6's 40 % spread criterion used to hold only at the committed seed pair (0.40029), clearing at 49 of 100 seed pairs under the single-step stochastic EnKF. **Fixed 2026-09-14 (a6e5774, ADR-0044):** the update is ES-MDA with perturbed observations (4 passes, moment-matched prior, still 50 members). Both pipes reach the top five at 100/100 seed pairs; the cut clears 40 % at 100/100 pairs on two grids and 98/100 on a third, and the median over ensemble seeds clears it at every observation draw (0.457-0.564). Median over seeds, the posterior agrees with exact Bayes to 0.017 in mean and 0.027 in cut. The xfail is gone and the floor is not widened. The baked demo runs predate the change. **And "posterior persistence" is not built either** (found 2026-09-13, 7b941f5): `run_cycle` calls `run_pulse` with no prior and nothing calls `load_posterior` or `save_posterior`, so no run holds a `posterior.json` and every cycle re-assimilates all observations up to its own instant from the city's prior. β still rises across the baked cycles because later cycles see more observations, which is a re-analysis per cycle rather than 11.6's filter or 11.11's carry-forward. The Pulse docstring said the carry-forward happened in memory within a bake, when no bake existed. Wiring it needs Pulse narrowed to the observations since the previous cycle, or each observation is counted again every cycle.
- [x] P7.4 Drain-health product, observation effects, disagreement list, desilting CSV, feedback count (2026-09-10, 90efeea)
- [x] P7.5 `make train`: ≥ 200 Twin runs (design storms × β draws), Flash-lite fit, held-out RMSE/CSI written to `docs/verification/flash_lite.json` (2026-09-10, 90efeea — **8 runs, not 200**: design storms at 25/50/75/110 mm/h at two blockage draws, three minutes of CPU each. The fit and the report are real and the skill is measured on held-out storms rather than asserted: RMSE 5.7 cm, CSI 0.085 at 30 cm. ADR-0025 explains why more runs would not have rescued it — the limit is structural, not statistical)
- [x] P7.6 Ensemble products from Flash-lite (50 members) replace the Phase 5 placeholder; probabilities on the map (2026-09-26, ADR-0076 - 50 members over the 20 Sky members, covered rather than resampled, each at its own draw of blockage from this cycle's Pulse posterior and of the storage coefficient; every run prints its band and what each axis contributes. Pulse runs before Flash for it. The blockage axis measures -0.14 cm on a 16.3 cm band at 08:40, and the members still differ only in the amplitude of the AOI-mean rain.)
- [x] P7.7 Attribution by finite-difference cleaning sensitivity; "clean top 14" combined effect (2026-09-26, ADR-0071 - on `drain1d` on each junction's catchment with the street frozen at the Twin's depth, every candidate within five upstream hops in one solver call, ranked by magnitude with the sign kept, the top 14 cleaned together as their own run, at this cycle's posterior blockage. Hindmata refuses at every cycle (best 0.015-0.027 cm); at 08:40 four junctions name pipes and cleaning Sion Subway 1's fourteen makes it 2 cm deeper. 24-55 s per storm cycle, recorded as `products_attribution`.)
- [x] P7.8 `/v1/whatif` (< 1 s) and `/v1/whatif/physics-check` (2026-09-26, ADR-0077 - two coupled Twin runs on a 990 m crop around the run's worst junction, compared as changes; 2.4-4.7 s warm and ~20 s on a process's first call against 10 s. `/whatif` and the console drawer print the bar, each junction's emulator and Twin change, the window, the crop's mass balance and the check's own time. What-if p95 301-581 ms.)
- [x] P7.9 `/drains` page and the console "Drains" mode: β ramp, dashed inferred pipes, κ inlets, `DrainHealthTable`, assimilation timeline, before/after cross-fade, CSV export (2026-09-10, 90efeea)
- [x] P7.10 What-if drawer + `/whatif` page: controls, diff layer wipe, `DeltaTable`, physics-check agreement bar, emulator badge (2026-09-10, 90efeea)
- [x] P7.11 Console hotspot drawer now shows real attribution; "Clean in what-if" deep-links with the pipes preselected (2026-09-26 - the drawer prints the ranking, the combined effect when positive or negative, the candidate count and the method, or the measured refusal; each entry says whether it was ranked, refused or not attempted. The deep link carries the streets over the ranked pipes into `/whatif`, where Flash-lite's element-wise cleaning ceiling is printed beside them.)
- [-] P7.12 GNN surrogate: dataset ≥ 500 runs, PyG model, training, MLflow run, metrics on `/verify`, served behind `FlashModel` — P1 (not built, 2026-09-26: it needs torch and PyTorch Geometric, which are not installed, and at least 500 coupled Twin runs at 35-60 s each - 5-8 hours of this laptop's CPU before training starts. Flash-lite stays the emulator, with its measured skill printed beside every answer, and attribution no longer waits on a GNN: it runs on `drain1d` (ADR-0071).)

**Exit:** demo script 3:30–5:20 runs (drain X-ray, EnKF update visibly changes Hindmata, what-if under a second, physics check agrees).

### Phase 8 — Route, reachability, alerts, pumps

- [x] P8.1 Road graph with base travel times; forecast join; profile thresholds and risk tolerance (2026-09-10, 1892d91)
- [x] P8.2 Time-dependent Dijkstra (FIFO), alternates, explanation; tests (2026-09-10, 1892d91 - 85 ms against a 300 ms budget; FIFO asserted on a synthetic network rather than argued, ADR-0028)
- [x] P8.3 Reachability isochrones per facility per slice; collapse flag (2026-09-10, 1892d91 - served from `/v1/reachability`; the share of dry is counted in junctions rather than hull area, which is monotone where the hull is not. `reachability.geojson` is not written into the run directory: the endpoint computes it in about a second and a scrub would need 36 of them per facility)
- [x] P8.4 API: route, reachability, road-conditions feed (2026-09-10, 1892d91 - 586 segments impassable for a car on the 2 July storm). GTFS-RT alerts stay P1
- [x] P8.5 `/route` page: pickers, profiles, tolerance, `RouteCompare`, avoided list, alternates (2026-09-10, 1892d91). The draw-on animation (M14) and "Send to dispatch" are Phase 10
- [x] P8.6 Console reachability tab with `ReachabilityClock` rings and the isochrone layer (2026-09-10, 1892d91). The polygons swap rather than morph (M15) - Phase 10
- [x] P8.7 Alert state machine, CAP 1.2 generation (Exercise on replay), schema validation test, escalation matrix, WhatsApp/SMS templates (2026-09-10, 954e968; the schema validation test named in the task did not exist until 2026-09-14, 78f6c0b, when `xmlschema` was first imported. Still open under this tick: cross-cycle hysteresis (7.5 AC1) and `config/escalation.yaml`, which does not exist)
- [x] P8.8 `/alerts` page: queue, `CapViewer`, `PhoneMock`, delivery log, acknowledge/escalate; optional real sender behind env keys — P2 (2026-09-22, ADR-0068 - the queue, CAP viewer, phone mock, escalation matrix from the new `config/escalation.yaml`, a delivery log, and acknowledge/escalate through the ops endpoints with the state surviving a reload. `notify.py` holds Twilio and WhatsApp Cloud adapters, each active only when all its keys are set, and a send counts as sent only on a 2xx carrying the provider's message id. **No real message has been sent** - there are no keys on this machine, so the adapters are tested against a fake transport - and the phone mock's map snapshot is still not built.)
- [x] P8.9 Pump inventory, greedy optimiser, benefit estimate via the emulator; MILP with OR-Tools — P1 (2026-09-10, 954e968)
- [x] P8.10 `/pumps` board with dnd-kit drag, optimise, `DispatchOrder`, dispatch → alert + phone mock + toast (2026-09-10, 954e968; **2026-09-15, b914733 (M17):** the board opened with the optimiser's plan already placed, so Optimise stayed disabled and the card flight had nothing to show. It opens with every pump in the pool now; Optimise places the plan computed at cycle time, the cards fly to their hotspots and each column's minutes above 45 cm roll from the no-pump figure to the plan's. A pump moved by hand rolls no number and the board says its figure is stale. `POST /v1/pumps/optimise` and `/dispatch` are still 501 stubs, so drag-assign pricing (7.6 AC2) is not built)
- [x] P8.11 Console Alerts and Pumps tabs mirror the pages (2026-09-11 - both were empty states over runs that already had alerts and a plan; they read the same endpoints their pages do, for the run the console is showing)
- [x] P8.12 Rust Axum routing service with the same contract — P2

**Exit:** demo script 5:20–6:30 runs (ambulance route, reachability shrink, phone buzz, pump order).

### Phase 9 — Landing, public map, report, onboarding, verification, API docs
 (2026-09-22, `services/route-rs`, ADR-0063. **72 of 72 parity checks against the Python router**, leaf by leaf on 31 trips across all seven profiles, six cycles, a dozen trip ids and three closures, excluding only `ms` and with no tolerance. KEM to Sion p95 **2-3 ms** against Python's 62-73 ms; the cross-city trip 10.6-13.7 ms against 207-230 ms; graph load 19-21 ms against 1.7-2.7 s. Contraction hierarchies serve **only the naive, dry-weather search**, where they are exact; the time-dependent searches stay Dijkstra and `/healthz` says so. The deployed API still serves the Python router.)
- [x] P9.1 Landing page sections 1-10 per 7.1, hero embed, OG image, metadata, sitemap (2026-09-10, 505b2a4 - the hero opens on a globe that unrolls into a world map and hands over to the live console map; the topology is committed rather than fetched from a CDN. **2026-09-15, 04276b1 and a2c1c90:** the cycle section shows each stage's measured time from `/v1/cycle/status`, with a generated offline copy, and its beams travel only while in view (M3); the roadmap trace follows scroll (M5); proof numbers roll once in view (M4); the footnote links `/verify#limitations`. Production-build LCP measured 976 ms median over 7 warm runs after the change and 1,344 ms before, CLS 0 both times, with 24 and 12 python processes running. The footer lists the blueprint's six roles without names, because no names are in the repository)
- [x] P9.2 Landing performance (2026-09-11 — **LCP 1.6 s, CLS 0, accessibility 1.00**, best practices 1.00, SEO 1.00 against a production build. The console map is `next/dynamic` now rather than in the first bundle, which took LCP from 3.5 s. Composite performance is **0.72**, not 0.90: Speed Index scores 0.05 because the hero animates, which section 7.1 requires — ADR-0037 records why that gate is a warning rather than an error)
- [x] P9.3 `/map` public map (mobile-first, three colours, passable-until sheet, vehicle selector, honesty line, saved locations) (2026-09-10; **2026-09-15/16, 7c3ddb0:** the honesty line could never name a run, because `/map` mounts no app shell and read an empty store. It is timed from the run the map drew now, and says so when nothing is baked or the load failed instead of "Loading" forever. All 11 touch targets measure at least 44 px at 390 x 844 (Save this location was 146 x 28); street rows print the OSM name or "Unnamed road", which is 10 of 12 listed streets because OSM names none of 52.6 % of Mumbai's segments; the opened sheet carries its own Report water button, since the floating one sits beneath it)
- [x] P9.4 `/report` three-step flow -> `POST /v1/reports` (2026-09-10 - a report is stored and queued; `feedback_streets` stays null until a cycle has assimilated it, rather than carrying a number the request did not compute)
- [x] P9.5 Chennai pre-cache (`make city-cache CITY=chennai`) and `POST /v1/onboard` job with progress events (2026-09-10 - `--cache-only` used to run only the DEM *verification* step and download nothing else, so a pre-cached Chennai still had to fetch its roads from Overpass the moment the wizard started; it now runs every step with an external input. ADR-0031, ADR-0032)
- [x] P9.6 `/onboard` wizard: steps, real log stream, layer stacking, first forecast from `CHN-IDF-25yr` (2026-09-10 - the build runs in a thread and a structlog processor forwards the pipeline's own lines, so nothing in the log is composed by the screen. Chennai builds from cache in 36 s against a 5-minute budget)
- [x] P9.7 `services/verify` per 11.12 and `/verify` page; committed `public/verification.json` (2026-09-10, 27b18fe - CSI 0.22 / POD 0.41 / 31 min median lead at 15 cm, swept across 5/15/30 cm because the pins record waterlogging and not a depth, ADR-0029. The reliability diagram and Brier score are returned as unavailable with their reasons: the baked runs had one member. Since the 2026-09-13 re-bake they carry 20, and the reason now says the scorer does not read their probabilities yet; re-scored on the new runs the 15 cm headline is unchanged)
- [x] P9.8 `/api` explorer with presets; `docs/API.md` — P1 (2026-09-22 - `/api` reads the committed snapshot, lists all 60 paths with a form each and opens on three presets pinned to the 08:40 cycle; it never sends a write with a passphrase and never shows one. `docs/API.md` lists every path including weather, cities, ops, terrain, basemap and the route's spread/trip_id/explain. Found on the way: `GET /v1/nowcast/segments` is described by one handler and answered by another, so the snapshot's parameters are not the ones the API reads.)
- [x] P9.9 i18n EN/HI/MR for the public map and report — P1 (2026-09-22, ADR-0067 - next-intl with no locale routing, the choice kept in localStorage and carried from the map into the report, Devanagari loaded only when Hindi or Marathi is chosen. 0 axe violations and no target under 44 px at 390 x 844 in all three. **The Hindi and Marathi are drafts** written without a native speaker; the glossary and tests enforce consistency and Latin numerals, not fluency. The server always renders English, so a returning reader sees one English frame.)
- [x] P9.10 PWA manifest + offline caching of the last forecast and PMTiles basemap — P1

**Exit:** demo script 6:30–7:20 runs; landing deployed (Vercel) with the offline numbers fallback verified.

### Phase 10 — Polish, rehearsal, packaging
 (2026-09-22, ADR-0066 - a PMTiles vector basemap built from the city's own OSM layers by `make city-basemap`, plus a service worker scoped to /map and /report that caches the shell, that basemap and the last forecast, and queues an offline report in IndexedDB. Driven in a browser with the API stopped: /map drew the cached forecast and said how old it was, and a report submitted offline arrived when the connection returned. Esri's imagery is deliberately never cached - it is not redistributable.)
- [x] P10.1 Reduced-motion audit with the OS setting on (2026-09-11 — five Playwright tests with `prefers-reduced-motion: reduce` emulated: the CSS escape hatch flattens every animation and transition on the console (0 offenders), skeletons do not shimmer, the route is whole rather than drawing on, and the **map canvas is provably still** — two screenshots 700 ms apart compare byte-identical, which is the only way to observe a WebGL pulse. The motion *catalogue* itself is not fully implemented — M15 and M19 are outstanding, and M9 was until 2026-09-15, when it was built on deck's animation clock (ADR-0053) with nothing to draw until the runs are re-baked with reversed-edge geometry — so this is the audit, not the pass. Motion infrastructure landed 2026-09-15: `city-map.tsx` is split into per-layer modules under `components/map/layers/` (1,133 to 380 lines, proven equal to the monolith on 12 captured scenarios, with inert seams for the motion and drain chunks), and `lib/motion.ts` is pinned to this section's table M1-M26 by a parity test, with `useTween` and `useLayerFade` ready for M7-M19.)
- [x] P10.2 Design QA (§6.11) per screen; screenshots in `docs/screens/`; fix every deviation (2026-09-24, ADR-0080 - `tests/e2e/design-qa.spec.ts` over all 17 routes: 0 px horizontal overflow at 1366 x 768, 1440 x 900 and 2560 x 1440, a visible focus ring on the first tab stop, no placeholder copy, zero console errors on 16 of 17 - `/dashboard` logs Google's `InvalidKeyMapError`, which is D-26. Six defects fixed on the first pass. 6.11's four judgement lines are annotated per screen for a person on every run, not faked as assertions.)
- [x] P10.3 Accessibility pass (2026-09-11 — axe over 13 screens plus two keyboard tests, 0 violations. Four real classes found: `--text-3` was below 6.10's own 4.5:1 floor (ADR-0036), every slider in the app was unlabelled, links in text blocks relied on colour alone, and five scroll regions were unreachable by keyboard)
- [x] P10.4 Performance pass: bundle analysis, map fps at 1440 × 900, API p95, cycle timings, memory < 1 GB tab; regressions fixed (2026-09-26, ADR-0081 - `tests/e2e/performance.spec.ts` prints a verdict for every section 14 budget on a production build. Met: LCP 160-212 ms, 57-60 fps on the Iris Xe, 676-959 MB, route 64-82 ms, what-if 301-581 ms, physics check 4.1-7.4 s. Missed and named: first render (stamp 1.0-1.8 s, then 3.6-5.7 s blocked), scrub handler 19-21 ms, segments 507-553 ms. Fixed after the pass: `bbox` on the segments route, gzip level 6, Google's 3D Tiles loader lazy. Earlier: b930b54, 101a5db, 885edd8.)
- [x] P10.5 Playwright end-to-end test of the demo script (2026-09-11 — twelve tests, one per beat of section 15, asserting numbers exist rather than their values; four also assert zero console errors, which section 14 requires and nothing was checking)
- [x] P10.6 `make pack` offline package (2026-09-11 — 830 MB: baked runs, both cities' layers, the bundles, the open-data cache and the fitted emulator. The basemap imagery is deliberately not in it - Esri's tiles are not redistributable - and the package says so when it finishes)
- [ ] P10.7 `make demo-video` fallback recording (8 minutes, 1080p) stored outside the repo and on two USB sticks (**recorded 2026-09-26; the USB copies are the team's step.** `uv run varuna demo-video` resets the shared replay clock to 06:40, warms every route, and records one 1920 x 1080 take in a headed browser on the GPU, paced to section 15's clock, into `../varuna-demo-video/` - it refuses a path inside the repository. The take is `../varuna-demo-video/varuna-demo-20260926-0307.webm`, 8 min 36 s, 51.7 MB, every beat present. Headless was tried first and cannot do it: SwiftShader at 1080p with the encoder running starved the main thread until clicks timed out. Still to do: copy it to two USB sticks and both demo laptops.)
- [x] P10.8 `README.md` with the measured numbers and the offline section; `docs/API.md` (2026-09-11). `docs/VERIFICATION.md` and `docs/LIMITATIONS.md` are covered by `/verify` and `docs/QA.md` rather than duplicated into prose that would go stale
- [x] P10.9 `docs/QA.md` with every number measured (2026-09-11 — including the ones that are bad: the 8 s budget missed at 74 s, CSI 0.22 at 15 cm, Flash-lite at CSI 0.085, and the 06:40 cycle over its mass-balance budget at 0.18 %, 0.215 % after the 2026-09-13 re-bake)
- [ ] P10.10 Two full rehearsals under 8 minutes with the timer; who answers which question assigned; feature freeze tag `v1.0-finale`

**Exit:** all eight readiness boxes in §1 ticked; two clean rehearsals; package on two laptops.

---

## 14. Quality gates and budgets (acceptance criteria)

**Performance**

| Metric | Budget |
|---|---|
| Landing LCP / CLS / Lighthouse perf / a11y | < 2.5 s / < 0.1 / ≥ 90 / ≥ 95 |
| Console first meaningful render after run load | < 2 s |
| Scrub restyle | ≤ 16 ms; no network during scrub |
| Map frame rate (streets + raster + markers + pins, 1440 × 900) | ≥ 55 fps on an integrated GPU laptop |
| API p95: segments in bbox / route / what-if / physics check | < 200 ms / < 300 ms / < 1 s / < 10 s |
| Baked run publish → map swap | < 200 ms |
| Live cycle (Compute live) on the demo laptop | ≤ 15 s total (Sky ≤ 5, Twin ≤ 8, rest ≤ 2) |
| Onboarding (Chennai, from cache) | ≤ 5 min |
| Browser tab memory during a full replay | < 1 GB |

**Correctness**

- Engine unit tests (§11) pass; mass balance < 0.1 %; EnKF recovery test passes; route detour test passes; CAP XML validates.
- Idempotent bakes (byte-identical); every run has `stage_ms` and versions.
- `pytest --cov` ≥ 70 % on `services/`; vitest on `lib/` and stores.

**Experience**

- Zero console errors/warnings during the Playwright demo run.
- Every screen passes §6.11; `pnpm lint:design` clean.
- Offline package verified with networking disabled.
- Every honesty label present: reconstructed replay, inferred drain graph, reduced-order emulator, synthetic pumps/traffic/reports, Exercise status on replay CAP alerts.

---

## 15. Demo script → what must work (mirrors blueprint §13)

> **Replay window (ADR-0007).** The demo replays 2 July 2019 from 05:40 to 09:40 IST, opening at 06:40. The
> curated ground truth puts the civic reports between 08:07 and 14:28 IST that morning, so thirteen sourced
> pins land 87 to 150 minutes after VARUNA flags the street. The original 15:40 opening sat after the flood
> had receded, where no pin would land.

| Time | Jury sees | Route / action | Must-work items |
|---|---|---|---|
| 0:00 | Console, Mumbai, banner "Replay 2 Jul 2019 · 06:40 · 30×" | `/console?bundle=MUM-2019-07-02` auto-play | R1, ModeBanner, RunStamp |
| 0:40 | Radar animation → 20-member fan chart at King's Circle diverging after 90 min | Hotspot rail → Hindmata → drawer | Sky cube, FanChart, spread band |
| 1:40 | Scrub to +2 h: King's Circle, Sion, Gandhi Market and Milan subway turn red; manholes surcharge; tide-locked outfall shows reversed flow | TimeBar scrub, layers S on | Twin/drain coupling, M8, M9 |
| 2:40 | Ground-truth pins drop where VARUNA was already red, ticker shows sources | replay clock passes pin times | P2.6, M18 |
| 3:30 | Drain X-ray: 14 pipes glow; attribution; EnKF update after a traffic anomaly is assimilated | `D` layer / `/drains`, before/after | Pulse, drain health, M12 |
| 4:30 | What-if: rain +30 % in under a second, with the emulator's measured skill printed beside the answer; physics check reports the disagreement | What-if drawer | Flash-lite, diff wipe M13 |
| 5:20 | Ambulance KEM → Sion: naive vs VARUNA; safe-until; hospital reachability shrinks; phone buzzes with the alert; pump order | `/route`, Reachability tab, `/alerts` phone mock, `/pumps` | Route, isochrones, alerts, pumps, M14–M17 |
| 6:30 | Chennai onboarding runs; first forecast appears | `/onboard` | R8, M19 |
| 7:20 | Verification and limitations | `/verify` | Scores from artifacts |

> **The cleaning lever is measured now, and it does not say what section 7.2 hoped (ADR-0071).**
> Attribution runs on `drain1d`, so the drawer answers "why this junction floods" with named pipes
> or with a measured refusal. On the shipped cycles Hindmata refuses - its inlets, not its pipes,
> limit it, and the best single pipe moves it 0.015-0.027 cm - and where pipes are named, cleaning
> all fourteen can make a surcharging junction **deeper**: Sion Subway 1 goes from 9.6 to 11.6 cm at
> 08:40. So 3:30 shows the drawer's answer as it is, 4:30 is the rain scale with the physics check
> beside it, and "cleaning these 14 pipes: 55 -> 20 cm" is not said on stage.

**Rehearsal checklist**

- [ ] Every step reachable by clicks or the command palette; no terminal
- [x] The replay is pre-seeked to 06:40 and paused on load; Play is the first click (2026-09-29: the API's shared clock now opens at 06:40 too, `VARUNA_REPLAY_OPEN_AT`, placed without an event; rewind still returns to the 05:40 window start. Earlier, partial, 2026-09-16: `/console` with no `?run=` opens on the 06:40 cycle, paused, instead of the newest run (09:10, the calm cycle after the storm), and the map holds its load until it knows which run to ask for. The API's replay clock still boots at the bundle's 05:40 opening, so the mode banner and the map read different times until one clock drives both. Play at the end of the window used to do nothing on the shared deployed clock; it now starts the window again)
- [ ] Sound on for the phone mock; phone mock visible on the second screen if available
- [ ] A physical phone receives the alert only if a real sender is configured; otherwise the on-screen mock is the story
- [ ] Fallback video ready on both laptops; offline package verified the morning of the finale
- [ ] The 10-second judge test: from a cold `/console`, within 10 seconds a stranger can read the city, the time, the mode, and see red streets

---

## 16. Judge Q&A — prototype-honest answers (fill the numbers from `/verify` into `docs/QA.md`)

- **"Is the physics real?"** Yes: a local-inertial 2D shallow-water solver on a 30 m conditioned DEM, coupled to a head-driven 1D drain model with capacity, surcharge and backflow, with inlet capture and surcharge exchange. The blueprint's full dynamic-wave 1D and 5 m GPU nests are the pilot upgrade; interfaces are in place (PySWMM adapter, Rust core).
- **"Is the radar real?"** The replay radar is a storm-designer reconstruction calibrated to public gauge totals for the event, because raw IMD volumes need a MoES request. The decoder for IMD's public radar images and the NetCDF reader are in the ingest service; switching is a config change.
- **"Where is the drain GIS?"** Inferred from roads, terrain and design norms, calibrated to the chronic-spot register, with every pipe carrying a blockage random variable that Pulse learns. When a SWMM model arrives we import it.
- **"Sub-second forecast — how?"** A reduced-order emulator calibrated to our own physics runs; the GNN surrogate is the pilot upgrade. Physics check shows the emulator's error live.
- **"Ground truth?"** Sourced public reports with timestamps and uncertainty; count and sources on `/verify`. Traffic and citizen streams in the replay are synthetic and labelled; they demonstrate the mechanism.
- **"DEM accuracy?"** We claim pattern and timing from open DEMs, calibrate at chronic spots, and budget LiDAR in the pilot. MAE is on the screen.
- **"How is this different from IFLOWS?"** Strategic layer versus tactical layer: ward-level 6–72 h versus street-level 0–3 h with drains, learning, probabilities and routing. We consume IMD/NCMRWF/IFLOWS outputs.

---

## 17. Risks and fallbacks (prototype)

| Risk | Fallback (implement this, do not stall) |
|---|---|
| Copernicus bucket unreachable | NASADEM via OpenTopography key; last resort: committed 30 m clip of the AOI |
| OSMnx/Overpass rate limits | Cache raw responses; ship the cached GeoPackages in `make pack` |
| pySTEPS install fails | `fallback_steps.py` (own advection + AR(2) + noise) behind the same function |
| Numba compile issues on the demo laptop | AOT cache (`cache=True`) committed per platform; NumPy vectorised path at 60 m |
| Live cycle > 15 s on stage | Baked replay is the default; "Compute live" only in the demo's replay panel moment |
| < 10 sourced ground-truth pins for 2 July 2019 | Switch bundle to another Mumbai event with better coverage; document in DECISIONS |
| deck.gl/MapLibre fps low on the venue laptop | Reduce raster opacity layers, drop buildings at low zoom, cap segments to class ≥ tertiary at z < 13 |
| WebGL context loss on projectors | Auto-recover layers; keep the last run in memory |
| No network at the venue | `make pack`; landing numbers from committed JSON; Chennai from cache |
| Time runs out before P1 | Every P1 button reads "coming in pilot" with one sentence of plan — never a dead control |

---

## Appendix A — Equation sheet (from the blueprint; implement exactly these)

| Topic | Equation |
|---|---|
| Reflectivity to rain | `Z = 10^(dBZ/10)`; `R = (Z/a)^(1/b)`; adaptive (a, b) by log–log least squares; `MFB = ΣG/ΣR` |
| Nowcast blend (disabled in P0) | `R_blend(τ) = e^(−τ/τ_s)·R_ext + (1 − e^(−τ/τ_s))·R_NWP` |
| Effective rain | `R_eff = I·max(0, R − d_s) + (1 − I)·max(0, R − f)`; SCS-CN `Q = (P − 0.2S)²/(P + 0.8S)`, `S = 25400/CN − 254` |
| 2D local inertial | `q^{t+Δt} = [q^t − g h_f Δt ∂(h+z)/∂x] / [1 + g Δt n² abs(q^t) / h_f^{10/3}]`; `Δt = α Δx/√(g h_max)`, α = 0.7 |
| Pipe capacity | `Q_full = (1/n)(1−β)A · R_h^{2/3} S^{1/2}` |
| 1D flow (P0) | `Q = sign(ΔH)·min(Q_full, (1/n)A_eff R_h^{2/3} √(abs(ΔH)/L))`; node `dH/dt = (ΣQ_in + Q_inlet − ΣQ_out − Q_surch)/A_s` |
| Inlet capture | `Q_inlet = (1−κ)·min(1.66·L·h^{3/2}, 0.6·A_o·√(2gh), Q_avail)` |
| Surcharge | `Q_surch = 0.6·A_m·√(2g(H − z_g − h))` when `H > z_g + h` |
| EnKF update | `θ^a = θ^f + P_θy(P_yy + R)^{-1}(y + ε − H(θ^f))`, θ = logit β |
| Route cost | `c_e(τ, v) = t_e·φ(h_e(τ))` if `P(h_e(τ) > θ_v) < p_max` else ∞ |
| Pedestrian hazard | unsafe if `h·v ≥ 0.5 m²/s` or `h ≥ 0.3 m` |
| Pump dispatch | `min Σ_i w_i Σ_τ max(0, V_i(τ) − Σ_p x_p,i c_p (τ − d_p,i)^+)`, `Σ_i x_p,i ≤ 1` |

## Appendix B — Command cheat sheet

```
make setup
make city CITY=mumbai            # ~10 min first time (downloads), then from cache
make city CITY=chennai --cache-only
make bundle BUNDLE=MUM-2019-07-02
make train                        # Flash-lite fit (≥ 200 Twin runs)
make bake BUNDLE=MUM-2019-07-02   # all cycles → data/runs
make demo                         # http://localhost:3000/console
make test && make e2e
make pack && make demo-video
uv run varuna cycle --bundle MUM-2019-07-02 --t 2019-07-02T06:40+05:30 --live
uv run varuna verify --event MUM-2019-07-02
```

## Appendix C — Things to verify before they appear on a slide

- Coordinates of every hotspot and asset (Nominatim/OSM), stored with the verified source.
- Gauge totals and the timeline of 2 July 2019 (IMD/BMC/news), stored in `manifest.sources`.
- Tide times for the replay day (Survey of India / INCOIS tables); else the tide is labelled illustrative.
- BMC pumping stations and holding tanks (public BMC documents); pumps are synthetic.
- Numbers quoted about IFLOWS, C-FLOWS, UFRMP (from the blueprint's references); cite them on the landing table.

## Appendix D — Glossary (UI vocabulary)

nowcast · scrub · hotspot · segment · surcharge · backflow · blockage (β) · clogging (κ) · safe until · passable · reachability · isochrone · dispatch · what-if · physics check · replay · baked · live · degraded · inferred · reconstructed · CAP · Exercise/Actual · run stamp.

---

*End of spec. Update the STATUS BOARD, then start Phase 0.*
