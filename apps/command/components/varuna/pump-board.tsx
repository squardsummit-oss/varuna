"use client";

import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { PackageOpen } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/varuna/empty-state";
import { MinutesFlow } from "@/components/varuna/minutes-flow";
import { PumpCard, type Pump } from "@/components/varuna/pump-card";
import { Sparkline } from "@/components/varuna/sparkline";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { presetFor } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** A hotspot column of the board and a dnd-kit droppable. `pumps` are the pumps placed there. */
export interface PumpColumn {
  /** Stable id, also the droppable id, e.g. "hindmata". */
  id: string;
  /** Hotspot name as the operator reads it, e.g. "Hindmata junction". */
  title: string;
  /** Pumps assigned to this hotspot. */
  pumps: Pump[];
  /** Minutes above 45 cm without and with the plan; null until a run has been loaded. */
  minutesAbove45?: { before: number; after: number } | null;
  /**
   * The place's forecast depth over the next three hours, one value per step, with no pump and
   * with the optimiser's plan (`GET /v1/pumps/map`). `after` is null when the board no longer is
   * the optimiser's plan, since the series belongs to that plan and not to a hand-moved one.
   */
  depthCm?: { before: readonly number[]; after: readonly number[] | null } | null;
}

/** The three chronic hotspots the demo dispatches to (SPEC.md section 3.3). */
export const DEFAULT_PUMP_COLUMNS: readonly PumpColumn[] = [
  { id: "hindmata", title: "Hindmata junction", pumps: [], minutesAbove45: null },
  { id: "kings-circle", title: "King's Circle", pumps: [], minutesAbove45: null },
  { id: "sion-circle", title: "Sion Circle", pumps: [], minutesAbove45: null },
];

/** The one sentence every disabled control on this board carries. */
export const PUMP_ACTIONS_HELPER =
  "The plan is the greedy optimiser's; it is recomputed when the cycle runs.";

/** The shared vertical scale of every column's sparkline, so a taller line is deeper water. */
function depthCeiling(columns: readonly PumpColumn[]): number {
  let peak = 60;
  for (const c of columns) for (const v of c.depthCm?.before ?? []) peak = Math.max(peak, v);
  return peak;
}

/**
 * What a column's sparkline shows, in words: the peak with no pump, and with the plan.
 *
 * `pending` is what to say while there is no series because the answer that carries it has not
 * arrived or failed - "No depth series" is kept for a settled answer that has none, since said
 * while the series is still loading it is false.
 */
export function depthCaption(
  column: PumpColumn,
  planApplied: boolean,
  pending: string | null = null,
): string {
  const before = column.depthCm?.before ?? [];
  if (before.length === 0) return pending ?? "No depth series for this place in this run";
  const peakBefore = Math.round(Math.max(...before));
  const after = planApplied ? column.depthCm?.after : null;
  if (after && after.length > 0) {
    return `Peak ${Math.round(Math.max(...after))} cm with the plan, ${peakBefore} cm without`;
  }
  return `Peak ${peakBefore} cm with no pump, next 3 h`;
}

export interface PumpBoardProps {
  /** The available pumps, not yet assigned to a hotspot. */
  pumps: Pump[];
  columns: readonly PumpColumn[];
  /** Runs the greedy optimiser; absent in Phase 0, so the button stays disabled. */
  onOptimise?: () => void;
  /** Sends the current plan; absent in Phase 0, so the button stays disabled. */
  onDispatch?: () => void;
  /** Moves a pump to a hotspot column, or back to the pool when `columnId` is null.
   *
   * Absent leaves the board read-only: a card that can be picked up but not put down is worse
   * than one that never moves. */
  onAssign?: (pumpId: string, columnId: string | null) => void;
  /**
   * Whether the optimiser's plan is on the board. True (the default) prints each column's
   * minutes above 45 cm with the plan; false prints the figure with no pump sent, and pressing
   * Optimise rolls it to the plan's (motion M17).
   */
  planApplied?: boolean;
  /**
   * What a column with no depth series says while the series is loading or failed to load (the
   * screen's words); null or absent once the answer is settled.
   */
  depthNote?: string | null;
  className?: string;
}

/** The droppable id of the "Available pumps" column: dropping here un-assigns a pump. */
const POOL_ID = "__pool__";

