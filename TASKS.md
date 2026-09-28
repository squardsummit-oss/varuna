# Task list — citizen dashboard, authority desk, rural advisory

Reads with `PRD.md`, `TECH_SPEC.md`, `UI_SPEC.md`. Task ids are stable and are used in commit
messages: `[D-07] …`. The protocol is `SPEC.md` section 0 rule 3: `- [ ]` not done, `- [x]` done
with date and short hash, `- [-]` deliberately skipped with the reason. Never delete a task.

**Definition of done for every task below:** code written · tests pass · wired into a screen or an
endpoint a person can reach · this checkbox updated · gates green (`pnpm lint && pnpm typecheck &&
pnpm test && uv run pytest`, `pnpm lint:design`) · committed.

---

## Wave 0 — foundations that everything else needs (no UI)

- [x] **D-01 Exceedance is real.** `services/route/varuna_route/forecast.py` reads `p_gt_*` from the
  run and falls back to the median step only for a one-member run; correct the stale docstring.
  *Accepts:* on the 08:40 run a segment where members disagree returns a probability strictly
  between 0 and 1; `risk_tolerance` changes a route in a test. *Why first:* every probability the
  new screens print is 1.0 or 0.0 until this lands. (2026-09-19, 3bc358f - the router reads `p_gt_*` from the run and falls back to the median step only for a one-member run.)
- [x] **D-02 Pump benefit is the emulator.** Pass the AOI hyetograph the cycle already computes into
  `build_pump_plan` in `services/cycle/varuna_cycle/twin_cycle.py`. *Accepts:* `pump_plan.json`
  records `benefit_model: "emulator"`; the run note says which model produced it; a unit test pins
  that the bathtub path is only taken when no rain series is available. (2026-09-19, 3bc358f - the AOI hyetograph the cycle already computes is passed into `build_pump_plan`; `pump_plan.json` records which model priced it.)
- [x] **D-03 Street names stop leaking Python.** 95 segments carry a stringified list as their name
  and the demo route crosses two. Normalise at the city export and at read time. *Accepts:* no
  name in `segments.parquet`, the route response, the road-conditions feed or an alert headline
  starts with `[`. (2026-09-19, 3bc358f - `services/products/varuna_products/names.py` normalises at the city export and at read time.)
- [x] **D-04 `varuna_schemas.net`.** The outbound-HTTP helper ADR-0006 mandates and that does not
  exist: timeout, retry, `VARUNA_OFFLINE` refusal, truststore. *Accepts:* the weather proxy (D-05)
  uses it; a test proves `VARUNA_OFFLINE=1` refuses without a socket. (2026-09-19, 3bc358f - `packages/schemas/varuna_schemas/net.py`; a test replaces five socket entry points with tripwires and proves `VARUNA_OFFLINE=1` refuses without opening one.)
- [x] **D-05 `GET /v1/weather`.** Open-Meteo proxy with a 15-minute in-process cache, a last-good
  disk copy, `age_s`, `stale`, `source`, `licence`. *Accepts:* second call makes no outbound
  request; offline serves the stamped copy; upstream 500 degrades with the section 12 envelope;
  contract test and `pnpm typegen` regenerated. (2026-09-19, 3bc358f - measured by the implementer at 1,260.7-1,465.0 ms uncached and 5.2-21.8 ms cached; the contract snapshot went 46 -> 47 paths and `pnpm typegen` ran on merge. ADR-0061.)
- [x] **D-06 Ops overlay store.** `data/ops/<city>.jsonl`, append-only, with
  `varuna_route.overlay.apply(...)` honoured by the router and the road-conditions feed.
  *Accepts:* a closure makes the next route avoid the street and annotates the reason; the sha256
  of every baked product is unchanged after writes (rule 8). (2026-09-19, 3bc358f - `services/route/varuna_route/ops_overlay.py`, applied at read time by the router and the road-conditions feed.)
