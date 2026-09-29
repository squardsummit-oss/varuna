"use client";

import { Layers } from "lucide-react";

import { Switch } from "@/components/ui/switch";

/** The layers the wizard's own map can draw. Deliberately not the console's list: this panel
 * offers what the pipeline has written for this city and nothing else (SPEC.md 17 - a switch
 * that does nothing is worse than no switch). */
export type WizardLayerId = "streets" | "buildings" | "drains" | "depth";

export interface WizardLayerState {
  /** Whether the layer is switched on. */
  on: boolean;
  /** How many features the pipeline wrote; undefined until the layer has arrived. */
  count?: number;
  /**
   * The layer is written and can be switched on, but is not fetched until it is: buildings, which
   * are 72,522 polygons for Chennai (2.4 MB gzipped) and off by default, as on the console.
   */
  lazy?: boolean;
  /** Switched on and still arriving. */
  loading?: boolean;
  /** One line under the row about what is drawn, e.g. which step of the forecast. */
  detail?: string;
}

export interface OnboardLayersProps {
  value: Record<WizardLayerId, WizardLayerState>;
  onChange: (key: WizardLayerId, next: boolean) => void;
}

// Two switches an officer understands at a glance: the city's streets, and the ones the forecast
// floods. Buildings and drains stay off this panel - they hid the water rather than explain it.
const ROWS: readonly { key: WizardLayerId; label: string }[] = [
  { key: "streets", label: "Streets" },
  { key: "depth", label: "Flooded streets" },
];

/**
 * The wizard's layer panel, scoped to what this build has produced (task D-21).
 *
 * A row is switchable once its layer exists on disk and says which step writes it until then, so
 * the panel doubles as a legend for the stack forming beside it. Counts are the features the
 * pipeline actually wrote for the city, never a target. Solid `--deep` rather than glass: section
 * 6.4 allows a backdrop blur on the console time bar alone.
 */
export function OnboardLayers({ value, onChange }: OnboardLayersProps) {
  return (
    <div className="rounded-panel border-line bg-deep w-[236px] border">
      <div className="border-line flex h-9 items-center gap-2 border-b px-3">
        <Layers size={16} strokeWidth={1.75} className="text-text-2 shrink-0" aria-hidden="true" />
        <span className="type-small text-text flex-1">Layers</span>
      </div>
      <ul className="p-1">
        {ROWS.map((row) => {
          const state = value[row.key];
          const ready = state.count !== undefined;
          const switchable = ready || Boolean(state.lazy);
          return (
            <li key={row.key} className="px-2 py-1.5">
              <div className="flex items-center gap-2.5">
                <Switch
                  id={`wizard-layer-${row.key}`}
                  checked={state.on}
                  disabled={!switchable}
                  onCheckedChange={(next: boolean) => onChange(row.key, next)}
                />
                <label
                  htmlFor={`wizard-layer-${row.key}`}
                  className="type-small text-text min-w-0 flex-1 truncate"
                >
                  {row.label}
                </label>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
