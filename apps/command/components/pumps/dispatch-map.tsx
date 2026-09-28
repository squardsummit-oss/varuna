"use client";

import { useMemo } from "react";

import { CityMap } from "@/components/map/city-map";
import { pumpRouteLayers, type DispatchClock } from "@/components/map/layers/pump-routes";
import { EmptyState } from "@/components/varuna/empty-state";
import type { PumpMap } from "@/lib/api/pumps";
import { cn } from "@/lib/utils";

import {
  assignmentSentence,
  depotPoints,
  dispatchBounds,
  routeLegs,
  unservedPlaces,
} from "./model";

export interface DispatchMapProps {
  map: PumpMap | null;
  loading?: boolean;
  /** Why the map could not load, in the API's own words. */
  error?: string | null;
  /** The heading over `error`, when what failed was not the map itself. */
  errorTitle?: string;
  selectedPumpId?: string | null;
  /**
   * The dispatch being drawn (M33): idle before Optimise (lorries at their depots, no roads),
   * running after it; null draws it finished. The screen owns the buttons that change it.
   */
  clock?: DispatchClock | null;
  /** Empty-state copy when the cycle sends no pump; the screen knows which cycle does. */
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: React.ReactNode;
  className?: string;
}

const NO_FRAMES: readonly (ImageBitmap | null)[] = [];
const NO_SEGMENTS: never[] = [];

/**
 * The frame keeps the plan clear of what floats over the map: the road note and the actions
 * along the top, the attribution strip along the bottom. The key sits in a bottom corner, which
 * a plan that runs north to south along Mumbai leaves empty. The camera gives margin back on a
 * narrow window rather than fitting into nothing (`usablePadding`).
 */
const FIT_PADDING = { top: 72, right: 32, bottom: 44, left: 32 };

/**
 * The signature of Jalayantra: the fleet on the city. White dots are the ward depots the lorries
 * leave from; each tide line is the road a truck would take at the cycle time, drawn behind its
 * lorry as it drives (M33); each place is a ring in the depth ramp at its peak with no pump, whose
 * disc turns to its peak with the pump when the lorry arrives, inside a tide ring that says a pump
 * is there. Faint red rings are places that cross 45 cm with no lorry left to send.
 *
 * The view opens framed on every depot and place in the plan, not on the whole city, so the
 * affected area fills the frame. A visually hidden list reads every assignment for a screen
 * reader, since a canvas cannot.
 */
export function DispatchMap({
  map,
  loading = false,
  error = null,
  errorTitle = "The dispatch map did not load",
  selectedPumpId = null,
  clock = null,
  emptyTitle,
  emptyDescription,
  emptyAction,
  className,
}: DispatchMapProps) {
  const legs = useMemo(() => routeLegs(map), [map]);
  const depots = useMemo(() => depotPoints(map), [map]);
  const unserved = useMemo(() => unservedPlaces(map), [map]);
  const bounds = useMemo(() => dispatchBounds(map), [map]);
  const layers = useMemo(
    () => pumpRouteLayers({ legs, depots, unserved, selectedPumpId, clock }),
    [legs, depots, unserved, selectedPumpId, clock],
  );
  const drawable = map !== null && legs.length > 0;

  return (
    <section
      aria-label="Dispatch map"
      aria-describedby={drawable ? "pumps-map-text" : undefined}
      className={cn(
        "rounded-panel border-line bg-deep relative min-h-[22rem] overflow-hidden border",
        className,
      )}
    >
      {drawable ? (
        <>
          <CityMap
            frames={NO_FRAMES}
            rasterBounds={null}
            baseSegments={NO_SEGMENTS}
            segments={NO_SEGMENTS}
            surcharge={NO_SEGMENTS}
            hotspots={NO_SEGMENTS}
            showRaster={false}
            showSegments={false}
            showSurcharge={false}
            showBuildings={false}
            showHotspots={false}
            basemapLayers={layers}
            bounds={bounds}
            fitBounds={bounds ?? null}
            fitPadding={FIT_PADDING}
            fitKey={map.runId}
            step={0}
          />
          <ol id="pumps-map-text" className="sr-only">
            {map.legs.map((leg) => (
              <li key={leg.pumpId}>{assignmentSentence(leg, map.thresholdCm)}</li>
            ))}
          </ol>
          <MapLegend />
        </>
      ) : (
        <EmptyState
          title={
            error ? errorTitle : loading ? "Drawing the fleet" : (emptyTitle ?? "No pumps to draw")
          }
          description={
            error ??
            (loading
              ? "Tracing each lorry's road from its depot. The first answer for a cycle takes a few seconds; the plan's numbers are already above."
              : (emptyDescription ??
                "This cycle's plan sends no pump anywhere, so there is no road to draw."))
          }
          action={!error && !loading ? emptyAction : undefined}
        />
      )}
      {drawable ? (
        <p className="type-micro text-text-2 bg-ink/80 rounded-control pointer-events-none absolute top-3 left-3 max-w-[45%] px-2 py-1">
          {map.summary.routed > 0
            ? `Roads for a truck at the cycle time; ${map.summary.routed} of ${map.legs.length} routed`
            : "Straight lines: the router gave no road for these lorries"}
        </p>
      ) : null}
    </section>
  );
}

/** What each mark on the dispatch map is. Colours are tokens; the depth ramp is depth only. */
function MapLegend() {
  return (
    <dl
      aria-label="Map key"
      className="type-micro text-text-2 bg-ink/85 rounded-control border-line pointer-events-none absolute right-3 bottom-10 grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1 border px-2.5 py-2"
    >
      <dt aria-hidden="true" className="flex w-4 justify-center">
        <span className="bg-text border-ink size-2.5 rounded-full border" />
      </dt>
      <dd>Ward depot</dd>
      <dt aria-hidden="true" className="flex w-4 items-center gap-0.5">
        <span className="bg-tide h-1 w-2.5 rounded-full" />
        <span className="bg-tide border-ink size-2 rounded-full border" />
      </dt>
      <dd>Lorry and its road</dd>
      <dt aria-hidden="true" className="flex w-4 justify-center">
        <span className="border-depth-4 bg-ink flex size-3.5 items-center justify-center rounded-full border-2">
          <span className="bg-depth-1 size-1.5 rounded-full" />
        </span>
      </dt>
      <dd>Ring: peak with no pump. Centre: with it</dd>
      <dt aria-hidden="true" className="flex w-4 justify-center">
        <span className="border-tide size-3.5 rounded-full border" />
      </dt>
      <dd>A pump is there</dd>
      <dt aria-hidden="true" className="flex w-4 justify-center">
        <span className="border-depth-4 size-2.5 rounded-full border opacity-60" />
      </dt>
      <dd>Above 45 cm, no pump left to send</dd>
    </dl>
  );
}
