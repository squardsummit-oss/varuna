"use client";

import { useState } from "react";

import type { DrainStats, PipeCardData } from "@/app/drains/drain-model";
import { DrainHealthTable, type DrainHealthRow } from "@/components/varuna/drain-health-table";
import { DrainStatsStrip } from "@/components/varuna/drain-stats";
import { ObservationCard, type Observation } from "@/components/varuna/observation-card";
import { ObservationStrip } from "@/components/varuna/observation-strip";
import { Panel } from "@/components/varuna/panel";
import { PipeCard } from "@/components/varuna/pipe-card";
import type { AssimilatedObservation } from "@/lib/api/drains";
import { navItem } from "@/lib/nav";

import { Demo } from "./section";

/*
 * Nadi on the 08:40 IST cycle of MUM-2019-07-02 (run MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-
 * baked), field for field as `GET /v1/drains/health` and `GET /v1/observations` answered on
 * 2026-09-28. Every pipe is inferred; every traffic anomaly and seeded report is synthetic.
 */

const CYCLE_TS = "2019-07-02T08:40:00+05:30";

/** Four of the cycle's 23 assimilated observations: two traffic anomalies and two reports. */
const OBSERVATIONS: AssimilatedObservation[] = [
  {
    id: "obs-traffic-S1080263622-000",
    kind: "traffic",
    ts: "2019-07-02T08:40:00+05:30",
    place: "Marol Maroshi Road",
    locality: null,
    edgeId: "MUM-E001743",
    depthCm: 20,
    depthSdCm: 8,
    speedKmh: 3,
    baselineKmh: 23.9,
    z: -5.49,
    chip: null,
    synthetic: true,
    betaBefore: 0.15,
    betaAfter: 0.3426,
    innovationCm: 19.16,
  },
  {
    id: "obs-traffic-S1139147207-000",
    kind: "traffic",
    ts: "2019-07-02T08:40:00+05:30",
    place: "D S Babrekar Marg",
    locality: null,
    edgeId: "MUM-E005210",
    depthCm: 20,
    depthSdCm: 8,
    speedKmh: 3.8,
    baselineKmh: 19.3,
    z: -5.4,
    chip: null,
    synthetic: true,
    betaBefore: 0.2,
    betaAfter: 0,
    innovationCm: -795.71,
  },
  {
    id: "obs-report-RPT-MUM-HS-04-ankle",
    kind: "report",
    ts: "2019-07-02T07:05:00+05:30",
    place: "Gandhi Market (Matunga)",
    locality: null,
    edgeId: "MUM-E012246",
    depthCm: 10,
    depthSdCm: 8,
    speedKmh: null,
    baselineKmh: null,
    z: null,
    chip: "ankle",
    synthetic: true,
    betaBefore: 0.15,
    betaAfter: 0.2018,
    innovationCm: 10,
  },
  {
    id: "obs-report-RPT-MUM-HS-27-ankle",
    kind: "report",
    ts: "2019-07-02T07:13:00+05:30",
    place: "Sion pedestrian subway",
    locality: null,
    edgeId: "MUM-E005531",
    depthCm: 10,
    depthSdCm: 8,
    speedKmh: null,
    baselineKmh: null,
    z: null,
    chip: "ankle",
    synthetic: true,
    betaBefore: 0.15,
    betaAfter: 0.1518,
    innovationCm: -9.23,
  },
];

/** The same report as the P0 observation card reads it. */
const CARD_OBSERVATION: Observation = {
  id: "RPT-MUM-HS-04-ankle",
  kind: "report",
  ts: "2019-07-02T07:05:00+05:30",
  place: "Gandhi Market (Matunga)",
  inferredDepthCm: 10,
  pipeId: "MUM-E012246",
  betaBefore: 0.15,
  betaAfter: 0.2018,
  synthetic: true,
};

/** The three pipes the posterior raised furthest, from the drain-health features. */
const ROWS: DrainHealthRow[] = [
  {
    id: "MUM-E018763",
    street: "off D S Babrekar Marg",
    betaMean: 0.4505,
    betaSd: 0.1926,
    capacityReduction: 0.631,
    hotspotsExplained: [],
    observations: 0,
    lastUpdated: CYCLE_TS,
    betaDelta: 0.2505,
  },
  {
    id: "MUM-E005209",
    street: "D S Babrekar Marg",
    betaMean: 0.4112,
    betaSd: 0.2017,
    capacityReduction: 0.586,
    hotspotsExplained: [],
    observations: 0,
    lastUpdated: CYCLE_TS,
    betaDelta: 0.2112,
  },
  {
    id: "MUM-E005212",
    street: "D S Babrekar Marg",
    betaMean: 0.4064,
    betaSd: 0.1866,
    capacityReduction: 0.581,
    hotspotsExplained: [],
    observations: 0,
    lastUpdated: CYCLE_TS,
    betaDelta: 0.2064,
  },
];

