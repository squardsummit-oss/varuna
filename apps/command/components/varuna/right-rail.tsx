"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/varuna/empty-state";
import { HotspotRail } from "@/components/varuna/hotspot-rail";
import { ReachabilityPanel } from "@/components/varuna/reachability-panel";
import type { Hotspot, HotspotSet } from "@/lib/api/hotspots";
import { RIGHT_RAIL_TABS, useUiStore, type RightRailTab } from "@/lib/stores/ui";

const TAB_LABELS: Record<RightRailTab, string> = {
  hotspots: "Hotspots",
  reachability: "Reachability",
};

export interface RightRailProps {
  /** The current run's ranked hotspots; null before one has loaded. */
  hotspots?: HotspotSet | null;
  /** Step on the time bar, so the rail's depth chips track the map. */
  step?: number;
  selectedHotspotId?: string | null;
  onSelectHotspot?: (hotspot: Hotspot) => void;
  hotspotsLoading?: boolean;
  /** The scrub time the catchment is measured at; the tab is empty without one. */
  simTime?: string | null;
  /** The console draws the bands this hands back on its own map. */
  onIsochrones?: (rings: { minutes: number; rings: [number, number][][] }[]) => void;
}

function isRightRailTab(value: unknown): value is RightRailTab {
  return typeof value === "string" && (RIGHT_RAIL_TABS as readonly string[]).includes(value);
}

/**
 * The console's right rail (SPEC.md section 6.5). It carried Hotspots, Alerts, Pumps and
 * Reachability; the Alerts and Pumps mirrors were removed at the team's request, because /alerts
 * and /pumps own those screens in full and the mirrors repeated them beside the map. SPEC.md
 * 6.5, 7.2 and task P8.11 still describe the four-tab rail.
 */
export function RightRail({
  hotspots = null,
  step = 0,
  selectedHotspotId = null,
  onSelectHotspot,
  hotspotsLoading = false,
  simTime = null,
  onIsochrones,
}: RightRailProps = {}) {
  const tab = useUiStore((s) => s.rightRailTab);
  const setTab = useUiStore((s) => s.setRightRailTab);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        if (isRightRailTab(value)) setTab(value);
      }}
      className="flex h-full min-h-0 flex-col gap-0"
    >
      <TabsList variant="line" className="h-10 w-full shrink-0 border-b border-line px-2">
        {RIGHT_RAIL_TABS.map((t) => (
          <TabsTrigger key={t} value={t}>
            {TAB_LABELS[t]}
          </TabsTrigger>
        ))}
      </TabsList>

      <TabsContent value="hotspots" className="min-h-0 flex-1">
        <HotspotRail
          hotspots={hotspots?.hotspots ?? []}
          step={step}
          selectedId={selectedHotspotId}
          onSelect={onSelectHotspot}
          ranking={hotspots?.ranking}
          impassableThresholdCm={hotspots?.impassableThresholdCm}
          loading={hotspotsLoading}
        />
      </TabsContent>

      <TabsContent value="reachability" className="min-h-0 flex-1 overflow-y-auto p-4">
        {simTime ? (
          <ReachabilityPanel at={simTime} onIsochrones={onIsochrones} />
        ) : (
          <EmptyState
            title="No reachability clocks yet"
            description="Reachability clocks appear with the first run."
          />
        )}
      </TabsContent>
    </Tabs>
  );
}
