# Technical specification — citizen dashboard, authority desk, rural advisory

**Reads with:** `PRD.md` (what and why), `UI_SPEC.md` (how it looks and moves), `TASKS.md` (order of
work), `SPEC.md` (the build spec every rule below inherits).

---

## 0. Ground truth about the code we are building on

Measured on the working tree at `b3f2d04`. These facts decide most of the design.

| Fact | Consequence |
|---|---|
| The Google key is **referrer-restricted**; Directions, Geocoding, Static Maps and Places all answer `REQUEST_DENIED` server-side, and Places (New) is not enabled on the project. The Maps JavaScript bootstrap returns 200 and 832 KB | Google is a **basemap only**. Every route, search and marker comes from VARUNA |
| `AppShell` is a component each screen opts into, not a Next layout; `app/map/layout.tsx` is a seven-line full-viewport wrapper | A new citizen route is a folder plus a seven-line layout. No route groups |
| **MapLibre is installed and unused.** The console renders deck.gl standalone over Esri raster tiles; `components/map/basemap.ts` is imported by nothing | Do not "integrate with MapLibre". There is nothing to integrate with |
| `@deck.gl/google-maps@9.3.11` matches every installed deck.gl package | The Google overlay needs no version bump |
| The floating console column caps its height and declares no overflow; `LayerPanel`'s root carries `overflow-hidden`, which zeroes its flex minimum size. The panel needs ~324 px and is clipped at 1366 × 768 | The "stuck scrolling" bug is CSS on the column, not wheel handling. deck.gl never sees the event |
| Satellite tiles stop at zoom 17; Esri World Imagery serves to 23 and returns real imagery over Hindmata at 18 and 19 | "Zoom without losing quality" is a one-constant fix plus a tile-budget check |
| The router computes `P(depth > threshold)` as a hard 1 or 0 from the median depth, though every baked run carries 20-member exceedance | `risk_tolerance` is a placebo today. Fix before any UI claims a probability |
| `build_pump_plan` is called without the AOI hyetograph the cycle already computes, so every shipped pump benefit is the bathtub estimate, not the emulator | One argument at the cycle's call site turns the pump numbers real |
| Nothing writes a pump's effect back into `segment_forecast.parquet` or `segments_wet.json` | A dispatched pump cannot change a route. It changes a **stated estimate**, and the UI says so |
| `POST /v1/reports` is the only endpoint in the API that writes persistent state; ack, escalate, optimise and dispatch are 501 with request models already defined | The authority desk is four handlers and one overlay store, not new infrastructure |
| Depth endpoints call `_resolve(run_id)` and never pass a city; runs sort reverse by name so `MUM-` always beats `CHN-` | Chennai can never be served Chennai depth until `city` is threaded through |
| Open-Meteo answers for Mumbai with no key: 10,000 calls/day, CC BY 4.0 attribution required | Live weather is a small server proxy, not a vendor integration |

---

## 1. Architecture

```
Browser                                   API (FastAPI)                 Artifacts
-------                                   -------------                 ---------
/dashboard  ── Google Maps JS (basemap) ── GET  /v1/nowcast/*            data/runs/<run_id>/
            └─ deck.gl overlay (water)  ── POST /v1/route (+spread)      city/<city>/
            └─ weather chip ─────────────── GET  /v1/weather ─────────── Open-Meteo (live)
            └─ report sheet ────────────── POST /v1/reports ──────────── data/reports/inbox.jsonl
/authority  ── passphrase header ────────── POST /v1/ops/closures        data/ops/<city>.jsonl
                                          ── POST /v1/pumps/{optimise,dispatch}
                                          ── POST /v1/alerts/{id}/{ack,escalate}
/rural      ── server-rendered, no JS ───── GET  /v1/route (server side)
```

Nothing in the new surfaces writes into `data/runs/`. Authority edits live in a separate
append-only overlay that is applied when a route or a feed is read, so `make bake` stays
byte-identical (rule 8, `P5.9`).

---

## 2. Google Maps integration

### 2.1 Libraries and loading

- Add `@vis.gl/react-google-maps` (React wrapper; depends on `@googlemaps/js-api-loader`) and
  `@deck.gl/google-maps@9.3.11`.
- `<APIProvider apiKey={googleMapsKey()}>` mounted **only** in `/dashboard`'s layout. No other
  screen loads Google.
