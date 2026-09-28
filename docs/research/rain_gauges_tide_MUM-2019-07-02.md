# Rainfall, gauges, tide and radar for replay bundle MUM-2019-07-02

Status: RESEARCH COMPLETE for the cached evidence (research team, cache fetched 2026-09-04/05, this pass 2026-09-06).
Every fact below carries the URL it was read from and, where the fact rests on a cached copy, the local path.
Facts the evidence does not support are marked **NOT FOUND** with a note on what would settle them.
Nothing here is invented. Numbers legible only inside an image chart are labelled as such and are not used.

Scope: the demo replay window for `MUM-2019-07-02` is 15:00-21:00 IST on 2 July 2019 over AOI `MUM-CENTRAL`
(lon 72.815-72.905, lat 18.995-19.135, WGS84).

Companion machine-readable file: `docs/research/bmc_aws_stations.json`.

---

## 1. IMD rainfall, 1-3 July 2019

### 1.1 The headline 24-hour totals (08:30 to 08:30 IST)

IMD's Mumbai observatories are **Colaba** (island city) and **Santacruz** (suburbs). IMD's own extreme-rainfall
pages give their station identifiers: **43003 = Mumbai-Santacruz**, **43057 = Mumbai-Colaba**
(source_url: https://city.imd.gov.in/citywx/extreme.php (station id shown in page body); cached:
`docs/research/_raw/imd_extreme_43003.html`, `docs/research/_raw/imd_extreme_43057_m7.html` - note both cached
copies are for **month = 9 (September)**, not July; see §1.5).

| Window (IST) | Station | Total | Source |
|---|---|---|---|
| 24 h ending 08:30, **Mon 1 Jul 2019** | Santacruz | **91 mm** | IMD official quoted by Deccan Herald: "The weather station in Santacruz recorded 91 mm of rainfall in 24 hours ending at 8.30 am on Monday" |
| 24 h ending 08:30, **Mon 1 Jul 2019** | "the city" (station not named) | **91.9 mm** | Scroll: "The city recorded 91.9 mm rain in the 24 hours ending Monday morning" |
| 24 h ending 08:30, **Tue 2 Jul 2019** | **Santacruz** | **375.2 mm** | Scroll live blog 1.07 pm, attributing The Indian Express |
| 24 h ending 08:30, **Tue 2 Jul 2019** | **Colaba** | **137.88 mm** | Scroll live blog 1.07 pm, attributing The Indian Express |
| 24 h ending 08:00, **Tue 2 Jul 2019** | BMC gauge, **Dindoshi fire station** | **479.56 mm** | Scroll live blog 1.07 pm (BMC "weather apparatus") |
| 24 h ending 08:30, **Tue 2 Jul 2019** | Thane | 220.42 mm | Scroll live blog 9.34 am, attributing The Times of India |
| 24 h ending ~08:30, **Tue 2 Jul 2019** | Powai (lake catchment) | 402 mm | Scroll live blog 1.09 pm, attributing Hindustan Times |
| 24 h ending 08:00, **Wed 3 Jul 2019** | BMC ward average, island city | **107 mm** | Outlook/PTI |
| 24 h ending 08:00, **Wed 3 Jul 2019** | BMC ward average, eastern suburbs | **172 mm** | Outlook/PTI |
| 24 h ending 08:00, **Wed 3 Jul 2019** | BMC ward average, western suburbs | **152 mm** | Outlook/PTI |

Sources, in full:

- Santacruz 375.2 mm and Colaba 137.88 mm, 24 h to 08:30 IST 2 Jul 2019 -
  source_url: https://scroll.in/latest/929092 (cached: `docs/research/_raw/pages/scroll_929092.txt`, line 189):
  "1.07 pm: The Met department records 137.88 mm and 375.2 mm rainfall at Colaba and Santacruz in 24 hours ending
  8.30 am on Tuesday, reports The Indian Express. The Brihanmumbai Municipal Corporation's weather apparatus at the
  Dindoshi fire station records 479.56 mm rain in 24 hours ending 8 am Tuesday."
- The same 375.2 mm figure and its ranking - source_url: https://www.deccanherald.com/india/mumbai-rains-live-744003
  (cached: `docs/research/_raw/pages/dh_744003.txt`, line 78): "At 375.2 mm, the rainfall in the 24-hour period
  before 8.30 am Tuesday was the highest since the July 26, 2005, deluge in Mumbai." Same page, line 234:
  "In the last 24 hours, Santa Cruz has recorded 375 mm of rains which is the highest in a decade for July.
  The highest ever was in 2005 when the city recorded 944.2 mm rain in 24 hours."
