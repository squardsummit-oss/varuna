"use client";

import { useId } from "react";
import { Clock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { formatIst, formatPct } from "@/lib/format";
import {
  DEFAULT_RISK_TOLERANCE,
  PROFILE_LABELS,
  VEHICLE_PROFILES,
  type VehicleProfile,
} from "@/lib/stores/ui";
import { useReplayStore } from "@/lib/stores/replay";
import { cn } from "@/lib/utils";

/** Origins and destinations the demo offers: the two hospitals and the chronic hotspots. */
export interface RoutePlace {
  id: string;
  name: string;
  /** Which group the option sits under. Mumbai's asset register has 354 hospitals and 14 fire
   * stations; a flat list of them is not a picker, and grouping is what makes it one. */
  group?: string;
}

/** Option groups, in the order the pickers show them. */
const GROUP_ORDER = ["Demo trip", "Chronic junctions", "Hospitals", "Fire stations"] as const;

function grouped(places: readonly RoutePlace[]): [string, RoutePlace[]][] {
  const buckets = new Map<string, RoutePlace[]>();
  for (const place of places) {
    const key = place.group ?? "Places";
    const bucket = buckets.get(key);
    if (bucket) bucket.push(place);
    else buckets.set(key, [place]);
  }
  const known = GROUP_ORDER.filter((g) => buckets.has(g)).map(
    (g) => [g, buckets.get(g) as RoutePlace[]] as [string, RoutePlace[]],
  );
  const rest = [...buckets.entries()].filter(
    ([g]) => !(GROUP_ORDER as readonly string[]).includes(g),
  );
  return [...known, ...rest];
}

function Options({ places }: { places: readonly RoutePlace[] }) {
  return (
    <>
      {grouped(places).map(([group, items]) => (
        <optgroup key={group} label={group}>
          {items.map((place) => (
            <option key={`${group}-${place.id}`} value={place.id}>
              {place.name}
            </option>
          ))}
        </optgroup>
      ))}
    </>
  );
}

export const ROUTE_PLACES: readonly RoutePlace[] = [
  { id: "kem-hospital", name: "KEM Hospital" },
  { id: "sion-hospital", name: "Sion Hospital" },
  { id: "hindmata", name: "Hindmata junction" },
  { id: "kings-circle", name: "King's Circle" },
  { id: "sion-circle", name: "Sion Circle" },
  { id: "kurla-lbs", name: "Kurla LBS Marg" },
  { id: "milan-subway", name: "Milan subway" },
];

export const DEFAULT_ORIGIN_ID = "kem-hospital";
export const DEFAULT_DESTINATION_ID = "sion-hospital";
export const DEFAULT_PROFILE: VehicleProfile = "ambulance";

/** The body of `POST /v1/route` as the form holds it (SPEC.md section 12). */
export interface RouteRequest {
  originId: string;
  destinationId: string;
  /** ISO 8601 with the +05:30 offset. */
  departAt: string;
  profile: VehicleProfile;
  /** P(impassable) above which a segment is avoided, 0 to 1. */
  riskTolerance: number;
}

/** The demo trip: KEM Hospital to Sion Hospital by ambulance at the replay clock. */
export function defaultRouteRequest(departAt: string): RouteRequest {
  return {
    originId: DEFAULT_ORIGIN_ID,
    destinationId: DEFAULT_DESTINATION_ID,
    departAt,
    profile: DEFAULT_PROFILE,
    riskTolerance: DEFAULT_RISK_TOLERANCE[DEFAULT_PROFILE],
  };
}

/**
 * Switching profile resets the tolerance to that profile's default, because a tolerance the
 * operator set for a car (0.5) is the wrong risk to carry into an ambulance (0.2).
 */
export function withProfile(request: RouteRequest, profile: VehicleProfile): RouteRequest {
  return { ...request, profile, riskTolerance: DEFAULT_RISK_TOLERANCE[profile] };
}

/** Replaces the clock time of an IST ISO string, keeping its date and offset. */
export function withDepartureTime(departAt: string, hhmm: string): string {
  if (!/^\d{2}:\d{2}$/.test(hhmm)) return departAt;
  const date = departAt.slice(0, 10);
  return `${date}T${hhmm}:00+05:30`;
}

function isVehicleProfile(value: string): value is VehicleProfile {
  return (VEHICLE_PROFILES as readonly string[]).includes(value);
}

function firstValue(value: number | readonly number[]): number {
  return Array.isArray(value) ? Number(value[0]) : Number(value);
}

const FIELD =
  "h-8 w-full rounded-control border border-line bg-well px-2 type-small text-text outline-none focus-visible:border-line-strong focus-visible:ring-3 focus-visible:ring-tide/50";

export interface RouteFormProps {
  value: RouteRequest;
  onChange: (next: RouteRequest) => void;
  /** Called by "Find route"; absent or `disabled` keeps the button off with its reason. */
  onSubmit?: (request: RouteRequest) => void;
  /** The pickable places. Defaults to the demo shortlist; the page passes the city's own register
   * once it has loaded, so the ids in the request resolve to sourced coordinates. */
  places?: readonly RoutePlace[];
  disabled?: boolean;
  /** One sentence saying why "Find route" is off. */
  submitDisabledReason?: string;
  className?: string;
}

/**
 * The route planner's control column (SPEC.md section 7.4): the trip, the departure time bound
 * to the replay clock, the vehicle profile and its risk tolerance. Controlled: the page owns the
 * request so the map and the comparison read the same values.
 */
export function RouteForm({
  value,
  onChange,
  onSubmit,
  places = ROUTE_PLACES,
  disabled = false,
  submitDisabledReason = "Routing lands in Phase 8; the form is live so the demo trip is preset",
  className,
}: RouteFormProps) {
  const simTime = useReplayStore((s) => s.simTime);
  const uid = useId();
  const originId = `${uid}-origin`;
  const destinationId = `${uid}-destination`;
  const departId = `${uid}-depart`;
  const profileId = `${uid}-profile`;
  const toleranceId = `${uid}-tolerance`;
  const submitHelpId = `${uid}-submit-help`;

  const canSubmit = Boolean(onSubmit) && !disabled;
  const clock = value.departAt.slice(11, 16);

  return (
    <form
      className={cn("space-y-5", className)}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmit?.(value);
      }}
    >
      <div className="space-y-2">
        <label htmlFor={originId} className="type-small text-text block font-medium">
          Origin
        </label>
        <select
          id={originId}
          className={FIELD}
          value={value.originId}
          onChange={(event) => onChange({ ...value, originId: event.target.value })}
        >
          <Options places={places} />
        </select>
      </div>

      <div className="space-y-2">
        <label htmlFor={destinationId} className="type-small text-text block font-medium">
          Destination
        </label>
        <select
          id={destinationId}
          className={FIELD}
          value={value.destinationId}
          onChange={(event) => onChange({ ...value, destinationId: event.target.value })}
        >
          <Options places={places} />
        </select>
      </div>

      <div className="space-y-2">
        <label htmlFor={departId} className="type-small text-text block font-medium">
          Departure time
        </label>
        <div className="flex items-center gap-2">
          <input
            id={departId}
            type="time"
            className={cn(FIELD, "num")}
            value={clock}
            onChange={(event) =>
              onChange({
                ...value,
                departAt: withDepartureTime(value.departAt, event.target.value),
              })
            }
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => onChange({ ...value, departAt: simTime })}
          >
            <Clock size={16} strokeWidth={1.75} aria-hidden="true" />
            Use scrub time
          </Button>
        </div>
        <p className="type-micro text-text-3">
          The replay clock reads <span className="num">{formatIst(simTime)}</span> IST.
        </p>
      </div>

      <div className="space-y-2">
        <label htmlFor={profileId} className="type-small text-text block font-medium">
          Vehicle profile
        </label>
        <select
          id={profileId}
          className={FIELD}
          value={value.profile}
          onChange={(event) => {
            const next = event.target.value;
            if (isVehicleProfile(next)) onChange(withProfile(value, next));
          }}
        >
          {VEHICLE_PROFILES.map((profile) => (
            <option key={profile} value={profile}>
              {PROFILE_LABELS[profile]}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor={toleranceId} className="type-small text-text font-medium">
            Risk tolerance
          </label>
          <output htmlFor={toleranceId} className="num type-small text-text-2">
            {formatPct(value.riskTolerance)}
          </output>
        </div>
        <Slider
          id={toleranceId}
          aria-label="Risk tolerance"
          min={0}
          max={1}
          step={0.05}
          value={[value.riskTolerance]}
          onValueChange={(next) => onChange({ ...value, riskTolerance: firstValue(next) })}
        />
        <p className="type-micro text-text-3">
          Avoid a street once P(impassable) passes this. Each profile has its default.
        </p>
      </div>

      <div className="border-line space-y-1 border-t pt-4">
        <Button
          type="submit"
          className="w-full"
          disabled={!canSubmit}
          aria-describedby={canSubmit ? undefined : submitHelpId}
        >
          Find route
        </Button>
        {canSubmit ? null : (
          <p id={submitHelpId} className="type-micro text-text-3">
            {submitDisabledReason}
          </p>
        )}
      </div>
    </form>
  );
}