/** The summary block of the same answer. */
const STATS: DrainStats = {
  nPipes: 49770,
  nMoved: 247,
  nUp: 227,
  nDown: 20,
  partial: false,
  nObs: 23,
  nTraffic: 12,
  nReports: 11,
  nSynthetic: 22,
  nReal: 1,
  capacity: {
    postPct: 28.269,
    priorPct: 28.188,
    learnedPoints: 0.081,
    learnedM3s: 52.7,
    fullM3s: 64564.1,
  },
  biggest: {
    edge: "MUM-E018763",
    name: "off D S Babrekar Marg",
    locality: null,
    place: null,
    prior: 0.2,
    post: 0.4505,
  },
  note: null,
};

/** The pipe the largest rise landed on, as the pipe cards describe it. */
const PIPE_UP: PipeCardData = {
  id: "MUM-E018763",
  name: "off D S Babrekar Marg",
  locality: null,
  place: null,
  prior: 0.2,
  post: 0.4505,
  sd: 0.1926,
  direction: "up",
  capacityLostPct: 63.1,
  diameterM: 0.45,
  observations: 0,
  movedBy: null,
  lon: 72.836727,
  lat: 19.021549,
};

/** And the one a traffic anomaly cleared: the model held 816 cm there, the queue said 20. */
const PIPE_DOWN: PipeCardData = {
  id: "MUM-E005210",
  name: "D S Babrekar Marg",
  locality: null,
  place: null,
  prior: 0.2,
  post: 0,
  sd: 0.05,
  direction: "down",
  capacityLostPct: 0,
  diameterM: 1.2,
  observations: 1,
  movedBy: OBSERVATIONS[1] ?? null,
  lon: 72.836616,
  lat: 19.021783,
};

/** Nadi's panels: the summary strip, the pipe cards, the desilting table and the observations. */
export default function NadiStories() {
  const drains = navItem("drains");
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [pipe, setPipe] = useState<string | null>(null);

  return (
    <Panel
      title={`${drains.label}: what the drains learned`}
      description="The 08:40 cycle: 23 observations moved 247 of 49,770 inferred pipes. The drain graph is inferred from roads and terrain; the traffic anomalies and seeded reports are synthetic."
    >
      <div className="flex flex-col gap-6">
        <Demo label="Summary strip">
          <DrainStatsStrip stats={STATS} />
        </Demo>
        {/* The strip's loading state is not drawn here: its skeleton puts aria-label on a div with no
            role, which axe (WCAG 2 A, aria-prohibited-attr) refuses. That is the component's to fix;
            the story returns when it carries role="status". */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo label="Pipe raised" bare>
            <PipeCard
              pipe={PIPE_UP}
              selected={pipe === PIPE_UP.id}
              onSelect={(p) => setPipe(p.id)}
            />
          </Demo>
          <Demo label="Pipe cleared by an observation" bare>
            <PipeCard
              pipe={PIPE_DOWN}
              selected={pipe === PIPE_DOWN.id}
              onSelect={(p) => setPipe(p.id)}
            />
          </Demo>
        </div>
        <Demo
          label="Desilting table"
          note="Sortable by blockage, spread, capacity and change."
          bare
        >
          <DrainHealthTable rows={ROWS} defaultSort="change" />
        </Demo>
        <Demo label="Desilting table, empty" bare>
          <DrainHealthTable rows={[]} />
        </Demo>
        <Demo
          label="Assimilation strip"
          note="Each observation with the blockage change it caused; press one to select it."
          bare
        >
          <ObservationStrip
            observations={OBSERVATIONS}
            cycleTs={CYCLE_TS}
            selectedIds={selected}
            onSelect={(obs) => setSelected([obs.id])}
            onSelectGroup={(members) => setSelected(members.map((m) => m.id))}
          />
        </Demo>
        <Demo label="Assimilation strip, nothing assimilated" bare>
          <ObservationStrip observations={[]} cycleTs={CYCLE_TS} />
        </Demo>
        <Demo
          label="Observation card"
          note="The report at Gandhi Market and the pipe it moved."
          bare
        >
          <div className="max-w-md">
            <ObservationCard obs={CARD_OBSERVATION} />
          </div>
        </Demo>
      </div>
    </Panel>
  );
}
