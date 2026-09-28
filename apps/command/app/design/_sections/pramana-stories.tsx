"use client";

import { BrierByLeadChart, brierByLeadRows } from "@/components/varuna/brier-by-lead-chart";
import { Panel } from "@/components/varuna/panel";
import { ReliabilityDiagram } from "@/components/varuna/reliability-diagram";
import { SkillByLeadChart, skillByLeadRows } from "@/components/varuna/skill-by-lead-chart";
import { parseRainSkill, type RainScope, type RainSkillScored } from "@/lib/api/verification-rain";
import { navItem } from "@/lib/nav";

import { Demo } from "./section";
import rainSkillBody from "./samples/rain-skill-aoi-20mmh.json";

/**
 * `GET /v1/verification/rain-skill?event=MUM-2019-07-02` as the local API answered it on
 * 2026-09-28, trimmed to the city-grid scope at the headline 20 mm/h (the domain scope, the other
 * thresholds and the per-cycle curves are dropped to keep the file small). Every number drawn
 * below is the scorer's, over eight baked cycles against the bundle's reconstructed truth.
 */
const SKILL = parseRainSkill(rainSkillBody, "MUM-2019-07-02") as RainSkillScored;
const THRESHOLD = SKILL.headlineThresholdMmH;
const AOI: RainScope = SKILL.byScope.aoi;
const HORIZON =
  AOI.horizons.find((h) => h.thresholdMmH === THRESHOLD && h.forecast === "mean") ?? null;

const CSI_ROWS = skillByLeadRows(AOI, THRESHOLD, "csi", "mean");
const POD_ROWS = skillByLeadRows(AOI, THRESHOLD, "pod", "mean");
const BRIER_ROWS = brierByLeadRows(AOI, THRESHOLD);
const BANDS = AOI.reliability[String(THRESHOLD)] ?? [];

/**
 * The scorer's reason when the runs keep no member cube, in its own words (the sentence the
 * reliability tests pin, from `unavailable.probability_20_mm_h`).
 */
const NO_MEMBERS_REASON = "8 of 8 runs keep no member cube (rain/cube.zarr).";

/** `loadRainSkill`'s sentence when the API is not running (`RainSkillLoadError`). */
const UNREACHABLE = "The API is unreachable.";

/**
 * Pramana's rain charts on the scored event: skill against lead time with persistence and the
 * cycle-to-cycle band, the Brier score by lead, and the reliability diagram - each with the state
 * it draws when there is nothing to draw.
 */
export default function PramanaStories() {
  const verify = navItem("verify");
  return (
    <Panel
      title={`${verify.label}: rain skill`}
      description={`Scored on the reconstructed replay: the storm designer's own rain field is the truth, over ${SKILL.nCycles} baked cycles, at ${THRESHOLD} mm/h inside the city grid.`}
    >
      <div className="flex flex-col gap-6">
        <Demo
          label="Skill by lead time, CSI"
          note="The ensemble mean against persistence, with the p10 to p90 band across cycles and the scorer's horizon."
        >
          <SkillByLeadChart
            rows={CSI_ROWS}
            metric="csi"
            thresholdMmH={THRESHOLD}
            forecast="mean"
            horizon={HORIZON}
            csiFloor={SKILL.csiFloor}
          />
        </Demo>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo label="Skill by lead time, POD" note="No horizon: it is drawn on the CSI view only.">
            <SkillByLeadChart
              rows={POD_ROWS}
              metric="pod"
              thresholdMmH={THRESHOLD}
              forecast="mean"
              height={220}
            />
          </Demo>
          <Demo label="Brier score by lead" note="Lower is better; persistence and climatology beside it.">
            <BrierByLeadChart rows={BRIER_ROWS} thresholdMmH={THRESHOLD} height={220} />
          </Demo>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Demo label="Skill, loading" note="Shimmer at the plot's height.">
            <SkillByLeadChart
              rows={[]}
              metric="csi"
              thresholdMmH={THRESHOLD}
              forecast="mean"
              loading
              height={160}
            />
          </Demo>
          <Demo label="Skill, error" note="The loader's sentence, verbatim.">
            <SkillByLeadChart
              rows={[]}
              metric="csi"
              thresholdMmH={THRESHOLD}
              forecast="mean"
              error={UNREACHABLE}
              height={160}
            />
          </Demo>
          <Demo label="Brier, empty" note="No lead scored at this threshold.">
            <BrierByLeadChart rows={[]} thresholdMmH={THRESHOLD} height={160} />
          </Demo>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo
            label="Reliability diagram"
            note="Forecast probability against observed frequency per lead band, with the diagonal."
          >
            <ReliabilityDiagram bands={BANDS} thresholdMmH={THRESHOLD} />
          </Demo>
          <Demo label="Reliability, empty" note="The scorer's reason, when no band has a bin.">
            <ReliabilityDiagram bands={[]} thresholdMmH={THRESHOLD} emptyReason={NO_MEMBERS_REASON} />
          </Demo>
        </div>
      </div>
    </Panel>
  );
}