- Santacruz 91 mm, 24 h to 08:30 IST 1 Jul 2019 - source_url: https://www.deccanherald.com/india/mumbai-rains-live-744003
  (cached: `docs/research/_raw/pages/dh_744003.txt`, line 350).
- 91.9 mm to Monday morning - source_url: https://scroll.in/latest/929041
  (cached: `docs/research/_raw/pages/scroll_929041.txt`, line 119).
- BMC ward averages for the 24 h to 08:00 IST Wed 3 Jul 2019 - source_url: https://www.outlookindia.com/website/story/india-news-mumbai-rains-live-updates/207195
  (cached: `docs/research/_raw/pages/outlook_207195.txt`, line 73): "In the 24-hour period ending at 8 am on
  Wednesday, the island city (south Mumbai) received an average 107 mm rainfall, while the eastern and western
  suburbs recorded 172 mm and 152 mm downpour, respectively." The same paragraph names Hindmata, Dadar, Sion,
  Gandhi Market and Sion road number 24 as inundated - useful corroboration for the hotspot register.
- Record framing - source_url: https://www.outlookindia.com/website/story/india-news-mumbai-rains/333356
  (cached: `docs/research/_raw/pages/outlook_333356.txt`, line 66): "Mumbai received 375 mm of rainfall on Monday,
  making it the maximum July downpour that the city has seen over a 24-hour period, breaking the record of 1974."
  Gulf News agrees: "At average 375mm, Monday's was the maximum July rain that Mumbai has seen over a 24-hour
  period breaking records since 1974" - source_url: https://gulfnews.com/world/asia/india/mumbai-rains-record-july-rainfall-1.65100295
  (cached: `docs/research/_raw/pages/gulfnews_record_july.txt`, line 28).
  **Caution:** these two say "on Monday"; the IMD window is the 24 h *ending* 08:30 Tuesday, i.e. mostly Monday
  1 July's evening and overnight rain. Use the window, not the weekday, when calibrating the storm designer.

### 1.2 Sub-daily breakdown (what exists, and what does not)

The cache yields three sub-daily numbers, all from officials or a private forecaster, none a full hyetograph:

| Window (IST) | Place | Amount | Source |
|---|---|---|---|
| 3 hours, overnight 1-2 Jul | Kurla-Thane belt | **183 mm in 3 h** | Central Railway CPRO Sunil Udasi, via ANI |
| 12 hours, to ~midday 2 Jul | "the city" | **300-400 mm in 12 h** | CM Devendra Fadnavis |
| 6 hours, 23:30 (Sun 30 Jun) - 05:30 (Mon 1 Jul) | Mumbai | **63 mm in 6 h** | Skymet |
| 6 hours (day/date not stated on the page) | Santacruz | 35 mm in 6 h | Skymet, via Deccan Herald |
| 24 h to 2 Jul, "Mumbai and suburbs" | area estimate | 350-500 mm | Skymet's Mahesh Palawat |

- 183 mm in 3 h, Kurla-Thane - source_url: https://scroll.in/latest/929092
  (cached: `docs/research/_raw/pages/scroll_929092.txt`, line 140): "Kurla-Thane belt saw unprecedented rain of
  183 mm within 3 hours which caused some water logging."
- 300-400 mm in 12 h - source_url: https://gulfnews.com/world/asia/india/mumbai-rains-record-july-rainfall-1.65100295
  (cached: `docs/research/_raw/pages/gulfnews_record_july.txt`, line 36): "In the past 12 hours, the city has
  received an unprecedented 300 to 400mm of rain, the highest in the past decade. The existing drainage systems
  are unable to cope with such a heavy downpour, coupled with a high tide this afternoon."
- 63 mm in 6 h and 35 mm in 6 h at Santacruz, and 350-500 mm/24 h - source_url:
  https://www.deccanherald.com/india/mumbai-rains-live-744003 (cached: `docs/research/_raw/pages/dh_744003.txt`,
  lines 342, 321, 277). The 63 mm line reads "in a span of six hours from 1130 pm on Sunday to 05:30 am on
  Monday" in an entry stamped 1 July 2019, and the Sunday before that is 30 June, so the window is 23:30 IST
  30 June to 05:30 IST 1 July - the night before the event night, a day and four hours before the replay window
  opens. The 35 mm line does not state which 6 hours; do not date it.

