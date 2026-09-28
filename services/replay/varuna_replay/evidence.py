"""The sourced evidence behind ``MUM-2019-07-02``, and the arithmetic built on top of it.

Every number the reconstruction is calibrated to lives here, next to the URL it was read
from, so that one file can be audited against ``docs/research/`` without reading the builder.
The division this module exists to keep visible is the one rules 6 and 7 of SPEC.md 0 care
about:

**Measured.** IMD's own Mumbai chart records **375.2 mm at Santacruz for the 24 hours ending
08:30 IST on 2 July 2019** (the chart footnote defines that window). That is the single
IMD-primary rainfall number in hand.

**Reported, second-hand.** Colaba 137.88 mm for the same window; 183 mm in 3 hours over the
Kurla-Thane belt overnight (Central Railway's chief PRO); 63 mm in the 6 hours from 23:30 IST
30 June to 05:30 IST 1 July, the night *before* the event night (Skymet); 300-400 mm in the
12 hours to midday (the Chief Minister); a midday high water of 4.59 m forecast for 11:52 and
4.92 m as stated afterwards by the municipal corporation for 11:30.

**Not found, and this is the fact that shapes the whole bundle.** There is no hourly or
three-hourly hyetograph for any Mumbai station covering the demo window, and no sub-daily
anchor touches it: the nearest one in the clock, 63 mm in 6 h, *ends* at 05:30 on 1 July, a
day and four hours before the window opens. So the accumulation this bundle carries for
05:40-09:40 IST is an **inference** from the 24-hour total, never a measurement, and
:data:`CALIBRATION_BASIS` says so in the manifest and on screen. There is likewise no tide table for the day, so ``tide.csv`` is ``illustrative``.

See ``docs/research/rain_gauges_tide_MUM-2019-07-02.md`` (sections 1.1, 1.2, 3, 4.4),
``docs/research/REVIEW.md`` (the audit that re-read the IMD chart) and ADR-0007.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from varuna_schemas.constants import IST
from varuna_schemas.models.bundle import BundleSource, TideDatum

BUNDLE_ID = "MUM-2019-07-02"
CITY = "mumbai"
EVENT_DATE = date(2019, 7, 2)
SEED = 2019
"""The demo seed (SPEC.md rule 8)."""

# ------------------------------------------------------------------ the replay window
T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
"""ADR-0007: the replay window opens at 05:40 IST on 2 July 2019 ..."""

WINDOW_MIN = 240
"""... and closes at 09:40, four hours later, with the demo's first cycle at 06:40."""

T1 = T0 + timedelta(minutes=WINDOW_MIN)

CYCLE_OPENS = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
"""Where the console is pre-seeked when the judges arrive (SPEC.md 15)."""

WINDOW_LABEL = "05:40-09:40 IST, 2 July 2019"

# ------------------------------------------------------------------ measured rainfall
SANTACRUZ_24H_MM = 375.2
"""IMD-primary. Read off IMD Mumbai's own 'highest one-day rainfall in July' chart; the audit
in docs/research/REVIEW.md re-opened the cached PNG and read the 2019 bar and its day label."""

SANTACRUZ_24H_WINDOW = "24 hours ending 08:30 IST on 2 July 2019"

SANTACRUZ_24H_ENDS_IST = datetime(2019, 7, 2, 8, 30, tzinfo=IST)
"""The instant the 24-hour total closes, per the chart's own footnote.

The replay window (05:40-09:40) crosses it, so only 170 of its 240 minutes lie inside the
total the calibration target is a share of. `build.py` splits the truth cube here so the
manifest can publish the overlap instead of implying there is none."""
IMD_SANTACRUZ_CHART_URL = "https://mausam.imd.gov.in/mumbai/mcdata/Highest_Scz_July.gif"
IMD_SANTACRUZ_CHART_CACHE = "docs/research/_raw/imd_Highest_Scz_July.png"

# ------------------------------------------------------------------ reported, second-hand
COLABA_24H_MM = 137.88
"""Same window as Santacruz, but second-hand: Scroll citing The Indian Express, not IMD."""

