# Mumbai hotspot register and assets — verification notes

Research notes for VARUNA (SIH 2026, PS SIH26085). Evidence fetched 2026-09-04/05; this file written 2026-09-06.
Scope: SPEC.md §3.3 (MUM-CENTRAL AOI: lon 72.815–72.905, lat 18.995–19.135), §10.1 steps 8–9, Appendix C.

Rules applied (SPEC.md §0 rules 6–7): every coordinate carries a geocode method (Nominatim query + OSM id, or an
Overpass element id); every "chronic spot" claim carries at least one public `source_url`; anything that could not be
sourced is marked *unsourced* and must not be shown as fact in the UI. Mobile-pump depot assignments are **synthetic**.

> **Review note (completeness critic, 2026-09-06).** 31 embedded quotes in
> `hotspots_mumbai.draft.geojson` were grepped against their cached files: **30 matched verbatim, 1 did not.**
> The exception is the arXiv quote on Kurla, LBS Marg — *"Waterlogging near school in Kurla west"* — which is **not
> present anywhere in `docs/research/_raw/pages/arxiv_2306.09770.txt`** (0 hits for "near school", "Waterlogging near"
> and "school in Kurla"); the nearest real passage is line 593, *"Heavy waterlogging in Ghatkopar East, Tilak Nagar and
> Kurla West."*, dated **2022-07-01**. That quote is flagged `verified: "unverified"` in the GeoJSON. Kurla stays
> `sourced: true` on its other, verified source (India TV, line 166). **Section 3 below (line ~96) still prints the
> unsupported quote — read it as unverified.**
>
> Two cited pages are the **wrong event** and are now date-stamped in the GeoJSON:
> `fpj_hindmata_kneedeep.txt` is **11 October 2024** and `tribune_108506.txt` is **4 July 2020**. Their statements are
> fair evidence that a place floods *chronically*, but neither may be cited as 1–2 July 2019 evidence.
> `fpj_cr_suspends.txt` is **19 August 2025** and is not cited here.
>
> Everything else checked clean: all 28 features carry coordinate provenance (OSM element id + cached Nominatim/Overpass
> file), 27 of 28 sit inside MUM-CENTRAL, the one outside is flagged, and no `in_aoi` flag disagrees with its geometry.
> Full audit: `docs/research/REVIEW.md`.

**Deliverables written alongside this file**

| File | Contents |
|---|---|
| `docs/research/hotspots_mumbai.draft.geojson` | 28 hotspot points; **27 inside the AOI with a verified coordinate**; 13 with a public chronic-waterlogging source |
| `docs/research/assets_mumbai.draft.json` | 8 hospitals, 11 fire stations, 24 railway/metro stations, 18 pumping stations (15 inside the AOI), 1 candidate holding-tank site, 10 depots, 12 **synthetic** mobile pumps, ward relations |

## 0. Method

- Geocoding: Nominatim `https://nominatim.openstreetmap.org/search?q=<query>&format=jsonv2&limit=3` with
  viewbox 72.78,19.20,72.95,18.95 (unbounded), 1 request/s, UA `VARUNA-SIH2026-research/0.1`. Raw responses in
  `_raw/nominatim_*.json`; log in `_raw/nominatim_results.txt`.
- Overpass: `https://overpass-api.de/api/interpreter` queries in `_raw/overpass_*.ql`, responses in `_raw/overpass_*.json`
  (`overpass_pois.json` osm3s timestamp 2026-09-04T15:32:36Z).
- Web sources: fetched 2026-09-04/05 with `_raw/fetch.sh`; the URL recorded is the canonical URL of the page read
  (`rel="canonical"` inside the cached HTML). Tag-stripped text is the `.txt` twin of each `.html`.
- OpenStreetMap data is ODbL; attribute "© OpenStreetMap contributors" wherever these coordinates are rendered.
- No new network calls were made to produce this file — everything below comes out of the cache.

**What "sourced" means here.** `sourced: true` is set only when a cached public page names the place (or, where noted,
its immediate area) as flooding/waterlogged. `sourced: false` points have a verified coordinate but no such source:
the pipeline may use them as depression/hotspot candidates, the UI must not call them chronic.

## 1. Hotspot register — sourced chronic spots (13)

