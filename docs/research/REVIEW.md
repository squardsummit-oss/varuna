# Research review — completeness and source audit

**Reviewer:** completeness critic (VARUNA research team) · **Date:** 2026-09-06
**Scope:** every file in `docs/research/` produced by the four research passes.
**Method:** all JSON/GeoJSON parsed; every feature checked for a source URL, for AOI containment and for
`inside_aoi`/`in_aoi` flag agreement; every embedded quote grepped against the cached file it names; every cached
path cited in the four Markdown files checked for existence; and the headline numbers re-derived from the cache
(including reading the IMD chart PNG).
**Rule applied (SPEC.md §0 rules 6–7):** nothing was added. Claims the cache does not support were downgraded to
*unverified* in the file itself with a one-line note; mechanical errors were fixed in place.

---

## 1. File-by-file verdict

| File | Items | Sourced | Unverified after review | Outside AOI (flagged) | Parses |
|---|---|---|---|---|---|
| `ground_truth_MUM-2019-07-02.draft.geojson` | 33 features | 33/33 have `source_url` + existing `cached_path` | 0 | 1 point (`MUM19-90`, lat 19.179) + 3 with null geometry (`MUM19-13/14/91`) — all `inside_aoi: false` | yes |
| `ground_truth_MUM-2019-07-02.md` | 318 lines, 26 distinct cached files cited | all cited files exist | 0 | — | n/a |
| `hotspots_mumbai.draft.geojson` | 28 features | 13 with a flood-claim source; **all 28** carry coordinate provenance (OSM element id + cached geocode) | **1 quote** (Kurla/arXiv) now `verified: "unverified"` | 1 feature `in_aoi:false` (Khar Subway, `coord_verified:false`) | yes |
| `hotspots_assets_mumbai.md` | 353 lines | all 8 cited cached files exist | inherits the 1 quote above — review banner added at the top | — | n/a |
| `assets_mumbai.draft.json` | 84 items | 84/84 carry a URL; 12 mobile pumps explicitly `synthetic: true` | 0 | 3 pumping stations, correctly `in_aoi:false` | yes |
| `bmc_aws_stations.json` | 33 stations (10 with coordinates, 6 in AOI) | 33/33 carry a source URL | 0 | flags agree with geometry throughout | yes |
| `rain_gauges_tide_MUM-2019-07-02.md` | 393 lines, 46 URLs, 28 cached files | all cited files exist **after one path fix** | 0 | — | n/a |
| `chennai_hotspots.draft.geojson` | 30 features | 6 with a flood-claim source; 24 explicitly `source_url: null` + `flood_source_verified: false` + "not to be displayed as fact" | 0 (honestly self-flagged) | 0 outside CHN-SOUTH; 1 null geometry (`CHN-OF-03`) | yes |
| `data_sources.md` | 602 lines, 68 URLs, 30 cached files | all cited files exist | 0 | — | n/a |

**Quote audit:** 31 verbatim quotes are embedded with a `cached_path`. **30 matched their cached file exactly.**
(One apparent failure — the India TV "Areas most affected by rains in Mumbai are Sion, Malad, Bandra, Andheri West,
Kurla, King's Circle…" line — is a Unicode-apostrophe artefact only; it matches on a strict alphanumeric comparison
and is **verified**.)

---

## 2. What I re-derived from the cache myself (25 checks)

