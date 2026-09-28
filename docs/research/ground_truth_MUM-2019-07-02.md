# Ground truth — Mumbai, 1–2 July 2019 (bundle `MUM-2019-07-02`)

Status: COMPLETE for the cached evidence (research team, cache fetched 2026-09-04/05, compiled 2026-09-06).
Every fact below carries the URL it was read from and the local cache path.
Rules applied: SPEC.md §0 rules 6–7 (no fake numbers, no fabricated ground truth; `source_url` mandatory), §10.2
(≥ 10 sourced pins inside the AOI or switch bundle), Appendix C.

AOI `MUM-CENTRAL`: lon 72.815–72.905, lat 18.995–19.135 (WGS84).
Companion file: `docs/research/ground_truth_MUM-2019-07-02.draft.geojson` (33 features, one per row below).

**Headline result — §10.2 is satisfied.** 29 sourced, time-stamped, placeable pins fall inside `MUM-CENTRAL`
(threshold is 10). `MUM-2019-07-02` stays the demo bundle; no event switch is needed. Section 6 records what the
alternatives would have cost.

**Headline caveat — no numeric depths exist.** `depth_cm` is `null` on every one of the 33 features. Not one cached
source states or shows a depth in centimetres for 1–2 July 2019. Verification against these pins can therefore score
occurrence, location and timing (CSI/POD/FAR, timing error) but **not** depth MAE. If `/verify` needs a depth MAE
for this event, the number does not exist yet and must not be invented — see §7.

---

## 0. Method and honesty notes