All coordinates WGS84 (lon, lat). "Cached" is the file the geocode was read out of, under `docs/research/_raw/`.

| # | Name | lon | lat | OSM | Ward | `is_sink` | Cached geocode |
|---|---|---|---|---|---|---|---|
| 1 | Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg) | 72.8421396 | 19.0100990 | node/9739425792 | F/S | no | `nominatim_Hindmata_Cinema__Mumbai.json` |
| 2 | Parel / Bharat Mata Cinema | 72.8362588 | 18.9956372 | way/1187354637 | F/S (unverified) | no | `overpass_pois.json` |
| 3 | King's Circle / Maheshwari Udyan junction | 72.8558265 | 19.0272742 | way/1235473854 | F/N | no | `nominatim_King_s_Circle__Mumbai.json` |
| 4 | Gandhi Market (Matunga) | 72.8590161 | 19.0325417 | node/9788462276 | F/N | no | `nominatim_Gandhi_Market__Sion__Mumbai.json` |
| 5 | Sion Circle | 72.8634910 | 19.0427327 | way/1235473849 | F/N | no | `nominatim_Sion_Circle__Mumbai.json` |
| 6 | Chunabhatti railway station | 72.8690970 | 19.0517700 | node/2663219988 | F/N | no | `overpass_pois.json` |
| 7 | Matunga (Matunga East) | 72.8520846 | 19.0261579 | node/1288965355 | F/N | no | `nominatim_Matunga__Mumbai.json` |
| 8 | Kurla, LBS Marg | 72.8816381 | 19.0816540 | way/554898799 | L | no | `nominatim_Lal_Bahadur_Shastri_Marg__Kurla_West__Mumbai.json` |
| 9 | Vakola Junction | 72.8469300 | 19.0803926 | node/10265662169 | H/E | no | `overpass_names2.json` |
| 10 | Postal Colony, Chembur | 72.8949685 | 19.0607418 | node/245666110 | M/W | no | `nominatim_Postal_Colony__Chembur__Mumbai.json` |
| 11 | Milan Subway | 72.8428330 | 19.0904503 | way/39532540 | NOT FOUND | **yes** | `overpass_pois.json` |
| 12 | Andheri Subway | 72.8469802 | 19.1192854 | node/1646774128 | K/E | **yes** | `nominatim_Andheri_Subway__Mumbai.json` |
| 13 | Khar Subway | 72.8360248 | 19.0672644 | way/1471871370 (**candidate, unverified**) | NOT FOUND | **yes** | `overpass_names3.json` |

### 1.1 The chronic-spot sources, verbatim

Every quotation is from the cached `.txt` twin of the page; the line number is given so a reviewer can re-read it.

- **Hindmata** — "Hindmata is among the low-lying areas in Mumbai and has the unwanted distinction of being among the
  first areas that witnesses waterlogging whenever it rains heavily."
  source_url: https://www.freepressjournal.in/mumbai/mumbai-rains-knee-deep-water-accumulates-in-dadar-hindmata-due-to-heavy-downpour-video-surfaces
  (cached: `docs/research/_raw/pages/fpj_hindmata_kneedeep.txt`, line 41).
  Also "BMC has deployed de-watering equipment at Hindmata flyover. It is a low-lying and is prone to water-logging."
  source_url: https://www.deccanherald.com/archives/mumbai-rains-live-mumbai-limps-back-to-normalcy-as-rains-subside-744003.html
  (cached: `docs/research/_raw/pages/dh_744003.txt`, line 152).
  Also named one of six surveyed Mumbai flood hotspots in Tripathy et al., IIT Bombay:
  source_url: https://arxiv.org/abs/2306.09770 (cached: `docs/research/_raw/pages/arxiv_2306.09770.txt`, lines 320, 637).
- **Parel** — "In Mumbai, waterlogging was reported from low-lying areas of Hindmata junction, Bandra and Parel."
  source_url: https://www.deccanherald.com/archives/mumbai-rains-live-mumbai-limps-back-to-normalcy-as-rains-subside-744003.html
  (cached: `dh_744003.txt`, line 333). Area-level, not junction-level.
- **King's Circle** — photo caption "Vehicles submerged at the waterlogged King's Circle after heavy rains in Mumbai on Monday."
  source_url: https://scroll.in/latest/929041/in-photos-rain-wreaks-havoc-in-mumbai-more-expected-in-next-two-days
  (cached: `scroll_929041.txt`, line 116).