**NOT FOUND: an hourly or three-hourly IMD hyetograph for Santacruz or Colaba covering 15:00-21:00 IST on
2 July 2019.** Nothing in the cache carries it, and IMD's public site publishes only current observations plus
monthly/extreme summaries. What would settle it: (a) the IMD RMC Mumbai daily weather report PDF archive for
July 2019, (b) an MoES/IMD data request for the AWS 15-minute series at 43003/43057, or (c) the BMC disaster
management portal's historical station report (POST-only API, see §2.3). Until one of those arrives, the storm
designer must be calibrated on the **24-hour totals in §1.1 plus the three sub-daily anchors above**, and the
bundle manifest must say so.

**Important for calibration:** the biggest documented burst is *overnight 1-2 July* (the 183 mm/3 h and the
24 h-to-08:30 total), not the 15:00-21:00 IST afternoon of 2 July that the demo replays. The afternoon of
2 July is the *waterlogging and high-tide* phase: the AMC said waterlogging had receded by 17:22 IST
(§3). If the demo window stays at 15:00-21:00 IST, `manifest.notes` must state that the storm is a
**reconstruction of a comparable convective burst placed inside the demo window**, calibrated to the event's
documented totals - not a claim that 375.2 mm fell between 15:00 and 21:00.

### 1.3 July 2019 monthly context

**NOT FOUND in a textual source in the cache.** The IMD monthly totals for July 2019 at Colaba and Santacruz exist
only inside GIF charts, which are images and cannot be read as numbers here:

- https://mausam.imd.gov.in/mumbai/mcdata/Monthly_Scz_July.gif (cached: `docs/research/_raw/imd_Monthly_Scz_July.gif`, `.png`)
- https://mausam.imd.gov.in/mumbai/mcdata/Monthly_Clb_July.gif (cached: `docs/research/_raw/imd_Monthly_Clb_July.gif`, `.png`)
- https://mausam.imd.gov.in/mumbai/mcdata/Highest_Scz_July.gif (cached: `docs/research/_raw/imd_Highest_Scz_July.gif`, `.png`)
- https://mausam.imd.gov.in/mumbai/mcdata/Highest_Clb_July.gif (cached: `docs/research/_raw/imd_Highest_Clb_July.gif`, `.png`)

These four URLs are the "Monthly Total -> July" and "Highest One Day Total -> July" links on the RMC Mumbai
rainfall page - source_url: https://mausam.imd.gov.in/mumbai/mcdata/rainfall_info.php
(cached: `docs/research/_raw/imd_mumbai_rainfall_info.html`). **Do not transcribe numbers from these charts into
the bundle.**

The textual equivalent would be IMD's "Extreme Weather events for the Month of <month>" table at
`https://city.imd.gov.in/citywx/extreme.php?id=43003` (Santacruz) / `?id=43057` (Colaba) - but see §1.5: that page
**cannot be asked for a past month**.

### 1.4 What the table that is retrievable does prove (method check, not a July number)

For **September** 2019 the IMD extreme table reports Santacruz monthly total 1115.7 mm (an all-time September
record for the station) and Colaba 855.8 mm - source_url: https://city.imd.gov.in/citywx/extreme.php?id=43003 and
`?id=43057` (cached: `docs/research/_raw/imd_extreme_43003.html`, `imd_extreme_43003_m7.html`,
`imd_extreme_43057_m7.html`, and re-fetched 2026-09-06 as `imd_extreme_43003_july.html`,
`imd_extreme_43057_july.html`). Quoted only to show the table's format and to confirm the station-id mapping
(43003 = Mumbai-Santacruz, 43057 = Mumbai-Colaba). It is **not** a July figure and must not appear in the bundle
as one.

### 1.5 The IMD extreme table always serves the current month (tested)

The `m=` query parameter is **ignored**. On 2026-09-06 both
`https://city.imd.gov.in/citywx/extreme.php?id=43003&m=7` and `...?id=43057&m=7` returned HTTP 200 with a body that
says `month is :9` and a heading "Extreme Weather events for the Month of September" - i.e. the month is chosen
server-side from the current date, and the page carries no month selector, form or alternative parameter
(cached: `docs/research/_raw/imd_extreme_43003_july.html`, `docs/research/_raw/imd_extreme_43057_july.html`).
The three earlier cached files (`imd_extreme_43003.html`, `imd_extreme_43003_m7.html`, `imd_extreme_43057_m7.html`)
are therefore not mis-named captures - they are what the endpoint serves in September.

**Consequence: the July 2019 monthly totals and July highest-one-day totals for Colaba and Santacruz are NOT
retrievable from this endpoint outside July, and remain NOT FOUND in text.** What would settle them: (a) hit the
same URLs during July, (b) read the four GIF charts above with a human eye or OCR and record who read them, or
(c) request the figures from IMD/MoES. Until then the bundle must not state a July 2019 monthly total.