KURLA_THANE_3H_MM = 183.0
KURLA_THANE_3H_HOURS = 3.0
"""'Kurla-Thane belt saw unprecedented rain of 183 mm within 3 hours' - Central Railway's
chief PRO via ANI, overnight 1-2 July. An official's statement, not a gauge trace."""

SKYMET_6H_MM = 63.0
SKYMET_6H_HOURS = 6.0
SKYMET_6H_WINDOW = "23:30 IST 30 June to 05:30 IST 1 July 2019"
"""Skymet, via Deccan Herald. The live-blog entry is stamped 1 July 2019 and names '1130 pm on
Sunday'; the Sunday before it was 30 June, so this is the night before the event night."""

CM_12H_RANGE_MM = (300.0, 400.0)
"""'In the past 12 hours, the city has received an unprecedented 300 to 400mm of rain' - the
Chief Minister, around midday on 2 July. A politician's round numbers for 'the city'; used
only as a bracket, never as a target."""

SCROLL_URL = "https://scroll.in/latest/929092"
DECCAN_HERALD_URL = (
    "https://www.deccanherald.com/archives/"
    "mumbai-rains-live-mumbai-limps-back-to-normalcy-as-rains-subside-744003.html"
)
INDIA_TV_URL = (
    "https://www.indiatvnews.com/news/"
    "india-mumbai-rains-live-updates-wall-collapse-local-trains-flight-cancelled-pune-531812"
)
GULF_NEWS_URL = "https://gulfnews.com/world/asia/india/mumbai-rains-record-july-rainfall-1.65100295"
OUTLOOK_URL = (
    "https://www.outlookindia.com/website/story/india-news-mumbai-rains-live-updates/207195"
)
MUMBAI_RAIN_PLATFORM_URL = "https://www.mumbairain.org/"
NOAA_ISD_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
IMD_RADAR_URL = "https://mausam.imd.gov.in/responsive/radar_animation.php?id=VRV"

# ------------------------------------------------------------------ the inferred share
WINDOW_SHARE_OF_DAILY = 0.20
"""The share of the 24-hour Santacruz total assigned to the four-hour demo window.

One fifth of the day's depth in one sixth of its hours. :data:`CALIBRATION_BASIS` shows the
arithmetic that brackets it and says why this share and not another. It is a **choice**, and
the only defensible kind of choice available while no hyetograph exists.
"""

CALIBRATION_TOLERANCE_FRAC = 0.05
"""The agreement the manifest states between the designed accumulation and the target."""


def window_target_mm() -> float:
    """The window accumulation target in mm: a share of the sourced 24-hour total.

    Rounded to two decimals so the manifest records exactly the number that was solved for.
    """
    return round(SANTACRUZ_24H_MM * WINDOW_SHARE_OF_DAILY, 2)


def daily_mean_mm_h() -> float:
    """375.2 mm spread evenly over its 24 hours."""
    return round(SANTACRUZ_24H_MM / 24.0, 3)


def uniform_share_mm() -> float:
    """What the window would hold at the daily mean rate: the reference the share is judged against."""
    return round(daily_mean_mm_h() * WINDOW_MIN / 60.0, 2)


def persistence_share_mm() -> float:
    """What the window would hold at the last documented sub-daily rate anywhere in the event.

    That rate is Skymet's 6 hours to 05:30 IST on 1 July, a day and four hours before the
    window opens, so this bracket is an extrapolation across a gap, not a persistence.
    """
    return round(SKYMET_6H_MM / SKYMET_6H_HOURS * WINDOW_MIN / 60.0, 2)


def burst_share_mm() -> float:
    """What the window would hold at the heaviest documented burst rate (183 mm in 3 h)."""
    return round(KURLA_THANE_3H_MM / KURLA_THANE_3H_HOURS * WINDOW_MIN / 60.0, 2)


# ------------------------------------------------------------------ the tide
TIDE_HIGH_WATER_M = 4.92
TIDE_HIGH_WATER_IST = datetime(2019, 7, 2, 11, 30, tzinfo=IST)
"""'Mumbai witnessed 4.92 metres high tide at 11.30 am. That is why we could not pump out
water on the central line' - the Additional Municipal Commissioner, via News18, that evening.
Height above chart datum; the datum is not stated in the source."""