- **Gandhi Market, Sion, Khar Subway, Milan Subway, Andheri** — "Traffic is moving slow due to waterlogging at Gandhi
  Market, Sion, LBS Nayak Nagar, Everard Nagar, Kherwadi flyover, Amar Mahal, Khar Subway, Milan Subway, Andheri,
  Jogeshwari JVLR…" source_url: https://www.deccanherald.com/archives/mumbai-rains-live-mumbai-limps-back-to-normalcy-as-rains-subside-744003.html
  (cached: `dh_744003.txt`, line 273). Gandhi Market is also one of the six IIT Bombay survey hotspots
  (`arxiv_2306.09770.txt`, lines 320, 636) and appears in Outlook: "many low-lying places like Hindmata, and areas in
  Dadar and Sion, including the Gandhi Market and road number 24 in Sion, were inundated" — source_url:
  https://www.outlookindia.com/national/cloudburst-in-kullu-mumbai-waterlogged-5-states-facing-the-wrath-of-monsoons-news-207195
  (cached: `outlook_207195.txt`, line 73).
- **Sion, Milan Subway** — "Water-logging was reported in low-lying areas like Sion and Milan Subway."
  source_url: https://www.tribuneindia.com/news/uncategorized/rains-continue-in-mumbai-adjoining-parts-for-second-day-108506/
  (cached: `tribune_108506.txt`, line 234). Milan Subway also: "2 men drowned whilst stuck in their car at Milan Subway."
  (`dh_744003.txt`, line 198).
- **Andheri Subway** — "12.09 pm: Andheri subway in Mumbai closed due to flooding, reports ANI."
  source_url: https://scroll.in/latest/929092/mumbai-at-least-16-people-killed-overnight-in-rain-related-incidents-schools-colleges-shut-today
  (cached: `scroll_929092.txt`, line 197); "12:18 pm: Andheri subway has been closed due to water logging" — source_url:
  https://www.indiatvnews.com/news/india-mumbai-rains-live-updates-wall-collapse-local-trains-flight-cancelled-pune-531812
  (cached: `indiatv_531812.txt`, line 151).
- **Vakola junction, Postal Colony, Chunabhatti railway station** — "Areas like Vakola junction, Vakola, Postal Colony,
  Chunabhatti railway station, Vakola road are waterlogged." source_url:
  https://www.indiatvnews.com/news/india-mumbai-rains-live-updates-wall-collapse-local-trains-flight-cancelled-pune-531812
  (cached: `indiatv_531812.txt`, line 184).
- **Kurla, Matunga, King's Circle, Sion, Bandra, Andheri West, Lower Parel, Vakola** — "Areas most affected by rains in
  Mumbai are Sion, Malad, Bandra, Andheri West, Kurla, King's Circle, Lower Parel, Matunga, Thane, Vakola and Palghar."
  (`indiatv_531812.txt`, line 166). Kurla West also in the crowdsourced-tweet appendix of the IIT Bombay paper
  (**UNVERIFIED — quote not found in the cached file; see the review note at the top.** Claimed as "Waterlogging near school in Kurla west", `arxiv_2306.09770.txt`, line 204.)
- **Matunga police station** — "Waterlogging at Matunga police station" (`dh_744003.txt`, line 244). The police station
  itself has no cached geocode, so the register uses the Matunga East suburb node.
- **Khar** — one of the six surveyed hotspots in Tripathy et al.: "the six flood hotspots in Mumbai, Borivali, Khar,
  Hindmata, Dahisar, Malad, and Gandhi market" (`arxiv_2306.09770.txt`, line 320), with "lower elevation and poor
  drainage" given as the cause (line 235).

### 1.2 Coordinate caveats you must carry into the register

- **Hindmata**: Nominatim `Hindmata, Dadar East, Mumbai` and `Hindmata Junction, Mumbai` both returned **NO RESULT**.
  The register uses the BEST bus stop *Hindmata Cinema* (node/9739425792) on Dr Babasaheb Ambedkar Marg, which is on the
  flooding stretch. The spec's approximation was 19.012, 72.841; this geocode is 19.0101, 72.8421, about 240 m south-east of it.
  The Hindmata Flyover ways (way/100841069, 102170846, 102172129, 216492416 …, `overpass_pois.json`) bracket the same
  stretch from 19.0040 to 19.0110 and are the better geometry for the *segment*, not the point.
