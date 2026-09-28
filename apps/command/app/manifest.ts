import type { MetadataRoute } from "next";

/**
 * Installable public map (task P9.10). It opens on `/map`, and the service worker in
 * `public/sw.js` keeps `/map` and `/report` with the last forecast, so the installed app opens with
 * no connection and says how old its forecast is.
 *
 * `id` pins the app's identity to the public map, so a later change of `start_url` does not make
 * phones that installed it see a second app. The scope stays `/` so "Report water" and the links
 * out of the map open inside the installed window rather than in a browser tab; the worker itself
 * is registered for `/map` and `/report` only and never controls the console.
 *
 * Colours are literals because the manifest is read by the operating system before any stylesheet
 * loads; they equal --ink and --tide in `tokens.json`.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/map",
    name: "VARUNA",
    short_name: "VARUNA",
    description:
      "Street-by-street flood depth for the next three hours: which streets are passable, and until when. Works offline with the last forecast.",
    start_url: "/map",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    lang: "en-IN",
    dir: "ltr",
    background_color: "#0A1020", // lint-design-allow: manifest colours must be literals; equals --ink
    theme_color: "#0A1020", // lint-design-allow: manifest colours must be literals; equals --ink
    categories: ["weather", "navigation", "utilities"],
    prefer_related_applications: false,
    // Raster icons at the sizes an install asks for, drawn from the team's logo by
    // `tools/brand_assets.py`. The SVG favicon is not listed: Android's install and splash
    // screens want a 192 and a 512 PNG, and the maskable one keeps the mark inside the safe zone
    // a launcher crops to a circle or squircle.
    icons: [
      {
        src: "/brand/varuna-app-icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/brand/varuna-app-icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/brand/varuna-app-icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      {
        name: "Report water",
        short_name: "Report",
        description: "Tell VARUNA how deep the water is where you are.",
        url: "/report",
        icons: [{ src: "/brand/varuna-app-icon-192.png", sizes: "192x192", type: "image/png" }],
      },
    ],
  };
}