TIDE_FORECAST_M = 4.59
TIDE_FORECAST_IST = datetime(2019, 7, 2, 11, 52, tzinfo=IST)
"""The morning forecast reported by ANI, which conflicts with the figure above. Recorded, not
averaged with it, and not used: a statement of what happened outranks a forecast of it."""

TIDE_PERIOD_MIN = 745.2
"""The M2 semi-diurnal period, 12 h 25.2 min. One constituent, because one sourced height
cannot support more."""

# ------------------------------------------------------------------ the tide datum
PSMSL_STATION_URL = "https://psmsl.org/data/obtaining/stations/43.php"
"""PSMSL station 43, Bombay (Apollo Bandar): the benchmark notes the conversion rests on."""

PSMSL_RLR_ANNUAL_URL = "https://psmsl.org/data/obtaining/rlr.annual.data/43.rlrdata"
"""PSMSL station 43 annual mean sea level, millimetres above the station's RLR datum."""

RLR_BELOW_BENCHMARK_M = 13.0
"""Station page note (1991-06-11): 'Bombay (Apollo B) 500/041 RLR(1964) is 13.0m below BM
2(PP)(1)'."""

BENCHMARK_ABOVE_CHART_DATUM_M = 8.522
"""Station page note (2010-09-16): 'For data 1937 onwards the datum was Chart Datum with BM 2PP1
being 8.522m above this'."""

RLR_ANNUAL_MSL_MM: dict[int, int] = {2015: 7141, 2017: 7154, 2020: 7207, 2024: 7211}
"""Annual mean sea level in mm above RLR, from the rlrdata file. 2016, 2018, 2019 and 2021-2023
are -99999 (no value), so 2019 itself is not in the record."""

MSL_ABOVE_CHART_DATUM_M = 2.70
"""Mean sea level above chart datum at Apollo Bandar used for 2 July 2019. Interpolating the
bracketing annual means (2017, 2020) gives 2.711 m; 2.70 m is the value used, one centimetre
below that and inside the span of the four recent annual means."""

MSL_ABOVE_CHART_DATUM_RANGE_M = (2.66, 2.73)
"""The span of mean sea level above chart datum over the 2015-2024 annual means, rounded."""

COPERNICUS_DEM_HANDBOOK_URL = (
    "https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/"
    "geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf"
)
"""The Copernicus DEM product handbook, which states the DEM's vertical datum."""


def rlr_above_chart_datum_m() -> float:
    """Where RLR sits relative to chart datum: BM 2PP1 above chart datum, minus RLR below it."""
    return round(BENCHMARK_ABOVE_CHART_DATUM_M - RLR_BELOW_BENCHMARK_M, 3)


def msl_above_chart_datum_for(year: int) -> float:
    """One year's annual mean sea level, re-expressed above chart datum."""
    return round(RLR_ANNUAL_MSL_MM[year] / 1000.0 + rlr_above_chart_datum_m(), 3)


TIDE_DATUM_DERIVATION = (
    "PSMSL station 43 (Bombay, Apollo Bandar): RLR(1964) is 13.0 m below BM 2(PP)(1), and BM "
    "2PP1 is 8.522 m above chart datum for data from 1937 onwards, so RLR = chart datum - "
    "4.478 m. Annual mean sea level in mm above RLR: 7,141 (2015), 7,154 (2017), 7,207 (2020), "
    "7,211 (2024), which is 2.663, 2.676, 2.729 and 2.733 m above chart datum. 2019 has no "
    "annual value; interpolating 2017 and 2020 gives 2.711 m. The bundle uses 2.70 m, with "
    "the 2015-2024 span of 2.66-2.73 m as its range. The same PSMSL pages carry two caveats: "
    "in 1966 the benchmark was changed to BM 5/88, 7.44 m above chart datum, and the 2020 and "
    "2024 annual values carry the 'Y' missing-days flag; neither changes the 2.70 m or the "
    "2.66-2.73 m range, because the flagged means are used as PSMSL publishes them and the RLR "
    "series is PSMSL's reduction to one datum across the benchmark change."
)

TIDE_STAGE_REFERENCE = (
    "chart datum (assumed: the 4.92 m civic statement does not name its datum; taken as the "
    "Apollo Bandar chart datum PSMSL documents)"
)

