"use client";

/**
 * The header's live-weather chip (UI_SPEC 5, task D-13).
 *
 * **It is one fixed box in every state.** Loading, live, stale and unavailable all render at the
 * same width, so the header does not jump when the reading arrives - UI_SPEC 5's "no layout shift
 * when it loads" is a promise about a header the reader is already looking at, and the only way
 * to keep it is to reserve the box before there is anything to put in it.
 *
 * **It never shows a temperature it was not given.** With no reading, the chip says there is no
 * live weather and still opens the dialog, which carries the API's own reason. A chip that
 * silently kept the last number it saw would be the one place on this screen where a stale value
 * looked current (SPEC.md rule 6).
 *
 * **Stale is visible, not hidden.** Past the API's cache window the word "Live" is replaced by the
 * copy's age in `--status-degraded`, because a cached reading is still useful and still true - it
 * is just true about a different minute.
 */

import { useCallback, useEffect, useState } from "react";

import { WeatherDialog, WeatherIcon } from "@/components/citizen/weather-dialog";
import {
  ageLabel,
  DEFAULT_WEATHER_CITY,
  loadWeather,
  temperatureLabel,
  type WeatherState,
} from "@/lib/api/weather";
import { cn } from "@/lib/utils";

export interface WeatherChipProps {
  /** City slug asked of `GET /v1/weather`. */
  city?: string;
  /** The city as a person writes it, for the dialog's title. */
  cityLabel?: string;
  /** What the map is showing, so the dialog's separation block can name it. */
  replayLabel?: string;
  /**
   * A reading supplied by the caller instead of fetched. Tests and server-rendered callers pass
   * it; when it is absent the chip loads once on mount.
   */
  state?: WeatherState;
  className?: string;
}

/**
 * The chip's fixed footprint: 44 px tall for UI_SPEC 10's touch floor, and wide enough for the
 * longest string any state produces ("No live weather"), so none of them resizes the header.
 */
const CHIP_BOX =
  "inline-flex h-11 min-w-[9.5rem] items-center justify-center gap-2 rounded-chip border border-line bg-deep px-3 text-small text-text";

/** What the chip reads in each state; one place, so the label and the aria label cannot diverge. */
interface ChipFace {
  /** The line beside the icon. */
  lead: string;
  /** The word after it: "Live", an age, or nothing. */
  tail: string | null;
  tailClass: string;
  /** Full sentence for a screen reader and the title attribute. */
  description: string;
}

function faceFor(state: WeatherState): ChipFace {
  if (state.kind === "loading") {
    return {
      lead: "",
      tail: null,
      tailClass: "",
      description: "Loading the live weather",
    };
  }
  if (state.kind === "unavailable") {
    return {
      lead: "No live weather",
      tail: null,
      tailClass: "",
      description: `No live weather. ${state.reason}`,
    };
  }
  const { weather } = state;
  const temperature = temperatureLabel(weather);
  const lead = temperature ?? "No temperature";
  if (weather.stale) {
    const age = ageLabel(weather.ageS);
    return {
      lead,
      tail: age,
      tailClass: "text-status-degraded",
      description: `${lead}, cached ${age}. Open for the source and what the map is showing instead.`,
    };
  }
  return {
    lead,
    tail: "Live",
    tailClass: "text-tide",
    description: `${lead} now, live from ${weather.source}. Open for the source and what the map is showing instead.`,
  };
}

/** "28 °C · Live" in the dashboard header; opens {@link WeatherDialog} on click or Enter. */
export function WeatherChip({
  city = DEFAULT_WEATHER_CITY,
  cityLabel = "Mumbai",
  replayLabel,
  state: supplied,
  className,
}: WeatherChipProps) {
  // The reading is kept with the city it answers for, so switching cities shows the skeleton
  // rather than the previous city's temperature, without a setState in the effect body to clear
  // it - a cascading render that React's own lint rule is right to refuse.
  const [loaded, setLoaded] = useState<{ city: string; state: WeatherState } | null>(null);
  const [open, setOpen] = useState(false);
  const fetched: WeatherState = loaded && loaded.city === city ? loaded.state : { kind: "loading" };
  const state = supplied ?? fetched;

  useEffect(() => {
    if (supplied) return;
    const controller = new AbortController();
    let live = true;
    void loadWeather(city, { signal: controller.signal }).then((next) => {
      if (live) setLoaded({ city, state: next });
    });
    return () => {
      live = false;
      controller.abort();
    };
  }, [city, supplied]);

  const onOpenChange = useCallback((next: boolean) => setOpen(next), []);

  const face = faceFor(state);

  return (
    <>
      <button
        type="button"
        data-slot="weather-chip"
        data-state={state.kind}
        title={face.description}
        aria-label={face.description}
        className={cn(
          CHIP_BOX,
          "focus-visible:outline-tide focus-visible:outline-2 focus-visible:outline-offset-2",
          className,
        )}
        onClick={() => setOpen(true)}
      >
        {state.kind === "loading" ? (
          <span
            aria-hidden="true"
            className="rounded-chip bg-well h-3 w-24 animate-pulse motion-reduce:animate-none"
          />
        ) : null}
        {state.kind === "ready" ? (
          <WeatherIcon code={state.weather.current.weatherCode} className="text-text-2 size-4" />
        ) : null}
        {face.lead ? <span className="num">{face.lead}</span> : null}
        {face.tail ? (
          <span aria-hidden="true" className={cn("text-micro", face.tailClass)}>
            {face.tail}
          </span>
        ) : null}
      </button>
      <WeatherDialog
        open={open}
        onOpenChange={onOpenChange}
        state={state}
        cityLabel={cityLabel}
        {...(replayLabel === undefined ? {} : { replayLabel })}
      />
    </>
  );
}