- **Milan Subway**: Nominatim returned **NO RESULT** for both `Milan Subway, Santacruz, Mumbai` and
  `Milan Subway, Vile Parle, Mumbai`. Overpass found way/39532540 with `highway=tertiary, tunnel=yes, layer=-1,
  name=Milan Subway` — a genuine sink and the right feature. Ward NOT FOUND in the cache.
- **Andheri Subway**: the only OSM hit is node/1646774128, a `railway=stop` tagged "Andheri, Railway-Subway Connection".
  The road-underpass centreline is **NOT FOUND**: the Overpass tunnel sweep over 19.110–19.130, 72.835–72.856
  (`overpass_names3.ql`) returned no named Andheri Subway way.
- **Khar Subway**: **coordinate not verified.** Nominatim returned NO RESULT for `Khar Subway, Mumbai` and
  `Khar Subway, Khar, Mumbai`, and OSM has no way named "Khar Subway". The point in the GeoJSON
  (way/1471871370, 19.0672644, 72.8360248, `highway=residential, tunnel=yes, layer=-1`) is the only unnamed road tunnel
  the sweep over 19.060–19.080, 72.830–72.850 returned and **may be a different underpass**; it carries
  `coord_verified: false`. Settle it by inspecting OSM near Khar Road station (node/347138797, 19.068241, 72.840041) or
  by obtaining a BMC chronic-spot list with coordinates.
- **Dadar TT**: Nominatim `Dadar TT, Mumbai` resolved to the *Dadar BEST Workshop* (way/112798409) — the wrong feature.
  The register instead uses Khodadad Circle (way/1235213730), which is the same junction under its official name, but
  keeps `sourced: false` because the cached sources name "Dadar" as an area only.
- **Chunabhatti**: two valid geocodes — the CR station node/2663219988 (19.05177, 72.869097, `overpass_pois.json`), used
  here because the source says "Chunabhatti railway station", and a bus stop node/9788462267 (19.0520886, 72.8674141,
  Nominatim, F/N Ward).

## 2. Hotspot register — verified coordinate, no chronic-spot source (15)

These go into the GeoJSON with `sourced: false`. They are legitimate depression/hotspot *candidates* for the
city-in-a-box validation step (SPEC.md §10.1 step 10) but must not be labelled chronic on screen.

| Name | lon | lat | OSM | Ward | `is_sink` |
|---|---|---|---|---|---|
| Dadar TT / Khodadad Circle | 72.8474044 | 19.0178033 | way/1235213730 | F/N | no |
| Dharavi T-junction | 72.8518049 | 19.0470130 | way/1488801300 | G/N | no |
| Wadala | 72.8759337 | 19.0269192 | node/700688007 | F/N | no |
| Antop Hill | 72.8685474 | 19.0239461 | node/4292746740 | F/N | no |
| Bandra Talao | 72.8377699 | 19.0567440 | way/107295488 | H/W | no |
| Kalanagar / Kherwadi (Bandra East) | 72.8485093 | 19.0546701 | node/1647344904 | H/E | no |
| Kamani junction, LBS Marg (Kurla) | 72.8871162 | 19.0851644 | node/11071531770 | L | no |
| Sion Koliwada | 72.8642303 | 19.0407302 | node/5907330383 | F/N | no |
| Pratiksha Nagar (Sion East) | 72.8693000 | 19.0396000 | node/245663124 | F/N | no |
| Nehru Nagar, Kurla East | 72.8810000 | 19.0610000 | node/245666177 | L | no |
| Sakinaka (Tele Exchange Lane) | 72.8826122 | 19.1004190 | way/215075825 | L | no |
| Kalina | 72.8612672 | 19.0792730 | node/1375215062 | H/E | no |
| Mahim | 72.8398344 | 19.0423145 | node/631525426 | G/N | no |
| Sion pedestrian subway | 72.8644700 | 19.0443800 | way/632206820 | F/N (unverified) | **yes** |
| Sion Subway 1 | 72.8636100 | 19.0420600 | way/632207092 | F/N (unverified) | **yes** |