- `googleMapsKey()` lives in `apps/command/lib/maps/google.ts` and copies the established reader in
  `components/map/basemap.ts`: empty, `"undefined"` and `"null"` all mean absent.

### 2.2 Rendering split

- **Google** draws the basemap, its labels and its gestures, and owns the camera.
- **deck.gl** draws VARUNA's data through `GoogleMapsOverlay` in **overlaid** mode
  (`interleaved: false`). Overlaid avoids the aliasing that interleaved rendering shows on Google's
  WebGL2 context, which has no multisampling, and keeps our layer modules unchanged.
- Layers reused as-is from `components/map/layers/`: street depth, the route paths, surcharge
  markers, hotspot rings, truth pins. The depth **raster** stays off on the citizen screen: 36
  decoded frames are an operator's tool, and the street colours carry the same information.

### 2.3 Styling without a Map ID

The `styles` array is refused on any vector map or any map with a Map ID, and cloud styling needs
Cloud Console access this build does not have. Therefore:

- The citizen map is a **raster** Google map (no `mapId`) with an inline `styles` array.
- The array is **built at runtime from tokens** (`cssVar("--ink")` and friends), exactly as
  `public-legend.tsx` reads its colours, so no raw hex enters `apps/command` and `pnpm lint:design`
  stays clean.
- A Map ID with cloud styling is recorded as the upgrade path in `docs/SIMPLIFICATIONS.md`.

### 2.4 Camera

- First paint: `map.fitBounds(cityBounds(city), padding)` — the citizen equivalent of
  `layers/camera.ts`'s derived fit. Never a stored zoom.
- The globe hand-off (UI_SPEC §2) ends at the same bounds, so the cross-fade lands on an already
  framed map.
- Zoom quality: Google serves its own tiles at every zoom; the Esri cap only applies to the deck
  basemap on operator screens, which `TASKS.md` D-14 raises from 17 to 19.

### 2.5 Failure and offline

`onError` from the API provider, plus a 4 s timeout on the bootstrap, switch the screen to
`<FloodMap>` (deck.gl + Esri) with a labelled notice: *"Google Maps did not load; showing VARUNA's
own map."* Consequences: no console error escapes to break the zero-error e2e gate, `make pack`
still produces a working offline demo, and a referrer mistake in the Cloud Console degrades rather
than breaks.

### 2.6 Key handling

- `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` in `.env.local` (git-ignored, verified) and in Vercel
  production (already set, encrypted). `.env.example` declares the **name only**, never a value.
- The key is a browser key and cannot be secret. Protection is the Cloud Console's HTTP-referrer
  list (`https://varuna-dhrishta.vercel.app/*`, `http://localhost:3000/*`) plus an API restriction
  to Maps JavaScript API. This is a human step and is recorded in `docs/DEPLOY.md`.

---

## 3. API additions and changes

All new paths follow section 12: `/v1` prefix, the error envelope, ISO 8601 with +05:30, and every
response carrying `run_id` where a run is involved. Each one regenerates
`apps/command/openapi.json` and `lib/api/types.ts` (`uv run varuna openapi && pnpm typegen`).

### 3.1 `GET /v1/weather?city=mumbai` (new)

- Proxies Open-Meteo `https://api.open-meteo.com/v1/forecast` with `current=` (temperature,
  precipitation, weather_code, wind) and `hourly=precipitation,precipitation_probability` limited to
  `forecast_hours=4`.
- 15-minute TTL cache in process, modelled on the sweep cache in `routers/verify.py` (ADR-0058);
  the last good response is also written to `data/cache/weather-<city>.json` and served, stamped
  with its age, when the network is unavailable.
- `VARUNA_OFFLINE=1`: never calls out; serves the cached copy with its age, or returns the section
  12 envelope naming `VARUNA_OFFLINE` as the reason.
- Response carries `source: "open-meteo"`, `licence: "CC BY 4.0"`, `fetched_at`, `age_s` and
  `stale: bool`. The UI prints the attribution; the licence requires it.
- Outbound HTTP goes through a new `varuna_schemas.net.get_json()` helper, which ADR-0006 mandates
  and which does not yet exist; the city pipeline's fetches move behind it in a later task.

### 3.2 `POST /v1/route` (extended)

Additive request fields, all optional:

