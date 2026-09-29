"use client";

import { Radar } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/varuna/empty-state";
import { RadarPreview } from "@/components/varuna/radar-preview";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst, formatMinutes } from "@/lib/format";
import { cssVar, rainBand } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** One convective cell of the storm designer (SPEC.md section 10.2). */
export interface StormCell {
  id: string;
  /** Birth time, ISO 8601 with +05:30. */
  birth: string;
  lifetimeMin: number;
  /** Start point in WGS84. */
  startLat: number;
  startLon: number;
  /** Advection speed in m/s (wind from the south-west for the demo). */
  velocityMs: number;
  /** Gaussian sigma in km. */
  sigmaKm: number;
  /** Peak intensity in mm/h. */
  peakMmH: number;
}

/**
 * A design storm's Chicago hyetograph (SPEC.md section 10.2, task P2.8). Design-storm bundles
 * carry no convective cells, so this takes the cell table's place. The peak sits at
 * `peakPositionR` of the duration: it is a shaped storm, never a uniform block of rain.
 */
export interface DesignStormHyetograph {
  /** Block length in minutes. */
  stepMin: number;
  /** Block intensities in mm/h, one per block. */
  blocksMmH: number[];
  /** Depth over the whole storm in mm. */
  totalDepthMm: number;
  /** Where the peak block sits, as a fraction of the duration (0.4 = 40 % in). */
  peakPositionR: number;
}

export interface StormDesignerProps {
  /** Cells of the selected bundle; empty until the bundle is generated, and on a design storm. */
  cells: StormCell[];
  /** The hyetograph a design storm carries instead of cells; absent on a reconstructed replay. */
  designStorm?: DesignStormHyetograph;
  /** Bundle the preview plays. */
  bundleId: string;
  /** False until `make bundle` has written the bundle folder; the preview then fetches nothing. */
  built: boolean;
  /** Bundle members `make bundle` has not written yet, from `GET /v1/replay/bundles`. */
  missingMembers: readonly string[];
  /**
   * True while the bundle list or the storm index is on its way. Without it the designer said
   * "Run make bundle" over a bundle that was built and merely still loading.
   */
  loading?: boolean;
  /** Why the storm index did not load, in the API's words; the cell table says so. */
  cellsError?: string | null;
  /** Reason the storm cannot be edited yet. */
  generateDisabledReason?: string;
  className?: string;
}

/** "19.012, 72.841" for a start point. */
export function formatLatLon(lat: number, lon: number): string {
  return `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
}

/** Floor on a bar's height so the lightest block of a design storm is still visible. */
const MIN_BAR_FRACTION = 0.06;

/** The rain ramp's colour for one block, so a block reads like a radar pixel of the same rate. */
function blockColor(mmH: number): string {
  const band = rainBand(mmH);
  return cssVar(band ? `--rain-${band.key}` : "--rain-1");
}

/**
 * A design storm's hyetograph as a block sparkline: one bar per block, height linear in mm/h
 * against the peak, coloured by the shared rain ramp (SPEC.md section 6.2). The caption
 * carries the depth, the peak and where the peak sits, so colour never carries the meaning
 * alone and nobody can read the storm as a uniform block of rain.
 */
function HyetographSparkline({ storm }: { storm: DesignStormHyetograph }) {
  const peakMmH = Math.max(...storm.blocksMmH);
  const durationMin = storm.stepMin * storm.blocksMmH.length;
  const peakMin = Math.round(storm.peakPositionR * durationMin);
  const peakPercent = Math.round(storm.peakPositionR * 100);
  const summary =
    `${Math.round(storm.totalDepthMm)} mm over ${formatMinutes(durationMin)} in ` +
    `${storm.stepMin}-minute blocks. Peak ${Math.round(peakMmH)} mm/h at ${peakMin} min, ` +
    `${peakPercent} % into the storm.`;

  return (
    <figure className="space-y-2">
      <figcaption className="type-small text-text-2">Chicago hyetograph</figcaption>
      <div
        role="img"
        aria-label={summary}
        className="rounded-control border-line bg-well/40 flex h-16 items-end gap-px border p-1"
      >
        {storm.blocksMmH.map((mmH, index) => (
          <div
            key={index * storm.stepMin}
            className="rounded-chip min-w-0 flex-1"
            style={{
              height: `${Math.max(MIN_BAR_FRACTION, mmH / peakMmH) * 100}%`,
              background: blockColor(mmH),
            }}
          />
        ))}
      </div>
      <p className="num type-micro text-text-3">{summary}</p>
    </figure>
  );
}

/**
 * Read-only storm designer: the cell table, the radar preview slot and a disabled
 * "Generate bundle" that says why. Editing is a pilot feature; the demo storm is fixed by seed.
 * A design storm has no cells, so the table gives way to its hyetograph sparkline.
 */
export function StormDesigner({
  cells,
  designStorm,
  bundleId,
  built,
  missingMembers,
  loading = false,
  cellsError = null,
  generateDisabledReason = "Coming in pilot. The demo storm is fixed by its seed.",
  className,
}: StormDesignerProps) {
  const helpId = "storm-designer-generate-help";

  return (
    <div className={cn("space-y-4", className)}>
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="min-w-0">
          {loading ? (
            <div className="space-y-1.5" aria-busy="true" aria-label="Loading the storm">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : cellsError ? (
            <EmptyState icon={Radar} title="The storm did not load" description={cellsError} />
          ) : cells.length === 0 && designStorm ? (
            <HyetographSparkline storm={designStorm} />
          ) : cells.length === 0 ? (
            <EmptyState
              icon={Radar}
              title="No storm cells yet"
              description="Storm cells appear when the bundle is generated."
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Birth time</TableHead>
                    <TableHead>Lifetime</TableHead>
                    <TableHead>Start point</TableHead>
                    <TableHead className="text-right">Velocity</TableHead>
                    <TableHead className="text-right">Size</TableHead>
                    <TableHead className="text-right">Peak intensity</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cells.map((cell) => (
                    <TableRow key={cell.id} className="h-8">
                      <TableCell className="num">{formatIst(cell.birth)}</TableCell>
                      <TableCell className="num">{formatMinutes(cell.lifetimeMin)}</TableCell>
                      <TableCell className="num">
                        {formatLatLon(cell.startLat, cell.startLon)}
                      </TableCell>
                      <TableCell className="num text-right">
                        {cell.velocityMs.toFixed(1)} m/s
                      </TableCell>
                      <TableCell className="num text-right">{cell.sigmaKm.toFixed(1)} km</TableCell>
                      <TableCell className="num text-right">
                        {Math.round(cell.peakMmH)} mm/h
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>

        <div role="region" aria-label="Radar preview" className="min-w-0">
          {loading && !built ? (
            <Skeleton className="aspect-square w-full" />
          ) : (
            <RadarPreview bundleId={bundleId} built={built} missingMembers={missingMembers} />
          )}
        </div>
      </div>

      <div className="border-line flex flex-wrap items-center gap-3 border-t pt-4">
        <Button variant="outline" disabled aria-describedby={helpId}>
          Generate bundle
        </Button>
        <p id={helpId} className="type-micro text-text-3">
          {generateDisabledReason}
        </p>
      </div>
    </div>
  );
}
