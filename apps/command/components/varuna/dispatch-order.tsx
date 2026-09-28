import { ClipboardList } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import { MinutesFlow } from "@/components/varuna/minutes-flow";
import { Panel } from "@/components/varuna/panel";
import { formatMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One move of the plan: this pump, from this depot, to this hotspot. */
export interface DispatchOrderMove {
  /** Stable per pump, so a new plan for the same pump rolls its figures instead of remounting. */
  id: string;
  /** Pump id, e.g. "P-12". */
  pumpId: string;
  /** Depot the pump leaves, e.g. "Parel depot". */
  from: string;
  /** Hotspot the pump serves, e.g. "Hindmata junction". */
  to: string;
  /** Travel time in minutes. */
  etaMinutes: number;
  /** Minutes above 45 cm the move avoids at that hotspot. */
  minutesAvoided: number;
}

/** The plan the optimiser produced, in the order it is read out to the control room. */
export interface DispatchOrderPlan {
  /** Run the plan was computed from, e.g. "MUM-20190702T0640-...-baked". */
  runId?: string;
  moves: DispatchOrderMove[];
}

export interface DispatchOrderProps {
  order: DispatchOrderPlan | null;
  className?: string;
}

/** "Move P-12 from Parel depot to Hindmata junction now; ETA 25 min; prevents about 40 min above 45 cm." */
export function describeMove(move: DispatchOrderMove): string {
  return `Move ${move.pumpId} from ${move.from} to ${move.to} now; ETA ${formatMinutes(
    move.etaMinutes,
  )}; prevents about ${formatMinutes(move.minutesAvoided)} above 45 cm.`;
}

/**
 * The dispatch order in plain language (SPEC.md section 7.6), one line per pump, ready to be
 * read out to the control room. The ETA and the minutes prevented are the optimiser's and roll
 * when the plan changes (motion M17); the sentence reads exactly as {@link describeMove} does,
 * and under reduced motion it is that sentence.
 */
export function DispatchOrder({ order, className }: DispatchOrderProps) {
  const moves = order?.moves ?? [];

  return (
    <Panel
      title="Dispatch order"
      description="The plan in the words the control room hears."
      className={cn("min-w-0", className)}
    >
      {moves.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title="No dispatch order yet"
          description="Press Optimise once a run is loaded."
        />
      ) : (
        <ol className="flex flex-col gap-2">
          {moves.map((move) => (
            <li key={move.id} className="rounded-control border-line bg-ink border p-3">
              <p className="num type-body text-text">
                Move {move.pumpId} from {move.from} to {move.to} now; ETA{" "}
                <MinutesFlow value={move.etaMinutes} />; prevents about{" "}
                <MinutesFlow value={move.minutesAvoided} /> above 45 cm.
              </p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