TIDE_DEM_DATUM = (
    "Copernicus DEM GLO-30 heights are relative to the EGM2008 geoid. The product handbook: "
    '"The vertical reference datum is the Earth Gravitational Model 2008 (EGM2008; EPSG 3855)."'
)

TIDE_DATUM_RESIDUAL = (
    "Not quantified: the separation between the EGM2008 geoid, the DEM's datum, and local mean "
    "sea level at Mumbai. The conversion puts the stage at mean sea level and treats that as "
    "the DEM's frame, so any geoid-to-sea-level offset stays in the tidal boundary as an "
    "unmeasured bias, as does any revision of the chart datum since PSMSL's note."
)


def tide_datum() -> TideDatum:
    """``manifest.tide_datum`` for the reconstruction: chart datum, converted to mean sea level."""
    return TideDatum(
        stage_datum="chart_datum",
        stage_reference=TIDE_STAGE_REFERENCE,
        msl_above_chart_datum_m=MSL_ABOVE_CHART_DATUM_M,
        range_m=MSL_ABOVE_CHART_DATUM_RANGE_M,
        derivation=TIDE_DATUM_DERIVATION,
        source_urls=[PSMSL_STATION_URL, PSMSL_RLR_ANNUAL_URL],
        dem_datum=TIDE_DEM_DATUM,
        dem_datum_source_url=COPERNICUS_DEM_HANDBOOK_URL,
        residual=TIDE_DATUM_RESIDUAL,
    )


# ------------------------------------------------------------------ the honesty labels
CALIBRATION_BASIS = (
    "Inferred, not measured. No hourly or three-hourly hyetograph exists for any Mumbai "
    "station covering {window}, so the accumulation this bundle carries for the demo window "
    "is an inference from the one IMD-primary total in hand, never a gauge reading. "
    "MEASURED: {santacruz:.1f} mm at Santacruz for the {santacruz_window}, read off IMD "
    "Mumbai's own highest-one-day-rainfall chart ({chart}), whose footnote defines the "
    "08:30-to-08:30 window. REPORTED SECOND-HAND, used only to bracket the rate: "
    "{burst:.0f} mm in {burst_h:.0f} hours over the Kurla-Thane belt overnight (Central "
    "Railway's chief PRO via ANI, {scroll}); {skymet:.0f} mm in the {skymet_h:.0f} hours "
    "from {skymet_window} - the night before the event night, a day and four hours before "
    "this window opens (Skymet via "
    "Deccan Herald, {dh}); and 300-400 mm in the 12 hours to midday (the Chief Minister, "
    "{gulf}). INFERRED: this bundle assigns {share:.0f} % of the daily total to the "
    "four-hour window, {fraction:.2f} x {santacruz:.1f} = {target:.1f} mm. The arithmetic "
    "behind that share, so it can be argued with: the window is 4 of the 24 hours, so a "
    "uniform rate would give 16.7 % = {uniform:.1f} mm ({daily_rate:.1f} mm/h); "
    "OVERLAP, because the share is applied to a window that does not sit inside the total "
    "it is a fraction of: the 24-hour total closes at 08:30 and the window runs to 09:40, so "
    "{inside_min:.0f} of its 240 minutes are inside that total and the last 70 fall in the "
    "next day's, for which the only figures in hand are the Chief Minister's 12-hour range "
    "and the ward averages. Of the designed field, {before:.1f} mm lands before 08:30 and "
    "{after:.1f} mm after, so {inside_share:.1f} % of the {santacruz:.1f} mm is actually "
    "placed inside the window that number describes. "
    "extrapolating the last documented sub-daily rate anywhere in the event, {skymet:.0f} mm "
    "/ {skymet_h:.0f} h = {skymet_rate:.1f} mm/h from the night before, "
    "would give {persistence:.1f} mm ({persistence_share:.0f} % of the day); the heaviest "
    "documented burst, {burst:.0f} mm / {burst_h:.0f} h = {burst_rate:.0f} mm/h, would give "
    "{burst_window:.0f} mm ({burst_share:.0f} %), which the same daily total cannot "
    "accommodate alongside the overnight cloudburst that dominates it. {share:.0f} % sits "
    "just above the uniform share and far below the burst, because the sourced record has "
    "the flooding starting at 08:07 and worsening until 14:28 IST and the Chief Minister "
    "put 300-400 mm in the 12 hours to midday - the rain did not hold to the previous "
    "night's rate - while the 24-hour total leaves no room for the burst to repeat. "
    "ACHIEVED: the designed area-mean accumulation over MUM-CENTRAL is {achieved:.1f} mm "
    "against the {target:.1f} mm target, {error:.1f} % away, inside the stated "
    "{tolerance:.0f} % tolerance - met by construction, not by an independent check: the "
    "intensity multiplier is solved from the same area-mean integral the verification "
    "re-runs, so the tolerance catches a rounding residual and nothing else. POINT VERSUS "
    "AREA: the target is a share of a point total at one observatory (Santacruz, 19.089 N, "
    "72.868 E) but is imposed as a mean over the whole area of interest, so the station the "
    "calibration is named after does not read it - the same field accumulates "
    "{santacruz_pixel:.1f} mm at that observatory's own pixel and {colaba_pixel:.1f} mm at "
    "Colaba's, and gauges.csv publishes a synthetic Santacruz series sampled from it. "
    "PLACEMENT: fitted to the record rather than to a radar "
    "image - the cell tracks are aimed at the {clusters} latitudinal clusters of the "
    "{pins} sourced ground-truth pins inside the area of interest, in proportion to the "
    "pins in each, so the heaviest rain falls over the catchments feeding the streets where "
    "water was actually reported; accumulation over the pins averages {pin_ratio:.2f} times "
    "the area mean. Read no line of this as a measurement of the window: no gauge measured "
    "it, and until IMD's RMC Mumbai daily weather reports for 1-3 July 2019 or a MoES data "
    "request supply a sub-daily series, none can."
)
"""Template for ``manifest.calibration_basis``. :func:`calibration_basis` fills it in."""