- [x] **D-07 Authority endpoints.** Implement `POST /v1/alerts/{id}/ack`, `/escalate`,
  `POST /v1/pumps/optimise`, `/dispatch`, `POST /v1/ops/closures`, `POST /v1/ops/pumps/{id}/status`,
  `GET /v1/ops/log`. Gate writes behind `VARUNA_OPS_PASSPHRASE` (unset = refuse, with the reason)
  and a 30-per-minute limit. The optimiser honours pump `status`. *Accepts:* each endpoint has a
  test for success, refusal without the passphrase, and the rate limit; `/v1/alerts` reflects an
  acknowledgement after a reload. (2026-09-19, 19bfc19 - every endpoint with tests for success, refusal without the passphrase and the rate limit; the sha256 of every file in a baked run is unchanged after a closure, a pump status change and a dispatch. **`GET /v1/alerts` does not yet reflect an acknowledgement** - that handler is in a router this chunk did not own - so the ops log is the record until it is wired. ADR-0062. **2026-09-26, 8963ff4:** the passphrase is read from `.env` through Settings as well as from the environment, an exported value winning; before, one written into `.env` left the desk read-only. ADR-0084.)
- [x] **D-08 Route spreading and reasons.** `spread`, `trip_id`, `explain` on `POST /v1/route`;
  `corridors[]` and structured `reasons[]` in the response, per TECH_SPEC §3.2–3.3. *Accepts:*
  same `trip_id` always lands on the same corridor; 1,000 ids land within 2 % of `share`; every
  corridor satisfies the profile threshold; the disclosure note is present. (2026-09-19, 3bc358f - `spread`, `trip_id` and `explain` on the request; `corridors[]` and structured `reasons[]` back. ADR-0060. **Its gates were run separately after it landed**: ruff, ruff format, eslint, tsc, vitest and the full pytest suite pass. No adversarial review has read this code.)
- [x] **D-09 City threading.** `city` through `depth.py`'s twelve `_resolve` calls and every depth
  route; the console, the run store and the city switcher read `?city=`. *Accepts:* with Chennai
  built, `/console?city=chennai` serves Chennai depth and the switcher lists Chennai without a
  hard-coded row. (2026-09-26, ef7fdd9 - **an unknown city was served another city's run**: a name with no run-id code gave an empty prefix, which filtered nothing, so `?city=atlantis` got Chennai's newest run. It is 404 `unknown_city` now on every depth, what-if and ops route, blaming the request or `VARUNA_CITY` as appropriate, and the no-run hints name that city's own bundle rather than `MUM-2019-07-02`. The route feeds and the report feedback count keep the old fallback. ADR-0083.)

## Wave 1 — the citizen dashboard
 (2026-09-19, 19bfc19 - `city` through the depth routes, `GET /v1/cities`, and the switcher reading `?city=` instead of a hard-coded disabled row. Typing `/console?city=chennai` by hand still pins a Mumbai run; the INTEGRATE chunk owns that.)
- [x] **D-10 Google map shell.** `@vis.gl/react-google-maps` + `@deck.gl/google-maps`, key reader,
  runtime token-built dark style, `GoogleMapsOverlay` in overlaid mode, `fitBounds` on first paint,
  and the `FloodMap` fallback when the key is absent or refused. *Accepts:* with the key unset the
  screen renders VARUNA's own map and logs no console error; with it set, Google tiles draw under
  VARUNA's streets; `pnpm lint:design` clean (no raw hex). (2026-09-19, 19bfc19 - and the fallback is the path that runs: the key is refused at `localhost:3000` and at `varuna-dhrishta.vercel.app`, so the deployed dashboard draws VARUNA's own Esri-and-deck map under a notice saying so. Two defects found by opening it rather than reading it: an auth failure reaches an app through neither `onError` nor the timeout, only `window.gm_authFailure`; and Google paints its own grey error surface before that fires, so its map is covered until `onTilesLoaded`. ADR-0059.)
