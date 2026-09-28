# VARUNA API

FastAPI service in `services/api` (`uv run uvicorn varuna_api.main:app --port 8000`). OpenAPI 3.1
at `http://localhost:8000/docs`. The committed snapshot is `apps/command/openapi.json`
(`uv run varuna openapi`), and `pnpm typegen` regenerates `apps/command/lib/api/types.ts` from it,
so the console and the service share one contract; `services/api/tests/test_contract.py` fails
when the snapshot and the app drift apart.

The console's `/api` screen reads that same snapshot: every operation below, a form for each, and
three presets that run against the real API (see [The explorer](#the-explorer)).

Most responses carry `run_id`, and forecast products carry `valid_ts`. Tiers follow SPEC.md
section 3.1.

## Endpoints

Every path in the committed snapshot: **52 paths, 56 operations**. Query parameters are listed
as the snapshot declares them. "Gated" means the request needs the desk passphrase in the
`X-Varuna-Ops` header (see [Authority writes](#authority-writes)).

### Health, runs and cities

| Method and path | Purpose |
| --- | --- |
| `GET /healthz` | Liveness, mode (`replay`/`live`/`degraded`), bundle, last run with its `total_ms` and mass balance |
| `GET /v1/runs?city=&bundle=&limit=` | Run registry, newest first |
| `GET /v1/runs/{run_id}` | Run provenance: versions, `stage_ms`, mass balance |
| `GET /v1/cities` | Cities with a config, whether each is built, its bbox and latest run, and the default city (added 2026-09-19) |
| `GET /v1/city/{city}/layers/{name}?bbox=` | Static layers simplified for the map: segments, drains, assets, hotspots, buildings. `bbox` is honoured here |

### Nowcast products

| Method and path | Purpose |
| --- | --- |
| `GET /v1/nowcast/segments?run_id=&bbox=&t=&profile=&format=` | Per-segment depth series for the street layer. **See the note below: the served handler differs from the snapshot** |
| `GET /v1/nowcast/segments/{segment_id}/series?run_id=` | One segment's fan chart and safe-until table. Answers 501 `not_implemented` today |
| `GET /v1/nowcast/raster?run_id=&t=&stat=` | Depth PNG for one step and statistic (`p50`, `p90`, `prob30`) |
| `GET /v1/nowcast/raster/bounds?run_id=&city=` | Where a run's rasters sit (lon/lat bounds) and what they are |
| `GET /v1/nowcast/hotspots?run_id=&city=&limit=` | Ranked hotspots with time to peak, exposure and the attribution label |
| `GET /v1/nowcast/surcharge?run_id=&city=` | Manholes surcharging and pipes running backwards |
| `GET /v1/nowcast/rain?run_id=&compute=&bundle=&t=` | Every Sky member's AOI hyetograph and the spread band; `compute=true` runs a live Sky cycle, one at a time |
| `GET /v1/nowcast/rain/series?hotspot=&lon=&lat=&run_id=&compute=&bundle=&t=` | Rain fan chart at one junction: quantiles and exceedance per step |

**`/v1/nowcast/segments` in the snapshot is the section 12 contract, not what is served.** The
snapshot documents `bbox`, `t`, `profile` and `format`, but the handler that answers the path
(`services/api/varuna_api/routers/depth.py`) reads `run_id`, `city` and `min_depth_cm` and returns
every segment that reaches `min_depth_cm` (default 5 cm) at some step, as
`{run_id, valid_ts[], depth_cm{segment_id: [cm per step]}, p_gt, n_segments_wet, ...}`. Measured
on the 08:40 IST cycle: 6,492 of 21,296 segments, 3,675,940 bytes, whether or not `bbox` is sent.
A true bounding-box query, without depths, is `GET /v1/city/mumbai/layers/segments?bbox=` (248,905
bytes for the Hindmata box below). Both routes are registered on the same path, so the snapshot
takes its parameters from one and the server answers from the other.

### Drains and observations

| Method and path | Purpose |
| --- | --- |
| `GET /v1/drains/health?run_id=&city=&min_beta=&limit=` | Pulse's posterior blockage per pipe |
| `GET /v1/drains/health.csv?run_id=&city=` | Desilting priority list as CSV |
| `GET /v1/observations?run_id=&city=` | What Pulse assimilated this cycle, with its effect on beta |
| `GET /v1/reports?limit=` | Reports received since the service started |
| `POST /v1/reports` | Citizen or field report (depth chips ankle/knee/waist); answers 202, and the feedback count arrives after a cycle has assimilated it |

### Routing, reachability and feeds

| Method and path | Purpose |
| --- | --- |
| `POST /v1/route` | A trip routed around the forecast water, beside the naive route. Body below |
| `GET /v1/route/facilities?city=` | Hospitals and fire stations, from the city's asset layer |
| `GET /v1/reachability?facility=&t=&profile=&city=&run_id=` | One facility's 5/10/15-minute catchment against dry, with the collapse flag |
| `GET /v1/feeds/road-conditions?profile=&run_id=&city=` | Impassable and degraded roads as GeoJSON with validity windows, closures applied |

`GET /v1/feeds/gtfs-rt/alerts` (section 12, P1) is not in the snapshot and answers 404.

**`POST /v1/route` body:** `{origin, destination, depart_at?, profile?, risk_tolerance?, run_id?,
spread?, trip_id?, explain?}`.

- `origin`, `destination`: `[lon, lat]` or `{lon, lat}`. Anything else is 422 `bad_point`.
- `depart_at`: ISO 8601 with an offset; 422 `bad_time` otherwise. Omitted means the run's cycle time.
- `profile`: `ambulance` (default), `fire_tender`, `bus`, `truck`, `car`, `two_wheeler`, `pedestrian`;
  422 `unknown_profile` names the valid ones.
- `risk_tolerance`: 0 to 1, the profile's default when omitted; 422 `bad_tolerance` outside it.
- `spread` (default true, added 2026-09-19): adds `corridors[]`, up to three safe roads with the
  share of traffic the spreading policy gives each and the one this request is assigned to.
- `trip_id` (added 2026-09-19): makes that assignment stable for one trip, so a reader who reloads
  is not sent somewhere else. It is the client's own random id; nothing is stored against it.
- `explain` (default true, added 2026-09-19): adds `reasons[]`, structured data (`kind` is
  `avoided`, `design`, `timing` or `closure`) that the frontend turns into sentences.
- `spread` and `explain` must be JSON booleans; anything else is 422 `bad_flag`.

The response carries `run_id, profile, depart_at, naive, varuna, alternates, avoided, corridors,
reasons, trip_id, notes, ms`. The KEM-to-Sion ambulance preset at 08:40 IST measured a server
`ms` of 84.5 and a round trip of 0.297-0.346 s warm (2.04 s on the first request after start) from
curl on the same machine, with about 14 python processes running.

### Alerts, pumps and the desk

| Method and path | Purpose |
| --- | --- |
| `GET /v1/alerts?run_id=&city=&level=` | Alerts a run raised |
| `GET /v1/alerts/{alert_id}.cap?run_id=&city=` | CAP 1.2 XML for one alert (`Exercise` on replay, `Actual` live) |
| `POST /v1/alerts/{alert_id}/ack?run_id=` | Acknowledge; recorded in the ops log. Gated |
| `POST /v1/alerts/{alert_id}/escalate?run_id=` | Escalate up the matrix; recorded in the ops log. Gated |
| `GET /v1/ops/alerts?run_id=&city=&level=` | A run's alerts with the desk's acknowledgements applied (added 2026-09-19) |
| `GET /v1/ops/closures?city=&at=` | Streets an authority has closed, optionally as of a time (added 2026-09-19) |
| `POST /v1/ops/closures` | Close or reopen a street (`ClosureRequest`). Gated (added 2026-09-19) |
| `POST /v1/ops/pumps/{pump_id}/status` | Set a pump's status (`PumpStatusRequest`). Gated (added 2026-09-19) |
| `GET /v1/ops/log?city=&limit=&kind=` | The append-only authority log, with `writes_enabled` (added 2026-09-19) |
| `GET /v1/pumps?run_id=&city=` | The run's pump inventory (synthetic, labelled) and dispatch plan |
| `POST /v1/pumps/optimise` | Re-run the greedy optimiser on demand. Gated |
| `POST /v1/pumps/dispatch` | Record a dispatch order (`PumpDispatchRequest`); sends no lorry. Gated |

### What-if

| Method and path | Purpose |
| --- | --- |
| `POST /v1/whatif` | Body `{run_id?, rain_scale?, cleaned_segments?, tide_offset_m?}`. The run's own Twin depths plus the emulator's difference between scenario and baseline, with the emulator's measured skill |
| `POST /v1/whatif/physics-check` | Re-run the Twin on a scenario (`PhysicsCheckRequest`) and report the disagreement |

`cleaned_segments` are road-segment ids (`S...`), not drain edge ids (`MUM-E...`); a request where
none match is 422 `unknown_segments`. A non-zero `tide_offset_m` is refused with 422
`unsupported_scenario`, because the emulator has no representation of a different sea level. A run
baked before the stored rain series is 409 `no_rain_series`. The 1.3x rain preset on the 08:40
cycle measured 0.67-0.77 s warm and 1.99 s on first use, 322,776 bytes.

### Replay, cycle and onboarding

| Method and path | Purpose |
| --- | --- |
| `GET /v1/replay/bundles` | Replay bundles on disk, with build and bake status |
| `GET /v1/replay/bundles/{bundle_id}/radar` | A bundle's radar frames: count, times, size, where to fetch them |
| `GET /v1/replay/bundles/{bundle_id}/radar/{index}.png` | One radar frame, coloured by rain rate |
| `GET /v1/replay/bundles/{bundle_id}/radar/accumulation.png` | Rain accumulated over the replay window, in mm |
| `GET /v1/replay/ground-truth?bundle=` | The event's sourced ground-truth pins |
| `GET /v1/replay/clock?bundle=` | The replay clock |
| `POST /v1/replay/play?bundle=` · `/pause` · `/seek` · `/speed` | Clock controls; seek and speed take a JSON body |
| `POST /v1/replay/bundle` | Point the clock at another bundle |
| `POST /v1/cycle/compute` | Compute live: run one real cycle now (202) |
| `GET /v1/cycle/status` | Orchestrator status, stage timings and budgets |
| `POST /v1/onboard` | Start a city-in-a-box job (202) |
| `GET /v1/onboard/{job_id}` | Job status with the pipeline's own log tail |
| `GET /v1/onboard/city/{city}` | The most recent job for a city |

### Verification and weather

| Method and path | Purpose |
| --- | --- |
| `GET /v1/verification?event=` | Scores for an event against its sourced ground truth, computed by `services/verify` |
| `GET /v1/weather?city=` | Current conditions and the next four hours from Open-Meteo, at the grid point nearest the city, labelled with its offset and time; degrades to a stamped copy with its age (added 2026-09-19, ADR-0061) |

### Not in the snapshot

`WS /v1/live` relays `runs.published`, `cycle.stage`, `alert.*`, `obs.assimilated`,
`replay.clock` and `onboard.progress`. WebSockets are not described by OpenAPI, so the explorer
does not list it.

## Authority writes

Every desk act (`POST /v1/ops/*`, alert acknowledge and escalate, pump optimise and dispatch) is
gated by `VARUNA_OPS_PASSPHRASE`:

- Unset on the server: the write is refused with `ops_writes_disabled`, and `GET /v1/ops/log`
  reports `writes_enabled: false`. The deployed API leaves it unset on purpose.
- Set: the request must carry it in `X-Varuna-Ops`; a missing one is `ops_passphrase_required`, a
  wrong one `ops_passphrase_rejected`. Thirty writes a minute per process.
- A write appends a line to `data/ops/<city>.jsonl` and changes no baked product; the router, the
  road-conditions feed and `/v1/ops/alerts` apply it when read.
- Reads (`GET /v1/ops/*`) are ungated: a closure is a public fact.

## The explorer

`/api` in the console (`apps/command/app/api`, `components/varuna/api-explorer*`), task P9.8.

- It reads the committed `apps/command/openapi.json`, reduced on the server, so what it lists is
  the contract the console was typed against.
- It sends every `GET` and the two POSTs that compute and store nothing, `/v1/route` and
  `/v1/whatif`. Every other write (desk acts, replay controls, a live cycle, onboarding, a report,
  the physics check) is shown with its reason and copied as curl, never sent from the page.
- It never sends and never shows a passphrase: the `X-Varuna-Ops` header is removed from every
  form, the request builder refuses one, and a gated request's curl reads
  `-H "x-varuna-ops: $VARUNA_OPS_PASSPHRASE"`.
- Each answer shows status, round-trip time measured in the browser, content type and size, and
  the body in Geist Mono (cut at 60,000 characters, with the full length stated). A refusal shows
  the API's own `error.code` and `error.message`.
- The three presets are pinned to `MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked`, the 08:40
  IST cycle: segments with `bbox=72.835,19.005,72.850,19.020` (Hindmata, with the caveat above
  printed beside it), the KEM-to-Sion ambulance route with both hospitals read from
  `/v1/route/facilities`, and a what-if at 1.3x rain.

## Error envelope

Every error response, whatever the status code, has this shape:

```json
{
  "error": {
    "code": "run_not_found",
    "message": "Run MUM-20190702T1210-sky1.0-twin1.0-flash0.3-baked is not in data/runs. Bake the bundle or pick a run from GET /v1/runs.",
    "run_id": "MUM-20190702T1210-sky1.0-twin1.0-flash0.3-baked",
    "details": null
  }
}
```

- `code` is a stable snake_case identifier the console can switch on.
- `message` says what happened and what to do; it is shown to operators verbatim, so it follows
  the copy rules in SPEC.md section 6.8 (never "Something went wrong").
- `run_id` is the run the request referred to, or `null` when none applies.
- `details` carries structured context when there is any, else `null`.

Status codes: 400 for malformed input, 404 for an unknown run, segment, alert, city or bundle, 409
when a request conflicts with state (a replay control against the clock, a run with no stored rain
series), 422 for schema violations and refused scenarios (FastAPI's validation errors are wrapped
into the same envelope), 501 `not_implemented` for a contract whose engine has not landed, 503
when the mode is degraded and the requested product is unavailable.

## Time formats

- All timestamps are ISO 8601 with an explicit offset: `2019-07-02T08:40:00+05:30`. The API never
  emits naive datetimes or `Z` for Indian data.
- Query parameters `t`, `depart_at` and `since` accept the same format.
- `valid_ts` is the forecast valid time of the returned product; `cycle_ts` is when the cycle was
  issued; `radar_frame_ts` is the newest radar frame the run consumed.
- Run identifiers embed the cycle time in compact UTC:
  `<CITY>-<cycle_ts UTC compact>Z-sky<v>-twin<v>-flash<v>[-live|-baked]`, so 08:40 IST on
  2 July 2019 is `MUM-20190702T0310Z-...`.
- The UI renders IST 24-hour times with the lead in brackets: "08:20 (+40 min)".

## Conventions

- Depth is centimetres, probability is a fraction in JSON (`0.82`) and a percentage on screen.
- Vehicle profiles: `two_wheeler`, `car`, `bus`, `truck`, `ambulance`, `fire_tender`, `pedestrian`.
- CAP alerts on replay use `status=Exercise`; live runs use `Actual`.
- GeoJSON is WGS84 (EPSG:4326); computation happens in the city CRS (EPSG:32643 for Mumbai,
  EPSG:32644 for Chennai).
