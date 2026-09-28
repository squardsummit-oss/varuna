/**
 * Which city a screen is showing (task D-09).
 *
 * Every city's runs live in one directory and their ids sort chronologically, so "the newest run"
 * is only a well-posed question once a city is attached to it. The API takes `?city=` on every
 * depth route; this is the browser half of the same thread: read the slug out of the URL, keep it
 * on every link, and hand it to the fetches.
 *
 * The slug is validated before it ever reaches a URL. `city` becomes a path segment on the API
 * (`/v1/city/{city}/layers/segments`), and a value out of the address bar is not to be trusted
 * with that; anything that is not a plain lowercase slug is treated as absent.
 */

/** The city every Mumbai screen means when the URL says nothing. Matches the API's own default. */
export const DEFAULT_CITY = "mumbai";

/** Pages whose default city is not DEFAULT_CITY, so an explicit `?city=mumbai` must survive. */
const OWN_DEFAULT_PATHS: ReadonlySet<string> = new Set(["/onboard"]);

/** City ids are lowercase slugs, the same shape `varuna_schemas` validates a path segment with. */
const SLUG = /^[a-z][a-z0-9-]{0,31}$/;

/** One row of `GET /v1/cities`: what the config says and what the pipeline has actually written. */
export interface CityRow {
  id: string;
  /** Display name from the city config, e.g. "Chennai". */
  name: string;
  /** Three-letter run-id code, e.g. "CHN"; null when the config could not be read. */
  code: string | null;
  /** True when `city/<id>/map/segments.geojson` exists, which is what every screen draws first. */
  built: boolean;
  /** The newest run with depth products for this city, or null when nothing is baked for it. */
  latestRunId: string | null;
}

/**
 * The city named by a query string, or the default.
 *
 * Takes the search string rather than reading `window`, so it is pure and testable; the reader
 * below is the one place that touches the browser.
 */
export function cityFromSearch(search: string, fallback = DEFAULT_CITY): string {
  const value = new URLSearchParams(search).get("city")?.trim().toLowerCase();
  return value && SLUG.test(value) ? value : fallback;
}

/** The city this page is showing. `DEFAULT_CITY` on the server, where there is no URL to read. */
export function currentCity(): string {
  if (typeof window === "undefined") return DEFAULT_CITY;
  return cityFromSearch(window.location.search);
}

/**
 * The same URL with `?city=` set, every other parameter kept.
 *
 * Kept rather than dropped because the console's `?run=` and `?bundle=` are how the demo script
 * opens a screen; switching city must not quietly discard them.
 */
export function withCity(path: string, search: string, city: string): string {
  const params = new URLSearchParams(search);
  // /onboard's own default is Chennai, not DEFAULT_CITY, so dropping `?city=mumbai` there would
  // land the reader back on Chennai; everywhere else the default city carries no parameter.
  if (city === DEFAULT_CITY && !OWN_DEFAULT_PATHS.has(path)) params.delete("city");
  else params.set("city", city);
  // A run id belongs to one city, so carrying it across a switch would ask for a Chennai console
  // and then pin it to a Mumbai run.
  params.delete("run");
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

interface CitiesResponse {
  cities?: {
    id?: string;
    name?: string;
    code?: string | null;
    built?: boolean;
    latest_run_id?: string | null;
  }[];
  default?: string;
}

/** Parse `GET /v1/cities`. Rows with no usable id are dropped rather than rendered as blanks. */
export function parseCities(body: CitiesResponse): CityRow[] {
  return (body.cities ?? [])
    .filter((row): row is { id: string } & typeof row => typeof row.id === "string" && !!row.id)
    .map((row) => ({
      id: row.id,
      name: typeof row.name === "string" && row.name ? row.name : row.id,
      code: typeof row.code === "string" ? row.code : null,
      built: row.built === true,
      latestRunId: typeof row.latest_run_id === "string" ? row.latest_run_id : null,
    }));
}