---

## 2. Rain-gauge network

### 2.1 The two IMD observatories (verified coordinates)

| Station | WMO/IMD id | lat, lon (WGS84) | Source |
|---|---|---|---|
| Mumbai-Santacruz (IMD observatory, at CSMIA) | 43003 | 19.089, 72.868 | NOAA ISD station history: `"430030","99999","CHHATRAPATI SHIVAJI MAHARAJ INTL","IN","","VABB","+19.089","+072.868","+0011.3"` |
| Mumbai-Colaba (IMD observatory / RMC Mumbai) | 43057 | 18.900, 72.817 (ISD) · 18.8975995, 72.8131691 (OSM/Nominatim, finer) | NOAA ISD `"430570","99999","BOMBAY / COLABA","IN",...,"+18.900","+072.817","+0011.0"`; Nominatim "Regional Meteorological Centre Mumbai, Nanabhai Moos Marg, Colaba" |

- source_url: https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv (fetched 2026-09-06; the two rows above were
  read from the live file, saved to the scratch path used by this run - re-fetchable at any time from that URL).
- source_url: https://nominatim.openstreetmap.org/search?q=Regional+Meteorological+Centre,+Colaba,+Mumbai&format=json
  (cached: `docs/research/_raw/nominatim_Regional_Meteorological_Centre__Colaba__Mumbai_.json`).
- Station-id to name mapping (43003 = Santacruz, 43057 = Colaba): source_url: https://city.imd.gov.in/citywx/extreme.php
  (cached: `docs/research/_raw/imd_extreme_43003.html`, `docs/research/_raw/imd_extreme_43057_m7.html`).

**Geometry note for the bundle:** Colaba at ~18.90 N lies **outside** `MUM-CENTRAL` (south of lat 18.995) by about
11 km. Santacruz at ~19.089 N, 72.868 E lies **inside** the AOI. So only one of the two IMD observatories can act
as an in-AOI synthetic gauge; Colaba must be simulated as an out-of-domain gauge or dropped, and the bundle
manifest should say which.

### 2.2 The BMC / IMD / IITM gauge list (real names, no published coordinates)

The IIT Bombay "Mumbai Rain" platform exposes a station picker listing **116 gauges** across Mumbai, Thane and
Navi Mumbai, each tagged with its operator - `(BMC)`, `(IMD)` or `(IITM)` - and an internal `station_id` used by
`get_data.php?station_id=...`.

- source_url: https://www.mumbairain.org/ (station `<option>` list in the page source; cached:
  `docs/research/_raw/iitm_mumbairain.html`; the data endpoints are cached as
  `docs/research/_raw/iitm_get_data.php.txt`, `iitm_get_graph_data.php.txt`, `iitm_get_records.php.txt` - all three
  return PHP warnings when called without `station_id`, confirming the parameter name).

This is the best public evidence of *which* BMC gauges exist and *where they are notionally sited* (fire stations,
ward offices, pumping stations, workshops, hospitals). It carries **no latitude or longitude**. Stations inside or
near `MUM-CENTRAL` from that list, with their platform ids:

Dadar / Parel / Worli / Byculla: `35 Dadar Fire Station (BMC)`, `23 SWD Workshop Dadar (BMC)`,
`54 Worli Fire Station (BMC)`, `31 Byculla Fire Station (BMC)`, `192 Haji Ali Pumping Station (BMC)`,
`198 Britannia Storm Water Pumping Station (BMC)`, `5 F South Ward Office (BMC)`, `4 F North Ward Office (BMC)`,
`7 G South Ward Office (BMC)`.
Mahim / Matunga / Sion / Dharavi / Wadala: `38 Dharavi Fire Station (BMC)`, `213 MATUNGA_MUMBAI (IMD)`,
`218 SION_MUMBAI (IMD)`, `53 Wadala Fire Station (BMC)`, `21 Rawali Camp (BMC)`, `1 Pr. Thackeray Natya Mandir (BMC)`.
Kurla / BKC / Bandra: `44 Kurla Fire Station (BMC)`, `13 L Ward Office (BMC)`, `29 BKC Fire Station (BMC)`,
`199 Bandra Fire Station (BMC)`, `109 BANDRA (IMD)`, `9 H West Ward Office (BMC)`.
Santacruz / Vile Parle / Andheri: `211 MUMBAI_SANTA_CRUZ (IMD)`, `110 SWM Santacruz Workshop (BMC)`,
`52 Vile Parle Fire Station (BMC)`, `206 JUHU_AIRPORT (IMD)`, `26 Andheri Fire Stn (BMC)`,
`8 K East Ward Office (BMC)`, `12 K West Ward Office (BMC)`, `46 Marol Fire Station (BMC)`.
Reference / just outside the AOI: `205 MUMBAI_COLABA (IMD)`, `215 CSMT_MUMBAI (IMD)`, `105 MAHALAXMI (IMD)`,
`39 Dindoshi Fire Station (BMC)` (the 479.56 mm gauge of §1.1), `217 VIDYAVIHAR (IMD)`, `189 RAM_MANDIR (IMD)`.