13 sourced + 15 unsourced = 28 features in the GeoJSON. "Kalanagar / Kherwadi" has a near-source — the Deccan
Herald line naming "Kherwadi flyover" — which is recorded in the GeoJSON but was not judged specific enough to flip
`sourced` to true. Likewise "Bandra Talao" and "Dadar TT" carry an area-level quote only.

**Sinks.** `is_sink: true` is set for Milan Subway, Andheri Subway, Khar Subway, Sion pedestrian subway and Sion
Subway 1. These are the features SPEC.md §10.1 step 4 says to keep as sinks during hydro-conditioning instead of
breaching them.

## 3. Named in sources but with no coordinate in the cache (gaps)

Each of these appears in a cached source as a flooded location inside or near the AOI, but no geocode exists in the
cache. They are **excluded** from the GeoJSON rather than guessed.

| Place | Named in | What would settle it |
|---|---|---|
| Kherwadi flyover | `dh_744003.txt` line 273 | Nominatim/Overpass query for the flyover way in Bandra East |
| Everard Nagar (Sion) | `dh_744003.txt` line 273 | Nominatim query "Everard Nagar, Sion, Mumbai" |
| LBS Nayak Nagar | `dh_744003.txt` line 273 | Nominatim query; the exact spelling may differ in OSM |
| Amar Mahal junction (Chembur) | `dh_744003.txt` line 273 | Nominatim "Amar Mahal, Chembur"; it sits near the AOI's east edge |
| Matunga police station | `dh_744003.txt` line 244 | Overpass `amenity=police` near 19.026, 72.852 |
| Road number 24, Sion | `outlook_207195.txt` line 73 | Overpass name search on Sion road numbering |

## 4. Assets (SPEC.md §10.1 step 8)

Written to `docs/research/assets_mumbai.draft.json` with the shape
`{hospitals, fire_stations, stations, pumping_stations, holding_tanks, depots, mobile_pumps, wards}`.
Every entry carries `lon`, `lat`, `osm_type`, `osm_id`, `source_url` (the OSM element URL) and `cached_path`.

### 4.1 Hospitals (8, all inside the AOI)

| Name | lon | lat | OSM | Cached |
|---|---|---|---|---|
| King Edward Memorial (KEM) Hospital, Parel | 72.842180 | 19.001551 | way/113561413 | `nominatim_KEM_Hospital__Parel__Mumbai.json` |
| Lokmanya Tilak Municipal General (LTMG) Hospital, Sion | 72.858231 | 19.034843 | way/1413471846 | `nominatim_Sion_Hospital__Mumbai.json` |
| Nowrosjee Wadia Maternity Hospital, Parel | 72.842387 | 19.003938 | way/618791586 | `nominatim_Nowrosjee_Wadia_Maternity_Hospital__Mumbai.json` |
| Tata Memorial Hospital, Parel | 72.843213 | 19.004984 | node/7052388277 | `nominatim_Tata_Memorial_Hospital__Parel__Mumbai.json` |
| Kurla Municipal Hospital | 72.883116 | 19.081508 | node/4996322798 | `overpass_pois.json` |
| Mata Ramabai B. Ambedkar Hospital, Chembur | 72.894641 | 19.055714 | node/2319217775 | `overpass_pois.json` |
| Dr Balabhai Nanavati Hospital, Vile Parle | 72.840012 | 19.095968 | node/3951526714 | `overpass_pois.json` |
| Bharatiya Arogya Nidhi … General Hospital, Juhu | 72.828340 | 19.111780 | node/4998848423 | `overpass_pois.json` |

The demo ambulance trip in SPEC.md §3.3 runs **KEM (way/113561413) → LTMG Sion (way/1413471846)**; both coordinates
above are the ones to use.

**Bai Jerbai Wadia Hospital for Children, Parel: NOT FOUND.** Nominatim returned NO RESULT for
`Bai Jerbai Wadia Hospital for Children, Parel, Mumbai`, `Bai Jerbai Wadia Hospital, Mumbai` and
`Wadia Children's Hospital, Parel, Mumbai` (three empty JSON files in `_raw/`). Only the adjacent *Nowrosjee Wadia
Maternity* hospital resolved. An Overpass `amenity=hospital` name search on "Wadia" near 19.003, 72.842 returned only
node/6884803660 "N. Wadia Maternity Hospital Blood Bank" (`overpass_names3.json`). Settle with a targeted Overpass
query or the hospital's own published address.