| Claim | Verdict | Evidence |
|---|---|---|
| Santacruz **375.2 mm**, 24 h ending 08:30 IST 2 Jul 2019 | **CONFIRMED, IMD-primary** | I opened `docs/research/_raw/imd_Highest_Scz_July.png`: the 2019 bar reads 375.2, the day label inside the bar is "2", and the chart footnote states "Rainfall is reported from 08.30 a.m of previous day to 08.30 am of same day." Also in `dh_744003.txt` line 78 and `scroll_929092.txt` line 189. |
| Colaba **137.88 mm** and BMC Dindoshi **479.56 mm** | Verified as *reported* (second-hand) | `scroll_929092.txt` line 189, Scroll citing The Indian Express |
| **4.92 m** high tide at **11:30**, "that is why we could not pump out water on the central line", **53 flooding spots** | Verified verbatim | `scroll_929092.txt` line 135 (17:22 entry, AMC Ashwini Joshi via News18) |
| **4.59 m** expected at **11:52** (ANI, 10:18) | Verified verbatim | `scroll_929092.txt` line 213 — genuinely conflicts with the 4.92/11:30 figure; both correctly recorded, neither averaged |
| **183 mm in 3 h**, Kurla–Thane | Verified verbatim | `scroll_929092.txt` line 140 (CR CPRO Sunil Udasi) |
| Area averages **107 / 172 / 152 mm** | Present in cache | `outlook_207195.txt` |
| **21 dead, 78 injured** (Malad wall collapse) | Present in cache | `indiatv_531812.txt`, `latestly_malad_978296.txt` |
| Copernicus GLO-30 tile sizes **4,027,713 / 13,336,459 / 7,135,610 / 12,362,785 B** | Byte-exact | `head_verify_20260906c.txt` lines 2–24 |
| WorldCover **125,766,199 B** (N18E072) and **119,065,806 B** (N12E078); **N12E081 → 404** | Byte-exact, and the 404 confirms the 3-degree snap-down rule | `head_verify_20260906c.txt` lines 25–41 |
| OpenTopography un-keyed call → **401** | Verified | same file |
| Overpass highway-way counts **24,710** (Mumbai) / **13,474** (Chennai) | Exact | `overpass_mumbai_count.json`, `overpass_chennai_count.json` |
| Radar site codes are lowercase: `caz_vrv.gif` **200**, `sri_vrv.gif` **200**, `caz_cni.gif` **200**; `caz_VRV.gif`, `caz_CHN.gif`, `caz_chn.gif` **404** | Verified | `head_verify_20260906c.txt` lines 37–72 and `head_verify_20260906.txt` line 77 |
| Veravali radar at **19.1342 N, 72.8672 E** | Verified | `imd_radar_animation.html` (IMD's own marker JS) |
| The `location18 = [19.0760, 72.8777]` "Mumbai-Colaba" marker is a city centroid, not the observatory | Verified — both coordinate pairs are in the cached page | `imd_radar_animation.html` |
| True Colaba observatory at **18.8976, 72.8132** | Verified | `nominatim_Regional_Meteorological_Centre__Colaba__Mumbai_.json` |
| All 33 ground-truth pins: `cached_path` exists **and** contains the pin's place name | 33/33 | scripted check over the cache |

---

## 3. Findings — what I changed

### 3.1 One unsupported quote, downgraded (not deleted)

`hotspots_mumbai.draft.geojson`, hotspot **"Kurla, LBS Marg"**, cited
`arxiv_2306.09770.txt` line 204 for *"Waterlogging near school in Kurla west"*.
**That string is not in the file** — grep returns 0 hits for "near school", "Waterlogging near" and "school in
Kurla". The nearest real passage is line 593, *"Heavy waterlogging in Ghatkopar East, Tilak Nagar and Kurla West."*,
timestamped **2022-07-01** in the paper's tweet appendix.

Action: the source entry now carries `"verified": "unverified"` and a note. The hotspot **stays `sourced: true`**
because its *other* source (India TV live blog, line 166) verified verbatim — so the register still holds **13**
sourced hotspots. The Markdown line that printed the bad quote is now marked unverified in place.

### 3.2 Three cached pages are the wrong event — two of them are cited

Confirmed by reading their datelines:

| Cached file | Actual publication date | Cited as evidence? |
|---|---|---|
| `pages/fpj_hindmata_kneedeep.txt` | **11 October 2024** | Yes — Hindmata |
| `pages/tribune_108506.txt` | **4 July 2020** | Yes — Sion Circle, Milan Subway |
| `pages/fpj_cr_suspends.txt` | **19 August 2025** | No |

These are legitimate evidence that a place floods *chronically* — which is what the hotspot register is for — but
they are **not** 1–2 July 2019 evidence. Both cited entries now carry `source_published` and a `review_note` saying
so, and the Markdown carries a banner. The ground-truth researcher independently caught this and excluded all three
from the pin set; the two registers now agree.

### 3.3 Mechanical fixes

- `rain_gauges_tide_MUM-2019-07-02.md`: cached path `docs/research/_raw/_caz_mum.gif` → `docs/research/_caz_mum.gif`
  (the file is one directory up). It was the only broken cached path in 58 cited across the four Markdown files.
- `chennai_hotspots.draft.geojson`: added `coordinate_source_url` to 29 features, derived mechanically from the
  `osm_id` already present in each feature. This gives every feature a URL for its **coordinate**; `source_url`
  stays `null` on the 24 features with no flood claim, which is correct and must stay that way.
- No `inside_aoi` / `in_aoi` flag disagreed with its geometry in any file. Nothing was deleted.

### 3.4 One gap is partly closed by evidence already in the cache

`hotspots_assets_mumbai.md` records "Hindmata holding tanks: existence … NOT FOUND". The cache does in fact contain
a citable statement of existence — `arxiv_2306.09770.txt` line 320: *"The large-scale underground storage tanks and
pumping stations are working effectively in reducing the water level significantly"*, naming Hindmata among four
hotspots where mitigation works, with the survey placing those works in **2020–2021**. That establishes *existence
and rough date*; it still gives **no location, no capacity, no commissioning date**, so the asset file's
`holding_tanks` entry must stay a candidate. Worth citing rather than leaving the gap fully open.

---

## 4. Bundle verdict — `MUM-2019-07-02` against SPEC.md §10.2

| §10.2 rule | Verdict |
|---|---|
| `source_url` mandatory | **PASS** — 33/33 features, every cached file present and containing the pin's place name |
| Time uncertainty kept | **PASS** — `ts_uncertainty_min` on 33/33 |
| Depth only when the source states or shows it | **PASS** — `depth_cm` is `null` on all 33; only `depth_phrase` carries the qualitative wording |
| ≥ 10 pins inside the AOI | **PASS — 29 pins** inside MUM-CENTRAL with a geometry *and* a timestamp |

**Verdict: keep `MUM-2019-07-02`. Do not switch the demo bundle.** The event clears the §10.2 bar nearly three
times over, and eight of the ten registered chronic hotspots have at least one time-stamped 2019 observation.

**But two consequences must be accepted in the same breath, and both are build decisions, not research gaps:**

1. **The demo window does not contain the event.** The only primary total (Santacruz 375.2 mm) covers 08:30 IST
   1 Jul → 08:30 IST 2 Jul; the cloudburst was overnight; and BMC stated at 17:22 on 2 July that waterlogging had
   *receded*. SPEC.md §15 opens the demo at **15:40 IST on 2 July** — after the peak. Nearly every pin falls
   between 08:00 and 14:30 IST on 2 July. Two researchers reached this independently. **Decide before P2.3:** move
   the replay window to roughly **22:00 1 Jul – 04:00 2 Jul**, or keep 2 July afternoon and label the storm a
   reconstruction *placed inside* the demo window in `manifest.sources`. The demo's 2:40 ground-truth beat only
   works if the window contains the pins.
2. **`/verify` must not show a depth MAE for this event.** No cached source states a depth in centimetres for
   1–2 July 2019. Verification can score occurrence, place and timing (CSI / POD / FAR, timing error) against 29
   pins; it cannot score depth. Shipping a depth MAE here would be a fabricated number under rule 6.

**One pin must not reach the map:** `MUM19-91` (two men drowned in a car, ~23:30 on 1 July). Three sources say
*Malad subway*; the Deccan Herald live blog says *Milan Subway*. The two sites are ~9 km apart and only Milan Subway
is inside the AOI. It is correctly stored with **null geometry** — leave it that way until a source settles it.

---

## 5. What Phase 1 and Phase 2 can build now

**Phase 1 — city-in-a-box (Mumbai), unblocked:**

- **P1.2 DEM.** Four Copernicus GLO-30 tiles, byte-verified this week, no key: `N18E072`, `N19E072` (Mumbai),
  `N12E080`, `N13E080` (Chennai), 36.9 MB total. Fallback and its 401 behaviour are documented.
- **P1.3 OSM.** Tag-filter block and rate-limit policy are written from the vendored `osmnx/_overpass.py`, not from
  memory; both AOIs are single sub-50 km requests. Expect ~24,710 highway ways in MUM-CENTRAL.
- **P1.4 land cover.** WorldCover v200/2021 `N18E072` and `N12E078`; the 3-degree snap-**down** rule is proven by
  the `N12E081` 404 — do not snap up.
- **P1.9 hotspot register.** 27 AOI points with verified coordinates, 13 of them sourced. Ship the 13 as the chronic
  register; the other 15 may feed the pipeline but the UI must not call them chronic (the flag is in the file).
- **P1.9 assets.** 8 hospitals including both ambulance endpoints (KEM `way/113561413`, LTMG Sion
  `way/1413471846`), 11 fire stations, 24 stations, 18 pumping stations, 10 ward relations.
- **ADR-0006** (corporate TLS interception; `truststore.inject_into_ssl()`, download once, never `/vsicurl/`
  streaming, `verify=False` rejected) should be copied into `docs/DECISIONS.md` before anyone writes a fetcher.

**Phase 2 — replay bundle, unblocked with two labels:**

- **P2.3 calibration target:** Santacruz **375.2 mm / 24 h ending 08:30 IST 2 Jul 2019**, IMD-primary. Put the URL
  and the 08:30–08:30 window in `manifest.sources` verbatim; a total quoted without that window is wrong by a day.
- **P2.4 tide:** `tide.csv` must carry `source: "illustrative"`. Two civic statements 33 cm and 22 min apart, no
  tide table, and no evening value at all. Do **not** average them.
- **P2.6 ground truth:** 29 AOI pins are ready to ship as-is.
- **P2.8 design storms:** `MUM-IDF-25yr` / `CHN-IDF-25yr` may be built, but the manifest must carry the
  `design_storm_basis` string declaring the Chicago hyetograph parameters as **assumed, not derived from any
  published IDF curve**, with the matching UI label.
- **Storm-designer frame interval:** 10 min is an **assumption**, not a sourced cadence — label it.

**Honesty labels that are now evidence-backed, not hedging:** *reconstructed replay* (no public 2019 radar archive
exists — every IMD radar URL is "latest" only, overwritten in place), *inferred drain graph*, *synthetic pumps /
traffic / reports*, *illustrative tide*, and — the one nobody should soften — **every BMC pumping station inside the
AOI is tagged in OSM as *sewage*, not stormwater.** Only two features in the whole cache are named stormwater
(Haji Ali, outside the AOI; Danda, inside). The UI must not relabel a sewage plant as a stormwater pumping station.

---

## 6. Remaining gaps, each with its exact next step

| # | Gap | Exact next step |
|---|---|---|
| G1 | Demo window vs event window (§4.1) — **the only gap that blocks a build decision** | Human call before P2.3: move the replay to ~22:00 1 Jul – 04:00 2 Jul, or keep 2 July afternoon and write the reconstruction caveat into `manifest.sources`. |
| G2 | No numeric depth anywhere; `depth_cm` null on 33/33 | Suppress depth MAE on `/verify` for this event. To close: photographs with a scale, BMC flooding-spot depth records, or IIT-B platform data (G4). |
| G3 | Colaba 2 Jul 2019 daily total is second-hand only | Re-fetch `city.imd.gov.in/citywx/extreme.php` for **month=7**, ids 43057 and 43003 — but note the trap already found: that page **ignores its `m=` parameter and serves the current month**, so a cached "July" file may hold September data. Verify the month rendered in the response before quoting. |
| G4 | IIT-B Mumbai Flood platform yielded 19-byte pages | Parse `docs/research/_raw/mumbaiflood_bundle.js` for its XHR endpoints — the same walk that produced the Mumbai gauge roster from `mcgm_main.js`. Most likely route to real depth data. |
| G5 | No 2 July 2019 Hindmata observation (only 1 July pumps, 3 July summary) — the demo's 2:40 beat depends on it | Mumbai Mirror / Mid-Day / HT archives for 2 Jul 2019, or BMC's own log. Folds into G1: moving the window may make existing pins land on Hindmata. |
| G6 | BMC AWS coordinates missing for 23 of 33 stations | The MCGM `loadActiveAWSLocations` endpoint is **POST-only** (405 on GET, checked twice) and was never POSTed to. One POST would likely resolve nearly all of them. |
| G7 | The "53 flooding spots identified last night" list was never published | Check the cached `mcgm_rainfall.html` / `dm_api_reports_*.json` for a historical flooding-spot endpoint; otherwise MoES SPOC. This would be the ideal verification artefact. |
| G8 | No tide table for 2 Jul 2019 (Mumbai) or any Chennai tide series | Survey of India / INCOIS / Mumbai Port 2019 tide tables. Until then both `tide.csv` files stay `illustrative`. |
| G9 | Khar Subway and Amar Mahal ungeocodable; Kathipara and Pondy Bazaar (Chennai) likewise | One Overpass name query per point, e.g. `nwr["name"~"Kathipara",i](12.96,80.20,13.05,80.28)`; for Khar, inspect OSM near Khar Road station `node/347138797`. |
| G10 | `MUM19-91`: "Malad subway" vs "Milan Subway" — decides whether the event is even in the AOI | Keep null geometry. Settle with a fourth independent report naming the site, or drop the pin. |
| G11 | No IDF curve for either city; no official BMC or GCC chronic-spot list | CPHEEO *Manual on Storm Water Drainage Systems* 2019 IDF tables, or IMD's short-duration IDF atlas. The `design_storm_basis` string is the interim mitigation. |
| G12 | IMD radar cadence unproven (two HEAD samples, both `HH:40:0x`) | Poll `caz_vrv.gif` once a minute for an hour and record distinct `Last-Modified` values. Until then 10 min is labelled an assumption. |
| G13 | Mumbai ward coverage inferred from relation centroids, not polygons | Re-run the Overpass ward query with `out geom` and do a real intersection before wards are used for aggregation. |
| G14 | `bmc_aws_stations.json` cites NOAA ISD (`isd-history.csv`) with `cached_path: null` — the row is not in the cache | Download `isd-history.csv` into `_raw/` so the Santacruz coordinate is verifiable offline, as `make pack` requires. |
| G15 | Two Chennai geocodes resolved to the wrong feature type (Velachery and Guindy "railway station" → `highway=bus_stop`); Kasturba Nagar → a building | Re-resolve with `railway=station` before using them as exposure assets. Both are flagged in the file. |
| G16 | MOSDAC INSAT-3DS product id and cadence unverified (download behind ISRO/SAC registration) | Do not put a MOSDAC product name or cadence on a slide until someone with an account fetches one file. |
| G17 | FABDEM licence (CC BY-NC-SA 4.0) stated from prior knowledge, not re-fetched | Confirm on the University of Bristol data repository before the exclusion appears on a slide. |

---

## 7. Overall

The four deliverables are **honest and unusually well-anchored**: 58 distinct cached files cited across the
Markdown with one broken path, 30 of 31 embedded quotes verbatim-correct, byte-exact HTTP evidence for every
download the build depends on, and — the part that matters most under SPEC.md rule 7 — the researchers marked
their own weak evidence rather than smoothing it. Three of them independently caught the same wrong-event pages and
the same demo-window mismatch, which is a good sign the cache was actually read rather than recalled.

The two things a reviewer must not let slide are both already visible in the files: **the demo window does not
contain the event it replays**, and **there is no depth number anywhere in the ground truth**. Neither is fixable by
more research; both are decisions for the build. Everything else on this page has a next step attached.