Where a station's host facility could be matched **unambiguously by name** to an OpenStreetMap feature, the
facility coordinate is carried into `bmc_aws_stations.json` with `verified: true` and a note that the coordinate is
the facility, not a surveyed gauge mast. Everything else is carried with `lon: null, lat: null, verified: false`.

### 2.3 The BMC disaster-management portal (station list is behind a POST API)

The MCGM/BMC disaster-management rainfall portal is an Angular single-page app; its station list is not in the
HTML. Its API base and the two relevant endpoints are visible in the shipped bundle:

- Portal: source_url: https://dmweb.mcgm.gov.in/ (cached: `docs/research/_raw/mcgm_rainfall.html`, `mcgm_main.js`,
  `mcgm_main_root.js`).
- API base `https://dmwebtwo.mcgm.gov.in/api/`, endpoints `location/loadActiveAWSLocations` and
  `reports/getWeatherStationSuburbZoneWardFromStationType` (string literals in `mcgm_main_root.js`).
- Both endpoints return **HTTP 405 Method Not Allowed** on GET, i.e. they are POST-only:
  cached: `docs/research/_raw/dm_api_reports_getWeatherStationSuburbZoneWardFromStationType.json`
  (`{"status":405,"error":"Method Not Allowed"}`), and a fresh GET on 2026-09-06 to
  `https://dmwebtwo.mcgm.gov.in/api/location/loadActiveAWSLocations` returned the same 405.

**NOT FOUND: BMC AWS station coordinates.** `loadActiveAWSLocations` is very likely the list VARUNA needs
(name + ward + lat/lon), but it needs a POST with the app's expected body, which was not attempted here. What
would settle it: a single POST to that endpoint with the payload the Angular app sends, or an MoES/BMC data
request. Until then the synthetic gauge network must site its BMC stations at the OSM facility coordinates in
`bmc_aws_stations.json` and label the siting **inferred** in the manifest.

- IMD's own AWS list for Maharashtra is **not public**: `https://mausam.imd.gov.in/.../aws_maharashtra` redirects to
  an internal path (cached: `docs/research/_raw/imd_aws_maharashtra_list.html`, a 208-byte page whose only content
  is `<meta http-equiv="refresh" content="0; url=../../internal" />`).

---

## 3. Tide at Mumbai, 2 July 2019

Two independently reported numbers exist for the **midday** high tide of 2 July 2019, and they disagree. Record
both; do not average them.

| Value | Time (IST) | Nature | Source |
|---|---|---|---|
| **4.59 m** | ~11:52 | **forecast**, as reported that morning | ANI, via Scroll live blog 10.18 am, and India TV live blog 9:34 am |
| **4.92 m** | 11:30 | **as stated afterwards** by BMC's Additional Municipal Commissioner | Ashwini Joshi, via News18, quoted in Scroll live blog 5.22 pm |
| "high tide at 12 noon" | ~12:00 | CM's verbal statement | Devendra Fadnavis, via ANI, Scroll live blog 11.43 am |

- source_url: https://scroll.in/latest/929092 (cached: `docs/research/_raw/pages/scroll_929092.txt`):
  - line 213, "10.18 am: High tides of about 4.59 meters are expected in Mumbai at around 11.52 am, ANI reports."
  - line 202, "11.43 am: Fadnavis says high tide is expected at 12 noon."
  - line 135, "5.22 pm: Mumbai's Additional Municipal Commissioner Ashwini Joshi says waterlogging has receded.
    'Western Railway functioning normally,' News18 quotes him as saying. 'Mumbai witnessed 4.92 metres high tide at
    11.30 am. That is why we could not pump out water on the central line. Moreover, 53 flooding spots were
    identified last night.'"
- source_url: https://www.indiatvnews.com/news/india/mumbai-rains-live-updates-531812
  (cached: `docs/research/_raw/pages/indiatv_531812.txt`, lines 172-173; page `datePublished` is
  `2019-07-02T06:24:47+05:30`, which fixes the day): "9:34 am- High tide expected at 11:52 am today. A high tide of
  4.59 metres is expected at 11.52 am today. Tides of the Arabian Sea have a major effect on Mumbai's drainage
  system. In case of a high tide, the drainage system removes water from the city at a lower rate."