- Sources are the cached pages under `docs/research/_raw/pages/`; each row cites the canonical URL (taken from the
  page's own `rel="canonical"`) plus the cached file.
- Coordinates come only from the cached geocodes (`_raw/nominatim_*.json`, `_raw/nominatim_results.txt`,
  `_raw/nominatim_2019_results.txt`, `_raw/overpass_names2.json`, `_raw/overpass_pois.json`). Every feature records
  the OSM element it was anchored to in `geocode`, and a `geocode_precision` of either `osm_exact` (the named
  feature itself is in OSM) or `locality_approx` (only the surrounding locality/station is in OSM — for police
  stations, housing societies and unnamed junctions). Two features carry `not_geocoded` and are written with
  `"geometry": null`.
- Times are IST (+05:30) as printed by the source. Live-blog stamps are the time the entry was published, which is
  at or after the observation; `ts_uncertainty_min` covers that lag and any source disagreement.
- Where only a day is given ("on Monday"), `ts` is the midpoint of the possible window and
  `ts_uncertainty_min` is widened to half that window (one pin, MUM19-06, ±490 min).
- Nothing here is synthetic. `synthetic: false` on every feature. The synthetic traffic/report streams of §10.2 of
  SPEC.md are a separate artefact and must stay labelled separately.

### 0.1 Two cached pages are the wrong event — do not cite them for this bundle

| Cached file | What it actually is | Verdict |
|---|---|---|
| `_raw/pages/fpj_hindmata_kneedeep.txt` | Free Press Journal, **10–11 October 2024** — "knee-deep water … in Dadar Hindmata" | Wrong event. The only "knee-deep" phrase in the cache belongs to 2024, **not** to 2 July 2019. It must not be used as a 2019 depth hint. |
| `_raw/pages/fpj_cr_suspends.txt` | Free Press Journal, **19 August 2025** — Central Railway suspends Thane–CSMT | Wrong event. Use the 2019 rail sources in §2 instead. |
| `_raw/pages/tribune_108506.txt` | Tribune/PTI, a Friday–Saturday spell (Colaba 169 mm, Santacruz 157 mm in 24 h from 08:30 Friday) | Not 1–2 July 2019 (a Monday–Tuesday). Excluded. |
| `_raw/pages/dna_2767236.txt`, `_raw/pages/wion_232185.txt` | "Access Denied" edge-server error pages, no article body | Empty. Excluded. |
| `_raw/pages/dh_750660.txt` | Deccan Herald, **6 September 2019** wall collapse at Kalva, Thane | Wrong event. Excluded. |
| `_raw/pages/mumbaiflood_home.txt`, `mumbaiflood_root.txt` | 19 bytes each — no content captured from the IIT Bombay Mumbai Flood platform | Empty. See §7 gap G3. |

---

## 1. Sources used (all cited rows trace to one of these)

| Key | Source | URL | Cached |
|---|---|---|---|
| `scroll41` | Scroll.in, "In photos: Rain wreaks havoc in Mumbai…", 1 Jul 2019 16:17 IST | https://scroll.in/latest/929041/in-photos-rain-wreaks-havoc-in-mumbai-more-expected-in-next-two-days | `_raw/pages/scroll_929041.txt` |
| `scroll92` | Scroll.in live blog, "At least 16 people killed overnight…", 2 Jul 2019 07:52 IST, updated 22:37 IST | https://scroll.in/latest/929092/mumbai-at-least-16-people-killed-overnight-in-rain-related-incidents-schools-colleges-shut-today | `_raw/pages/scroll_929092.txt` |
| `scroll77` | Scroll.in, "Two men die as their car drowns in a waterlogged underpass in Malad", 2 Jul 2019 18:27 IST | https://scroll.in/latest/929177/mumbai-two-men-die-as-their-car-drowns-in-waterlogged-underpass-in-malad-area | `_raw/pages/scroll_929177.txt` |
| `dh` | Deccan Herald live blog, "Mumbai rains LIVE", published 1 Jul 2019 10:34 IST, runs to 3 Jul | https://www.deccanherald.com/archives/mumbai-rains-live-mumbai-limps-back-to-normalcy-as-rains-subside-744003.html | `_raw/pages/dh_744003.txt` |
| `itv` | India TV live blog, "Mumbai rains live updates", 2 Jul 2019 | https://www.indiatvnews.com/news/india-mumbai-rains-live-updates-wall-collapse-local-trains-flight-cancelled-pune-531812 | `_raw/pages/indiatv_531812.txt` |
| `latestly` | LatestLY, "Malad wall collapse death toll rises to 21, 78 injured", 2 Jul 2019 18:40 IST | https://www.latestly.com/india/news/malad-wall-collapse-death-toll-rises-to-21-78-injured-in-tragedy-after-overnight-downpour-978296.html | `_raw/pages/latestly_malad_978296.txt` |
| `outlook195` | Outlook/PTI, "Cloudburst in Kullu, Mumbai waterlogged…" (3 Jul figures) | https://www.outlookindia.com/national/cloudburst-in-kullu-mumbai-waterlogged-5-states-facing-the-wrath-of-monsoons-news-207195 | `_raw/pages/outlook_207195.txt` |
| `outlook356` | Outlook/IANS, "Mumbai rains: 12 killed as wall collapses…, public holiday declared" | https://www.outlookindia.com/national/india-news-mumbai-rains-12-killed-as-wall-collapses-due-to-overnight-downpour-public-holiday-declared-news-333356 | `_raw/pages/outlook_333356.txt` |
| `gulf` | Gulf News/IANS, "Record July rains paralyse Mumbai" | https://gulfnews.com/world/asia/india/record-july-rains-paralyse-mumbai-1.1562055210330 | `_raw/pages/gulfnews_record_july.txt` |
| `aljaz` | Al Jazeera, "India floods: dozens killed in Mumbai and Pune as walls collapse", 2 Jul 2019 | https://www.aljazeera.com/news/2019/7/2/india-floods-dozens-killed-in-mumbai-and-pune-as-walls-collapse | `_raw/pages/aljazeera_20190702.txt` |
| `imd_scz` | IMD Mumbai, "Highest one day rainfall over Santacruz in July (1991–2026)" chart | https://mausam.imd.gov.in/mumbai/mcdata/Highest_Scz_July.gif | `_raw/imd_Highest_Scz_July.png` |
| `imd_clb` | IMD Mumbai, "Highest one day rainfall over Colaba in July (1991–2026)" chart | https://mausam.imd.gov.in/mumbai/mcdata/Highest_Clb_July.gif | `_raw/imd_Highest_Clb_July.png` |

---

## 2. Event timeline (each line with its source)

All times IST. "Live-blog stamp" means the time the entry was published, which bounds the observation from above.

### Before the event — how the rain built

| Time | Fact | Source |
|---|---|---|
| From Fri 28 Jun, through the weekend | "The showers that started on Friday continued over the weekend and the intensity increased from the wee hours of Monday." | `dh` (entry stamped 1 Jul 13:54) |
| Sun 30 Jun 23:30 → Mon 1 Jul 05:30 | Mumbai recorded **63 mm in six hours** (private forecaster Skymet, quoted second-hand) | `dh` (1 Jul 13:54) |
| Mon 1 Jul 08:30 | **Santacruz 91 mm** in the 24 h ending 08:30 IST Monday (IMD official, quoted by PTI). Scroll gives the same day's city figure as **91.9 mm**. | `dh` (1 Jul 12:37); `scroll41` |

### Monday 1 July 2019 — the first day

| Time | Fact | Source |
|---|---|---|
| ~04:00–11:00 | Long-distance and suburban services hit: a bamboo structure fell on the overhead wire near Marine Lines (WR); a goods train derailed between Karjat and Lonavala (CR); 17 WR and 10+ CR long-distance trains cancelled | `dh` (1 Jul 12:37, 11:02) |
| 10:41 | "Waterlogging at Matunga police station" — **stamp is ambiguous, see §5** | `dh` |
| 10:44 | BMC deploys de-watering pumps at **Hindmata flyover** ("low-lying and prone to water-logging") | `dh` |
| 11:02 | Marine Lines slow lines restored; all four lines working Churchgate–Mumbai Central | `dh` |
| 11:23 | Heavy traffic jam in the **Sion** area | `dh` |
| 11:35 | Waterlogging at **Sion railway station** | `dh` |
| 11:52 | Heavy traffic jam at **Bhakti Park, Eastern Freeway** (Wadala) | `dh` |
| 14:00 | IMD Mumbai nowcast warning: intense spells likely to continue in Raigad and Palghar over the next two hours | `dh` (1 Jul 14:00 bulletin, quoted 14:11) |
| by 16:17 | **King's Circle** — "vehicles submerged at the waterlogged King's Circle" (IANS photo, caption says "Monday"); waterlogging also reported at Hindmata junction, Bandra and Parel; flights 25–30 min late; police ask motorists to avoid central Mumbai **from Sion to Lower Parel**; 2,000+ traffic police deployed; Western Express Highway at a standstill Goregaon→Andheri | `scroll41`; `dh` (1 Jul 13:54) |
| ~23:30 | Two men, Irfan Khan (37) and Gulshad Shaikh (38/40), drown when their SUV stalls in a flooded **subway** and its automatic doors jam — see the location conflict in §5 | `scroll77`; `itv` (2 Jul 12:41); `dh` (2 Jul 12:33) |
| ~23:45 | SpiceJet SG 6237 from Jaipur **overshoots the main runway** at CSMIA; no injuries; the aircraft blocks the main runway | `scroll92` (2 Jul 12:40); `dh` (2 Jul 07:36) |

### Overnight and Tuesday 2 July 2019 — the deluge

| Time | Fact | Source |
|---|---|---|
| 00:30 (bulletin) | IMD Mumbai deputy DG K. S. Hosalikar: "very intense spells of rain … during the next four hours"; local forecast now indicates "intermittent heavy showers with isolated extremely heavy rainfalls in city and suburbs" | `scroll92` (relayed 07:28) |
| ~02:00 | **Malad East, Pimpripada compound wall collapse** onto hutments — the event's largest loss of life | `latestly`; `dh` (headline) |
| ~02:00 onward | A girl (Sanchita) trapped in the Pimpripada debris "has been crying for help since 2 am" | `scroll92` (13:13) |
| before 07:49 | **Central Railway suspends suburban services**: "Moving trains in such rains in Kurla Thane section is safety hazard … Suburban Train movement has been suspended till further advice" | `scroll92` (07:49) |
| 08:00 | BMC says **all train movement on Central Railway, Western Railway and the Harbour Line has been stopped**. CR simultaneously says it will run CSMT–Bandra (Harbour), Vashi–Panvel, Thane–Vashi–Panvel (Trans-Harbour) and Thane–Kalyan/Khopoli (Main) only | `scroll92` (08:00); `dh` (2 Jul 07:54) |
| 08:03 | Maharashtra government: **only emergency services will remain functional** in Mumbai | `itv` |
| 08:07 | BMC: about **1,000 people evacuated from Kranti Nagar, Kurla**, Mithi river overflowing | `dh`; `scroll92` (09:29); `gulf` (Navy/INS Tanaji, ~1,000 rescued from Kranti Nagar slums) |
| 08:08 | **Public holiday declared.** Maharashtra government declares a public holiday in Mumbai for Tuesday; all schools and colleges closed; CMO advises people to stay indoors. BMC had declared the schools/colleges holiday first and the state extended it to the coastal Konkan region | `scroll92`; `outlook356` |
| 08:31 | BMC waterlogging tweet: **Vakola junction, Vakola, Postal Colony, Chunabhatti railway station, Vakola road** | `itv` |
| 08:47 | Traffic advisory: traffic slow from waterlogging at **Gandhi Market, Sion, LBS Nayak Nagar, Everard Nagar, Kherwadi flyover, Amar Mahal, Khar Subway, Milan Subway, Andheri, Jogeshwari JVLR, Malad chowky subway** | `dh` |
| 08:52 | Traffic jam on the Western Express Highway | `itv` |
| 09:10 | ANI visuals: **King's Circle** streets waterlogged; **ground floor of Kailash Parbat Society, Kurla East submerged**; **railway tracks submerged at Sion station**; **water inside Vakola police station** | `scroll92` |
| 09:16 / 09:34 | **54 flights diverted**; Thane recorded 220.42 mm in the 24 h to 08:30 | `scroll92` |
| 09:34 / 10:18 | **High tide of 4.59 m expected at 11:52** (see the tide conflict in §5) | `itv`; `scroll92` |
| 09:44 | Second-highest 24-hour rainfall for Mumbai since 1975; highest was 2005 (Times of India, quoted second-hand) | `scroll92` |
| 09:55 | 16 killed and 80 injured in wall collapses in Mumbai; 51 injured at Kurar village | `scroll92` |
| 10:06 / 10:58 | Main runway closed (SpiceJet aircraft still on it); alternative runway functional; flights rescheduled and diverted | `scroll92`; `itv` |
| 10:09 | **IMD sounds a red alert for Mumbai**; CM asks citizens to stay indoors | `scroll92` |
| 10:33 | "Massive water logging outside **Sion Station** … outside Sion police station" | `itv` |
| 10:36 / 12:26 | Western Railway back to normal Churchgate–Vasai then Churchgate–Virar; water down on all four lines at Nallasopara | `dh`; `scroll92` |
| 11:40 | CM Fadnavis: "Local trains operational on Western line but **yet to resume on Central line** as there's more flooding there" | `scroll92` |
| 11:43 | CM Fadnavis: high tide expected at 12 noon; Mumbai police received 1,600–1,700 tweets overnight | `scroll92` |
| 12:02 / 13:17 | **Water enters Sakinaka police station** | `dh` (12:02); `scroll92` (13:17, ANI) |
| 12:09 / 12:18 | **Andheri subway closed due to flooding** | `scroll92` (ANI); `itv` |
| 12:25 | Waterlogging near a school in **Kurla West** | `dh` |
| 12:43 | Airport says it could take **up to 48 hours** to reopen the main runway | `scroll92` |
| 13:07 | **Colaba 137.88 mm and Santacruz 375.2 mm** in the 24 h ending 08:30 Tuesday (Met department, via The Indian Express); BMC's Dindoshi fire-station gauge **479.56 mm** in the 24 h to 08:00 Tuesday | `scroll92` |
| 13:09 | **Powai lake overflows**; 402 mm in the region in 24 h (Hindustan Times, second-hand) | `scroll92` |
| 13:16 | BMC bus-diversion tweet — 13 BEST routes diverted or curtailed, including Sion Road no. 2, Gandhi Market, Antop Hill Sector 7, Maratha Colony WEH (via Vakola flyover), Milan Subway (via Milan flyover), Sunder Vihar Pratiksha Nagar | `itv` |
| 13:39 | **Chembur playground flooded** | `dh` |
| 14:07 | Six MCGM pumping stations had discharged **more than 14,000 million litres** to the sea | `itv` |
| 14:22 / 14:28 | Waterlogged streets of **Bandra**; heavy waterlogging on the tracks at **Kurla station** | `itv` |
| 16:48 | **179 BEST buses broken down** across the city; 74 removed from the road | `scroll92` |
| 17:22 | Additional Municipal Commissioner Ashwini Joshi: waterlogging has receded; "Mumbai witnessed **4.92 metres high tide at 11.30 am**. That is why we could not pump out water on the central line. Moreover, **53 flooding spots were identified** last night." | `scroll92` |
| 17:03 / 18:40 | **Malad wall collapse toll reaches 21** (78 injured) | `scroll92`; `latestly` |
| 20:59 / 22:30 | 70 domestic arrivals and 81 departures cancelled; **203 flights cancelled** in total | `scroll92` |
| (undated, same day) | CM Fadnavis: "In the past 12 hours, the city has received an unprecedented **300 to 400 mm** of rain … coupled with a high tide this afternoon" | `gulf` |

### Wednesday 3 July 2019 — the tail (context, not pins)

| Time | Fact | Source |
|---|---|---|
| 08:00 (24 h ending) | Island city (south Mumbai) **107 mm**, eastern suburbs **172 mm**, western suburbs **152 mm** | `outlook195` |
| ~10:30 | Landslide onto the two-storey Narayan Hadke Chawl, **Chunabhatti**; three injured including a minor | `outlook195` |
| through the day | Hindmata, areas in Dadar and Sion "including the **Gandhi Market and road number 24 in Sion**" inundated; BEST diverted two dozen routes at six locations | `outlook195` |

---

## 3. Rainfall totals (which are IMD and which are second-hand)

| Gauge / area | Window | Total | Status |
|---|---|---|---|
| **Santacruz (IMD, 43003)** | 24 h ending 08:30 IST **2 Jul 2019** | **375.2 mm** | **IMD primary.** Confirmed on IMD Mumbai's own chart "Highest one day rainfall over Santacruz in July (1991–2026)": the 2019 bar reads 375.2 with the day-of-month "2" inside it, and the chart's footnote states "Rainfall is reported from 08.30 a.m of previous day to 08.30 am of same day." Source: https://mausam.imd.gov.in/mumbai/mcdata/Highest_Scz_July.gif (cached: `docs/research/_raw/imd_Highest_Scz_July.png`). Also quoted second-hand by `scroll92` (13:07, via The Indian Express) and `dh` (10:54). |
| **Colaba (IMD, 43057)** | 24 h ending 08:30 IST **2 Jul 2019** | **137.88 mm** | **Second-hand only.** Quoted by `scroll92` (13:07) as a Met department figure via The Indian Express. The cached IMD Colaba July chart gives the *July maximum* for 2019 as 173.6 mm on the 24th, so 2 July was **not** Colaba's July peak — consistent with 137.88 mm but not a confirmation of it. Primary IMD daily value NOT FOUND in the cache (gap G1). |
| Santacruz (IMD) | 24 h ending 08:30 IST **1 Jul 2019** | **91 mm** (Scroll gives the city figure as 91.9 mm) | Second-hand (PTI/IMD official via `dh` 12:37; `scroll41`). |
| Colaba (IMD) | 24 h ending 08:30 IST **1 Jul 2019** | NOT FOUND | No cached source gives it. |
| Santacruz / Colaba | 24 h ending 08:30 IST **3 Jul 2019** | NOT FOUND as station values | `outlook195` gives area averages only: island city 107 mm, eastern suburbs 172 mm, western suburbs 152 mm (24 h to 08:00 Wednesday). |
| **BMC gauge, Dindoshi fire station** (Malad East, outside the AOI: 19.17506, 72.86100, OSM way/359074975) | 24 h ending 08:00 IST 2 Jul 2019 | **479.56 mm** | Second-hand (`scroll92` 13:07, via The Indian Express). Highest single-gauge figure in the cache. |
| Thane | 24 h ending 08:30 IST 2 Jul 2019 | 220.42 mm | Second-hand (Times of India via `scroll92` 09:34). Outside the AOI. |
| Powai region | 24 h to ~13:00 IST 2 Jul 2019 | 402 mm (lake overflowed) | Second-hand (Hindustan Times via `scroll92` 13:09). Not geocoded in the cache. |
| Dahanu, Palghar | 24 h to 1 Jul | 298 mm | Second-hand (`dh` 1 Jul 13:54). Far outside the AOI. |
| City-wide | "past 12 hours" to ~2 Jul midday | 300–400 mm (CM Fadnavis); "350 to 500 mm in last 24 hours" (Skymet, `dh` 08:08); ">300 mm over 24 hours in some areas" (AFP) | Second-hand, imprecise. Use only as a sanity band for the storm designer, never as a calibration target. |

**Records context (for the landing-page "reconstructed replay" copy).** IMD's own Santacruz chart shows July 2005 at
**944.2 mm** on the 27th — the 26 July 2005 deluge — against 375.2 mm on 2 July 2019, so the 2019 event is far below
2005 but the largest July daily total at Santacruz in the 1991–2026 chart other than 2005 (next nearest: 351.5 mm in
2000, 274.1 mm in 2009). `scroll77` describes it as "the highest since the deluge of 2005 and the second-highest
downpour since 1974"; `dh` (10:54) says "highest in a decade for July". These claims disagree in wording; the chart is
the authority.

**Calibration target for `make bundle` (SPEC.md §10.2, P2.3).** Use **Santacruz 375.2 mm, 08:30 IST 1 Jul → 08:30 IST
2 Jul 2019**, the one primary-sourced number, as the AOI accumulation anchor, and record the IMD chart URL in
`manifest.sources`. The 3-hour demo window (15:00–21:00 IST 2 Jul) sits *after* that accumulation window closed — see
gap G2, this is a real problem for the bundle design and must be resolved before P2.3 is ticked.

## 3.1 Tide — the sources disagree

| Claim | Time | Value | Source |
|---|---|---|---|
| Forecast high tide | 11:52 IST 2 Jul | **4.59 m** | `itv` (09:34); `scroll92` (10:18, ANI) |
| CM Fadnavis | "12 noon" 2 Jul | (no height) | `scroll92` (11:43) |
| Additional Municipal Commissioner Ashwini Joshi, after the event | **11:30 IST** 2 Jul | **4.92 m** | `scroll92` (17:22) |

Two civic figures for the same tide (4.59 m at 11:52 vs 4.92 m at 11:30) are 33 cm and 22 minutes apart. Neither is a
tide table. Until a Survey of India / INCOIS table is obtained (Appendix C), the replay tide series must be labelled
**illustrative** per SPEC.md §3.2. Joshi's quote is nonetheless the single best piece of evidence in the whole cache
for VARUNA's tide-lock story: *"That is why we could not pump out water on the central line."*

---

## 4. The pins — 29 inside `MUM-CENTRAL`, 4 outside or unplaceable

Full properties are in `ground_truth_MUM-2019-07-02.draft.geojson`. `depth_cm` is null on all of them.
Precision: `E` = `osm_exact`, `A` = `locality_approx`, `—` = `not_geocoded` (null geometry).

| id | Name | lon | lat | ts (IST) | ±min | kind | Prec. | Depth phrase | Source | In AOI |
|---|---|---|---|---|---|---|---|---|---|---|
| MUM19-01 | Hindmata flyover / junction, Dadar East–Parel | 72.84214 | 19.01010 | 2019-07-01 10:44 | 15 | log | E | — | `dh` | yes |
| MUM19-02 | Matunga police station | 72.85015 | 19.02744 | 2019-07-01 10:41 | 720 | log | A | — | `dh` | yes |
| MUM19-03 | Sion (junction area) | 72.86349 | 19.04273 | 2019-07-01 11:23 | 15 | news | E | — | `dh` | yes |
| MUM19-04 | Sion railway station | 72.86328 | 19.04652 | 2019-07-01 11:35 | 15 | rail | E | water logging at the station | `dh` | yes |
| MUM19-05 | Bhakti Park / Eastern Freeway, Wadala | 72.87757 | 19.02897 | 2019-07-01 11:52 | 15 | news | A | — | `dh` | yes |
| MUM19-06 | King's Circle / Maheshwari Udyan | 72.85774 | 19.03168 | 2019-07-01 08:00 | 490 | news | E | vehicles submerged | `scroll41` | yes |
| MUM19-07 | Kranti Nagar, Kurla East | 72.88685 | 19.06792 | 2019-07-02 08:07 | 30 | log | A | — | `dh` | yes |
| MUM19-08 | Gandhi Market, Sion / Matunga | 72.85902 | 19.03254 | 2019-07-02 08:47 | 20 | log | E | — | `dh` | yes |
| MUM19-09 | Sion (junction area) | 72.86349 | 19.04273 | 2019-07-02 08:47 | 20 | log | E | — | `dh` | yes |
| MUM19-10 | Milan Subway, Santacruz | 72.84283 | 19.09045 | 2019-07-02 08:47 | 20 | log | E | — | `dh` | yes |
| MUM19-11 | Kherwadi flyover / Kalanagar, Bandra East | 72.84690 | 19.05318 | 2019-07-02 08:47 | 20 | log | A | — | `dh` | yes |
| MUM19-12 | Andheri (locality named in advisory) | 72.84642 | 19.11970 | 2019-07-02 08:47 | 20 | log | A | — | `dh` | yes |
| MUM19-13 | Khar Subway | — | — | 2019-07-02 08:47 | 20 | log | — | — | `dh` | **no** (not geocoded) |
| MUM19-14 | Amar Mahal junction, Chembur | — | — | 2019-07-02 08:47 | 20 | log | — | — | `dh` | **no** (not geocoded) |
| MUM19-15 | Vakola junction, Santacruz East | 72.84693 | 19.08039 | 2019-07-02 08:31 | 20 | log | E | — | `itv` | yes |
| MUM19-16 | Chunabhatti railway station | 72.86910 | 19.05177 | 2019-07-02 08:31 | 20 | log | E | — | `itv` | yes |
| MUM19-17 | Postal Colony, Chembur | 72.89497 | 19.06074 | 2019-07-02 08:31 | 20 | log | A | — | `itv` | yes |
| MUM19-18 | King's Circle / Maheshwari Udyan | 72.85774 | 19.03168 | 2019-07-02 09:10 | 60 | news | E | waterlogged streets | `scroll92` | yes |
| MUM19-19 | Kailash Parbat Society, Kurla East | 72.88256 | 19.06289 | 2019-07-02 09:10 | 60 | news | A | ground floor submerged | `scroll92` | yes |
| MUM19-20 | Sion railway station | 72.86328 | 19.04652 | 2019-07-02 09:10 | 60 | rail | E | railway tracks submerged | `scroll92` | yes |
| MUM19-21 | Vakola police station | 72.84714 | 19.07986 | 2019-07-02 09:10 | 60 | log | A | — | `scroll92` | yes |
| MUM19-22 | Sion station / Sion police station area | 72.86328 | 19.04652 | 2019-07-02 10:33 | 15 | news | A | massive waterlogging | `itv` | yes |
| MUM19-23 | Sakinaka police station, Andheri East | 72.88096 | 19.09886 | 2019-07-02 12:02 | 90 | log | A | — | `dh` | yes |
| MUM19-24 | **Andheri subway** | 72.84703 | 19.11927 | 2019-07-02 12:09 | 15 | news | E | closed due to flooding | `scroll92` | yes |
| MUM19-25 | Kurla West (near a school) | 72.87658 | 19.06860 | 2019-07-02 12:25 | 20 | news | A | — | `dh` | yes |
| MUM19-26 | Chembur (playground) | 72.89797 | 19.05482 | 2019-07-02 13:39 | 20 | news | A | — | `dh` | yes |
| MUM19-27 | Antop Hill (BEST route curtailed) | 72.86695 | 19.03018 | 2019-07-02 13:16 | 20 | log | A | — | `itv` | yes |
| MUM19-28 | Pratiksha Nagar, Sion (route diverted) | 72.86930 | 19.03960 | 2019-07-02 13:16 | 20 | log | A | — | `itv` | yes |
| MUM19-29 | Vakola flyover, WEH | 72.84681 | 19.07454 | 2019-07-02 13:16 | 20 | log | E | — | `itv` | yes |
| MUM19-30 | Kurla railway station | 72.88028 | 19.06582 | 2019-07-02 14:28 | 20 | rail | E | heavy waterlogging on tracks | `itv` | yes |
| MUM19-31 | Bandra (streets) | 72.84014 | 19.05507 | 2019-07-02 14:22 | 20 | news | A | waterlogged streets | `itv` | yes |
| MUM19-90 | Pimpripada, Malad East (wall collapse, ~21 dead) | 72.87120 | 19.17905 | 2019-07-02 02:00 | 60 | news | A | — | `latestly` | **no** (lat > 19.135) |
| MUM19-91 | "Malad subway" drownings (location disputed) | — | — | 2019-07-01 23:30 | 30 | news | — | car submerged in the subway | `scroll77` | **no** (not geocoded) |

**`items_found` = 29** (pins inside `MUM-CENTRAL` carrying a `source_url`).

### 4.1 How the pins line up with the hotspot register (SPEC.md §3.3)

| Registered chronic hotspot | Covered by a 1–2 Jul 2019 pin? |
|---|---|
| Hindmata junction | **Yes** — MUM19-01 (1 Jul only; no 2 July Hindmata observation in the cache — see gap G4) |
| Dadar TT | No |
| Parel / Bharatmata | Only as part of the 1 Jul "Hindmata junction, Bandra and Parel" summary line (`dh` 13:54); no separate time-stamped pin |
| King's Circle / Maheshwari Udyan | **Yes** — MUM19-06, MUM19-18 |
| Gandhi Market | **Yes** — MUM19-08 (and again on 3 Jul in `outlook195`) |
| Sion Circle | **Yes** — MUM19-03, MUM19-09 (plus Sion station MUM19-04/20/22) |
| Kurla LBS Marg | Partly — MUM19-25 (Kurla West) and MUM19-30 (Kurla station); LBS Marg itself is not separately named |
| Khar subway | Named in the advisory but **not geocodable** — MUM19-13 |
| Milan subway | **Yes** — MUM19-10 |
| Andheri subway | **Yes** — MUM19-24, the cleanest pin in the set (a specific asset, a specific action, two independent stamps 9 min apart) |

Eight of the ten registered hotspots have at least one time-stamped 2019 observation. That is a strong verification
set for CSI/POD/FAR at chronic spots (SPEC.md §11.12) even without depths.

---

## 5. Conflicts and things the cache cannot settle

1. **"Malad subway" vs "Milan Subway" for the two drownings.** `scroll77` (via NDTV/PTI/Mumbai Mirror), `scroll92`
   (16:46) and `itv` (12:41) all say **Malad subway**; the Deccan Herald live blog (2 Jul 12:33) says **Milan Subway**.
   The two are ~9 km apart and Milan Subway is inside the AOI while Malad is not — so this changes whether the
   event belongs in the bundle at all. Three sources to one favours Malad; the pin is written with **null geometry**
   until a Mumbai Mirror or police record settles it. **Do not** put this on the demo map.
2. **Tide height and time**: 4.59 m at 11:52 (forecast, ANI/India TV) vs 4.92 m at 11:30 (Ashwini Joshi, after the
   event). See §3.1.
3. **Malad wall-collapse toll drifts through the day**: 13 (08:30) → 16 (09:55) → 18 → 21 with 78 injured (18:40).
   Use the final 21/78 figure with the 18:40 stamp.
4. **The Deccan Herald live blog interleaves 1 July and 2 July stamps** in a block around the "Matunga police
   station" and "Trains operational between Marine Lines and Churchgate" entries. MUM19-02 therefore carries a
   ±720 min uncertainty and is flagged "do not use for timing verification".
5. **Ages differ for Gulshad Shaikh** (38 in `scroll92`, 40 in `scroll77`). Immaterial to the map; noted for honesty.
6. **Whether Hindmata flooded on 2 July.** Every source treats Hindmata as *the* chronic spot, and BMC pumped there
   on 1 July, but the cache contains **no time-stamped 2 July Hindmata observation** — the 2 July mentions are the
   `outlook195` 3 July summary and generic "low-lying areas" lines. See gap G4; this matters because the demo script
   (SPEC.md §15, 2:40) turns on ground-truth pins landing at Hindmata.

---

## 6. Do we need to switch events? No — but here is the comparison

§10.2 requires ≥ 10 sourced pins inside the AOI, or a switch to another Mumbai event with better coverage.
**29 pins qualify, so `MUM-2019-07-02` stands.** For the record, from the cache plus what is known:

| Candidate | Coverage in this cache | Assessment |
|---|---|---|
| **1–2 Jul 2019 (chosen)** | 25 dated articles, two full live blogs with minute-level civic and traffic entries, IMD chart confirmation of the Santacruz total, 29 placeable AOI pins across eight of ten chronic hotspots | **Use this.** Best time resolution of the three; the tide-lock quote and the "53 flooding spots" line are unique demo assets. |
| 5 Aug 2020 | **Nothing in the cache** (no page fetched for it) | Would need a fresh research pass. Its known strength is a documented storm-surge/high-tide coincidence; its weakness for us is that it happened during a COVID lockdown, so traffic-anomaly evidence — the observation type VARUNA-Pulse leans on — is unrepresentative. Not worth the switch. |
| 26 Jul 2005 | Only indirectly — IMD's chart gives Santacruz **944.2 mm on 27 Jul 2005** (24 h to 08:30), and `dh`/`scroll77` use 2005 as the benchmark | Far better documented in the literature, but pre-Twitter: minute-stamped street-level logs are scarce, and a 944 mm event is so far outside design conditions that it would make the drainage model look better than it is. Reject. |

Decision to record in `docs/DECISIONS.md`: keep `MUM-2019-07-02`; the ground-truth set scores **occurrence, place and
timing only**, not depth.

---

## 7. Gaps — what is NOT FOUND and what would settle it

| # | Gap | What would settle it |
|---|---|---|
| G1 | **No primary IMD daily value for Colaba on 2 Jul 2019** (137.88 mm is second-hand). The cached IMD "extreme weather events" pages are both for month = 9 (September) despite the `_m7` filename — the July request did not take. | Re-fetch `https://mausam.imd.gov.in/…/extreme.php?…&month=7` for station 43057 (Colaba) and 43003 (Santacruz), or the IMD Mumbai daily rainfall archive. |
| G2 | **The demo window and the calibration window do not line up.** The one primary total (375.2 mm) is for 08:30 1 Jul → 08:30 2 Jul, whereas the demo replay runs 15:00–21:00 IST on 2 Jul (SPEC.md §15 opens at 15:40). The heaviest rain in this event fell **overnight on 1–2 July**, and by 17:22 on 2 July BMC was saying waterlogging had receded. | Either move the replay window to the overnight peak (≈ 22:00 1 Jul → 04:00 2 Jul) — which matches nearly every pin in §4 — or re-anchor the storm designer to the 3 Jul figures. This is a **design decision that must be made before P2.3**, and it is the single most consequential finding in this file. |
| G3 | **IIT Bombay Mumbai Flood platform yielded nothing** — `_raw/pages/mumbaiflood_home.txt` and `mumbaiflood_root.txt` are 19 bytes each; `mumbaiflood_bundle.js` was not parsed here. | Parse `mumbaiflood_bundle.js` for data endpoints, or request an archive export. It is the most likely source of *depths*. |
| G4 | **No time-stamped Hindmata observation for 2 July 2019**, the day the demo replays. | Mumbai Mirror / Mid-Day / Hindustan Times archives for 2 Jul 2019; BMC's own disaster-management log for the day; the "53 flooding spots identified last night" list Ashwini Joshi referred to (`scroll92` 17:22) would be the ideal artefact. |
| G5 | **No numeric depths anywhere.** `depth_cm` is null on all 33 features. | Photographs with a scale, BMC flooding-spot records with depth, or IIT-B platform data (G3). Until then `/verify` must not display a depth MAE for this event. |
| G6 | **Khar Subway and Amar Mahal are not geocodable** from the cache. Nominatim returned no result for "Khar Subway, Mumbai" or "Khar Subway, Khar, Mumbai", and a name scan of every cached Overpass extract (`overpass_names2/3.json`, `overpass_pois.json`) for `subway` returns only Milan Subway (node/9741160311-12, way/39532540), Sion Subway 1 (way/632207092), two Sion pedestrian subways and one unnamed pedestrian subway at 19.06786, 72.90290 — no Khar, no Andheri-named way, no Amar Mahal. | A fresh Overpass query for `name~"Khar"` plus `highway`/`tunnel` in the AOI, or manual identification of the underpass way id from the OSM editor. |
| G7 | **The "53 flooding spots" list itself was never published in the cached sources.** | BMC/MCGM disaster-management portal (`_raw/mcgm_rainfall.html`, `dm_api_reports_*.json` were fetched — worth checking for a historical flooding-spot endpoint). |
| G8 | **No tide table.** Both tide numbers are civic statements, not measurements. | Survey of India tide tables or INCOIS for Mumbai (Apollo Bandar), 2 Jul 2019. Until then the tide series is `illustrative`. |

---

## 8. What the bundle builder should do with this file

- Read `ground_truth_MUM-2019-07-02.draft.geojson`; filter `inside_aoi == true` → 29 features.
- Treat `geocode_precision == "locality_approx"` (15 of the 29) as a **≥ 150 m position tolerance** when scoring;
  14 pins are `osm_exact`. By kind, the 29 are 16 `log`, 10 `news`, 3 `rail`.
- Score CSI/POD/FAR and timing error against these pins. **Do not** compute depth MAE — no depths exist (G5).
- Every pin must render with its `source_url` in the console's "As it happened" ticker (SPEC.md §7.2), and the
  ticker copy should show the `±min` so the jury sees the honesty.
- The `depth_phrase` field carries the only qualitative depth evidence ("vehicles submerged", "ground floor
  submerged", "railway tracks submerged"). Show the phrase, never a converted number.