- [x] **D-11 `/dashboard` shell and rail.** Route, seven-line layout, header with run stamp and
  weather chip, rail on desktop and `BottomSheet` on phone, "Streets near you" from the reader's
  position, "Report water" in both places. *Accepts:* 390 × 844 and 1440 × 900 both usable; the
  map fills its pane within 1 px at three viewports; axe 0. (2026-09-19, 19bfc19 - the map fills its pane to 0 px at 1440x900, 1366x768 and 390x844, no page overflow, axe 0 violations at all three, and no control under 44 px.)
- [x] **D-12 Route card, corridors and explanations.** `lib/explain.ts` (pure, tested), the two-ETA
  comparison, up to four reasons, the corridor radio group with its disclosure, pumps near the
  route with the emulator chip, and "leave before". *Accepts:* every sentence renders from a
  reason object; a reason missing its number is dropped, not softened; keyboard reachable. (2026-09-19, 19bfc19 - `lib/explain.ts` is pure and tested per sentence kind, including the rule that a reason missing its number is dropped rather than softened. Mounted by `/dashboard` through the seam it was built against.)
- [x] **D-13 Weather chip and dialog.** Per UI_SPEC §5, including the separation sentence and the
  offline state. *Accepts:* live values with attribution; offline shows the age; no layout shift
  when it loads. (2026-09-19, 19bfc19 - live on the deployed site: 29 C, light drizzle, with the next hours' rain and the Open-Meteo attribution; the dialog carries the sentence that the map is a 2019 replay while the chip is today's sky.)
- [x] **D-14 Globe entry (M27).** Add the M27 row to `SPEC.md` section 8 **first**, then the
  third act in `globe-intro.tsx`, the 50 m detail with a 110 m fallback, the skip control, the
  once-per-session rule and the framed hand-off. *Accepts:* four acts run at ≥ 55 fps on the demo
  laptop; reduced motion shows the static Mumbai frame; the map is framed before the cross-fade.

## Wave 2 — authority desk and rural advisory
 (2026-09-19, 19bfc19 - three acts on the catalogue's durations, once per session, skippable, with a reduced-motion cut. **The budget is missed: 41.2-42.1 fps against section 14's 55**, measured on a harness with nothing else on screen, so the real dashboard will be slower.)
- [x] **D-15 `/authority`.** Passphrase gate, the two columns of UI_SPEC §6, the citizen inbox and
  the ops log. *Accepts:* closing a street changes the next route on screen; marking a pump
  unavailable removes it from the next optimise; an acknowledgement survives a reload; every
  right-column action says it changed no forecast. (2026-09-19, ecedc80 - **the loop was driven in a browser by the lead, not only by its implementer**: typing segment S618477973-001 and "Slab collapsed outside Bharatmata; police barricade across both carriageways" into the desk wrote one ops-log entry, and the next KEM-to-Sion ambulance route went from 5.6 min / 5,121 m with nothing avoided to 6.1 min / 5,241 m avoiding Dr Babasaheb Ambedkar Marg (Vincent Road), carrying the officer's own words as `closed_reason`. The gate's four states all render; the passphrase lives in one header and sessionStorage. **On the deployed API `VARUNA_OPS_PASSPHRASE` is deliberately unset**, so the desk there says it is read-only, as PRD 6 intends.)
- [x] **D-16 `/rural`.** Server-rendered advisory, no client JavaScript, under 30 KB, print
  stylesheet, share link carrying the query. *Accepts:* measured transfer size with JS disabled;
  the "what we do not know" block is present; the same numbers as the dashboard for the same trip.