def calibration_basis(
    *,
    achieved_mm: float,
    error_frac: float,
    pin_ratio: float,
    clusters: int,
    pins: int,
    minutes_inside: int,
    before_mm: float,
    after_mm: float,
    santacruz_pixel_mm: float,
    colaba_pixel_mm: float,
) -> str:
    """The calibration story with this build's achieved numbers substituted in.

    The overlap and point-versus-area numbers are measured off the cube this build just
    wrote, not constants: they are the two places the reasoning could otherwise read as
    stronger than it is.
    """
    return CALIBRATION_BASIS.format(
        inside_min=minutes_inside,
        before=before_mm,
        after=after_mm,
        inside_share=before_mm / SANTACRUZ_24H_MM * 100,
        santacruz_pixel=santacruz_pixel_mm,
        colaba_pixel=colaba_pixel_mm,
        window=WINDOW_LABEL,
        santacruz=SANTACRUZ_24H_MM,
        santacruz_window=SANTACRUZ_24H_WINDOW,
        chart=IMD_SANTACRUZ_CHART_URL,
        burst=KURLA_THANE_3H_MM,
        burst_h=KURLA_THANE_3H_HOURS,
        scroll=SCROLL_URL,
        skymet=SKYMET_6H_MM,
        skymet_h=SKYMET_6H_HOURS,
        skymet_window=SKYMET_6H_WINDOW,
        dh=DECCAN_HERALD_URL,
        gulf=GULF_NEWS_URL,
        share=WINDOW_SHARE_OF_DAILY * 100,
        fraction=WINDOW_SHARE_OF_DAILY,
        target=window_target_mm(),
        uniform=uniform_share_mm(),
        daily_rate=daily_mean_mm_h(),
        skymet_rate=round(SKYMET_6H_MM / SKYMET_6H_HOURS, 1),
        persistence=persistence_share_mm(),
        persistence_share=persistence_share_mm() / SANTACRUZ_24H_MM * 100,
        burst_rate=round(KURLA_THANE_3H_MM / KURLA_THANE_3H_HOURS, 0),
        burst_window=burst_share_mm(),
        burst_share=burst_share_mm() / SANTACRUZ_24H_MM * 100,
        achieved=achieved_mm,
        error=error_frac * 100,
        tolerance=CALIBRATION_TOLERANCE_FRAC * 100,
        clusters=clusters,
        pins=pins,
        pin_ratio=pin_ratio,
    )


