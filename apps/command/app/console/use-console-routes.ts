"use client";

/**
 * The console's Routes layer (SPEC.md 7.2, the R key): the demo ambulance trip, KEM Hospital
 * to Sion Hospital (SPEC.md 3.3), naive against VARUNA, departing at the scrub time.
 *
 * The two hospitals are found in the city's own facility register by the same names `/route`
 * preselects - never typed coordinates. The trip is re-planned when the scrub **settles** rather
 * than on every step: section 7.2 asks for no network during a scrub, and a route per 5-minute
 * step while the handle is being dragged would be thirty-six requests for one gesture.
 */

import { useEffect, useMemo, useState } from "react";

import type { RouteLine } from "@/components/map/city-map";
import { loadPlaces, planRoute, type Place, type RoutePlan } from "@/lib/api/route";

/** How long the scrub must rest before the trip is re-planned. */
const SETTLE_MS = 400;

/** Section 7.4's ambulance default. */
const AMBULANCE_TOLERANCE = 0.2;

export type ConsoleRouteState =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "ready"; plan: RoutePlan; lines: RouteLine[] }
  | { kind: "error"; message: string };

function linesOf(plan: RoutePlan): RouteLine[] {
  const lines: RouteLine[] = [];
  if (plan.naive) lines.push({ id: "console-naive", path: plan.naive.path, kind: "naive" });
  plan.avoided.forEach((a, i) =>
    lines.push({ id: `console-avoided-${i}`, path: a.path, kind: "avoided" }),
  );
  if (plan.varuna) lines.push({ id: "console-varuna", path: plan.varuna.path, kind: "varuna" });
  return lines;
}

export function useConsoleRoutes(
  enabled: boolean,
  city: string,
  runId: string | undefined,
  departAt: string | undefined,
): ConsoleRouteState {
  const [places, setPlaces] = useState<{ city: string; trip: [Place, Place] | null } | null>(null);
  const [result, setResult] = useState<{
    key: string;
    value: ConsoleRouteState;
  } | null>(null);

  useEffect(() => {
    if (!enabled || places?.city === city) return;
    const controller = new AbortController();
    loadPlaces(city, controller.signal)
      .then((loaded) => {
        const kem = loaded.find((p) => p.name.includes("(KEM)"));
        const sion = loaded.find((p) => p.name.includes("(LTMG)"));
        setPlaces({ city, trip: kem && sion ? [kem, sion] : null });
      })
      .catch(() => {
        if (!controller.signal.aborted) setPlaces({ city, trip: null });
      });
    return () => controller.abort();
  }, [enabled, city, places?.city]);

  const trip = places?.city === city ? places.trip : undefined;
  const key = `${runId ?? ""}|${departAt ?? ""}`;

  useEffect(() => {
    if (!enabled || !trip || !runId || !departAt) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      planRoute(
        {
          origin: trip[0],
          destination: trip[1],
          departAt,
          profile: "ambulance",
          riskTolerance: AMBULANCE_TOLERANCE,
          runId,
          spread: false,
          explain: false,
        },
        controller.signal,
      )
        .then((plan) => setResult({ key, value: { kind: "ready", plan, lines: linesOf(plan) } }))
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setResult({
            key,
            value: {
              kind: "error",
              message: error instanceof Error ? error.message : String(error),
            },
          });
        });
    }, SETTLE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [enabled, trip, runId, departAt, key]);

  return useMemo<ConsoleRouteState>(() => {
    if (!enabled) return { kind: "off" };
    if (trip === null) {
      return {
        kind: "error",
        message: "KEM Hospital and Sion Hospital are not both in this city's facility register.",
      };
    }
    // The last answer stays drawn while the next is planned, so the route never blinks off
    // during a scrub; it is replaced when the new departure's answer lands.
    if (result && (result.key === key || result.value.kind === "ready")) return result.value;
    return { kind: "loading" };
  }, [enabled, trip, result, key]);
}
