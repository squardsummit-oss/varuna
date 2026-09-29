"use client";

import { X } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { WhatIfDetails } from "@/components/varuna/whatif-details";
import { cn } from "@/lib/utils";

/** The four levers of a what-if scenario (SPEC.md section 7.7). */
export interface WhatIfValues {
  /** Rain multiplier, 0.5 to 2.0. */
  rainScale: number;
  /** Tide offset in metres, -0.5 to +1.0. */
  tideOffsetM: number;
  /** Clean the top 14 pipes by learned blockage, city-wide (beta to 0.05). */
  cleanTop14: boolean;
  /**
   * Road segments whose pipe is cleaned (beta to 0.05), picked on a hotspot and carried here by
   * the "Clean in what-if" deep link. Road-segment ids, which is the vocabulary `POST /v1/whatif`
   * takes; drain edge ids are refused by it with 422.
   */
  cleanedSegments: string[];
  /** Run the cycle's own pump plan from each pump's arrival (a lower bound). */
  pumpPlan: boolean;
}

/**
 * What cleaning can move at all, measured rather than asserted (ADR-0042): cleaning **every** one
 * of the city's 21,296 segments at once moves the deepest street by 3.5 cm on the 08:40 cycle. The
 * fit is element-wise per segment, so a segment's own cleaning is the only cleaning that reaches
 * it. Kept in the chips' Details, where the operator can read it before pressing Run.
 */
export const CLEANING_CEILING_NOTE =
  "Cleaning is element-wise per segment, so these streets change and no others. Measured " +
  "ceiling: cleaning all 21,296 segments at once moves the deepest street 3.5 cm (ADR-0042).";

/**
 * What the tide lever does, said before Run rather than discovered after it. Flash-lite has no sea
 * level (ADR-0025), so a tide runs on one full-city coupled Twin (`POST /v1/whatif/twin`).
 */
export const TIDE_OFFSET_NOTE = "Runs the full-city Twin; the bar shows the time left.";

/** The pump lever, as the endpoint prices it (SPEC.md 7.7; a lower bound, labelled). */
export const PUMP_PLAN_NOTE =
  "This cycle's plan, from each pump's arrival. A lower bound. Synthetic pump inventory.";

/** Section 7.7's "Clean top 14 by blockage", said as what the endpoint does. */
export const CLEAN_TOP_NOTE = "The 14 pipes with the highest learned blockage, cleaned to 0.05.";

/** The rain lever's one line: the demo's moment, as a number. */
export const RAIN_SCALE_NOTE = "1.3x is rain plus 30 %.";

export const RAIN_SCALE_MIN = 0.5;
export const RAIN_SCALE_MAX = 2.0;
export const TIDE_OFFSET_MIN = -0.5;
export const TIDE_OFFSET_MAX = 1.0;
export const WHATIF_STEP = 0.1;

export const DEFAULT_WHATIF_VALUES: WhatIfValues = {
  rainScale: 1.0,
  tideOffsetM: 0,
  cleanTop14: false,
  cleanedSegments: [],
  pumpPlan: false,
};

/** "1.0x", "1.3x". */
export function formatRainScale(scale: number): string {
  return `${scale.toFixed(1)}x`;
}

/** "+0.0 m", "-0.5 m", "+1.0 m"; always signed so an offset never reads as a stage. */
export function formatTideOffset(metres: number): string {
  const rounded = Math.round(metres * 10) / 10;
  const sign = rounded < 0 ? "-" : "+";
  return `${sign}${Math.abs(rounded).toFixed(1)} m`;
}

/**
 * The slider's value on the 0.1 grid it steps on. A range from -0.5 in steps of 0.1 lands on
 * 5.55e-17 rather than 0, and a tide that is not exactly 0 sends the question to the Twin.
 */
function firstValue(value: number | readonly number[]): number {
  const raw = Array.isArray(value) ? Number(value[0]) : Number(value);
  return Math.round(raw / WHATIF_STEP) / Math.round(1 / WHATIF_STEP);
}

/**
 * The tide on a lever that offers only some values (a cache-only API): the value itself when it
 * is one of them, else the run's own tide. With no stops, any value in range stands.
 */
export function snapTide(metres: number, stops: readonly number[] | null | undefined): number {
  if (!stops) return metres;
  return stops.some((stop) => Math.abs(stop - metres) < 1e-9) ? metres : 0;
}