- Corroboration that the tide mattered on that day - source_url:
  https://gulfnews.com/world/asia/india/mumbai-rains-record-july-rainfall-1.65100295
  (cached: `docs/research/_raw/pages/gulfnews_record_july.txt`, line 36): the CM ties the flooding to "a high tide
  this afternoon".
- Corroboration that a further high tide was expected the next day - source_url:
  https://www.deccanherald.com/india/mumbai-rains-live-744003 (cached: `docs/research/_raw/pages/dh_744003.txt`,
  line 82): Central Railway ran a Sunday timetable on 3 July "In view of IMD forecast of very heavy rainfall
  coupled with the high tide on Wednesday".

### 3.1 What is missing, and how the bundle must label the tide

**NOT FOUND: a full tide table (successive high and low water times and heights, with the chart datum) for Mumbai
on 2 July 2019.** In particular there is **no source in hand for the evening high tide**, which is the part that
matters most for a 15:00-21:00 IST replay window and for the tide-locked outfall in `VARUNA-Twin`. A web search on
2026-09-06 for the 2 July 2019 Mumbai tide table returned only present-day tide-prediction services
(tide-forecast.com, tidetime.org, tides4fishing, tidechecker) with no accessible 2019 archive; a direct fetch of a
2019 archive page on tides4fishing returned HTTP 404.

What would settle it, in order of authority:
1. Survey of India / INCOIS tide tables for Mumbai (Apollo Bandar) 2019 - the official predictions with the chart
   datum stated.
2. Mumbai Port Authority (formerly Mumbai Port Trust) tide tables for 2019.
3. A harmonic prediction service run for the date (label the series **"predicted (harmonic)"** in the manifest, per
   SPEC.md 3.2).

**Therefore `bundles/MUM-2019-07-02/tide.csv` must be labelled `illustrative` in its `source` column**, per
SPEC.md 3.2, and the console must carry the honesty label. The one thing the series *may* legitimately be
anchored to is the documented midday high water: a peak of **4.59 m (forecast) to 4.92 m (as reported by BMC) at
11:30-11:52 IST on 2 July 2019**, with the sourced statement that the high tide prevented pumping on the central
line. Anything after about 13:00 IST on that day is model, not record, and must be labelled as such.

Typical spring-tide range for Mumbai for a sanity check: **NOT FOUND** with a citable source in this pass. The
nearest defensible anchor in hand is the 4.59-4.92 m high water above (heights above chart datum, datum not stated
in the sources) - use that, and cite the tide table once obtained rather than quoting a range without a source.

### 3.2 The datum: chart datum to the DEM's frame (verified 2026-09-14)

`tide.csv` stays in **chart datum**, as sourced. That is an assumption, not a finding: neither the 4.92 m nor the
4.59 m statement names its datum, and tide heights in Indian port tables are conventionally above chart datum. The
terrain the Twin runs on is not in that frame. Copernicus DEM GLO-30 heights are relative to the EGM2008 geoid -
source_url: https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf,
which states "The vertical reference datum is the Earth Gravitational Model 2008 (EGM2008; EPSG 3855)."

The offset between the two comes from PSMSL station 43, Bombay (Apollo Bandar):

- source_url: https://psmsl.org/data/obtaining/stations/43.php - the station notes put RLR(1964) 13.0 m below
  benchmark BM 2(PP)(1), and BM 2PP1 8.522 m above chart datum for data from 1937 onwards. So RLR = chart datum -
  4.478 m.
- source_url: https://psmsl.org/data/obtaining/rlr.annual.data/43.rlrdata - annual mean sea level in mm above RLR:
  7,141 (2015), 7,154 (2017), 7,207 (2020), 7,211 (2024), which is 2.663, 2.676, 2.729 and 2.733 m above chart
  datum. 2019 has no annual value (2016, 2018, 2019 and 2021-2023 are all missing).

Interpolating 2017 and 2020 gives 2.711 m for 2019. **The bundle uses 2.70 m, range 2.66-2.73 m** (the 2015-2024
span), and records the derivation, both PSMSL URLs, the DEM's datum with its source and the residual in
`manifest.json` under `tide_datum`. `varuna_twin.city.load_tide` subtracts the 2.70 m once when the manifest declares
a chart-datum stage, so in the DEM's frame the 05:40-09:40 IST stage runs from -2.655 m to +1.236 m instead of
0.045 m to 3.936 m; the replay clock's `tide.stage` events carry the datum label and the converted stage beside the
sourced one. A bundle with no `tide_datum` is read exactly as written.

