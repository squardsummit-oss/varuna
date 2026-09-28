import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/site";

/** The prototype is public: crawl everything and point at the sitemap. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
