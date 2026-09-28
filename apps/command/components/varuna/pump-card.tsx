import { cn } from "@/lib/utils";
import { formatCount, formatMinutes, MISSING } from "@/lib/format";

/** Status of a mobile dewatering pump in the inventory (SPEC.md section 7.6). */
export type PumpStatus = "available" | "moving" | "pumping" | "maintenance";

export const PUMP_STATUS_LABELS: Record<PumpStatus, string> = {
  available: "Available",
  moving: "Moving",
  pumping: "Pumping",
  maintenance: "Maintenance",
};

/**
 * One pump of the synthetic inventory. Twelve pumps sit at plausible BMC depots and are labelled
 * synthetic everywhere they appear (SPEC.md section 3.3).
 */
export interface Pump {
  /** Inventory id, e.g. "P-12". */
  id: string;
  /** Rated capacity in cubic metres per hour. */
  capacityM3PerHour: number;
  /** Depot the pump stands at, e.g. "Parel depot". */
  depot: string;
  status: PumpStatus;
  /** Travel time to the hotspot it is being considered for; null until a run gives a route. */
  etaMinutes?: number | null;
  /** Hotspot column the pump is assigned to; undefined while it is still available. */
  assignedTo?: string;
}

export interface PumpCardProps {
  pump: Pump;
  className?: string;
}

/**
 * A pump as a draggable card (SPEC.md section 7.6): id, capacity, depot, status and the ETA to
 * the hotspot it would serve. Drag and drop lands in Phase 8; the card is static markup here.
 */
export function PumpCard({ pump, className }: PumpCardProps) {
  const eta =
    typeof pump.etaMinutes === "number"
      ? `ETA ${formatMinutes(pump.etaMinutes)}`
      : `ETA ${MISSING}`;

  return (
    <article
      data-slot="pump-card"
      aria-label={`Pump ${pump.id}`}
      className={cn(
        "rounded-control border-line bg-deep flex flex-col gap-2 border p-3",
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="num type-body text-text font-medium">{pump.id}</h4>
        <span className="num type-micro text-text-2">
          {formatCount(pump.capacityM3PerHour)} m³/h
        </span>
      </div>
      <p className="type-small text-text-2">{pump.depot}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-chip border-line type-micro text-text-2 border px-2 py-0.5">
          {PUMP_STATUS_LABELS[pump.status]}
        </span>
        <span className="num type-micro text-text-3">{eta}</span>
      </div>
    </article>
  );
}
