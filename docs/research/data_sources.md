# VARUNA open-data access research (MUM-CENTRAL and CHN-SOUTH)

Status: COMPLETE for sections 1–4 and 7; section 5 complete with per-feature sourcing caveats; section 6 (IDF) is a
documented gap with a labelled assumption.
Accessed: 2026-09-04 / 2026-09-05 (cached evidence) and **2026-09-06** (re-verification in this session).
Author: VARUNA research team. Repo: `docs/research/`.
Raw evidence (HEAD outputs, Overpass responses, cached IMD/news pages) lives in `docs/research/_raw/`.

Rules followed (SPEC.md §0 rules 6 and 7): every fact carries the exact URL it was read from and the access date.
Where a claim rests on a cached file, both the URL and the local path are given. Where no public source was found, the
gap is written as **NOT FOUND** with the fact that would settle it. Nothing is invented.

AOIs (WGS84):

| AOI | lon min | lon max | lat min | lat max | CRS for computation |
|---|---|---|---|---|---|
| MUM-CENTRAL | 72.815 | 72.905 | 18.995 | 19.135 | EPSG:32643 |
| CHN-SOUTH | 80.20 | 80.28 | 12.96 | 13.05 | EPSG:32644 |

---

## ADR-0006 — TLS interception on the build machine (copy this into `docs/DECISIONS.md`)

**Context.** The development laptop runs Norton with HTTPS scanning enabled. Norton terminates and re-signs every TLS
connection with its own root CA, which is installed in the **Windows certificate store** but not in the CA bundle that
`certifi` ships. Every Python library that pins `certifi` therefore fails: `requests` (and so `osmnx`, `pystac_client`)
raises `SSLCertVerificationError: unable to get local issuer certificate`, and GDAL/`rasterio`'s `/vsicurl/` and
`/vsis3/` drivers fail the same way (`CURL error: SSL certificate problem: unable to get local issuer certificate`), so
streaming a COG straight from `copernicus-dem-30m.s3.amazonaws.com` does not work. `curl.exe` on Windows succeeds
because it uses Schannel and the Windows store — which is why every reachability check in this document was made with
`curl -sI`.

**Decision.**
1. Inject the Windows trust store into Python at process start:
   `import truststore; truststore.inject_into_ssl()` — placed at the top of `services/city/__init__.py` and any CLI
   entry point that reaches the network, *before* `requests`/`urllib3`/`osmnx` are imported. Add `truststore` to
   `pyproject.toml`. `osmnx` gets the same treatment because it uses `requests` internally; the alternative
   (`ox.settings.requests_kwargs = {"verify": False}`) is rejected — it disables verification instead of fixing trust.
2. **Do not read rasters over the network.** `make city` downloads each tile once (curl, or `requests` under
   truststore) into `city/cache/<city>/`, verifies `Content-Length` against the tables below, and every subsequent read
   (`rasterio.open`, `rioxarray.open_rasterio`, GDAL mosaic) points at the **local file**. `/vsicurl/` tuning is not
   needed because nothing is streamed.
3. `VARUNA_OFFLINE=1` asserts that no step reaches the network at all; the cache must be complete before the finale.

**Alternatives considered.** Exporting the Norton root to a PEM and setting `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE`
(works, but is machine-specific and breaks on any other laptop); disabling Norton HTTPS scanning (not our machine to
reconfigure); `verify=False` (rejected — silently accepts any certificate).

**Consequence.** The pipeline is cache-first and reproducible, and it runs identically on a machine without TLS
interception. The cost is that first-run download is an explicit step (`make city CITY=… --cache-only`) rather than a
lazy `/vsicurl/` read. Date: 2026-09-06.

---

## 1. Copernicus GLO-30 DEM (public AWS bucket `copernicus-dem-30m`)

- Bucket index (all tile names, one per line): `https://copernicus-dem-30m.s3.amazonaws.com/tileList.txt` —
  `HTTP/1.1 200`, `Content-Length: 1110900`, `Last-Modified: Mon, 09 May 2022 18:23:01 GMT`
  (cached: `docs/research/_raw/head_ledger_20260905.txt`; the file itself is
  `docs/research/_raw/copernicus_tileList.txt`, 26 450 tile names).
- Bucket readme (licence and naming): `https://copernicus-dem-30m.s3.amazonaws.com/readme.html` — `HTTP/1.1 200`
  (cached: `docs/research/_raw/copernicus_readme.html`).
- Registry entry: `https://registry.opendata.aws/copernicus-dem/` — `HTTP/1.1 200`. **No AWS credentials, no key**;
  the objects are anonymously readable over HTTPS.

**Key/URL pattern.** Object key is
`Copernicus_DSM_COG_10_<N|S>YY_00_<E|W>XXX_00_DEM/Copernicus_DSM_COG_10_<N|S>YY_00_<E|W>XXX_00_DEM.tif`, where
`YY`/`XXX` are the **south-west corner** of a 1°×1° tile, `10` is the 10-arc-second (≈30 m) product, and the file is a
Cloud-Optimized GeoTIFF of the DSM (surface, not bare earth — see §1.3). Full URL prefix:
`https://copernicus-dem-30m.s3.amazonaws.com/`.

### 1.1 Tiles covering the two AOIs — verified with `curl -sI` on **2026-09-06**

MUM-CENTRAL spans lat 18.995 → 19.135, so it straddles the N18 and N19 rows at E072. CHN-SOUTH spans
lat 12.96 → 13.05, so it straddles N12 and N13 at E080. Four tiles in total:

| AOI | Tile key | URL | Status | Content-Length | ETag |
|---|---|---|---|---|---|
| MUM-CENTRAL | `Copernicus_DSM_COG_10_N18_00_E072_00_DEM` | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N18_00_E072_00_DEM/Copernicus_DSM_COG_10_N18_00_E072_00_DEM.tif` | **200 OK** | 4 027 713 B (3.84 MiB) | `f0558bb78b8a969502767c6a1340c4a0` |
| MUM-CENTRAL | `Copernicus_DSM_COG_10_N19_00_E072_00_DEM` | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N19_00_E072_00_DEM/Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif` | **200 OK** | 13 336 459 B (12.72 MiB) | `853d8d53d59a6a618f3b8cc4157c319e` |
| CHN-SOUTH | `Copernicus_DSM_COG_10_N12_00_E080_00_DEM` | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N12_00_E080_00_DEM/Copernicus_DSM_COG_10_N12_00_E080_00_DEM.tif` | **200 OK** | 7 135 610 B (6.80 MiB) | `e004f64cca33d8fe5444831bdd79a039` |
| CHN-SOUTH | `Copernicus_DSM_COG_10_N13_00_E080_00_DEM` | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N13_00_E080_00_DEM/Copernicus_DSM_COG_10_N13_00_E080_00_DEM.tif` | **200 OK** | 12 362 785 B (11.79 MiB) | `952247d695de2d82c33cd0210b8ed9d4` |