/**
 * Motion M17 for one card: a shared `layoutId`, which is what makes the card *fly* between columns
 * rather than vanish from one and appear in another, on the catalogue's M17 transition. Under
 * reduced motion there is no layout id and a zero-duration transition, so the card simply moves.
 */
export function pumpCardMotion(
  pumpId: string,
  reducedMotion: boolean,
): {
  layoutId: string | undefined;
  transition: NonNullable<ReturnType<typeof presetFor>["transition"]>;
} {
  return {
    layoutId: reducedMotion ? undefined : `pump-${pumpId}`,
    transition: presetFor("M17", reducedMotion).transition ?? { duration: 0 },
  };
}

/** One draggable pump card, flying between columns per {@link pumpCardMotion}. */
function DraggablePump({
  pump,
  disabled,
  reducedMotion,
}: {
  pump: Pump;
  disabled: boolean;
  reducedMotion: boolean;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: pump.id,
    disabled,
  });
  const { layoutId, transition } = pumpCardMotion(pump.id, reducedMotion);

  return (
    <motion.div
      ref={setNodeRef}
      layoutId={layoutId}
      transition={transition}
      // The original stays in place at low opacity while the overlay follows the cursor, so the
      // column it came from does not reflow underneath the drag.
      style={{ opacity: isDragging ? 0.35 : 1 }}
      className={disabled ? undefined : "cursor-grab active:cursor-grabbing"}
      {...listeners}
      {...attributes}
    >
      <PumpCard pump={pump} />
    </motion.div>
  );
}

/** A column that accepts a dropped pump, lit while one is over it. */
function DropColumn({
  id,
  children,
  className,
}: {
  id: string;
  children: React.ReactNode;
  className?: string;
}) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div
      ref={setNodeRef}
      data-column-id={id}
      className={cn(
        className,
        // `--line-strong` on hover-over: the only feedback that says "this is where it lands".
        isOver && "border-line-strong bg-well",
      )}
    >
      {children}
    </div>
  );
}

/**
 * "Minutes above 45 cm: 0 min with the plan, 1 h 40 min without". The first figure is the board as
 * it stands and keeps its place in the tree whether or not the plan is applied, so Optimise rolls
 * it from the no-pump figure to the plan's rather than swapping one string for another.
 */
function BenefitLine({ column, planApplied }: { column: PumpColumn; planApplied: boolean }) {
  const benefit = column.minutesAbove45;
  if (!benefit) return <>Minutes above 45 cm: no data</>;
  return (
    <>
      Minutes above 45 cm: <MinutesFlow value={planApplied ? benefit.after : benefit.before} />
      {planApplied ? (
        <>
          {" with the plan, "}
          <MinutesFlow value={benefit.before} />
          {" without"}
        </>
      ) : (
        " with no pump sent"
      )}
    </>
  );
}

/**
 * The dispatch board (SPEC.md section 7.6): an "Available pumps" column beside one column per
 * hotspot, each showing the minutes above 45 cm with and without the plan. The greedy optimiser
 * and the benefit estimate are live (P8.9). Cards fly between columns on a drag or on Optimise
 * and the benefit figures roll (motion M17). A drag moves a card and never a number: recomputing
 * the benefit per drop is the emulator's job, so the figures stay the optimiser's.
 */
