import type { Metadata } from "next";

import { Hero } from "@/components/landing/hero";
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
  title: "VARUNA - every street, three hours early",
  description:
    "VARUNA turns Doppler radar into street-by-street flood depth for the next three hours, learns a city's hidden drains from every flood, and routes emergency services around what is coming.",
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
