"use client";

import { DepthChip } from "@/components/varuna/depth-chip";
import {
  depthLegendStops,
  drainLegendStops,
  passabilityStops,
  probabilityLegendStops,
  rainLegendStops,
  type LegendStop,
  type PassabilityProfile,
} from "@/lib/ramps";

import { DesignSection, Meta } from "./section";

/** One legend swatch with its band label and what the band means on the street. */
function Stop({ stop }: { stop: LegendStop }) {
  return (
    <li className="flex items-center gap-2 rounded-control border border-line bg-deep p-2">
      <span
        aria-hidden="true"
        className="size-8 shrink-0 rounded-control border border-line"
        style={{ backgroundColor: stop.cssVar, opacity: stop.opacity }}
      />
      <div className="flex min-w-0 flex-col">
        <span className="type-small num font-medium text-text">{stop.label}</span>
        <span className="type-micro text-text-2">{stop.meaning}</span>
      </div>
    </li>
  );
}

function Ramp({
  title,
  description,
  stops,
}: {
  title: string;
  description: string;
  stops: LegendStop[];
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="type-h3 text-text">{title}</h3>
      <p className="type-small max-w-[72ch] text-text-2">{description}</p>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {stops.map((stop) => (
          <Stop key={stop.key} stop={stop} />
        ))}
      </ul>
    </div>
  );
}

/** Depths that put a chip in every band of the ramp, in centimetres. */
const CHIP_DEPTHS = [2, 8, 20, 35, 50, 80];

/** The three profiles the public map switches between most often. */
const PASSABILITY_PROFILES: readonly { profile: PassabilityProfile; label: string }[] = [
  { profile: "two-wheeler", label: "Two-wheeler" },
  { profile: "car", label: "Car" },
  { profile: "ambulance", label: "Ambulance" },
];

export function RampsSection() {
  return (
    <DesignSection
      id="ramps"
      title="Ramps"
      description="The ramps lib/ramps.ts builds from tokens.json, so map pixels, chips and charts agree. Depth colour is fixed meaning: it is never used for anything that is not water depth."
    >
      <div className="flex flex-col gap-8">
        <Ramp
          title="Depth"
          description="Six bands from dry to rescue vehicles only. The chips below read the same ramp."
          stops={depthLegendStops()}
        />
        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Depth chips</h3>
          <p className="type-small max-w-[72ch] text-text-2">
            Colour never carries the meaning alone: every chip also prints the number and unit.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {CHIP_DEPTHS.map((cm) => (
              <DepthChip key={cm} cm={cm} showBand />
            ))}
            <DepthChip cm={null} />
          </div>
        </div>
        <Ramp
          title="Rain"
          description="Radar echoes and the nowcast field, in millimetres per hour. It is deliberately a different palette from depth: rain overhead must never read as water on the street. The two upper edges are the exceedance thresholds Sky reports, 20 and 40 mm/h."
          stops={rainLegendStops()}
        />
        <Ramp
          title="Drain health"
          description="Posterior blockage beta per pipe after Pulse assimilates the cycle. Magenta means blocked."
          stops={drainLegendStops()}
        />
        <Ramp
          title="Probability mode"
          description="Colour stays the depth ramp at the p50 depth; opacity carries the exceedance probability, with a floor of 15 % so a segment never vanishes. Shown at the 30 cm threshold."
          stops={probabilityLegendStops(30)}
        />
        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Passability, by profile</h3>
          <p className="type-small max-w-[72ch] text-text-2">
            The public map collapses depth to three states per vehicle. An ambulance keeps moving
            where a two-wheeler has already stopped.
          </p>
          <ul className="flex flex-col gap-2">
            {PASSABILITY_PROFILES.map(({ profile, label }) => (
              <li
                key={profile}
                className="flex flex-wrap items-center gap-3 rounded-control border border-line bg-deep p-2"
              >
                <span className="type-small w-28 shrink-0 font-medium text-text">{label}</span>
                {passabilityStops(profile).map((stop) => (
                  <span key={stop.state} className="flex items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="size-4 shrink-0 rounded-chip border border-line"
                      style={{ backgroundColor: stop.cssVar }}
                    />
                    <span className="type-micro text-text">{stop.label}</span>
                    <Meta>{stop.range}</Meta>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </DesignSection>
  );
}
