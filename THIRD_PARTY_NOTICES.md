# Third-party notices

VARUNA's own source is licensed under [LICENSE](LICENSE). This file lists everything in the
repository that VARUNA did **not** write: where it came from, under what terms, and why it is here.

It exists so that a reader — or an automated similarity check — can tell authored work from
captured evidence without having to guess.

---

## 1. Vendored source code

Two files are verbatim copies of another project's source, kept as evidence for a diagnosis
recorded in `docs/research/data_sources.md` (§ "Overpass").

| Path | Upstream file | Package | Licence |
|---|---|---|---|
| `docs/research/_raw/osmnx_overpass.py` | `osmnx/_overpass.py` | OSMnx 2.1.1 | MIT © Geoff Boeing |
| `docs/research/_raw/osmnx_settings.py` | `osmnx/settings.py` | OSMnx 2.1.1 | MIT © Geoff Boeing |

OSMnx: <https://github.com/gboeing/osmnx> · MIT licence.

Neither file is imported by any VARUNA module. The build depends on the published `osmnx`
package declared in `pyproject.toml`; deleting both copies would not change a line of behaviour.
Each now carries a header stating its origin.

## 2. Data committed into the application

**`apps/command/public/world-110m.json`** — 108 KB TopoJSON world topology (country polygons at
1:110 m with English names), drawn by the landing hero's globe.

Fetched from a CDN copy of the `world-atlas` package (Mike Bostock, ISC licence), whose geometry
is derived from **Natural Earth**, which places its data in the public domain with no permission
needed and no attribution required (<https://www.naturalearthdata.com/about/terms-of-use/>).

The exact upstream release was not recorded when the file was committed (commit `505b2a4`).
Confirm the version before publication if the provenance needs to be exact.

It is committed rather than fetched at runtime because SPEC.md § 17 requires the finale to run
with the venue's network off, and a hero that needs a CDN is the one thing on the page that cannot.

**`apps/command/public/earth-bluemarble-4096.jpg`** — 704 KB (720,728 bytes), 4096 × 2048
equirectangular JPEG, sha256
`67aad248cda49c7de66f1db464e62657d969a903d9f7d4b92e97ede7e23e5249`. It is the photographic Earth
the landing hero's globe (M26) and the citizen dashboard's approach (M27) are painted with; the
fragment shader in `apps/command/components/landing/globe-texture.ts` samples it.

Derived from NASA's **Blue Marble: Land Surface, Shallow Water, and Shaded Topography**, retrieved
2026-09-23 from
<https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57752/land_shallow_topo_8192.tif>
(8192 × 4096 TIFF, 27,797,968 bytes, sha256
`fec3cb8e729347d1c57807cf66b7867c1f3c669bb2f1fc3a8ad1625562591b36`). The only change is a Lanczos
downsample to half its linear size and a JPEG encode at quality 78; no pixel was retouched, and the
projection is untouched, which is what lets the shader treat it as a plain lon/lat grid. The image
belongs to NASA's Earth Observatory Blue Marble collection,
<https://science.nasa.gov/earth/earth-observatory/collections/blue-marble/>; the older record page
`https://visibleearth.nasa.gov/images/57752` now redirects to the Earth Observatory index, which is
why the image URL above is the citable one.

NASA imagery is in the public domain: "NASA content — images, audio, video, and computer files used
in the rendition of 3-dimensional models, such as texture maps and polygon data in any format —
generally are not copyrighted and may be used for educational or informational purposes without
needing explicit permissions" (<https://www.nasa.gov/nasa-brand-center/images-and-media/>, retrieved
2026-09-23). No NASA endorsement of VARUNA is claimed or implied.

Committed rather than fetched at runtime for the same reason as the topology above. The page is
built so that a missing or undecodable texture costs nothing: the globe falls back to the vector
Earth that shipped before it.

## 3. Captured reference material

`docs/research/_raw/` holds **172 files captured verbatim from public sources** while researching
what open data was actually obtainable for Mumbai and Chennai:

- IMD pages (radar network, AWS station lists, extreme-rainfall tables for Colaba and Santacruz)
- IITM Mumbai rainfall pages and the Mumbai Flood platform index
- ESA WorldCover and Copernicus DEM documentation and tile listings
- MCGM / BMC rainfall pages
- Nominatim geocoding responses and Overpass API responses (ODbL, © OpenStreetMap contributors)
- Radar imagery and rainfall charts

Copyright in these pages and images remains with their publishers. Nothing in that directory is
authored by the VARUNA team, and nothing in it is executed, imported or bundled by the build.
Each file's source URL and access date is recorded in `docs/research/data_sources.md`, per
SPEC.md § 0 rules 6 and 7. See `docs/research/_raw/README.md`.

## 4. Libraries

Runtime dependencies are declared in `pyproject.toml` and `package.json` and installed from PyPI
and npm. No library source is vendored into this repository.

The stack VARUNA leans on most heavily: pySTEPS, NumPy, Numba, xarray, Zarr, GeoPandas, Shapely,
pyproj, rasterio, OSMnx, NetworkX, WhiteboxTools, scikit-learn and FastAPI on the Python side;
Next.js, React, deck.gl, Tailwind, Recharts, Zustand and dnd-kit on the web side. Each carries its
own licence, retained in the installed package metadata.

Map imagery is Esri World Imagery, used under Esri's terms and attributed on every map that draws
it. It is deliberately **not** included in `make pack` — those tiles are not redistributable.

## 5. Published methods

The physics is published work, implemented from the equations rather than from anyone's code.
Each is cited in the module that implements it, and the equation sheet is SPEC.md Appendix A:

| Method | Source | Implemented in |
|---|---|---|
| Local-inertial shallow water | Bates, Horritt & Fewtrell (2010) | `services/twin/varuna_twin/swe2d.py` |
| Manning's equation, Preissmann slot | standard hydraulics | `services/twin/varuna_twin/drain1d.py` |
| SCS curve number | USDA NRCS | `services/twin/varuna_twin/hydrology.py` |
| Marshall–Palmer Z–R | Marshall & Palmer (1948) | `services/sky/varuna_sky/zr.py` |
| STEPS ensemble nowcasting | Bowler et al. (2006), via pySTEPS | `services/sky/varuna_sky/nowcast.py` |
| Lucas–Kanade optical flow | via pySTEPS | `services/sky/varuna_sky/motion.py` |
| Ensemble Kalman filter | Evensen (1994, 2003) | `services/pulse/varuna_pulse/enkf.py` |
| Nash reservoir cascade | Nash (1957) | `services/flash/varuna_flash/model.py` |
| Rational method pipe sizing | standard drainage design | `services/city/varuna_city/sizing.py` |

Where VARUNA departs from a published scheme, the module says so and the departure is recorded as
an ADR in `docs/DECISIONS.md`.

---

*Last reviewed: 2026-09-23.*
