/**
 * The roadmap (SPEC.md 7.1 item 9, motion M5): V1, V10, V100 as a vertical timeline, a real
 * sequence, with a tracing beam that lights the line as the reader scrolls through it.
 *
 * A server component; only the beam itself is client code.
 */

import { BODY, H2, SECTION } from "@/components/landing/styles";
import { TracingBeam } from "@/components/ui/tracing-beam";

export const ROADMAP = [
  {
    tier: "V1",
    title: "This prototype",
    body: "One Mumbai AOI: reconstructed radar, inferred drains, a learning blockage map, routes and alerts.",
  },
  {
    tier: "V10",
    title: "A pilot city",
    body: "Real IMD radar, the ward's drain GIS, LiDAR at chronic spots, and a monsoon of observations.",
  },
  {
    tier: "V100",
    title: "Every city that wants one",
    body: "City-in-a-box from open data, a public map in three languages, and a routing feed for apps.",
  },
] as const;

export function Roadmap() {
  return (
    <section className={SECTION}>
      <div className="mx-auto max-w-[1200px]">
        <h2 className={H2}>V1, V10, V100</h2>
        <TracingBeam className="mt-10">
          <ol className="flex flex-col">
            {ROADMAP.map((stage) => (
              <li key={stage.tier} className="relative pb-10 pl-8 last:pb-0">
                <span
                  aria-hidden="true"
                  className="bg-tide absolute top-1.5 left-0 h-2 w-2 -translate-x-1/2 rounded-full"
                />
                <p className="num text-micro text-tide">{stage.tier}</p>
                <p className="text-h3 text-text mt-1">{stage.title}</p>
                <p className={`${BODY} mt-2`}>{stage.body}</p>
              </li>
            ))}
          </ol>
        </TracingBeam>
      </div>
    </section>
  );
}