Two caveats from the same PSMSL pages travel with the number: the station notes say that in 1966 the benchmark was
changed to BM 5/88, 7.44 m above chart datum, and the rlrdata file flags the 2020 and 2024 annual values - two of the
four used - `Y`, which source_url: https://psmsl.org/data/obtaining/notes.php defines as a monthly value missing from
the average or at least 30 missing days in the year; neither changes the 2.70 m or the 2.66-2.73 m range, because the
flagged means are used as PSMSL publishes them and the RLR series is PSMSL's reduction to one datum across the
benchmark change.

**Not quantified:** the separation between the EGM2008 geoid and local mean sea level at Mumbai. The conversion
puts the stage at mean sea level and treats that as the DEM's frame, so any geoid-to-sea-level offset - and any
revision of the Apollo Bandar chart datum since PSMSL's note - stays in the tidal boundary as an unmeasured bias.

---

## 4. IMD Doppler weather radar, Mumbai

### 4.1 The site

The Mumbai radar is **Mumbai-Veravali** (site code **VRV**), an S-band DWR. IMD's own radar page places its marker
at **19.1342 N, 72.8672 E**.

- source_url: https://mausam.imd.gov.in/responsive/radar.php (cached: `docs/research/_raw/imd_radar_php.html`),
  JavaScript marker definitions: `location34 = [19.1342, 72.8672]` then
  `addMarker(location34, 'Mumbai-Veravali', 'Veravali', 'left', [-5, -10]);`
- source_url: https://mausam.imd.gov.in/responsive/radar_animation.php?id=VRV
  (cached: `docs/research/_raw/imd_radar_animation_vrv.html`): `<h3 class="ipc-title">RADAR Animation -
  Mumbai-Veravali</h3>`, `og:image = https://mausam.imd.gov.in/Radar/caz_VRV.gif`.

The same page also defines `location18 = [19.0760, 72.8777]` labelled `'Mumbai-Colaba'`. **Treat that as an
approximate city marker, not the Colaba radar/observatory position** - 19.0760, 72.8777 is the generic Mumbai
centroid used all over the web (the same pair appears in `docs/research/_raw/iitm_mumbairain.html` as the map
centre), and it is ~19 km north of the true Colaba observatory at 18.8976, 72.8132 (§2.1). Use `VRV` for radar
geometry; do not use `location18` as a coordinate for anything.

Veravali at 19.1342 N, 72.8672 E sits just **north of the AOI's northern edge** (lat 19.135), which is convenient:
the whole of `MUM-CENTRAL` lies within roughly 15 km of the radar, well inside a 60 km Sky domain.

### 4.2 Products and their URLs

Public near-real-time products, per radar site id (`VRV` for Mumbai):

| Product | URL pattern (Mumbai) |
|---|---|
| CAZ (column max reflectivity), still | `https://mausam.imd.gov.in/Radar/caz_VRV.gif` |
| CAZ animation (converted) | `https://mausam.imd.gov.in/Radar/animation/Converted/VRV_MAXZ.gif` |
| SRI (surface rainfall intensity) animation | `https://mausam.imd.gov.in/Radar/animation/Converted/VRV_SRI.gif` |
| Product menu / picker | `https://mausam.imd.gov.in/responsive/radar.php?id=VRV` and `.../radar_animation.php?id=VRV` |
| Lower-case alias used by BMC's portal | `https://mausam.imd.gov.in/Radar/caz_vrv.gif`, `https://mausam.imd.gov.in/Radar/caz_mum.gif` |

- source_url: https://mausam.imd.gov.in/responsive/radar_animation.php?id=VRV
  (cached: `docs/research/_raw/imd_radar_animation_vrv.html`, `<img src="../../Radar/animation/Converted/VRV_MAXZ.gif">`
  and `.../VRV_SRI.gif`).
- The BMC disaster-management app embeds `https://mausam.imd.gov.in/Radar/caz_mum.gif` and
  `https://mausam.imd.gov.in/Radar/caz_vrv.gif` (cached: `docs/research/_raw/mcgm_main_root.js`), which is
  independent confirmation that these are the public Mumbai radar images. Cached samples of the live images are in
  `docs/research/_caz_mum.gif`, `caz_mum_frame0.png`, `caz_vrv_20260905.gif`,
  `caz_vrv_20260905_header.png`.

### 4.3 Cadence