## Wave 3 — repairs the demo path needs
 (2026-09-19, ecedc80 - **4,281 bytes in its worst state, 13.9 % of the 30 KB budget**, measured with JavaScript disabled: no script, stylesheet, font, image or inline handler, and Next ships no client bundle to the route. An unknown place is refused with the register's nearest names rather than an estimate, and the share link carries the run id so a forward reproduces the page.)
- [x] **D-17 Layers panel scrolls.** The floating column gets `min-h-0` and `overflow-y-auto`;
  `LayerPanel` drops `overflow-hidden`. *Accepts:* a test at 1366 × 768 asserts the panel scrolls
  and that every row is reachable; the cycle picker no longer overflows its column; the
  probability legend no longer draws over the panel. (2026-09-19, 19bfc19 - the floating column scrolls at 1366 x 768 with a test to prove it; the cycle picker no longer spills and the probability legend stacks below rather than over. **Nothing yet tells the operator it scrolls** - no scrollbar, no fade at the cut.)
- [x] **D-18 Maps fill their panes.** `/route` and `/onboard` stop capping the map to a band;
  `/dashboard`, `/console`, `/drains` verified at three viewports. *Accepts:* one Playwright test
  measures the map box against its pane on four screens. (2026-09-19, 19bfc19 - one Playwright test measures the map box against its pane on four screens.)
- [x] **D-19 Sharper zoom.** Satellite cap 17 → 19 with a tile-budget check. *Accepts:* imagery at
  z18 and z19 over Hindmata; request count and cache size recorded in `docs/QA.md`. (2026-09-19, 19bfc19 - the imagery cap is 19 where Esri serves to 23; `basemap.ts`'s constants were documentation rather than enforcement and the real cap was in `satellite.tsx`. No automated test: what it changes is the sharpness of a tile.)
- [x] **D-20 `/map` opens on the storm.** Pin the 06:40 cycle as `/console` does. *Accepts:* a test
  pins the opening run id. (2026-09-19, 19bfc19 - `/map` reuses `lib/opening-run.ts` rather than a second rule, with a test pinning the opening run id.)
- [x] **D-21 Chennai onboarding finishes properly.** Finish card opens `?city=chennai`; the wizard's
  map fades in each layer as its step completes (M19); the first forecast's depth is drawn; a
  scoped layers panel is present; the copy stops promising a switcher behaviour that does not
  exist. *Accepts:* a local run from cache ends on a Chennai console showing Chennai depth; the
  e2e test asserts the finish card's href. (2026-09-26, fe36aa7 - **a Chennai built before the session read "Waiting · 0 s" on all six steps** beside a map drawing it, with the finish card dim. The five build steps now read "Already built" with no elapsed time when the API reports the city built, and the First forecast row and the finish card follow only once a Chennai run has actually been read, because `built` means only that `segments.parquet` exists: the deployed API is built with no Chennai run, and says so.)

## Wave 4 — documentation, deployment, rehearsal
 (2026-09-19, 19bfc19 - the finish card lands on a Chennai console, each layer fades in as its step completes (M19), the first forecast's depth is drawn and a scoped layers panel is present. The e2e assertion on the card's href is not written; it is covered by a unit test of `consoleHref`.)
- [x] **D-22 ADRs.** ADR-0059 Google basemap with a deck overlay and no Google routing (written
  2026-09-19); ADR-0060 route spreading as a stated policy (written); ADR-0061 live weather
  beside a reconstructed replay (written); ADR-0062 authority edits as a read-time overlay
  (waits on D-07). The last two swapped numbers so they are written in the order they landed. (2026-09-19 - ADR-0059 the Google basemap and the key that refuses every origin tested, ADR-0060 spreading as a stated policy and the formula that first got it backwards, ADR-0061 live weather beside a replay, ADR-0062 authority edits as a read-time overlay.)
- [x] **D-23 Spec upkeep.** `SPEC.md` section 3.4 gains `/dashboard`, `/authority`, `/rural`;
  section 8 gains M27; the status board records what these tasks changed, including what is still
  missed. `docs/SIMPLIFICATIONS.md` gains the Map ID, the spreading policy and the pump-effect rows. (2026-09-19, 4b81b7f - SPEC.md 3.4 lists the three screens, section 8 carries M27, the status board has a row for this work with its four measured misses, and `docs/SIMPLIFICATIONS.md` gained five rows: the Map ID, the spreading policy, live weather, the authority passphrase and the rural scope.)
- [x] **D-24 Deploy and verify.** Vercel (key already set) and Railway; then a browser pass over
  `/dashboard`, `/authority`, `/rural`, `/console`, `/onboard` measuring console errors, first
  paint and the map fit. Record the numbers in `docs/QA.md`. (2026-09-19 - deployed and walked: `/dashboard` and `/rural` verified in a browser on `varuna-dhrishta.vercel.app` with the run stamp, the live chip and the advisory's own numbers; the API redeployed twice and every new endpoint answers 200 warm (`/v1/weather` 0.74 s, `/v1/cities` 0.83 s, `/v1/ops/log` 0.62 s). **Two gaps in this pass, stated rather than hidden:** the deployed `/authority` could not be read in my browser at all - the pane blocked 54 of its resources with `ERR_BLOCKED_BY_CLIENT`, while the same CSS answers 200 to curl and the same page renders locally - so it is verified locally and by API, not on the deployed origin; and `/console` and `/onboard` were not re-walked on the deployed site after this merge. A **cold** Railway container costs about twenty seconds before its first answer.)
- [x] **D-25 A Google key of our own (human).** (2026-09-23) *This task was impossible as
  written, and that is the finding.* It said to add our origins to "the key's" referrer list.
  The key was **MCGM's**, scraped from their public page into `.env.local` -
  `docs/research/_raw/mcgm_main.js:17` holds the same value byte for byte - so its referrer list
  naturally named no origin of ours, which is all ADR-0059 and the 2026-09-19
  `RefererNotAllowedMapError` ever measured. Nobody on this team could have added anything to it,
  and every call it served spent MCGM's quota.
  Closed by the team issuing a key on their own project, `project-b71068dc-8dd9-4753-807`, with
  billing linked and both the **Maps JavaScript API** and the **Map Tiles API** enabled. It took
  three projects: the first had the Map Tiles API off, the second and third had it on but no
  billing account linked, and an unbilled project answers every Map Tiles method with a bare
  `404 NOT_FOUND` that names nothing - Static Maps is the endpoint that says "You must enable
  Billing" out loud, which is how it was diagnosed.

- [ ] **D-26 Restrict the new key before the deployed site serves it (human, blocking a deploy).**
  Measured 2026-09-23: the key carries **no HTTP-referrer restriction** - the Map Tiles root
  answers 200 from curl with no `Referer`, with `http://localhost:3000/`, and with an arbitrary
  one - and it answers **200 to Static Maps, Geocoding and Directions** as well as to Map Tiles.
  It is read as `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`, which Next inlines into the JavaScript every
  visitor downloads, so on a public site it is a billed key anyone can lift and spend. In the
  Google Cloud Console set **Application restrictions -> HTTP referrers** to
  `https://varuna-dhrishta.vercel.app/*` and `http://localhost:3000/*`, and **API restrictions**
  to Maps JavaScript API and Map Tiles API only. Also set a budget alert on the billing account.
  *Owner: the team.*

---

## Sequencing

Wave 0 is a hard prerequisite for Wave 1: D-01 gates every probability, D-05 gates the weather
dialog, D-08 gates the route card. Waves 2 and 3 are independent of each other and can run in
parallel once Wave 0 lands. Wave 4 closes.

## Out of scope, with reasons

- Google Directions, Places, Geocoding, Static Maps — the key refuses them and the routing is ours.
- Live tide, decoded live radar, a live rain gauge feed — no defensible free source today.
- Real authentication — a prototype passphrase is honest; a fake login is not.
- A rural city build — the drain graph infers from road density, which is an urban assumption; the
  advisory ships against a built AOI and says so.