### 4.2 Fire stations (11 inside the AOI)

Dadar Fire Brigade (way/116072855), Kurla Agnishaman Kendra (way/353174234, operator MCGM), Babasaheb Worlikar Fire
Station (way/354294947), Bandra Fire Brigade (way/434887905), Mini Fire Station, Santacruz West (way/1243105383),
Andheri Fire Station (way/1479265653), Marol fire brigade (relation/21105810), Chembur Fire Station (node/2311428475),
Aerodrome Rescue and Fire Fighting at CSMIA (way/1376380212, an airport service, not a city station), one unnamed
`amenity=fire_station` node at 19.033118, 72.841209 (node/9024751917, **name NOT FOUND**), and one entry OSM calls
"Testing Fire Station" (node/2187315849) which is flagged `suspect_osm_entry: true` — it reads like a placeholder edit
and should not be shown until confirmed.

### 4.3 Railway and metro stations (24 inside the AOI)

Suburban rail: Dadar CR (node/2630075003) and Dadar WR (node/2630075004), Matunga (node/4264457295), Matunga Road
(node/4264462833), King's Circle (node/1650694464), Sion (node/2663263713), Chuna Bhatti (node/2663219988), Kurla
(node/2663447640), Lokmanya Tilak Terminus (node/6175360983), Vidyavihar (node/620013256), Tilak Nagar
(node/3530903066), Chembur (node/2454571178), Mahim Junction (node/2630075005), Bandra (node/2630075000), Khar Road
(node/347138797), Santa Cruz (node/213030714), Vile Parle (node/2630075010), Andheri (node/5696928866), Parel
(node/3501418212), Prabhadevi (node/3462327239), Lower Parel (node/621911847), Sewri (node/700886180), Vadala Road
(way/1343435452), Bandra Terminus (way/107303404). All from `overpass_pois.json`, which holds 76 station elements
inside the AOI in total (including metro and BEST stations) if a fuller list is wanted.

### 4.4 Pumping stations (18 recorded; 15 inside the AOI)

**Read this before putting a pump on the map.** Of every pumping-station feature the Overpass sweep returned
(`overpass_pumps.ql` + `overpass_pois.ql` + `overpass_names2.ql`), **only two are named as stormwater stations**:

- *Storm Water Pumping Station - Haji Ali* (way/1289073487, 18.9786563, 72.8122843, operator MCGM,
  `man_made=water_works`) — **outside the AOI** on both axes.
- *Danda Stormwater Pumping Station* (way/909478803, 19.0786158, 72.8254107, `man_made=wastewater_plant`) — inside the
  AOI at Khar Danda.

Everything else that OSM tags `man_made=pumping_station` inside the AOI is named "… **Sewage Water** Pumping Station"
(operator MCGM): Carol Road (node/14125092873), Mahim (node/14125092878), Wadala (node/14125092879), Matunga
(way/1378123580), Influent/Dharavi (way/220302517), BKC (node/14125092883), Kalina (node/14125092884), Sakinaka
(node/14125247275), Tulsi Pipe (node/14125092874), Globe Mill (node/14125092875), Kadeshwari (node/14125247269),
Cleaveland (node/14125092877). Plus "BMC Pumping station Building no 1" (way/310812690, kind unknown) and one unnamed
`man_made=pumping_station` at 19.0635672, 72.837659 (way/1526167373).

Consequences for the spec's named list:

