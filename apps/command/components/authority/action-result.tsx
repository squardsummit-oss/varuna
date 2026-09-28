"use client";

/**
 * What an authority act did, or did not do, in one box (UI_SPEC 6, task D-15).
 *
 * **This component is the whole point of the screen.** The desk has two kinds of act and the
 * split between them is the thing a ward officer has to be able to see: a closure reaches the
 * next route, a note reaches nobody. So every act on this screen reports through one component
 * with one of three faces, and the face is a prop rather than a decision each panel makes for
 * itself - two panels wording "it worked" differently is how a desk starts implying that a note
 * changed the water.
 *
 * - `changed` - an engine consumes this. The line says which engine and what it will do.
 * - `recorded` - it is in the log and nowhere else. The line says so in those words.
 * - `refused` - it did not happen. The line is the API's own, which already names the fix.
 *
 * Nothing here is composed from a template: the panel passes the sentence it was given, and
 * `notes` carries the API's own sentences underneath, including the one that says an authority
 * edit leaves every baked product byte-identical (SPEC.md rule 6).
 */

import { AlertTriangle, ArrowRightLeft, NotebookPen } from "lucide-react";

import { cn } from "@/lib/utils";

export type ActionOutcome = "changed" | "recorded" | "refused";

export interface ActionResultProps {
  outcome: ActionOutcome;
  /** The headline: "Dr Ambedkar Road is closed. The next route avoids it." */
  message: string;
  /** The API's own notes, printed underneath and never rewritten. */
  notes?: string[];
  /** IST time the act was recorded at, when the API stamped one. */
  at?: string | null;
  className?: string;
}

const FACE: Record<
  ActionOutcome,
  { icon: typeof ArrowRightLeft; tone: string; border: string; label: string }
> = {
  changed: {
    icon: ArrowRightLeft,
    tone: "text-tide",
    border: "border-tide/40",
    label: "Changed the forecast",
  },
  recorded: {
    icon: NotebookPen,
    tone: "text-text-2",
    border: "border-line",
    label: "Recorded only",
  },
  refused: {
    icon: AlertTriangle,
    tone: "text-danger",
    border: "border-danger/40",
    label: "Refused",
  },
};

export function ActionResult({ outcome, message, notes, at, className }: ActionResultProps) {
  const face = FACE[outcome];
  const Icon = face.icon;
  return (
    <div
      role="status"
      className={cn("rounded-panel bg-well/40 border p-3", face.border, className)}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn("mt-0.5 size-4 shrink-0", face.tone)} aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className={cn("type-micro font-medium", face.tone)}>{face.label}</p>
          <p className="type-small text-text">{message}</p>
          {at ? <p className="type-micro num text-text-3">Recorded at {at}.</p> : null}
          {notes?.length ? (
            <ul className="space-y-1 pt-1">
              {notes.map((note) => (
                <li key={note} className="type-micro text-text-3">
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}