interface SwitchRowProps {
  label: string;
  /** What the lever does, shown under the label while it works. */
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** The lever is not wired to the request; the switch is inert and says why. */
  disabled?: boolean;
  /** What is missing and what would land it. Replaces the description while disabled. */
  disabledReason?: string;
}

/**
 * One switch with its label and sub-copy. A lever the request does not carry is disabled and the
 * sub-copy becomes the reason, so the row never describes work the endpoint is not asked to do
 * (SPEC.md 6.8, and section 17: never a dead control).
 */
function SwitchRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled = false,
  disabledReason,
}: SwitchRowProps) {
  const uid = useId();
  const helpId = `${uid}-help`;
  const help = disabled ? disabledReason : description;

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="type-small text-text font-medium">{label}</p>
        {help ? (
          <p id={helpId} className="type-micro text-text-3">
            {help}
          </p>
        ) : null}
      </div>
      <Switch
        aria-label={label}
        aria-describedby={help ? helpId : undefined}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
      />
    </div>
  );
}

interface CleanedSegmentsProps {
  segmentIds: readonly string[];
  onRemove: (segmentId: string) => void;
  /** Where the ids came from, e.g. the hotspot the deep link was pressed on. */
  source?: string;
  /** A segment's street name for its chip; the id is shown when there is none. */
  segmentLabel?: (segmentId: string) => string | undefined;
}

/**
 * The segments this scenario cleans, as removable chips named by their street.
 *
 * They arrive from a hotspot's "Clean in what-if" rather than being typed. With none picked the
 * section says where they come from instead of showing an empty box (SPEC.md 6.8). The measured
 * ceiling sits in Details beside the chips (ADR-0042).
 */
