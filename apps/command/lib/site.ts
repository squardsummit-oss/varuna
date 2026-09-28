/** Where the app is served from, for absolute URLs in robots, sitemap and Open Graph metadata. */
export const DEFAULT_SITE_URL = "http://localhost:3000";

/** Every P0 route (SPEC.md section 3.4), in the order the demo visits them. */
export const P0_ROUTES = [
  "/",
  "/console",
  "/drains",
  "/route",
  "/alerts",
  "/pumps",
  "/whatif",
  "/replay",
  "/onboard",
  "/verify",
  "/map",
  "/report",
  "/api",
] as const;

function trimSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/**
 * NEXT_PUBLIC_SITE_URL when it carries a usable value, else the Vercel URL, else localhost.
 * Next inlines unset public vars as the string "undefined", so those read as unset.
 */
export function siteUrl(): string {
  const candidates = [process.env.NEXT_PUBLIC_SITE_URL, process.env.VERCEL_URL];
  for (const raw of candidates) {
    if (typeof raw !== "string") continue;
    const value = raw.trim();
    if (!value || value === "undefined" || value === "null") continue;
    return trimSlash(value.startsWith("http") ? value : `https://${value}`);
  }
  return DEFAULT_SITE_URL;
}