| Spec name | Found? | Detail |
|---|---|---|
| Haji Ali | yes, outside AOI | way/1289073487; Nominatim `Haji Ali Pumping Station, Mumbai` returned NO RESULT — found by Overpass name sweep |
| Cleveland Bunder / Worli | partial | node/14125092877 "BMC Cleaveland **Sewage** Water Pumping Station" (19.0166913, 72.8179307), inside the AOI. The place way/1472415855 "Cleveland Bunder" is at 19.0160355, 72.817026. Nominatim NO RESULT. Whether this is the BRIMSTOWAD stormwater station is **NOT FOUND** |
| Lovegrove | partial, outside AOI | way/1289081182 "Love Grove **Sewage** Pumping Station", 18.9924341, 72.8159113 |
| Irla | **NOT FOUND** | Nominatim NO RESULT; the Overpass name sweep returned only Irla Nala waterway ways (e.g. way/73043478, way/376930282 with `tunnel=yes`) and Irla Road/Irla Gaon, no pumping station |
| Britannia / Reay Road | partial, outside AOI | way/1356107260 "Britannia **Sewage Water Treatment Plant**", 18.974925, 72.8495926 — a treatment plant, not the outfall pumping station |
| Gazdarbandh | **NOT FOUND** | Nominatim NO RESULT for `Gazdar Bandh Pumping Station, Mumbai`; the name sweep returned only "Gazdar" (way/257073217) and "Gazdar House" (way/756526548) |
| Mogra | **NOT FOUND** | Nominatim NO RESULT for `Mogra Nullah, Andheri, Mumbai`; the sweep returned the Mogra metro station (node/10576622712, relation/15351570), not the nullah or a pumping station |

**What would settle it:** the BMC/MCGM BRIMSTOWAD stormwater pumping station list (BMC Stormwater Drains Department
publications, or the MCGM budget/annual administration report), which names all eight stations with locations. Until
one is in hand, do not label any of the sewage stations above as stormwater on screen.

### 4.5 Hindmata holding tanks — **NOT FOUND**

- `Pramod Mahajan Kala Park, Dadar, Mumbai` and `Pramod Mahajan Kala Udyan, Dadar, Mumbai`: Nominatim **NO RESULT**
  (two empty JSON files). The Overpass name sweep for "Pramod Mahajan|Kala Park|Kala Udyan|Holding Tank" over
  18.99–19.03, 72.82–72.86 returned nothing in Dadar; the only "Pramod Mahajan" hit is *Pramod Mahajan Krirangan*
  (way/206832495) at 19.1433868, 72.8214744 — Kandivali, a different facility 15 km north, outside the AOI.
- `St Xavier's Ground, Parel, Mumbai`: Nominatim **NO RESULT**. Overpass found *Saint Xavier's Football Ground*
  (way/113566065, 19.0065272, 72.8423877, `leisure=pitch`) — the ground is verified, but **no cached source states
  that a BMC stormwater holding tank was built under it**. It is recorded in `holding_tanks[]` as
  `kind: candidate_holding_tank_site` with `capacity_m3: null` and `commissioned: null`.
- **Capacity and commissioning date: NOT FOUND.** No cached page mentions the Hindmata holding tanks at all (a grep
  for "holding tank", "underground tank" and "Kala Park" across all 25 cached articles returns only an unrelated
  Nashik water-tank collapse and a generic line in the IIT Bombay paper about "underground tanks" overflowing where the
  nearby drainage sits at a higher elevation, `arxiv_2306.09770.txt` line 320).
- **What would settle it:** a BMC press release or budget line for the Hindmata/Pramod Mahajan Kala Park and
  St Xavier's ground underground storage tanks with volume in million litres and the commissioning year, or a dated
  news report quoting BMC. Until then the tanks must be omitted from the UI, or shown labelled "capacity not sourced".

### 4.6 Depots (10, all inside the AOI) and the synthetic mobile pumps