function CleanedSegments({ segmentIds, onRemove, source, segmentLabel }: CleanedSegmentsProps) {
  const uid = useId();
  const labelId = `${uid}-cleaned`;

  return (
    <section aria-labelledby={labelId} className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <span id={labelId} className="type-small text-text font-medium">
          Pipes to clean
        </span>
        <span className="num type-micro text-text-3">
          {segmentIds.length} segment{segmentIds.length === 1 ? "" : "s"}
        </span>
      </div>
      {segmentIds.length === 0 ? (
        <p className="type-micro text-text-3">
          None picked. Press &ldquo;Clean in what-if&rdquo; on a console hotspot.
        </p>
      ) : (
        <>
          <ul className="flex flex-wrap gap-1.5">
            {segmentIds.map((segmentId) => {
              const label = segmentLabel?.(segmentId) ?? segmentId;
              return (
                <li key={segmentId}>
                  <span
                    title={segmentId}
                    className="rounded-chip border-line bg-well inline-flex max-w-full items-center gap-1 border py-0.5 pr-1 pl-2"
                  >
                    <span className="type-micro text-text-2 truncate">{label}</span>
                    <button
                      type="button"
                      aria-label={`Remove segment ${segmentId}`}
                      onClick={() => onRemove(segmentId)}
                      className="rounded-chip text-text-3 hover:text-text focus-visible:ring-tide p-0.5 transition-colors focus-visible:ring-2 focus-visible:outline-none"
                    >
                      <X size={12} strokeWidth={1.75} />
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
          {source ? <p className="type-micro text-text-3">From {source}.</p> : null}
          <WhatIfDetails>
            <p>{CLEANING_CEILING_NOTE}</p>
          </WhatIfDetails>
        </>
      )}
    </section>
  );
}

interface TideStopsProps {
  labelId: string;
  stops: readonly number[];
  value: number;
  onChange: (metres: number) => void;
}

/**
 * The tide as the values a cache-only API can answer, one radio chip each, in place of a slider
 * that would reach values nothing can answer. Native radios, so the arrow keys move the choice.
 */
function TideStops({ labelId, stops, value, onChange }: TideStopsProps) {
  const name = useId();
  return (
    <div role="radiogroup" aria-labelledby={labelId} className="flex flex-wrap gap-1.5">
      {stops.map((stop) => {
        const checked = Math.abs(stop - value) < 1e-9;
        return (
          <label
            key={stop}
            className={cn(
              "rounded-chip num type-small inline-flex min-h-8 cursor-pointer items-center border px-3 transition-colors",
              "has-[:focus-visible]:ring-tide has-[:focus-visible]:ring-2",
              checked
                ? "border-tide bg-tide-soft text-text"
                : "border-line text-text-2 hover:text-text",
            )}
          >
            <input
              type="radio"
              name={name}
              className="sr-only"
              checked={checked}
              onChange={() => onChange(stop)}
            />
            {formatTideOffset(stop)}
          </label>
        );
      })}
    </div>
  );
}

export interface WhatIfControlsProps {
  /** Starting values; the component owns its state after mount. */
  initial?: Partial<WhatIfValues>;
  /** Called with the full scenario after every change. */
  onChange?: (values: WhatIfValues) => void;
  /** Called when "Run what-if" is pressed; the button is disabled while absent. */
  onRun?: (values: WhatIfValues) => void;
  /** Called when "Physics check" is pressed; the button is disabled while absent. */
  onPhysicsCheck?: (values: WhatIfValues) => void;
  /** Helper text under the disabled run button. */
  runDisabledReason?: string;
  /** Helper text under the disabled physics-check button. */
  physicsDisabledReason?: string;
  /** Where the preselected segments came from, printed under the chips. */
  cleanedSource?: string;
  /** A cleaned segment's street name for its chip. */
  segmentLabel?: (segmentId: string) => string | undefined;
  /**
   * The only tide offsets this API can answer (a cache-only API; `GET /v1/whatif/twin`). Given,
   * the tide is a set of chips instead of a slider, and a tide off the list is sent as +0.0 m.
   */
  tideStops?: readonly number[] | null;
  /** The tide lever's one line; the full-city Twin note when absent. */
  tideNote?: string;
  /** The clean-top-14 switch is inert; nothing ranks pipes by beta on this run. */
  cleanDisabled?: boolean;
  /** Why cleaning is inert, shown in place of the switch's sub-copy. */
  cleanDisabledReason?: string;
  /** The pump-plan switch is inert; the request carries no plan. */
  pumpDisabled?: boolean;
  /** Why the pump plan is inert, shown in place of the switch's sub-copy. */
  pumpDisabledReason?: string;
  className?: string;
}

/**
 * The what-if lab's control column: rain scale and tide offset sliders, the segments this
 * scenario cleans, the clean-top-14 and pump-plan switches, and the two actions. State is local;
 * the page reads it through `onChange`. Every number carries its unit and the sliders announce
 * their value.
 */
export function WhatIfControls({
  initial,
  onChange,
  onRun,
  onPhysicsCheck,
  // The defaults describe the component with no handler, which only a story renders: the lab
  // always passes `onRun`. They say what is missing rather than promising a phase that has
  // already come (SPEC.md 6.8).
  runDisabledReason = "Not connected to the emulator here. The what-if lab wires this button " +
    "to POST /v1/whatif",
  physicsDisabledReason = "Not connected here. The what-if lab wires this button to " +
    "POST /v1/whatif/physics-check",
  cleanedSource,
  segmentLabel,
  tideStops,
  tideNote = TIDE_OFFSET_NOTE,
  cleanDisabled = false,
  cleanDisabledReason,
  pumpDisabled = false,
  pumpDisabledReason,
  className,
}: WhatIfControlsProps) {
  const [values, setValues] = useState<WhatIfValues>({ ...DEFAULT_WHATIF_VALUES, ...initial });
  // The scenario as of the last change, which can be ahead of `values`: two changes that land
  // before a re-render (a slider drag, two chips removed in one batch) compose on each other
  // instead of the second rebuilding from the render both handlers were drawn in.
  const latest = useRef(values);
  const uid = useId();
  const rainLabelId = `${uid}-rain`;
  const tideLabelId = `${uid}-tide`;
  const runHelpId = `${uid}-run-help`;
  const physicsHelpId = `${uid}-physics-help`;

  // `onChange` is called here, in the handler, and never inside a state updater. React runs an
  // updater while it renders this component, and the lab passes its own setter as `onChange`, so
  // calling it from one updated the lab mid-render ("Cannot update a component while rendering a
  // different component").
  const update = (
    patch: Partial<WhatIfValues> | ((prev: WhatIfValues) => Partial<WhatIfValues>),
  ) => {
    const prev = latest.current;
    const next = { ...prev, ...(typeof patch === "function" ? patch(prev) : patch) };
    latest.current = next;
    setValues(next);
    onChange?.(next);
  };

  const canRun = Boolean(onRun);
  const canCheck = Boolean(onPhysicsCheck);
  // What is sent: a tide this API cannot answer is the run's own, and the control shows that.
  const tide = snapTide(values.tideOffsetM, tideStops);
  const asked = tide === values.tideOffsetM ? values : { ...values, tideOffsetM: tide };

  return (
    <div className={cn("space-y-6", className)}>
      <section aria-labelledby={rainLabelId} className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <span id={rainLabelId} className="type-small text-text font-medium">
            Rain scale
          </span>
          <output className="num type-small text-text-2" htmlFor={rainLabelId}>
            {formatRainScale(values.rainScale)}
          </output>
        </div>
        <Slider
          aria-labelledby={rainLabelId}
          min={RAIN_SCALE_MIN}
          max={RAIN_SCALE_MAX}
          step={WHATIF_STEP}
          value={[values.rainScale]}
          onValueChange={(value) => update({ rainScale: firstValue(value) })}
        />
        <p className="type-micro text-text-3">{RAIN_SCALE_NOTE}</p>
      </section>

      <section aria-labelledby={tideLabelId} className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <span id={tideLabelId} className="type-small text-text font-medium">
            Tide offset
          </span>
          {tideStops ? null : (
            <span className="flex items-baseline gap-2">
              <output className="num type-small text-text-2" htmlFor={tideLabelId}>
                {formatTideOffset(tide)}
              </output>
              {/* Back to the run's own tide in one press: a slider has no reliable way to land
                  exactly on 0 by drag, and a tide of +0.1 m sends the question to the Twin. */}
              <Button
                variant="ghost"
                size="xs"
                aria-label="Reset the tide offset to +0.0 m"
                disabled={tide === 0}
                onClick={() => update({ tideOffsetM: 0 })}
              >
                Reset
              </Button>
            </span>
          )}
        </div>
        {tideStops ? (
          <TideStops
            labelId={tideLabelId}
            stops={tideStops}
            value={tide}
            onChange={(metres) => update({ tideOffsetM: metres })}
          />
        ) : (
          <Slider
            aria-labelledby={tideLabelId}
            min={TIDE_OFFSET_MIN}
            max={TIDE_OFFSET_MAX}
            step={WHATIF_STEP}
            value={[values.tideOffsetM]}
            onValueChange={(value) => update({ tideOffsetM: firstValue(value) })}
          />
        )}
        <p className="type-micro text-text-3">{tideNote}</p>
      </section>

      <CleanedSegments
        segmentIds={values.cleanedSegments}
        source={cleanedSource}
        segmentLabel={segmentLabel}
        onRemove={(segmentId) =>
          update((prev) => ({
            cleanedSegments: prev.cleanedSegments.filter((id) => id !== segmentId),
          }))
        }
      />

      <section className="space-y-3">
        <SwitchRow
          label="Clean top 14 by blockage"
          description={CLEAN_TOP_NOTE}
          checked={values.cleanTop14}
          onCheckedChange={(checked) => update({ cleanTop14: checked })}
          disabled={cleanDisabled}
          disabledReason={cleanDisabledReason}
        />
        <SwitchRow
          label="Pump plan"
          description={PUMP_PLAN_NOTE}
          checked={values.pumpPlan}
          onCheckedChange={(checked) => update({ pumpPlan: checked })}
          disabled={pumpDisabled}
          disabledReason={pumpDisabledReason}
        />
      </section>

      <section className="border-line space-y-3 border-t pt-4">
        <div className="space-y-1">
          <Button
            className="w-full"
            disabled={!canRun}
            aria-describedby={canRun ? undefined : runHelpId}
            onClick={() => onRun?.(asked)}
          >
            Run what-if
          </Button>
          {canRun ? null : (
            <p id={runHelpId} className="type-micro text-text-3">
              {runDisabledReason}
            </p>
          )}
        </div>
        <div className="space-y-1">
          <Button
            variant="outline"
            className="w-full"
            disabled={!canCheck}
            aria-describedby={canCheck ? undefined : physicsHelpId}
            onClick={() => onPhysicsCheck?.(asked)}
          >
            Physics check
          </Button>
          {canCheck ? null : (
            <p id={physicsHelpId} className="type-micro text-text-3">
              {physicsDisabledReason}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
