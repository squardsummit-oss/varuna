import type { Metadata } from "next";

import { Hero } from "@/components/landing/hero";
import { SITE_DESCRIPTION, SITE_TITLE, structuredData } from "@/lib/seo";
import { Proof } from "@/components/landing/proof";
import {
  DataSources,
  Footer,
  FourWays,
  Landscape,
  Roadmap,
  SixEngines,
  TheCycle,
  TheGap,
} from "@/components/landing/sections";

export const metadata: Metadata = {
  // `absolute`: the landing page is the site's own title, not "<title> - VARUNA".
  title: { absolute: SITE_TITLE },
  description: SITE_DESCRIPTION,
  alternates: { canonical: "/" },
};

/**
 * The landing page (SPEC.md 7.1, task P9.1).
 *
 * Ten sections, in the order the spec sets: the working map first, then the gap it fills, the
 * physics, the engines, the scores, the landscape, the data, the roadmap and the footer.
 *
 * Only two sections are client components - the hero's scrub loop and the proof counters - so
 * everything below the fold ships as HTML and the page's LCP is the map, not a hydration wait.
 */
export default function LandingPage() {
  return (
    <main className="bg-ink text-text flex min-h-dvh flex-col">
      {/* What search engines and assistants read about the project (lib/seo.ts). */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData()) }}
      />
      <Hero />
      <TheGap />
      <FourWays />
      <TheCycle />
      <SixEngines />
      <Proof />
      <Landscape />
      <DataSources />
      <Roadmap />
      <Footer />
    </main>
  );
}