TIDE_BASIS = (
    "Illustrative, not measured. No tide table for Mumbai on 2 July 2019 could be sourced - "
    "Survey of India, INCOIS and Mumbai Port publish none that is publicly retrievable, and "
    "the prediction services that exist carry no 2019 archive. Two civic statements about "
    "the same midday high water survive and they disagree: 4.59 m forecast for 11:52 (ANI, "
    "that morning) and 4.92 m as stated afterwards for 11:30 by the Additional Municipal "
    "Commissioner, who tied it to the pumps failing on the central line. Both heights are "
    "above chart datum and neither source states the datum. This series is a single "
    "semi-diurnal harmonic (M2, period 12 h 25 min) whose crest is the 4.92 m statement at "
    "11:30 - the statement of what happened, not the forecast of it - and whose trough sits "
    "at 0.00 m, because chart datum is by convention about the lowest astronomical tide. "
    "That trough is an assumption, not an observation. Everything the demo shows about the "
    "tide inside 05:40-09:40 IST - a stage rising from about 0.0 m to about 3.9 m, and the "
    "tide-locked outfall that follows from it - is a modelled consequence of that one "
    "anchor, not a record of the morning. The series runs on to 12:40, three hours past the "
    "window, because the last cycle forecasts that far ahead and the tide is its sea "
    "boundary; the same harmonic carries it through 4.92 m at 11:30 (4.918 m in the nearest "
    "15-minute row, 11:25) to about 4.5 m at 12:40. Stopping at 09:40 held the sea flat at the "
    "09:40 stage for the rest of every late cycle. What would settle it: the Survey of India "
    "or INCOIS tide table for Mumbai (Apollo Bandar), 2019. DATUM: tide.csv stays in chart "
    "datum, as sourced. manifest.tide_datum carries mean sea level at 2.70 m above chart datum "
    "(PSMSL station 43), which the Twin subtracts to read the stage in the DEM's frame, where "
    "the window runs from about -2.7 m to about +1.2 m and the series crests at about +2.2 m; "
    "the separation between the DEM's EGM2008 geoid and local mean sea level is not "
    "quantified."
)

GAUGE_BASIS = (
    "Synthetic readings at real sites. The values are sampled from the reconstructed rain "
    "field, so they are as inferred as the storm is; the coordinates are not. Every site is "
    "one of the ten stations in docs/research/bmc_aws_stations.json that carries a published "
    "coordinate - the two IMD observatories from NOAA's station history, and municipal "
    "gauges sited at the OpenStreetMap position of their host facility, which is an "
    "inference from the facility, not a surveyed mast. The municipal network's own "
    "coordinates sit behind a POST-only endpoint that was not called. Reading the column: "
    "mm_5min is the depth in the five minutes ending at ts, reported every fifteen minutes as "
    "SPEC.md 10.2 specifies, so a rate in mm/h is that value times twelve and a station's "
    "column does not sum to the window accumulation."
)

TRAFFIC_BASIS = (
    "Synthetic. A weekday-morning baseline speed per road class, then a speed collapse below "
    "5 km/h on the segment nearest each sourced ground-truth pin inside the window, for ten "
    "minutes either side of the pin's time, spreading to its neighbours; plus unrelated "
    "slowdowns on 3 % of covered segments, which no flood explains and which VARUNA-Pulse "
    "must reject. No probe or provider feed for 2 July 2019 was obtainable."
)

REPORTS_BASIS = (
    "Two streams in one file. The synthetic citizen reports are placed at the chronic "
    "hotspots, with ankle/knee/waist chips that follow a stated rule from the reconstructed "
    "rain accumulated over the hotspot - not from a hydraulic depth. The sourced pins that "
    "fall inside the window are carried alongside them with synthetic false and their "
    "source_url, and with no depth chip at all, because no cached source states a depth in "
    "centimetres."
)

RADAR_BASIS = (
    "Reconstructed, not decoded. IMD's public Mumbai radar products are latest-image "
    "endpoints overwritten in place, with no archive and no dated path, so no July 2019 "
    "frame is publicly retrievable; archived volumes go through IMD's data-supply route. "
    "These frames are rendered from the designed rain field through the Marshall-Palmer "
    "inverse and quantised to the same 5 dBZ classes the public images carry, so the P1 "
    "decoder (services/sky/decode_imd.py) reads them the way it will read the real thing."
)