All four `Last-Modified: Mon, 09 May 2022` (14:07:11, 14:07:42, 14:04:30, 14:04:55 GMT respectively),
`Content-Type: image/tiff`. Total download for both cities: **36.9 MB**. Byte-identical to the 2026-09-05 cache
(`docs/research/_raw/head_copernicus_dem.txt`) — same ETags, same lengths; re-verification this session in
`docs/research/_raw/head_verify_20260906c.txt`. The N18 and N12 tiles are small because they are mostly sea (Arabian
Sea / Bay of Bengal), which the COG compresses to almost nothing — that is expected, not a truncated file.

Note the tiles are **1° tiles, so each is far larger than the AOI**: `make city` mosaics the two tiles per city, then
clips to the AOI bbox and reprojects to the metric CRS (EPSG:32643 Mumbai, EPSG:32644 Chennai) at 30 m.

### 1.2 Licence

Copernicus DEM GLO-30 is free and open under the ESA/Copernicus licence for the global 30 m product, with an
attribution requirement. Cite as required by the bucket readme
(`https://copernicus-dem-30m.s3.amazonaws.com/readme.html`, cached at `docs/research/_raw/copernicus_readme.html`).
Attribution string to use on the map and in `make pack`: "Contains modified Copernicus DEM data (© ESA / Copernicus)".

### 1.3 Known limitation to label in the UI (SPEC.md §0 rule 6)

