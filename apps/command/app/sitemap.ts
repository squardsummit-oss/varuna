import type { MetadataRoute } from "next";

import { P0_ROUTES, siteUrl } from "@/lib/site";

/** Every P0 screen in the inventory (SPEC.md section 3.4). */
export default function sitemap(): MetadataRoute.Sitemap {
  const base = siteUrl();
  const lastModified = new Date();
  return P0_ROUTES.map((route) => ({
    url: route === "/" ? base : `${base}${route}`,
    lastModified,
    changeFrequency: "weekly" as const,
    priority: route === "/" ? 1 : 0.7,
  }));
}