| Field | Meaning |
|---|---|
| `spread` (bool, default true) | Return up to three safe corridors and an assignment |
| `trip_id` (string) | Stable id for deterministic assignment; the client sends a random one per trip |
| `explain` (bool, default true) | Include structured reasons |

Additive response fields:

- `corridors[]`: each with `id`, `label` ("A", "B", "C"), the route, `share` (the policy's fraction),
  `assigned` (bool) and `capacity_score`.
- `reasons[]`: structured, never prose — `{kind, segment_id, name, depth_cm, threshold_cm, at,
  probability, design_intensity_mm_h, forecast_peak_mm_h}`. The frontend words them (UI_SPEC §4),
  which keeps sentences out of Python and lets i18n translate them later.
- `notes[]` gains the spreading disclosure and any overlay applied.

### 3.3 Route spreading algorithm

```
1. Plan the VARUNA route (existing time-dependent Dijkstra).
2. Plan up to two alternates by penalising used edges x3 (existing).
3. Keep only corridors whose max P(depth > threshold_profile) < risk_tolerance.
4. capacity_score(c) = min over edges of lanes * (1 - congestion_proxy(depth)) / minutes(c)
   congestion_proxy is the router's own phi(h) slowdown, so no new model is invented.
   Corrected 2026-09-19: this said "sum over edges", and a sum rewards length - measured on the
   08:40 run, a Worli-to-Chembur car trip gave 0.4038 to the 23.9-minute corridor and 0.2458 to
   an equally safe 13.5-minute one. A road is as wide as its narrowest point, and a corridor that
   holds a vehicle twice as long absorbs half the flow.
5. share(c) = capacity_score(c) / sum(capacity_score)
6. assignment = weighted choice keyed by sha256(trip_id) -> deterministic per trip,
   uniform over the population, reproducible in tests.
```

Honesty: `notes` states that the split is a policy, the demand is not measured, and all corridors
are shown. No screen claims a modelled traffic volume.

### 3.4 Exceedance fix (prerequisite for anything probabilistic)

`services/route/varuna_route/forecast.py` reads `p_gt_15/30/45/60` from the run when present and
falls back to the median-depth step function only when a run has one member. Its stale docstring
("a baked run is deterministic") is corrected. Without this, `risk_tolerance` does nothing and every
`avoided[].probability` is exactly 1.0 or 0.0.

### 3.5 Pump benefit fix

`services/cycle/varuna_cycle/twin_cycle.py` passes the AOI hyetograph it already computes into
`build_pump_plan(...)`, so the benefit comes from the emulator rather than the bathtub estimate. The
run note keeps saying which model produced the number.

### 3.6 Authority writes

| Endpoint | Status today | After |
|---|---|---|
| `POST /v1/alerts/{id}/ack`, `/escalate` | 501 | Appends `{ts, user, note, action}` to the ops log; `GET /v1/alerts` overlays state at read time |
| `POST /v1/pumps/optimise` | 501 | Runs the greedy optimiser on demand, honouring pump `status`; warm-start guard returns 503 with a reason while the index loads |
| `POST /v1/pumps/dispatch` | 501 | Appends the dispatch order, returns the plain-language order string the board already renders |
| `POST /v1/ops/closures` (new) | — | `{segment_id \| street, reason, until, user}` appended; `GET /v1/ops/closures?city=` returns the live set |
| `POST /v1/ops/pumps/{id}/status` (new) | — | `available \| unavailable \| moved`, with an optional new depot point |

**Overlay semantics.** One append-only JSONL per city under `data/ops/<city>.jsonl`. Routing reads
it through `varuna_route.overlay.apply(...)`, which marks closed segments impassable regardless of
depth and annotates the reason so the explanation can say *"closed by the ward officer at 08:12"*.
Products are never rewritten; a re-bake is unaffected; a restart replays the log.

**Gate.** `VARUNA_OPS_PASSPHRASE` (unset = writes refused with a named reason). The browser sends it
as `X-Varuna-Ops` after a local prompt; it is never stored in `localStorage`. Rate limit: 30 writes
per minute per process. On the deployed API the variable stays unset, so the desk is read-only
there and says so.

### 3.7 City threading

`services/api/varuna_api/routers/depth.py` passes `city` into `_resolve(...)` at all twelve call
sites, and every depth route accepts `?city=`. `apps/command` threads `city` from the URL into
`FloodMap`, the run store and the city switcher, which becomes data-driven from
`GET /v1/city/{city}/layers/...` availability rather than a hard-coded disabled row.

---

## 4. Frontend structure

```
apps/command/app/dashboard/            layout.tsx (full viewport, no AppShell), page.tsx, dashboard-screen.tsx
apps/command/app/authority/            layout.tsx, page.tsx, authority-screen.tsx
apps/command/app/rural/                page.tsx (server component, no client JS)
apps/command/components/citizen/       google-map.tsx, route-card.tsx, corridor-picker.tsx,
                                       weather-chip.tsx, weather-dialog.tsx, nearby-sheet.tsx,
                                       explain-list.tsx, pumps-near-you.tsx
apps/command/components/authority/     ops-inbox.tsx, closure-form.tsx, pump-status-table.tsx,
                                       alert-actions.tsx, ops-log.tsx, passphrase-gate.tsx
apps/command/lib/maps/                 google.ts (key reader), google-style.ts (tokens -> styles),
                                       overlay.ts (GoogleMapsOverlay lifecycle)
apps/command/lib/api/                  weather.ts, ops.ts, route.ts (extended)
apps/command/lib/explain.ts            structured reasons -> sentences (pure, unit-tested)
```

`lib/explain.ts` is pure and has no React import, so its sentences are testable without a DOM and
translatable later (`P9.9`).

---

## 5. The globe entry

The existing `components/landing/globe-intro.tsx` is a d3-geo raw-projection interpolation over a
committed 110 m world topology, driven by a single phase number. It is extended, not replaced:

- Act 1 (0 → 0.45): the sphere turns to bring India to the meridian. Unchanged machinery.
- Act 2 (0.45 → 0.75, **new**): the projection scales toward India's bounding box while the sphere
  flattens; India's own path is drawn from the same topology at a heavier stroke.
- Act 3 (0.75 → 1): the frame narrows to the Mumbai AOI and cross-fades into the dashboard's map,
  which is already framed on the same bounds, so nothing jumps.
- Land detail rises from 110 m to 50 m for Acts 2 and 3 only (the 50 m topology is fetched lazily
  and cached; if it fails, 110 m carries the whole sequence).
- A new catalogue row (`M27`) is added to `SPEC.md` section 8 **before** the code lands, per rule
  9, with its reduced-motion fallback: the finished Mumbai frame, no rotation.

"High quality" here means a crisp vector Earth in the product's palette with a soft limb shadow and
a graticule — not photographic imagery. A textured sphere would need 8–21 MB of NASA Blue Marble
tiles, which `make pack` and a venue without network cannot carry, and which no other screen uses.

---

## 6. Testing and gates

| Area | Test |
|---|---|
| `lib/explain.ts` | Sentence per reason kind; refuses to render a reason missing its number |
| Route spreading | Same `trip_id` always lands on the same corridor; a population of 1,000 ids lands within 2 % of `share`; corridors all satisfy the threshold |
| Exceedance | A 20-member run yields a probability strictly between 0 and 1 where members disagree |
| Overlay | A closure makes the next route avoid the street; the baked products' sha256 are unchanged after a write |
| Weather proxy | Cache hit costs no outbound call; `VARUNA_OFFLINE=1` serves the stamped copy; a 500 from upstream degrades rather than 500s |
| Google failure | With the key unset, `/dashboard` renders `FloodMap` and logs no console error (Playwright) |
| Layers panel | At 1366 × 768 the panel's scrollHeight exceeds its clientHeight **and** it scrolls (the missing test today) |
| Maps fill | On `/dashboard`, `/console`, `/route` and `/onboard`, the map element's box equals its pane within 1 px at three viewports |
| Axe | 0 violations on `/dashboard`, `/authority`, `/rural` |
| Contract | `test_contract.py` passes after each openapi regeneration |

CI stays the gate: `pnpm lint && pnpm typecheck && pnpm test && uv run pytest`, `pnpm lint:design`,
Playwright, Lighthouse.

---

## 7. Deployment

- **Vercel**: `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` set (done). The build is unchanged otherwise.
- **Railway**: new endpoints ship with the image. `VARUNA_OPS_PASSPHRASE` stays unset there, so the
  authority desk is read-only on the public URL; the demo laptop sets it.
- Outbound weather calls are made by the API, not the browser, so the venue firewall has one host to
  allow (`api.open-meteo.com`) and the offline path is the cached file.