def sources() -> list[BundleSource]:
    """Every public source the bundle was built from, with what each was used for."""
    return [
        BundleSource(
            name="IMD RMC Mumbai, highest one-day rainfall in July (Santacruz)",
            url=IMD_SANTACRUZ_CHART_URL,
            note=(
                f"{SANTACRUZ_24H_MM} mm for the {SANTACRUZ_24H_WINDOW}. IMD-primary: the "
                f"figure is IMD's own chart, cached at {IMD_SANTACRUZ_CHART_CACHE}; the "
                "chart footnote defines the 08:30-to-08:30 window."
            ),
            used_for=(
                f"The only measured number the storm is calibrated to. {WINDOW_SHARE_OF_DAILY:.0%} "
                f"of it, {window_target_mm():.1f} mm, is the target for {WINDOW_LABEL}."
            ),
        ),
        BundleSource(
            name="Scroll.in live blog, 2 July 2019",
            url=SCROLL_URL,
            note=(
                f"Colaba {COLABA_24H_MM} mm and Santacruz {SANTACRUZ_24H_MM} mm for the "
                f"{SANTACRUZ_24H_WINDOW}, citing The Indian Express; "
                f"{KURLA_THANE_3H_MM:.0f} mm in {KURLA_THANE_3H_HOURS:.0f} hours over the "
                "Kurla-Thane belt (Central Railway's chief PRO via ANI); and the "
                f"{TIDE_HIGH_WATER_M} m high water at "
                f"{TIDE_HIGH_WATER_IST:%H:%M} stated by the Additional Municipal "
                "Commissioner, who tied it to the pumps failing."
            ),
            used_for=(
                "The upper bracket on the window's rate, and the anchor of the illustrative "
                "tide curve."
            ),
        ),
        BundleSource(
            name="Deccan Herald live blog, 1-3 July 2019",
            url=DECCAN_HERALD_URL,
            note=(
                f"{SKYMET_6H_MM:.0f} mm in the {SKYMET_6H_HOURS:.0f} hours from "
                f"{SKYMET_6H_WINDOW} (Skymet), and the ranking of the "
                f"{SANTACRUZ_24H_MM} mm total as the highest since 26 July 2005."
            ),
            used_for=(
                "The lower bracket on the window's rate: the last documented sub-daily rate "
                "anywhere in the event, extrapolated across the day and four hours between "
                "it and the window. Also the source of 14 of the ground-truth pins."
            ),
        ),
        BundleSource(
            name="India TV live blog, 2 July 2019",
            url=INDIA_TV_URL,
            note=(
                f"The morning tide forecast of {TIDE_FORECAST_M} m for "
                f"{TIDE_FORECAST_IST:%H:%M}, which conflicts with the "
                f"{TIDE_HIGH_WATER_M} m stated afterwards. Recorded, not averaged."
            ),
            used_for="The conflicting tide figure the bundle did not use, and 9 pins.",
        ),
        BundleSource(
            name="Gulf News, record July rainfall",
            url=GULF_NEWS_URL,
            note=(
                "The Chief Minister's '300 to 400mm of rain' in the 12 hours to midday, and "
                "his tying of the flooding to the high tide that afternoon."
            ),
            used_for="A bracket showing the morning was not a lull; never a target.",
        ),
        BundleSource(
            name="Outlook/PTI, municipal ward rainfall averages",
            url=OUTLOOK_URL,
            note=(
                "107 mm island city, 172 mm eastern suburbs, 152 mm western suburbs for the "
                "24 hours ending 08:00 IST on 3 July 2019, and the naming of Hindmata, "
                "Dadar, Sion and Gandhi Market as inundated."
            ),
            used_for="Corroboration of the hotspot register; not a calibration input.",
        ),
        BundleSource(
            name="IIT Bombay Mumbai Rain platform, station roster",
            url=MUMBAI_RAIN_PLATFORM_URL,
            note=(
                "116 municipal, IMD and IITM gauges with names, operators and platform ids, "
                "and no coordinates."
            ),
            used_for="The gauge roster the synthetic gauge network is drawn from.",
        ),
        BundleSource(
            name="NOAA integrated surface database station history",
            url=NOAA_ISD_URL,
            note=(
                "Santacruz (43003) at 19.089 N, 72.868 E and Colaba (43057) at 18.900 N, 72.817 E."
            ),
            used_for="The two IMD observatory coordinates in gauges.csv.",
        ),
        BundleSource(
            name="IMD Mumbai-Veravali radar, public products",
            url=IMD_RADAR_URL,
            note=(
                "The site at 19.1342 N, 72.8672 E and its latest-image product URLs. No "
                "dated path or archive exists, so July 2019 frames are not retrievable."
            ),
            used_for=(
                "Why radar/frames.zarr is a reconstruction, and the product the P1 decoder targets."
            ),
        ),
    ]


