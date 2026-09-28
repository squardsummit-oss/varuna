"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { ArrivalTimeline } from "@/components/pumps/arrival-timeline";
import { DispatchMap } from "@/components/pumps/dispatch-map";
import { dispatchOrder } from "@/components/pumps/model";
import type { GaugeClock } from "@/components/pumps/place-gauges";
import { PlaceGauges } from "@/components/pumps/place-gauges";
import { PumpHeadline } from "@/components/pumps/pump-headline";
import { Panel } from "@/components/varuna/panel";
import {
  MAP_ROUTE_MISSING,
  parsePumpMap,
  type PumpMap,
  type PumpPlan,
  type RawPumpMap,
} from "@/lib/api/pumps";
import { useMotionPref } from "@/lib/motion";
import { navItem } from "@/lib/nav";

import { Demo } from "./section";
import pumpMapBody from "./samples/pump-map-0840.json";

/**
 * `GET /v1/pumps/map` for the 08:40 IST cycle of MUM-2019-07-02, as the API answered it for the
 * Jalayantra tests (`app/pumps/__tests__/pump-map.fixture.json`, copied so this page does not
 * break when a test fixture moves). Twelve synthetic pumps, 985 minutes above 45 cm with none.
 */
const MAP: PumpMap = parsePumpMap(pumpMapBody as unknown as RawPumpMap);

/** The plan `GET /v1/pumps` serves for the same cycle, rebuilt from the map's own legs. */
const PLAN: PumpPlan = {
  runId: MAP.runId,
  thresholdCm: MAP.thresholdCm,
  benefitLabel: MAP.benefitLabel,
  inventory: MAP.inventory,
  pumps: MAP.legs.map((l) => ({
    id: l.pumpId,
    capacityM3PerHour: l.capacityM3PerHour,
    depot: l.depot.name,
    status: "available",
  })),
  assignments: MAP.legs.map((l) => ({
    pumpId: l.pumpId,
    capacityM3PerHour: l.capacityM3PerHour,
    depot: l.depot.name,
    targetId: l.target.id,
    targetName: l.target.name,
    lon: l.target.lon,
    lat: l.target.lat,
    etaMin: l.etaMin,
    minutesBefore: l.minutesBefore,
    minutesAfter: l.minutesAfter,
    minutesSaved: l.minutesSaved,
  })),
  unassigned: MAP.unassigned.map((u) => ({ name: u.name, minutesAbove: u.minutesAbove })),
  totalMinutesSaved: MAP.summary.minutesSaved,
};

/** Five of the twelve places keep the gauges and the timeline short enough to review. */
const LEGS = MAP.legs.slice(0, 5);

const IDLE: GaugeClock = { key: "idle", startMs: null, idle: true };

/** The screen's own sentence for an API older than the map route, at the local API's address. */
const NO_MAP_ERROR = MAP_ROUTE_MISSING("http://localhost:8000");

/** One dispatch state: the headline beside the gauges, with the timeline under both. */
function DispatchState({ clock, reduced }: { clock: GaugeClock | null; reduced: boolean }) {
  const order = useMemo(() => dispatchOrder(MAP), []);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <PumpHeadline plan={PLAN} order={order} clock={clock} reduced={reduced} />
        <PlaceGauges
          legs={LEGS}
          thresholdCm={MAP.thresholdCm}
          order={order}
          clock={clock}
          reduced={reduced}
          selectedPumpId={selected}
          onSelect={setSelected}
        />
      </div>
      <ArrivalTimeline
        legs={LEGS}
        cycleTs={MAP.cycleTs}
        stepMin={MAP.stepMin}
        nSteps={MAP.nSteps}
        thresholdCm={MAP.thresholdCm}
        order={order}
        clock={clock}
        reduced={reduced}
        selectedPumpId={selected}
        onSelect={setSelected}
      />
    </div>
  );
}