export function PumpBoard({
  pumps,
  columns,
  onOptimise,
  onDispatch,
  onAssign,
  planApplied = true,
  depthNote = null,
  className,
}: PumpBoardProps) {
  const helperId = "pump-board-actions-helper";
  const reducedMotion = usePrefersReducedMotion();
  const [dragging, setDragging] = useState<Pump | null>(null);

  // A short activation distance, so a click on a card is still a click and only a deliberate
  // pull starts a drag.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const ceiling = depthCeiling(columns);

  const byId = new Map<string, Pump>();
  for (const pump of pumps) byId.set(pump.id, pump);
  for (const column of columns) for (const pump of column.pumps) byId.set(pump.id, pump);

  const onDragStart = (event: DragStartEvent) =>
    setDragging(byId.get(String(event.active.id)) ?? null);

  const onDragEnd = (event: DragEndEvent) => {
    setDragging(null);
    const over = event.over?.id;
    if (over === undefined || !onAssign) return;
    const target = String(over);
    onAssign(String(event.active.id), target === POOL_ID ? null : target);
  };

  const board = (
    <section
      data-slot="pump-board"
      aria-label="Pump board"
      className={cn("flex flex-col gap-3", className)}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="type-h3 text-text font-medium">Board</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!onOptimise}
            aria-disabled={!onOptimise}
            aria-describedby={onOptimise ? undefined : helperId}
            onClick={onOptimise}
          >
            Optimise
          </Button>
          <Button
            size="sm"
            disabled={!onDispatch}
            aria-disabled={!onDispatch}
            aria-describedby={onDispatch ? undefined : helperId}
            onClick={onDispatch}
          >
            Dispatch pumps
          </Button>
        </div>
      </div>

      {onOptimise && onDispatch ? null : (
        <p id={helperId} className="type-small text-text-3">
          {PUMP_ACTIONS_HELPER}
        </p>
      )}

      {/* `layoutScroll` so a card's flight is measured against this container's scroll offset;
          without it a board scrolled sideways would launch cards from the wrong place. */}
      <motion.div layoutScroll className="flex gap-4 overflow-x-auto pb-2">
        <DropColumn
          id={POOL_ID}
          className="rounded-panel border-line bg-deep flex w-[280px] shrink-0 flex-col border transition-colors"
        >
          <header className="border-line border-b px-4 py-3">
            <h3 className="type-small text-text font-medium">Available pumps</h3>
            <p className="type-micro text-text-3">Synthetic pump inventory</p>
          </header>
          <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
            {pumps.length === 0 ? (
              // An empty pool means two different things: no inventory at all, or every pump
              // already placed on a hotspot. Saying "no pumps loaded" over twelve assigned cards
              // would be false.
              columns.some((column) => column.pumps.length > 0) ? (
                <EmptyState
                  size="sm"
                  icon={PackageOpen}
                  title="Every pump is assigned"
                  description={
                    onAssign
                      ? "Drag a pump back here to take it off its hotspot."
                      : "Each pump is on a hotspot in the plan."
                  }
                />
              ) : (
                <EmptyState
                  size="sm"
                  icon={PackageOpen}
                  title="No pumps loaded yet"
                  description="The inventory arrives with the city layers."
                />
              )
            ) : (
              pumps.map((pump) => (
                <DraggablePump
                  key={pump.id}
                  pump={pump}
                  disabled={!onAssign}
                  reducedMotion={reducedMotion}
                />
              ))
            )}
          </div>
        </DropColumn>

        {columns.map((column) => (
          <DropColumn
            key={column.id}
            id={column.id}
            className="rounded-panel border-line bg-deep flex w-[280px] shrink-0 flex-col border transition-colors"
          >
            <header className="border-line border-b px-4 py-3">
              <h3 className="type-small text-text font-medium">{column.title}</h3>
              <p className="num type-micro text-text-3">{depthCaption(column, planApplied, depthNote)}</p>
            </header>
            <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
              {column.depthCm && column.depthCm.before.length > 1 ? (
                <Sparkline
                  values={
                    planApplied && column.depthCm.after?.length
                      ? column.depthCm.after
                      : column.depthCm.before
                  }
                  maxValue={ceiling}
                  width={254}
                  height={36}
                  className="rounded-control bg-ink"
                />
              ) : null}
              <p className="num type-micro text-text-2">
                <BenefitLine column={column} planApplied={planApplied} />
              </p>
              {column.pumps.length === 0 ? (
                <EmptyState
                  size="sm"
                  title="No pump assigned"
                  description="Drag a pump here, or press Optimise to place the plan."
                />
              ) : (
                column.pumps.map((pump) => (
                  <DraggablePump
                    key={pump.id}
                    pump={pump}
                    disabled={!onAssign}
                    reducedMotion={reducedMotion}
                  />
                ))
              )}
            </div>
          </DropColumn>
        ))}
      </motion.div>
    </section>
  );

  if (!onAssign) return board;

  return (
    <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
      {board}
      {/* The card under the cursor. A `DragOverlay` renders outside the scroll container, so the
          card does not disappear behind a column edge while it is being carried across. */}
      <DragOverlay dropAnimation={reducedMotion ? null : undefined}>
        {dragging ? (
          <div className="w-[256px] rotate-2 opacity-95">
            <PumpCard pump={dragging} />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