Measured, not claimed: on 2026-09-05 the four Mumbai product images (`caz`, `sri`, `pac`, `ppi`) were all fetched at
08:44:29-08:44:33 GMT and all reported `Last-Modified: Sat, 05 Sep 2026 08:40:01-08:40:03 GMT` - i.e. the whole
product set is regenerated together, and was about **4 minutes old** at fetch time
(cached: `docs/research/_raw/head_imd_radar_cadence.txt`). That is consistent with IMD's usual **~10-minute volume
scan**, but a single HEAD sample cannot prove the interval. **NOT FOUND: a stated cadence on an IMD page.**
What would settle it: HEAD the same URL every two minutes for an hour and record the distinct `Last-Modified`
values, or an IMD DWR specification document. IMD's own "IMD DWR Network" page, which would carry the site table
and scan strategy, currently renders only "This Page Under Maintance"
(source_url: https://mausam.imd.gov.in/imd_latest/contents/imd_dwr_network.php; cached:
`docs/research/_raw/imd_dwr_network.html`).

### 4.4 Are July 2019 frames publicly retrievable? No.

**No.** Every public Mumbai radar URL above is a *latest-image* endpoint - a fixed filename overwritten in place
(`caz_VRV.gif`, `VRV_MAXZ.gif`). There is no date or timestamp parameter in any of the cached page markup, no
archive index, and no dated path pattern. The animation GIFs contain only the most recent few hours of frames.
IMD's archived radar data is served through its paid/registered data-supply route
(`https://dsp.imdpune.gov.in`, linked as "डेटा आपूर्ति / Data supply" from RMC Mumbai; cached:
`docs/research/_raw/imd_mumbai_rainfall_info.html`), and the DWR network page names a historical-data contact
(Mr Ranjan Phukan, Sc. D) rather than publishing an archive
(cached: `docs/research/_raw/imd_dwr_network.html`).

**Consequence for the build, and it is the honest one to say on stage:** the `MUM-2019-07-02` radar frames cannot
be real. They are produced by the storm designer (`services/replay/storm.py`, seed 2019) calibrated to the
documented gauge totals in §1.1, and the UI must label the bundle **"Reconstructed replay"** per SPEC.md 3.2 and
6.8. The decoder for IMD's *live* public radar images (`services/sky/decode_imd.py`, P1) targets exactly the
`caz_VRV.gif` product documented above, so the switch from reconstruction to live radar is a config change, and the
5-dBZ legend quantisation the storm designer mimics is the quantisation of these very images.

---

## 5. What the bundle manifest must record

- `sources[]`: every URL in §1.1, §3 and §4 above, each with its access date (cache 2026-09-04/05, this pass 2026-09-06).
- The calibration target: **Santacruz 375.2 mm and Colaba 137.88 mm in the 24 h ending 08:30 IST 2 Jul 2019**
  (Scroll/Indian Express), with the sub-daily anchors 183 mm/3 h (Kurla-Thane) and 63 mm/6 h (Skymet, 23:30 IST 30 Jun - 05:30 IST 1 Jul).
- `label`: "Reconstructed replay".
- Radar: synthetic, designer-generated; real product URLs recorded for the P1 decoder; July 2019 frames not
  publicly retrievable (§4.4).
- Gauges: `synthetic: true`, sited at the coordinates in `bmc_aws_stations.json`; only stations with
  `verified: true` may be drawn on the map, and the panel must say the BMC siting is inferred from OSM facility
  positions (§2.2, §2.3).
- Tide: `source` column = `illustrative`; anchored to the documented 4.59 m (forecast) / 4.92 m (BMC statement)
  high water at 11:30-11:52 IST 2 Jul 2019; **no source for the evening high tide** (§3.1).

## 6. Open items, ranked by how much they would improve the bundle

1. July 2019 monthly total and highest one-day total for both observatories, in text. The IMD extreme table cannot
   supply them outside July (§1.5), so this needs OCR of the four RMC Mumbai GIF charts with the reader recorded,
   or an IMD/MoES request. Lowest-effort honest option: leave the monthly context out of the bundle.
2. Survey of India / INCOIS / Mumbai Port tide table for 2 July 2019 - turns the tide from "illustrative" into
   "predicted (harmonic)" or better, and supplies the evening high tide.
3. POST `https://dmwebtwo.mcgm.gov.in/api/location/loadActiveAWSLocations` - would very likely replace every
   `verified: false` row in `bmc_aws_stations.json` with a published BMC coordinate and ward.
4. IMD RMC Mumbai daily weather report archive for 1-3 July 2019 - the only realistic public route to a
   sub-daily hyetograph.