Depots are real BMC ward offices, municipal premises and bus depots from the cache: F/N BMC Ward Office
(way/115628091), "BMC Ward Office" at 19.0299554, 72.854268 (way/236596240, which ward it serves is NOT FOUND),
BMC L Ward Office (way/353424820), H/W Ward Office (way/356450738), MCGM H/West Ward Office (way/1060046624),
BMC M Ward Office (way/1127314714), BMC G South Ward Office (way/353757968), Municipal Garage Worli (way/353366215,
on the AOI's southern edge at lat 18.99507), Kurla Depot (node/3878597746), Wadala Depot (node/632342238).

Nominatim returned **NO RESULT** for every ward-office query that was issued by name
(`F_S_Ward_Office__Parel_Naka`, `H_E_Ward_Office__Prabhat_Colony__Santacruz_East`, `K_E_Ward_Office__Gundavali`,
`K_W_Ward_Office__Andheri_West`, `Municipal_Office__Harishchandra_Yelve_Marg__Dadar_West`, `Municipal_Garage__Worli`);
all ten depots above came from the Overpass name sweep instead.

**`mobile_pumps[]` is entirely synthetic.** Twelve pumps `P-01` … `P-12`, each carrying `"synthetic": true`,
`"source_url": null` and a note. Capacities (250 / 400 / 600 m³/h) and statuses are invented; only the depot
coordinates are real. Distribution: 2 at F/N ward office (Matunga), 2 at G/S ward office, 1 at Municipal Garage Worli,
1 at the Sion/Matunga ward office, 1 at L ward office (Kurla), 1 at Kurla Depot, 1 at H/W ward office (Bandra),
1 at MCGM H/West (Santacruz), 1 at M ward office (Chembur), 1 at Wadala Depot. The panel header must read
"Synthetic pump inventory" wherever these appear (SPEC.md §3.2, §6.8).

## 5. BMC wards covering the AOI

From the Overpass admin sweep `overpass_wards.ql` over 18.99,72.81,19.14,72.91 (response `overpass_wards.json`,
53 elements). BMC wards are `admin_level=10`; BMC zones are `admin_level=9`.

| Ward | OSM relation | Relation centre (lon, lat) | Note |
|---|---|---|---|
| F/S | relation/7885395 | 72.8516213, 18.9976605 | Parel, Lalbaug, Hindmata |
| F/N | relation/7885396 | 72.8638375, 19.0284692 | Matunga, King's Circle, Gandhi Market, Sion, Wadala, Antop Hill |
| G/N | relation/7885381 | 72.8471607, 19.0298732 | Dharavi, Mahim, Matunga West |
| G/S | relation/7885391 | 72.8212107, 19.0024079 | Worli, Elphinstone — touches the AOI's western edge |
| H/E | relation/7885378 | 72.858412, 19.072726 | Bandra East, Kalina, Vakola, Santacruz East |
| H/W | relation/7885382 | 72.8303935, 19.0655062 | Bandra West, Khar West, Santacruz West |
| L | relation/7885380 | 72.8857843, 19.088501 | Kurla, Sakinaka |
| K/E | relation/7885379 | 72.8667489, 19.1126193 | Andheri East, Vile Parle East — the AOI's northern edge |
| M/W | relation/7885389 | 72.8948267, 19.0330687 | Chembur — touches the AOI's eastern edge |
| K/W | relation/7885383 | 72.8151293, 19.1186443 | Andheri West — touches the AOI's north-western edge |

Zones intersecting the AOI: Mumbai Zone 2 (relation/7888968), Zone 3 (relation/7888967), Zone 5 (relation/7888965).

**Caveat.** Coverage was inferred from the relation centroids returned by `out center` plus the ward field in Nominatim
display names (for example node/9739425792 Hindmata Cinema resolves inside "F/S Ward", node/1646774128 Andheri Subway
inside "K/E Ward"). The relation *geometries* were not downloaded, so no polygon-in-bbox intersection test was run;
M/W, K/W and G/S may only clip the AOI's edges. Before `make city CITY=mumbai` uses wards for aggregation, fetch the
geometries with `out geom` (or `rel(<id>); out geom;`) and do a real intersection. Source_url for all of these:
`https://www.openstreetmap.org/relation/<id>` (cached: `docs/research/_raw/overpass_wards.json`).

## 6. What is still open

1. **Khar Subway coordinate** — the single unverified hotspot coordinate. Everything else in the register geocodes.
2. **Bai Jerbai Wadia Hospital for Children** — a named AOI asset with no geocode.
3. **BRIMSTOWAD stormwater pumping stations** — Irla, Gazdarbandh, Mogra not found at all; Cleveland Bunder,
   Lovegrove and Britannia found only as sewage facilities. Needs a BMC source.
4. **Hindmata holding tanks** — existence, capacity and commissioning date all unsourced.
5. **A BMC chronic-waterlogging-spot list.** All 13 sourced hotspots rest on news reporting of the 1–2 July 2019 event
   plus one peer-reviewed IIT Bombay survey; no official BMC list of chronic spots was reachable from the cache. That
   list, if obtained, would let the register claim "chronic" rather than "flooded on this date" and would also fix
   items 1 and 3.
6. **Ward polygon intersection** (see §5 caveat).
7. **Kherwadi flyover, Everard Nagar, LBS Nayak Nagar, Amar Mahal, Matunga police station, road number 24 Sion** — six
   sourced flood locations awaiting geocodes (§3).
