"use client";

import { ArrowDown, ArrowUp, CloudRain, Waves } from "lucide-react";
import { useEffect, useState } from "react";

import { loadTide, type TideState } from "@/lib/api/tide";
import { ageLabel, loadWeather, type WeatherState } from "@/lib/api/weather";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How often the card asks again while the console is open. */
export const LIVE_NOW_POLL_MS = 10 * 60_000;

export interface LiveNowCardProps {
  city: string;
  cityLabel: string;
  /** Supplied instead of fetched, for /design and tests. */
  weather?: WeatherState;
  tide?: TideState;
  className?: string;
}

/** The highest chance of rain in the served hours, as "60 %", or null. */
export function rainChance(state: WeatherState): string | null {
  if (state.kind !== "ready") return null;
  const values = state.weather.hourly
    .map((step) => step.precipitationProbabilityPct)
    .filter((v): v is number => typeof v === "number");
  return values.length > 0 ? `${Math.round(Math.max(...values))} %` : null;
}

/** Weather and sea level for `city`, asked now and every {@link LIVE_NOW_POLL_MS} while visible. */
function useLiveNow(
  city: string,
  weather: WeatherState | undefined,
  tide: TideState | undefined,
): { sky: WeatherState; sea: TideState } {
  const [sky, setSky] = useState<WeatherState>({ kind: "loading" });
  const [sea, setSea] = useState<TideState>({ kind: "loading" });
  const supplied = weather !== undefined && tide !== undefined;
  useEffect(() => {
    if (supplied) return;
    let controller = new AbortController();
    const run = () => {
      controller.abort();
      controller = new AbortController();
      const { signal } = controller;
      void loadWeather(city, { signal }).then((next) => {
        if (!signal.aborted) setSky(next);
      });
      void loadTide(city, { signal }).then((next) => {
        if (!signal.aborted) setSea(next);
      });
    };
    run();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") run();
    }, LIVE_NOW_POLL_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [city, supplied]);
  return { sky: weather ?? sky, sea: tide ?? sea };
}

/**
 * The sky and the sea over the city right now, beside a replayed flood.
 *
 * Two live answers an officer asks before anything else: is it raining, and is the tide coming in
 * (a tide-locked outfall is how a street floods under a light shower). Rain is `GET /v1/weather`
 * (Open-Meteo, cached by the API); the sea level is Open-Meteo's marine model. Both are labelled
 * live, not the replay, and neither changes the forecast on the map.
 */
export function LiveNowCard({ city, cityLabel, weather, tide, className }: LiveNowCardProps) {
  const { sky, sea } = useLiveNow(city, weather, tide);

  const skyLine =
    sky.kind === "ready"
      ? [
          sky.weather.current.weather,
          sky.weather.current.temperatureC !== null
            ? `${Math.round(sky.weather.current.temperatureC)} °C`
            : null,
        ]
          .filter(Boolean)
          .join(", ")
      : sky.kind === "loading"
        ? "Loading"
        : "No live weather";
  const rainNow =
    sky.kind === "ready" && sky.weather.current.precipitationMm !== null
      ? `${sky.weather.current.precipitationMm.toFixed(1)} mm in the last 15 min`
      : null;
  const chance = rainChance(sky);

  return (
    <section
      aria-labelledby="live-now-title"
      className={cn(
        "rounded-panel border-line w-[248px] shrink-0 border bg-[var(--ink)]/85 backdrop-blur-[12px]",
        className,
      )}
    >
      <div className="border-line flex h-9 items-center gap-2 border-b px-3">
        <span aria-hidden="true" className="bg-status-live size-2 shrink-0 rounded-full" />
        <h2 id="live-now-title" className="type-small text-text flex-1">
          {cityLabel} right now
        </h2>
        {sky.kind === "ready" ? (
          <span className="num type-micro text-text-2">{ageLabel(sky.weather.ageS)}</span>
        ) : null}
      </div>
      <div className="space-y-2.5 p-3">
        <div className="flex gap-2.5">
          <CloudRain
            size={16}
            strokeWidth={1.75}
            className="text-text-2 mt-0.5 shrink-0"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="type-small text-text">{skyLine}</p>
            {rainNow ? <p className="num type-micro text-text-2">{rainNow}</p> : null}
            {chance ? (
              <p className="num type-micro text-text-2">Rain chance next 4 h: up to {chance}</p>
            ) : null}
            {sky.kind === "unavailable" ? (
              <p className="type-micro text-text-2">{sky.reason}</p>
            ) : null}
          </div>
        </div>
        <div className="flex gap-2.5">
          <Waves
            size={16}
            strokeWidth={1.75}
            className="text-text-2 mt-0.5 shrink-0"
            aria-hidden="true"
          />
          <div className="min-w-0">
            {sea.kind === "ready" ? (
              <>
                <p className="num type-small text-text flex items-center gap-1">
                  Sea {sea.tide.now.m >= 0 ? "+" : ""}
                  {sea.tide.now.m.toFixed(2)} m
                  {sea.tide.rising ? (
                    <ArrowUp size={14} strokeWidth={1.75} aria-label="rising" />
                  ) : (
                    <ArrowDown size={14} strokeWidth={1.75} aria-label="falling" />
                  )}
                </p>
                {sea.tide.nextHigh ? (
                  <p className="num type-micro text-text-2">
                    High tide {formatIst(sea.tide.nextHigh.ts)},{" "}
                    {sea.tide.nextHigh.m >= 0 ? "+" : ""}
                    {sea.tide.nextHigh.m.toFixed(2)} m
                  </p>
                ) : null}
              </>
            ) : (
              <p className="type-small text-text-2">
                {sea.kind === "loading" ? "Loading sea level" : sea.reason}
              </p>
            )}
          </div>
        </div>
        <p className="type-micro text-text-3">
          Live, not the replay. Data by Open-Meteo.com (CC BY 4.0).
        </p>
      </div>
    </section>
  );
}