def synthetic_notes(
    *,
    achieved_mm: float,
    gauges: int,
    pins_in_window: int,
    pins_total: int,
    traffic_segments: int,
    confounders: int,
    synthetic_reports: int,
) -> list[str]:
    """UI-ready copy naming everything in this bundle that is not a measurement."""
    return [
        "Reconstructed replay: the rain field, the radar frames, the gauge readings, the "
        "tide, the traffic speeds and the citizen reports are all generated. The "
        "ground-truth pins are not - every one of them carries the URL it was read from.",
        f"Window accumulation: {achieved_mm:.1f} mm over {WINDOW_LABEL} is inferred from the "
        f"IMD Santacruz 24-hour total of {SANTACRUZ_24H_MM} mm, not measured. No hyetograph "
        "for this window exists.",
        f"Radar frames: {RADAR_BASIS}",
        f"Rain gauges: {gauges} synthetic stations. {GAUGE_BASIS}",
        f"Tide: {TIDE_BASIS}",
        f"Traffic: {traffic_segments} segments carry a synthetic feed, of which "
        f"{confounders} carry an unrelated slowdown as a confounder. {TRAFFIC_BASIS}",
        f"Citizen reports: {synthetic_reports} synthetic, plus the {pins_in_window} sourced "
        f"pins that fall inside the window. {REPORTS_BASIS}",
        f"Ground truth: {pins_total} pins inside the area of interest, every one with a "
        "source_url, a time uncertainty and no invented depth. depth_cm is null on all of "
        "them because no cached source states a depth in centimetres, so this event can be "
        "scored for occurrence, place and timing but not for depth error (ADR-0007).",
    ]


DESCRIPTION = (
    "Reconstruction of the Mumbai cloudburst of 1-2 July 2019 over MUM-CENTRAL, replayed "
    "from 05:40 to 09:40 IST on 2 July with the first cycle at 06:40 (ADR-0007). Thirteen "
    "sourced pins land inside the forecast window, 87 to 150 minutes after VARUNA flags the "
    "street. The storm is designed, not observed: it is calibrated to a share of IMD's "
    "375.2 mm Santacruz 24-hour total and aimed at the places the pins record."
)


__all__ = [
    "BUNDLE_ID",
    "CALIBRATION_BASIS",
    "CALIBRATION_TOLERANCE_FRAC",
    "CITY",
    "CM_12H_RANGE_MM",
    "COLABA_24H_MM",
    "CYCLE_OPENS",
    "DESCRIPTION",
    "EVENT_DATE",
    "GAUGE_BASIS",
    "IMD_SANTACRUZ_CHART_URL",
    "KURLA_THANE_3H_MM",
    "RADAR_BASIS",
    "REPORTS_BASIS",
    "SANTACRUZ_24H_MM",
    "SEED",
    "SKYMET_6H_MM",
    "T0",
    "T1",
    "TIDE_BASIS",
    "TIDE_FORECAST_M",
    "TIDE_HIGH_WATER_IST",
    "TIDE_HIGH_WATER_M",
    "TIDE_PERIOD_MIN",
    "TRAFFIC_BASIS",
    "WINDOW_LABEL",
    "WINDOW_MIN",
    "WINDOW_SHARE_OF_DAILY",
    "burst_share_mm",
    "calibration_basis",
    "daily_mean_mm_h",
    "persistence_share_mm",
    "sources",
    "synthetic_notes",
    "uniform_share_mm",
    "window_target_mm",
]