/**
 * Jalayantra's components (motions M33 to M35) on the 08:40 plan: idle before Optimise, sending,
 * and settled. The dispatch map is WebGL, so its canvas is drawn only on request; its loading,
 * error and empty states are plain DOM and always shown.
 */
export default function JalayantraStories() {
  const { reduced } = useMotionPref();
  const [sends, setSends] = useState(0);
  const [sending, setSending] = useState<GaugeClock>(() => ({ key: "send-0", startMs: null }));
  const [mapOpen, setMapOpen] = useState(false);
  const pumps = navItem("pumps");

  const send = () => {
    const next = sends + 1;
    setSends(next);
    setSending({ key: `send-${next}`, startMs: performance.now() });
  };

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={`${pumps.label}: what the plan buys`}
        description="The headline, the place gauges (M34) and the arrival timeline (M35) on the 08:40 plan. Synthetic pump inventory; the benefit is Flash-lite's estimate, with its measured skill printed beside it on the screen. Five of the twelve places are listed here."
      >
        <div className="flex flex-col gap-6">
          <Demo
            label="Idle, before Optimise"
            note="Every pump is at its depot: the no-pump minutes only, and the with-pumps figure reads Not sent."
          >
            <DispatchState clock={IDLE} reduced={reduced} />
          </Demo>
          <Demo
            label="Sending"
            note={
              reduced
                ? "Reduced motion is on: the figures land at their final values with no drain or slide."
                : "Each pump arrives on the dispatch clock; the gauges drain and the with-pumps figure counts down as it does."
            }
          >
            <div className="flex flex-col gap-3">
              <div>
                <Button size="sm" variant="outline" onClick={send}>
                  {sends === 0 ? "Send the plan" : "Send the plan again"}
                </Button>
              </div>
              <DispatchState clock={sends === 0 ? IDLE : sending} reduced={reduced} />
            </div>
          </Demo>
          <Demo
            label="Settled"
            note="With no dispatch to time from, the plan's own answer: 985 min to 70 min above 45 cm."
          >
            <DispatchState clock={null} reduced={reduced} />
          </Demo>
          <Demo label="Loading" note="Before GET /v1/pumps answers: shimmer, never a spinner.">
            <PumpHeadline plan={null} loading />
          </Demo>
        </div>
      </Panel>

      <Panel
        title="Dispatch map"
        description="The fleet on the city: depots, each lorry's road at the cycle time (M33) and the places it goes. The map is WebGL over Esri imagery, so this page draws it only when asked."
      >
        <div className="flex flex-col gap-4">
          <Demo
            label="The 08:40 dispatch"
            note="Drawn finished (no clock), which is also the reduced-motion state."
            bare
          >
            {mapOpen ? (
              <DispatchMap map={MAP} clock={null} className="h-[26rem]" />
            ) : (
              <div className="rounded-panel border-line bg-deep flex min-h-[10rem] flex-col items-start justify-center gap-2 border p-4">
                <p className="type-small text-text-2 max-w-[60ch]">
                  The map mounts MapLibre, deck.gl and the imagery basemap, which is most of this
                  page&rsquo;s weight. It is drawn here on request.
                </p>
                <Button size="sm" variant="outline" onClick={() => setMapOpen(true)}>
                  Draw the dispatch map
                </Button>
              </div>
            )}
          </Demo>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Loading" note="Tracing each lorry's road." bare>
              <DispatchMap map={null} loading className="min-h-[14rem]" />
            </Demo>
            <Demo label="Error" note="The API's own sentence." bare>
              <DispatchMap map={null} error={NO_MAP_ERROR} className="min-h-[14rem]" />
            </Demo>
            <Demo
              label="Empty"
              note="A plan that sends no pump anywhere; the screen passes copy naming a cycle that does."
              bare
            >
              <DispatchMap map={{ ...MAP, legs: [] }} className="min-h-[14rem]" />
            </Demo>
          </div>
        </div>
      </Panel>
    </div>
  );
}