GLO-30 is a **DSM** (Digital Surface Model): it includes building and canopy tops. The hydro-conditioning in
SPEC.md §10.1 step 4 (burn buildings +5 m, carve road centrelines −0.15 m, keep underpasses as sinks, breach
culverts) is what turns it into a routable surface. The 30 m posting cannot resolve a 12 m carriageway or the exact
invert of a rail underpass — this is the DEM-accuracy limitation for `docs/SIMPLIFICATIONS.md` and the honest answer to
the judge question in SPEC.md §16 ("we claim pattern and timing, calibrate at chronic spots, budget LiDAR in the
pilot").

### 1.4 Fallback: NASADEM via OpenTopography

- Endpoint: `https://portal.opentopography.org/API/globaldem`
- MUM-CENTRAL: `?demtype=NASADEM&south=18.995&north=19.135&west=72.815&east=72.905&outputFormat=GTiff&API_Key=<OPENTOPO_KEY>`
- CHN-SOUTH: `?demtype=NASADEM&south=12.96&north=13.05&west=80.20&east=80.28&outputFormat=GTiff&API_Key=<key>`
- Other `demtype` values usable here: `SRTMGL1` (30 m), `SRTMGL3` (90 m), `AW3D30` (ALOS World 3D 30 m),
  `COP30` (the same Copernicus GLO-30, served pre-clipped — useful because OpenTopography does the clipping for you).
- **Verified 2026-09-06:** the MUM-CENTRAL URL **without** an API key returns `HTTP/1.1 401` (see
  `docs/research/_raw/head_verify_20260906c.txt`) — the endpoint is live and reachable, and the key is mandatory. A key
  is free on registration. The `.env` variable `OPENTOPO_KEY` already exists in SPEC.md §4.4.
- `https://portal.opentopography.org/apidocs/swagger.json` returns **404** (cached:
  `docs/research/_raw/opentopo_api.json` is the Tomcat 404 page, not a spec) — do not build against that path.
- **Decision:** Copernicus stays primary because it needs no key and no registration; OpenTopography/NASADEM is the
  documented fallback for SPEC.md §17 row 1. Last resort remains a committed 30 m clip of each AOI in the repo.

### 1.5 FABDEM — licence position

FABDEM (Forest And Buildings removed Copernicus DEM, Hawker & Neal, University of Bristol) would be scientifically
preferable to GLO-30 for urban flood routing because the building and canopy tops are already removed. **We do not use
it in the prototype.** FABDEM v1-2 is distributed under **CC BY-NC-SA 4.0**: the *non-commercial* clause makes it
unsuitable for a system pitched to a ministry as a deployable product, and the ShareAlike clause would propagate to the
derived hydro-conditioned rasters we ship in `make pack`. The bare-earth correction is instead approximated in-house by
the §10.1 step 4 conditioning. **NOT VERIFIED in this session:** the current FABDEM licence text was not re-fetched
here — confirm on the University of Bristol data repository before quoting the licence on a slide. Record the decision
in `docs/DECISIONS.md`; if the pilot obtains a licence that permits the use, FABDEM is a drop-in replacement for the
DEM step.

---

## 2. ESA WorldCover 10 m (v200, 2021)

- Data-access page: `https://esa-worldcover.org/en/data-access` — `HTTP/1.1 200`
  (cached: `docs/research/_raw/worldcover_data_access.html`).
- Bucket readme: `https://esa-worldcover.s3.eu-central-1.amazonaws.com/readme.html` — `HTTP/1.1 200`
  (cached: `docs/research/_raw/worldcover_readme.html`).
- Bucket listing used: `docs/research/_raw/worldcover_list_N1.xml`.
- **No key, no registration**; anonymous HTTPS on a public S3 bucket in `eu-central-1`.

**URL pattern.**
`https://esa-worldcover.s3.eu-central-1.amazonaws.com/<version>/<year>/map/ESA_WorldCover_10m_<year>_<version>_<TILE>_Map.tif`
with `version/year` either `v200/2021` (current — use this) or `v100/2020`.

**Tile grid — this is the trap.** WorldCover tiles are **3° × 3°**, not 1° like the DEM, and `TILE` is the tile's
**south-west corner snapped down to a multiple of 3**. So:

| AOI | AOI SW corner | Snapped SW corner | Tile id | Tile covers |
|---|---|---|---|---|
| MUM-CENTRAL (18.995–19.135 N, 72.815–72.905 E) | N18 E72 | N18, E072 (both already multiples of 3) | **`N18E072`** | 18–21 N, 72–75 E — whole Mumbai AOI in one tile |
| CHN-SOUTH (12.96–13.05 N, 80.20–80.28 E) | N12 E80 | N12, **E078** (80 snaps down to 78) | **`N12E078`** | 12–15 N, 78–81 E — whole Chennai AOI in one tile |

**Verified with `curl -sI` on 2026-09-06:**

| Tile | URL | Status | Content-Length |
|---|---|---|---|
| N18E072 (Mumbai) | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N18E072_Map.tif` | **200 OK** | 125 766 199 B (119.9 MiB) |
| N12E078 (Chennai) | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N12E078_Map.tif` | **200 OK** | 119 065 806 B (113.6 MiB) |

Both `Last-Modified: Wed, 26 Oct 2022`, `Content-Type: binary/octet-stream`. The 3° rule is confirmed directly by a
negative: the naive guess `N12E081` (snapping 80 *up* instead of down) returns **404 Not Found** for both v200 and
v100 — cached in `docs/research/_raw/head_worldcover.txt`. The v100/2020 equivalents exist (`N18E072`
146 312 534 B, `N12E078` 125 717 901 B) but v200/2021 is the one to use.

The `_InputQuality.tif` sidecars are **not** in the `map/` prefix — both 404 (`head_ledger_20260905.txt`). The
prototype does not use them.

**Download cost:** 234 MB for both cities, one tile each. Clip to the AOI immediately after download; do not keep the
3° tile in `city/<city>/`.

**Use in the pipeline (SPEC.md §10.1 step 3).** WorldCover class codes map to imperviousness `I` and to the SCS curve
number `CN`: class 50 (built-up) → high `I`; 10/20/30 (tree cover / shrubland / grassland) → low; 80 (permanent water)
→ water; 40 (cropland); 60 (bare / sparse vegetation); 90 (herbaceous wetland). The building-footprint burn from OSM
refines class 50 inside the AOI. Licence: CC BY 4.0 — attribute "© ESA WorldCover project 2021 / Contains modified
Copernicus Sentinel data".

---

## 3. OpenStreetMap via OSMnx 2.x / Overpass

### 3.1 Endpoints and their state

| URL | Purpose | Result (date) |
|---|---|---|
| `https://overpass-api.de/api/interpreter` | main query endpoint (OSMnx default) | used for every `overpass_*.json` in the cache; **`https://overpass-api.de/api/status` 200 OK, re-verified 2026-09-06** |
| `https://overpass.kumi.systems/api/interpreter` | mirror | tried 2026-09-05 and it **failed under load**: `runtime error: open64: 0 Success /osm3s_osm_base Dispatcher_Client::request_read_and_idx::timeout. The server is probably too busy to handle your request.` (cached: `docs/research/_raw/overpass_mumbai_count_kumi.json`) — keep as fallback, do not make it primary |
| `https://nominatim.openstreetmap.org/search` | geocoding for the hotspot/asset registers | 60+ successful lookups cached as `docs/research/_raw/nominatim_*.json`; **`https://nominatim.openstreetmap.org/status` 200 OK, re-verified 2026-09-06** |

### 3.2 Rate-limit guidance (read from the OSMnx source we cached, not from memory)

`docs/research/_raw/osmnx_overpass.py` is the vendored `osmnx/_overpass.py` and `docs/research/_raw/osmnx_settings.py`
the vendored `osmnx/settings.py`. They show exactly how OSMnx behaves; these are the settings `services/city` must
respect:

- `settings.overpass_rate_limit = True` (default) makes OSMnx call `<overpass_url>/status` **before every request** and
  sleep until a slot is free (`_get_overpass_pause`). Leave it on. Only turn it off against a self-hosted instance.
- On HTTP **429** or **504** OSMnx sleeps `55 s` and retries recursively (`error_pause = 55` in `_overpass_request`).
- `settings.requests_timeout = 180` seconds is both the HTTP timeout and the `[timeout:…]` value sent to Overpass.
- `settings.max_query_area_size = 50 000 × 50 000 m²` — any polygon larger is subdivided into several requests
  (`_make_overpass_polygon_coord_strs`). Both AOIs (≈9.5 × 15.5 km and ≈8.7 × 10 km) are well under this, so each is
  **one request per filter**.
- `settings.use_cache = True` with `settings.cache_folder` — point it at `city/cache/<city>/osmnx/` so `make city`
  re-runs offline. `settings.cache_only_mode = True` is the way to pre-cache Chennai for the finale (SPEC.md §10.4):
  it downloads and raises `CacheOnlyModeInterrupt` without building the graph.
- Nominatim: `settings.nominatim_url = "https://nominatim.openstreetmap.org/"`, one request per second, and set
  `settings.http_user_agent` / `settings.http_referer` to something identifying VARUNA — the research cache used
  UA `VARUNA-SIH2026-research/0.1`. Nominatim's usage policy forbids unattended bulk geocoding; ours is a one-off
  register build of ~60 points, already cached, and must not be re-run at demo time.
- **ADR-0006 applies here:** OSMnx goes through `requests`, so `truststore.inject_into_ssl()` must run *before*
  `import osmnx`. Otherwise every Overpass and Nominatim call fails with `unable to get local issuer certificate` on
  this machine. `settings.requests_kwargs` exists as an escape hatch (`{"verify": …}`) but must not be used to disable
  verification.
- Historic snapshots are possible if we ever want the road network *as of* the replay day:
  `settings.overpass_settings = '[out:json][timeout:180][date:"2019-07-02T12:00:00Z"]'`. Not used in P0 — noted
  because a judge may ask whether the 2019 replay uses 2026 roads. **It does**; that is a labelled simplification.

### 3.3 Element counts actually observed inside the AOIs

Query (`docs/research/_overpass_mumbai_count.ql`):

```
[out:json][timeout:60];
( way["highway"](18.995,72.815,19.135,72.905); );
out count;
```

| AOI | `highway=*` ways | Overpass base timestamp | Evidence |
|---|---|---|---|
| MUM-CENTRAL | **24 710** | `2026-09-05T08:38:36Z` | `docs/research/_raw/overpass_mumbai_count.json` |
| CHN-SOUTH | **13 474** | `2026-09-05T08:36:36Z` | `docs/research/_raw/overpass_chennai_count.json` |

This is the raw way count *before* the `drive` filter and *before* splitting at intersections. SPEC.md §10.1 step 5
splits ways at intersections, so the segment table will be larger than the way count — budget on the order of 30–60 k
segments for Mumbai. That matters for SPEC.md §14 (map ≥ 55 fps with ~30 k segments) and for the §17 fallback "cap
segments to class ≥ tertiary at z < 13".

A points-of-interest query (`docs/research/_raw/overpass_pois.ql`, response `overpass_pois.json`, summary
`overpass_pois_summary.txt`, base timestamp `2026-09-04T15:32:36Z`) returned **593 elements** inside MUM-CENTRAL:

`hospital 373 · station 76 · other 63 · government 43 · pumping_station 13 · fire_station 11 · tunnel 5 · school 4 ·
parking 2 · library 1 · wastewater_plant 1 · cinema 1`

The hospital count is inflated by clinics and by way+node duplicates of the same facility — de-duplicate on name and
100 m proximity in `services/city`. Thirteen `man_made=pumping_station` elements operated by MCGM are present, e.g.
`node/14125092878 BMC Mahim Sewage Water Pumping Station 19.02520 72.84516` and
`way/220302517 BMC Influent Sewage Water Pumping Station 19.04659 72.83735`. **These are sewage pumping stations, not
the storm-water pumping stations of the BRIMSTOWAD programme** — do not relabel them in the UI.

Chennai waterways query (`docs/research/_raw/overpass_chennai_waterways.json`, base timestamp `2026-09-05T08:45:51Z`)
returned **108 elements**. Named canals and drains in or adjacent to CHN-SOUTH: Adyar River (`way/178105900`,
13.015169 80.220824), Mambalam Canal (`way/23161547`, 13.034904 80.238327 and `way/214162276`, 13.025767 80.232143),
Mambalam Drain (`way/214162275`, 13.030474 80.230746), Veerangal Canal (`way/328358961`, 12.978328 80.196478),
Raj Bhavan Canal (`way/27709553` 12.995318 80.217386, plus `way/102580554/558/561/579` around 12.988–12.995 N),
Reddikuppam Canal (`way/25460072`, 13.026496 80.226226), Jafferkhanpet Canal (`way/27709396`, 13.026963 80.212873),
Trustpuram Canal (`way/27709103`, 13.060434 80.226659), Adyar Creek (`way/111749073`, 13.018727 80.269511) and
unnamed/`Stormwater Drain` ways in Velachery (`way/384187222`, 12.971071 80.221949; `way/835785681/682`, 12.97086 N).
These are the trunk lines the synthetic drain graph (SPEC.md §10.1 step 7) must snap Chennai's outfalls to.

### 3.4 Tag filters SPEC.md §10.1 step 2 needs

Use `osmnx.graph_from_bbox(..., network_type="drive_service")` for the routable graph, plus
`osmnx.features_from_bbox(bbox, tags=…)` for everything else:

```python
# 1. routable road graph (drivable + service, per SPEC.md 10.1 step 2)
G = ox.graph_from_bbox(
    bbox=(west, south, east, north), network_type="drive_service", simplify=False, retain_all=False
)
# useful_tags_way already carries: access, area, bridge, est_width, highway, junction,
# landuse, lanes, maxspeed, name, oneway, ref, service, tunnel, width
ox.settings.useful_tags_way += ["layer", "covered", "surface"]  # needed for underpass detection

# 2. feature layers
TAGS = {
    "buildings": {"building": True},
    "waterways": {"waterway": ["drain", "canal", "stream", "river", "ditch"]},
    "culverts": {"tunnel": ["culvert"]},
    "bridges": {"bridge": True},
    "stations": {"railway": ["station", "halt"], "public_transport": ["station"]},
    "hospitals": {"amenity": ["hospital", "clinic"]},
    "fire": {"amenity": ["fire_station"]},
    "shelters": {"amenity": ["school", "community_centre"]},  # proxies - label as proxies in the UI
    "pumping": {"man_made": ["pumping_station"]},
    "landuse": {"landuse": True},
    "wards": {"boundary": "administrative"},  # admin_level 9/10/11
}
```

Underpasses and subways (the physical sinks that drown first — Khar, Milan, Andheri, Sion) are found with
`tunnel=yes|building_passage|underpass` **or** `layer < 0`, intersected with the hotspot register. The cached POI query
already picked up `way/39532540 Milan Subway (19.09045 72.84283)` and `way/632206820 Sion Pedestrian Subway
(19.04438 72.86447)` this way. Ward boundaries:
`relation|way["boundary"="administrative"]["admin_level"~"^(9|10|11)$"]` (query
`docs/research/_raw/overpass_wards.ql`, response `overpass_wards.json`).

Licence: ODbL. The attribution "© OpenStreetMap contributors" is mandatory on every map view.

---

## 4. IMD radar (mausam.imd.gov.in) and INSAT-3DS on MOSDAC

### 4.1 What IMD publishes publicly — verified 2026-09-06

The public product is a **rendered GIF image with a burnt-in colour legend**, not a data volume. Site codes are
**lowercase** in the image path.

| Product | URL | Status 2026-09-06 | Size |
|---|---|---|---|
| Mumbai (Veravali) max-Z / CAZ | `https://mausam.imd.gov.in/Radar/caz_vrv.gif` | **200 OK**, `Last-Modified: Sun, 06 Sep 2026 09:40:01 GMT` | 1 260 008 B |
| Mumbai SRI (surface rain intensity) | `https://mausam.imd.gov.in/Radar/sri_vrv.gif` | **200 OK**, `Last-Modified: 09:40:03 GMT` | 1 555 704 B |
| Mumbai animation loop (MAXZ) | `https://mausam.imd.gov.in/Radar/animation/Converted/VRV_MAXZ.gif` | **200 OK**, `Last-Modified: 09:25:32 GMT` | 24 974 946 B |
| **Chennai** CAZ | `https://mausam.imd.gov.in/Radar/caz_cni.gif` | **200 OK** | — |
| Per-site product page | `https://mausam.imd.gov.in/responsive/radar_animation.php?id=VRV` | **200 OK** | — |

Other product prefixes seen in the cached Mumbai site page (`docs/research/_imd_radar_mumbai.html`, from
`https://mausam.imd.gov.in/responsive/radar.php`): `caz_` (max reflectivity), `sri_` (surface rain intensity), `ppi_`,
`ppz_`, `ppv_`, `pac_` (accumulation), `vp2_`; and the animation variants `VRV_MAXZ.gif` / `VRV_SRI.gif` under
`/Radar/animation/Converted/`.

**Uppercase site codes 404.** `caz_VRV.gif`, `caz_CHN.gif`, `caz_che.gif` and `caz_mdr.gif` all returned
`404 Not Found` this session. Mumbai is `vrv` (Veravali); Chennai is `cni`. Both confirmed by HEAD, logged in
`docs/research/_raw/head_verify_20260906c.txt`.

**Cadence — partially established, do not overstate.** Two independent HEAD samples 25 hours apart both show
`Last-Modified` at **HH:40:0x**: `2026-09-05T08:40:01Z` (observed 08:44:29Z, cached
`docs/research/_raw/head_imd_radar_cadence.txt`, together with `sri`/`pac`/`ppi` at 08:40:03Z) and
`2026-09-06T09:40:01Z` (observed 09:42:44Z, this session). That proves the image is refreshed and shows how stale it is
when fetched, but **two samples do not establish a cadence**. The commonly quoted IMD DWR volume-scan interval is
10 minutes; the cached `https://mausam.imd.gov.in/responsive/dwr_network.php` page
(`docs/research/_raw/imd_dwr_network.html`) is JavaScript-driven and carries **no cadence text at all** — so the
10-minute figure is **NOT FOUND** in any source we hold. What would settle it: poll `caz_vrv.gif` once a minute for an
hour and record the distinct `Last-Modified` values, or obtain the DWR product specification from the MoES SPOC. Until
then the storm designer's 10-minute radar frame interval (SPEC.md §10.2) is **an assumption and must be labelled as
one in the bundle manifest**.

### 4.2 Consequence for P2.9 (IMD radar image decoder)

Decoding these images means: crop the plot area, look up each pixel against the burnt-in legend to recover a dBZ class
(IMD renders in roughly 5 dBZ classes — which is why SPEC.md §10.2 quantises the synthetic radar to 5 dBZ), and
georeference from the radar site position plus the range rings. There is **no world file and no embedded geotransform**;
the georeferencing has to be fitted from the range rings and the coastline. Cached artefacts to develop against:
`docs/research/_raw/radar_caz_vrv_20260905T0845Z.gif`, `radar_caz_VRV_anim_20260905T0845Z.gif`,
`docs/research/_raw/caz_vrv_20260905.gif`, and the extracted frames `caz_mum_frame0.png`, `vrv_f0_head.png`,
`vrv_hdr2.png`. This is why P2.9 is **P1, not P0**, and why the P0 replay radar is a storm-designer reconstruction.

**Archive access is the real blocker.** Nothing on `mausam.imd.gov.in` serves a **historical** frame for
2 July 2019 — the URLs above are "latest" only, overwritten every cycle. **NOT FOUND: any public archive of IMD DWR
frames for 2019.** What would settle it: a data request to IMD/MoES through the SIH SPOC (`https://dsp.imdpune.gov.in/`
is the IMD Data Supply Portal — a registered/paid request channel, not an open endpoint). This is exactly the honest
answer scripted in SPEC.md §16 ("the replay radar is a storm-designer reconstruction calibrated to public gauge
totals, because raw IMD volumes need a MoES request").

### 4.3 INSAT-3DS on MOSDAC

- `https://www.mosdac.gov.in/` — **200 OK** (verified 2026-09-06).
- `https://www.mosdac.gov.in/insat-3ds` — **200 OK** (mission page).
- `https://mosdac.gov.in/data/` issues a cross-host redirect to `https://www.mosdac.gov.in/data/` (follow with `-L`).
- **Registration is required.** MOSDAC download is behind a free ISRO/SAC user account; there is no anonymous bucket
  equivalent to the Copernicus or WorldCover buckets. The Hydro-Estimator / IMR (INSAT multi-spectral rainfall)
  products are the ones relevant to VARUNA's degraded mode ("no radar → gauge/satellite-only Sky with wider spread and
  a banner", SPEC.md §11.11).
- **NOT VERIFIED here:** the exact product identifier, its cadence, and the download URL template behind the login were
  not confirmed in this session. Do not put a MOSDAC product name or cadence on a slide until someone with an account
  has fetched one file and recorded its path.
- Consequence for P0: **satellite ingest is not built.** The degraded-mode banner in the console is driven by the
  replay bundle, and the satellite path stays an interface in `services/sky` with a "coming in pilot" label, per the
  SPEC.md §17 rule that no control is dead.

---

## 5. Chennai (CHN-SOUTH): hotspots, gauges, outfalls, tide

Deliverable: `docs/research/chennai_hotspots.draft.geojson` (same field set as the Mumbai register).

### 5.1 Method and honesty rules applied

Two different things are sourced separately, and the GeoJSON keeps them apart:

1. **The coordinate.** Every point is a Nominatim geocode of a named place and carries the resolved OSM element id
   (`osm_id`) plus the endpoint used. Logs: `docs/research/_raw/nominatim_chennai.txt` and `nominatim_chennai2.txt`;
   the query lists are `nominatim_chennai_queries.txt` and `nominatim_chennai_queries2.txt`. Endpoint:
   `https://nominatim.openstreetmap.org/search?q=<query>&format=jsonv2&limit=3` (status `200 OK`, re-verified
   2026-09-06). These coordinates are **verified**.
2. **The flooding claim.** A place is marked `flood_source_verified: true` only when a dated public page names it as
   waterlogged. Where no such page was retrieved, the feature carries `"flood_source_verified": false` and
   `"note": "UNSOURCED chronic-spot claim - not to be displayed as fact"`. Per SPEC.md §0 rule 7 those features must
   be filtered out of any UI that presents them as ground truth; they exist in the draft only as candidate locations
   for the onboarding demo, whose first forecast is already labelled "uncalibrated" (SPEC.md §7.9).

Several Nominatim lookups **failed outright** and have no coordinate at all: `Kathipara Junction, Guindy` and
`Kathipara, Guindy` (NO RESULT / HTTP error), `Pondy Bazaar, T. Nagar` (NO RESULT), `Vijayanagar Bus Stand, Velachery`
(NO RESULT), `Adyar River mouth` (NO RESULT), and `Thyagaraya Road`, `Guindy`, `Foreshore Estate`, `Ullagaram`,
`Puzhuthivakkam` (HTTP errors). Kathipara and Pondy Bazaar are two of the most-cited Chennai flood points and their
absence is a real gap — see §5.5.

### 5.2 Chronic waterlogging spots inside CHN-SOUTH — coordinates verified

| Name | lat | lon | OSM element | In AOI | Flood claim sourced? |
|---|---|---|---|---|---|
| Velachery (suburb) | 12.9801655 | 80.2228506 | `way/422418168` | yes | **yes** — DailyO 2023-12-06 |
| Velachery Main Road | 12.9842045 | 80.2227619 | `way/27480309` | yes | no (coordinate only) |
| Velachery Bypass Road | 12.9828040 | 80.2179443 | `way/721922454` | yes | no |
| Velachery Railway Station (bus-stop node) | 12.9712086 | 80.2192719 | `node/9402008106` | yes | no |
| Baby Nagar, Velachery | 12.9803651 | 80.2291946 | `node/4294644648` | yes | no |
| Dhandeeswaram, Velachery | 12.9826389 | 80.2241880 | `node/4294644649` | yes | no |
| AGS Colony, Velachery | 12.9782313 | 80.2120528 | `way/318725675` | yes | no |
| Taramani Link Road | 12.9817821 | 80.2309161 | `way/422927582` | yes | no |
| Taramani | 12.9849067 | 80.2404069 | `node/325198962` | yes | no |
| Adambakkam | 12.9822215 | 80.2091210 | `node/256202991` | yes | weak — Deccan Herald snippet only |
| Madipakkam | 12.9611348 | 80.2001292 | `node/278544356` | **edge** (lon 80.2001 ≈ AOI west edge) | no |
| Ram Nagar, Madipakkam | 12.9652457 | 80.2069875 | `node/442506948` | yes | no |
| Saidapet (suburb) | 13.0208170 | 80.2239536 | `node/247689106` | yes | no |
| Saidapet Bridge, Anna Salai | 13.0192595 | 80.2242262 | `node/306551393` | yes | no |
| Jafferkhanpet | 13.0299401 | 80.2056195 | `node/290741806` | yes | no |
| Ekkattuthangal | 13.0242453 | 80.2065506 | `node/364280369` | yes | no |
| Guindy Railway Station (bus-stop node) | 13.0080474 | 80.2131366 | `node/9920341495` | yes | no |
| North Usman Road, T. Nagar | 13.0460744 | 80.2328040 | `way/843860857` | yes | **yes** — Gulf News 2021-12-31 |
| Ashok Nagar | 13.0359275 | 80.2145697 | `node/247689098` | yes | no |
| West Mambalam | 13.0435900 | 80.2208090 | `node/2241055790` | yes | weak — Deccan Herald snippet only |
| Kotturpuram | 13.0193703 | 80.2435512 | `node/249497870` | yes | no |
| Nandanam (railway station) | 13.0317092 | 80.2411957 | `node/6285857243` | yes | no |
| Indira Nagar, Adyar (railway station) | 12.9960623 | 80.2497160 | `node/5311755238` | yes | no |
| Kasturba Nagar, Adyar | 13.0038682 | 80.2485192 | `node/1387068423` | yes | no |
| Lattice Bridge Road / Dr Muthulakshmi Salai, Adyar | 13.0041690 | 80.2569733 | `way/28278656` | yes | no |
| Sastri Road, Baby Nagar (Adyar) | 12.9778151 | 80.2276789 | `way/27370267` | yes | no |
| Velachery Lake | 12.9882955 | 80.2104711 | `way/25504265` | yes | n/a — water body, not a road spot |

Geocoded but **outside** the AOI and therefore excluded from the GeoJSON: Thiruvanmiyur (12.9859, 80.2644),
Besant Nagar (12.9997, 80.2682), Mylapore (13.0316, 80.2700), Alandur (13.0028, 80.1719). Also excluded: the Adyar
(`relation/7880736`, 13.0065 80.2578) and Perungudi (`relation/7879836`, 12.9710 80.2418) *zone* relations — those are
administrative centroids, not street points.

**Sourced flooding claims — the only ones that may be displayed as fact:**

- **Velachery.** "Velachery, covering an area of approximately 6.82 square kilometres and accommodating a population of
  143,991, faces recurrent flooding during the monsoons." DailyO, 6 December 2023 —
  `source_url: https://www.dailyo.in/news/why-does-velachery-in-chennai-flood-so-easily-every-monsoon-42820`
  (cached: `docs/research/_raw/pages/chn_dailyo_velachery.txt`, fetched 2026-09-06, HTTP 200).
- **T. Nagar / Usman Road.** "Heavy rain has caused water logging in Chennai which caused inconvenience to traffic
  movement in Jemini bridge and Valluvar Kottam, Thyagaraya Nagar (T. Nagar) and Usman road of the city." Gulf News,
  31 December 2021 —
  `source_url: https://gulfnews.com/world/asia/india/3-killed-as-rain-batters-tamil-nadu-leaves-parts-of-chennai-waterlogged-1.84684314`
  (cached: `docs/research/_raw/pages/chn_gulfnews_tn_rain.txt`, fetched 2026-09-06, HTTP 200). The same page carries a
  *real* pump quote from GCC Commissioner Gagandeep Singh Bedi — "More than 145 pumps are operating to clear the
  waterlogging caused by heavy rainfall in the city" — useful for the pump-dispatch narrative, unlike our synthetic
  inventory.
- **Adambakkam / West Mambalam (weak).** Named among waterlogged areas during Cyclone Nivar —
  `source_url: https://www.deccanherald.com/india/cyclone-nivar-heavy-rains-lead-to-waterlogging-across-chennai-919478.html`.
  **Caveat: this URL returned HTTP 403 to our fetcher in this session**; the area names come from a search-result
  snippet, not from the page body. Marked `flood_source_verified: false` with `source_strength: "weak"`. Re-fetch and
  quote the body before it appears in any UI.

### 5.3 Outfalls: Adyar and Buckingham Canal

From `docs/research/_raw/overpass_chennai_waterways.json` (Overpass base timestamp `2026-09-05T08:45:51Z`):

- **Adyar River** — `way/178105900`, way centroid **13.015169 N, 80.220824 E**, `waterway=river`. Runs west→east across
  the northern half of CHN-SOUTH and is the receiving water for the T. Nagar / Saidapet / Kotturpuram side. Mambalam
  Canal, Mambalam Drain, Reddikuppam Canal and Jafferkhanpet Canal (ids in §3.3) all discharge to it — these are the
  natural trunk outfalls for the synthetic drain graph.
- **Adyar Creek** — `way/111749073`, **13.018727 N, 80.269511 E**, `waterway=stream`; inside the AOI (lon < 80.28).
  This is the tidal reach and is the right place to put Chennai's **tide-locked outfall**, mirroring the Mahim/Mithi
  tide-lock in the Mumbai demo.
- **Buckingham Canal** — the only feature carrying that name in the response is `way/731551448` "Buckingham Kaalvaai"
  at **12.6084 N, 80.0977 E**, roughly 40 km south of the AOI, so it is **not usable as the AOI outfall**. Inside the
  AOI the canal alignment is present but under other names or unnamed: `way/85857111` (13.029584 80.241216),
  `way/650607195` (13.024558 80.244393) and `way/214148762` (13.034191 80.243491), all `waterway=drain` on the
  north–south line just west of the coast road; plus the park polygon `way/1130591112` "Buckingham Canal Green"
  (12.985419 80.252294) which marks the alignment but is `leisure=park`, not a waterway.
  **NOT FOUND: a single OSM way tagged as the Buckingham Canal waterway inside CHN-SOUTH.** What would settle it: an
  Overpass query `way["name"~"Buckingham",i](12.96,80.20,13.05,80.28)` plus a `waterway=canal` relation lookup — one
  query, not run here for time. Until then the Chennai city config declares the tidal boundary on the **Adyar Creek**
  reach and labels the Buckingham Canal alignment as inferred.
- **Adyar river mouth** — Nominatim returned **NO RESULT** for "Adyar River mouth, Chennai"
  (`docs/research/_raw/nominatim_chennai.txt`). It is written into the GeoJSON as an outfall *candidate* with
  `"coordinate_method": "NOT GEOCODED"` and a null geometry note, never as a verified point.

### 5.4 GCC rain gauges and the Chennai tide

- **Greater Chennai Corporation rain gauges: NOT FOUND as a public machine-readable feed.**
  `https://www.chennaicorporation.gov.in/gcc/` is reachable (**HTTP/1.1 200**, verified 2026-09-06), but no station
  list or rainfall API endpoint equivalent to Mumbai's MCGM disaster-management API (cached for Mumbai as
  `docs/research/_raw/dm_api_reports_getWeatherStationSuburbZoneWardFromStationType.json`, reached from
  `docs/research/_raw/mcgm_rainfall.html` / `mcgm_main.js`) was located in this session. What would settle it: walk the
  GCC site's JS bundle for an XHR endpoint the way `mcgm_main.js` was walked for Mumbai, or use the Tamil Nadu State
  Disaster Management Authority / Water Resources Department daily rainfall bulletins.
  **Consequence:** the Chennai bundle `CHN-IDF-25yr` is a **design storm**, not a reconstruction, so it needs no gauge
  network to remain honest — the onboarding wizard's own copy already reads "First forecast, uncalibrated"
  (SPEC.md §7.9). IMD's Chennai station observations (Nungambakkam, Meenambakkam) are reachable through the same
  `mausam.imd.gov.in` "extreme weather events" pages used for Mumbai (the Mumbai ones are cached as
  `imd_extreme_43003.html` for Santacruz and `imd_extreme_43057_m7.html`), but **the Chennai station ids were not
  resolved in this session** — do not quote a Chennai station total until they are.
- **Chennai tide: NOT FOUND as an open, downloadable series.** `https://incois.gov.in/portal/datainfo/drf.jsp`
  returned **404** (verified 2026-09-06). The authoritative predictions are the Survey of India / INCOIS tide tables
  for **Chennai Port**, which are not served as an open time series. **Consequence:** per SPEC.md §10.2 the Chennai
  `tide.csv` must be written with `source: "illustrative"` and the UI must say so. The same rule applies to the Mumbai
  tide unless a table is obtained.

### 5.5 Named gaps in the Chennai register

- **Kathipara junction (Guindy)** — Nominatim returned NO RESULT for "Kathipara Junction, Guindy, Chennai" and an HTTP
  error for "Kathipara, Guindy, Chennai". It is the largest cloverleaf interchange in the AOI's west and a standard
  flood point. **Missing coordinate.** Settle with `nwr["name"~"Kathipara",i](12.96,80.20,13.05,80.28)` on Overpass.
- **Pondy Bazaar / Thyagaraya Road (T. Nagar)** — both lookups failed; only North Usman Road resolved. The Gulf News
  quote names Usman Road, so T. Nagar is not unrepresented, but the Pondy Bazaar point is missing.
- **Zone / ward polygons** — no Chennai equivalent of the Mumbai `overpass_wards.ql` query was run. GCC zones appear in
  the Nominatim `display_name` strings (Zone 13 Adyar, Zone 10 Kodambakkam, Zone 14 Perungudi, Zone 9 Teynampet) and as
  `boundary=administrative` relations (`relation/7880736` Zone 13 Adyar, `relation/7879836` Zone 14 Perungudi), so one
  Overpass query would produce them.
- **No official GCC "vulnerable spots" list was located.** A web search for one returned only news coverage of
  individual events, not a corporation publication. Everything marked "no" in §5.2 is therefore a *plausible* chronic
  spot from local knowledge and news, and is flagged unsourced in the GeoJSON.

---

## 6. IDF / short-duration rainfall for the design storms

**Status: NOT FOUND.** No published, citable intensity–duration–frequency value for Mumbai or Chennai was obtained in
this session, and none exists in the cache.

What was tried, and why each failed:

- `docs/research/_raw/pages/arxiv_2306.09770.pdf` / `.txt` — Tripathy, Chaudhuri, Murtugudde, Mharte, Parmar, Pinto,
  Zope, Dixit and Ghosh, *Analysis of Mumbai Floods in recent Years with Crowdsourced Data* (IIT Bombay;
  `https://arxiv.org/abs/2306.09770`). Grepping the extracted text for `intensity-duration`, `IDF`, `mm/h`,
  `return period`, `25-year`, `design storm` and `CPHEEO` produced **zero hits**. The paper is about extracting flood
  hotspots from Twitter and validating them against HAND — valuable for the hotspot and ground-truth work, but it is
  **not** an IDF source.
- IMD "extreme weather events" pages give **24-hour** station maxima only. For Santacruz (station 43003), September:
  all-time 24-h record "318.2 (23-09-1981)", monthly total record "1115.7 (2019)"
  (cached: `docs/research/_raw/imd_extreme_43003.html`). A 24-hour depth is **not** an IDF curve and cannot be
  converted to a 25-year 1-hour intensity without an assumed depth–duration ratio, which would be inventing a number.
- `https://www.civil.iitb.ac.in/~kgupta/drainagedesign.pdf` (IIT Bombay, "Return Period and Rainfall Intensity for
  Drainage Design in India") — fetched 2026-09-06, 4.2 MB, but it is a **scanned image PDF** with no extractable text.
  Saved locally by the fetcher; would need OCR.
- `https://cpheeo.gov.in/cms/manual-on-storm-water-drainage-systems---2019.php` — the CPHEEO *Manual on Storm Water
  Drainage Systems, 2019* is the authoritative Indian design document and is the right citation, but the host returned
  no status line to `curl -sI` within the timeout in this session. **The manual was not read and no number from it is
  quoted here.** Secondary web results asserting "Mumbai 1-hour design intensity ~75–100 mm/h at a 5-year return
  period" and "CPHEEO recommends 100 mm/h" were seen but are blog-level, not primary, and are deliberately **not** used
  as facts.

**The one design intensity we did not have to guess.** SPEC.md §10.1 already fixes the drain-*sizing* design
intensity at **25 mm/h for legacy corridors and 50 mm/h for upgraded corridors** — the widely quoted BRIMSTOWAD design
basis for Mumbai's storm-water system. That number sizes the *pipes*. It is not the *storm*.

**The assumption the storm designer must label.** Until a primary IDF source is in hand, `MUM-IDF-25yr` and
`CHN-IDF-25yr` must be generated from an explicitly stated assumption, and `bundles/<ID>/manifest.json` must carry it
verbatim:

```json
"design_storm_basis": "ASSUMED - no primary IDF source obtained. Chicago hyetograph, 3-hour duration, r = 0.4 (peak at 40% of duration), peak 1-hour intensity assumed 100 mm/h for a nominal 25-year return period, 3-hour total assumed 180 mm. NOT derived from a published IDF curve for this city. Settle by digitising the IDF relation in the CPHEEO Manual on Storm Water Drainage Systems (2019), or by fitting a Gumbel/GEV IDF to IMD short-duration (15/30/60 min) station series for Santacruz (43003)/Colaba and Nungambakkam/Meenambakkam.",
"label": "Design storm - synthetic, intensity not calibrated to a published IDF curve"
```

That label must appear in the UI wherever a design-storm run is displayed (SPEC.md §0 rule 6), including the Chennai
onboarding finish card. What would settle it, in priority order: (1) the CPHEEO 2019 manual's return-period and IDF
tables; (2) IMD's short-duration rainfall intensity–duration–frequency atlas; (3) a peer-reviewed IDF fit for Mumbai or
Chennai with the Gumbel parameters printed. Any of the three replaces the assumption with a citation.

---

## 7. Reachability ledger — every URL checked with `curl -sI` in this session (2026-09-06)

Raw output: `docs/research/_raw/head_verify_20260906c.txt`. Earlier ledgers: `head_ledger_20260905.txt`,
`head_copernicus_dem.txt`, `head_worldcover.txt`, `head_imd_radar_cadence.txt`, `head_verify_20260906.txt`,
`head_verify_20260906b.txt`.

| # | URL | Status | Note |
|---|---|---|---|
| 1 | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N18_00_E072_00_DEM/Copernicus_DSM_COG_10_N18_00_E072_00_DEM.tif` | 200 | 4 027 713 B — Mumbai DEM, south tile |
| 2 | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N19_00_E072_00_DEM/Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif` | 200 | 13 336 459 B — Mumbai DEM, north tile |
| 3 | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N12_00_E080_00_DEM/Copernicus_DSM_COG_10_N12_00_E080_00_DEM.tif` | 200 | 7 135 610 B — Chennai DEM, south tile |
| 4 | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N13_00_E080_00_DEM/Copernicus_DSM_COG_10_N13_00_E080_00_DEM.tif` | 200 | 12 362 785 B — Chennai DEM, north tile |
| 5 | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N18E072_Map.tif` | 200 | 125 766 199 B — Mumbai land cover |
| 6 | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N12E078_Map.tif` | 200 | 119 065 806 B — Chennai land cover (3° snap-down confirmed) |
| 7 | `https://mausam.imd.gov.in/Radar/caz_vrv.gif` | 200 | 1 260 008 B — Mumbai CAZ, `Last-Modified 2026-09-06T09:40:01Z` |
| 8 | `https://mausam.imd.gov.in/Radar/sri_vrv.gif` | 200 | 1 555 704 B — Mumbai surface rain intensity |
| 9 | `https://mausam.imd.gov.in/Radar/animation/Converted/VRV_MAXZ.gif` | 200 | 24 974 946 B — Mumbai animation loop |
| 10 | `https://mausam.imd.gov.in/Radar/caz_cni.gif` | 200 | Chennai CAZ — site code is `cni` |
| 11 | `https://mausam.imd.gov.in/responsive/radar_animation.php?id=VRV` | 200 | per-site product page |
| 12 | `https://www.mosdac.gov.in/` | 200 | INSAT-3DS host; download behind registration |
| 13 | `https://www.mosdac.gov.in/insat-3ds` | 200 | mission page |
| 14 | `https://overpass-api.de/api/status` | 200 | primary Overpass endpoint live |
| 15 | `https://nominatim.openstreetmap.org/status` | 200 | geocoder live |
| 16 | `https://www.chennaicorporation.gov.in/gcc/` | 200 | GCC portal reachable; no gauge API found |
| 17 | `https://www.dailyo.in/news/why-does-velachery-in-chennai-flood-so-easily-every-monsoon-42820` | 200 | Velachery flooding source, cached |
| 18 | `https://gulfnews.com/world/asia/india/3-killed-as-rain-batters-tamil-nadu-leaves-parts-of-chennai-waterlogged-1.84684314` | 200 | T. Nagar / Usman Road source, cached |
| — | `https://portal.opentopography.org/API/globaldem?demtype=NASADEM&…` | **401** | live; API key required (expected) |
| — | `https://mausam.imd.gov.in/Radar/caz_VRV.gif` (uppercase) | **404** | site codes are lowercase |
| — | `https://mausam.imd.gov.in/Radar/caz_chn.gif`, `caz_che.gif`, `caz_mdr.gif` | **404** | Chennai is `cni`, not `chn`/`che` |
| — | `https://incois.gov.in/portal/datainfo/drf.jsp` | **404** | no open Chennai tide series |
| — | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N12E081_Map.tif` | **404** (2026-09-05) | negative result proving the 3° snap-down rule |
| — | `https://www.deccanherald.com/india/cyclone-nivar-heavy-rains-lead-to-waterlogging-across-chennai-919478.html` | **403** | body not retrieved; claim marked weakly sourced |
| — | `https://cpheeo.gov.in/cms/manual-on-storm-water-drainage-systems---2019.php` | no response within timeout | CPHEEO manual not read; the §6 gap stands |

**18 URLs verified reachable (HTTP 200) in this session.**
