"use client";

/**
 * The live-weather dialog (UI_SPEC 5, task D-13).
 *
 * **Why this dialog exists at all.** The dashboard shows a reconstructed replay of 2 July 2019
 * and, in its header, the temperature outside the window right now. Those two things must never
 * be read as one thing. A reader who sees "28 °C" above a map of flooded streets and concludes
 * the streets are flooded *today* has been misled by the layout, not by any sentence we wrote -
 * so the separation is not a footnote here, it is a boxed block with its own heading, and it is
 * the only part of this dialog that cannot be scrolled past.
 *
 * **Everything printed comes from the API's own body.** The attribution string, the licence, the
 * source name, the age and the grid offset are fields, not literals: if Open-Meteo's terms change
 * and the API's `attribution` changes with them, this dialog changes too. The one number that is
 * computed here is the bar height, which is a ratio of the millimetres the API sent.
 *
 * **Nothing is invented when the source is down.** No reading means the dialog says the live
 * source is unavailable and repeats the API's reason; it does not fall back to the replay's rain,
 * which would be the worst possible answer to "what is the weather".
 */

import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Sun,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ageLabel,
  isGridDistant,
  sourceLine,
  temperatureLabel,
  type Weather,
  type WeatherState,
} from "@/lib/api/weather";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The Lucide icon component for a WMO 4677 present-weather code, grouped as Open-Meteo documents
 * them.
 *
 * A picture of the sky, never a judgement about it: 63 is "moderate rain", so it gets the rain
 * cloud, and an unknown code gets the plain cloud rather than a guess at what it means. The
 * lookup returns an element rather than a component type, because a component chosen during a
 * render is a new component every render and loses its state.
 */
export function WeatherIcon({ code, className }: { code: number | null; className?: string }) {
  const props = { "aria-hidden": true as const, className, strokeWidth: 1.75 };
  if (code === null || !Number.isFinite(code)) return <Cloud {...props} />;
  if (code === 0) return <Sun {...props} />;
  if (code === 1 || code === 2) return <CloudSun {...props} />;
  if (code === 3) return <Cloud {...props} />;
  if (code === 45 || code === 48) return <CloudFog {...props} />;
  if (code >= 51 && code <= 57) return <CloudDrizzle {...props} />;
  if (code >= 71 && code <= 77) return <CloudSnow {...props} />;
  if (code >= 85 && code <= 86) return <CloudSnow {...props} />;
  if (code >= 95) return <CloudLightning {...props} />;
  return <CloudRain {...props} />;
}

/** The differentiator UI_SPEC 5 prints under the separation block, verbatim. */
export const DIFFERENTIATOR =
  "A 12 km forecast tells you it will rain. VARUNA tells you which 30 m of street it will sit on.";

/** Tallest bar in the three-hour column chart, in pixels; a bar is never shorter than 2 px. */
const BAR_MAX_PX = 56;

/** Millimetres an hour that fills the chart. Above it the bar is full and prints its own number. */
const BAR_FULL_MM = 10;

export interface WeatherDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Whatever the loader knows: loading, a reading, or the reason there is none. */
  state: WeatherState;
  /** The city the reading was asked for, as a person writes it ("Mumbai"). */
  cityLabel?: string;
  /** What the map is showing, so the separation block can name it. */
  replayLabel?: string;
}

/** One value with its label; a missing value prints an em dash rather than a zero. */
function Reading({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="border-line flex items-baseline justify-between gap-3 border-b py-1.5 last:border-b-0">
      <span className="text-micro text-text-2">{label}</span>
      <span className="num text-small text-text">{value ?? "—"}</span>
    </div>
  );
}

function HourlyBars({ weather }: { weather: Weather }) {
  if (weather.hourly.length === 0) {
    return (
      <p className="text-micro text-text-2">
        The source sent no hourly steps with this reading, so there is nothing to show for the next
        three hours.
      </p>
    );
  }
  return (
    <ul className="flex items-end gap-3" aria-label="Rain expected over the next hours">
      {weather.hourly.slice(0, 3).map((step) => {
        const mm = step.precipitationMm;
        const ratio = mm === null ? 0 : Math.min(mm / BAR_FULL_MM, 1);
        const height = mm === null ? 2 : Math.max(2, Math.round(ratio * BAR_MAX_PX));
        const probability = step.precipitationProbabilityPct;
        return (
          <li key={step.ts} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
            <span className="num text-micro text-text">{mm === null ? "—" : `${mm} mm`}</span>
            <span
              aria-hidden="true"
              className="rounded-control bg-depth-1 w-full"
              style={{ height: `${height}px` }}
            />
            <span className="num text-micro text-text-2">{formatIst(step.ts)}</span>
            <span className="num text-micro text-text-3">
              {probability === null ? "—" : `${Math.round(probability)} %`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** The boxed block that keeps today's sky and the replayed storm apart (UI_SPEC 5). */
function SeparationNote({ replayLabel }: { replayLabel: string }) {
  return (
    <section
      aria-labelledby="weather-separation"
      className="rounded-panel border-line-strong bg-well border p-4"
    >
      <h3 id="weather-separation" className="text-small text-text font-medium">
        What is on the map is not this.
      </h3>
      <p className="text-small text-text-2 mt-1.5 max-w-[68ch]">
        The map shows the {replayLabel}. The weather above is today&apos;s sky over Mumbai, read
        from a live source a moment ago. The two are never combined: no live reading is fed into a
        VARUNA forecast, and no forecast on this page was computed from today&apos;s weather.
      </p>
      <p className="text-small text-text mt-3 max-w-[68ch]">{DIFFERENTIATOR}</p>
    </section>
  );
}

/**
 * The dialog behind the header chip: now on the left, the next three hours on the right, the
 * licence Open-Meteo's CC BY 4.0 requires, and the separation block.
 */
export function WeatherDialog({
  open,
  onOpenChange,
  state,
  cityLabel = "Mumbai",
  replayLabel = "reconstructed 2 July 2019 replay",
}: WeatherDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="motion-reduce:animate-none sm:max-w-2xl" data-slot="weather-dialog">
        <DialogHeader>
          <DialogTitle>Live weather over {cityLabel}</DialogTitle>
          <DialogDescription>
            Conditions now and the rain expected over the next hours, from outside VARUNA.
          </DialogDescription>
        </DialogHeader>

        {state.kind === "loading" ? <WeatherDialogSkeleton /> : null}

        {state.kind === "unavailable" ? (
          <div data-slot="weather-unavailable" className="flex flex-col gap-3">
            <p className="text-body text-text">The live weather source is unavailable.</p>
            <p className="text-small text-text-2 max-w-[68ch]">{state.reason}</p>
          </div>
        ) : null}

        {state.kind === "ready" ? <WeatherBody weather={state.weather} /> : null}

        <SeparationNote replayLabel={replayLabel} />
      </DialogContent>
    </Dialog>
  );
}

function WeatherBody({ weather }: { weather: Weather }) {
  const temperature = temperatureLabel(weather);
  const rain = weather.current.precipitationMm;
  const wind = weather.current.windKmh;
  const humidity = weather.current.humidityPct;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <section aria-labelledby="weather-now" className="flex flex-col gap-2">
          <h3 id="weather-now" className="text-small text-text-2 font-medium">
            Now
          </h3>
          <div className="flex items-center gap-3">
            <WeatherIcon code={weather.current.weatherCode} className="text-tide size-8" />
            <div className="min-w-0">
              <p className="num font-display text-h1 tracking-display text-text leading-none">
                {temperature ?? "—"}
              </p>
              {weather.current.weather ? (
                <p className="text-small text-text-2">{weather.current.weather}</p>
              ) : null}
            </div>
          </div>
          <div className="flex flex-col">
            <Reading
              label="Rain in the last interval"
              value={rain === null ? null : `${rain} mm`}
            />
            <Reading label="Wind" value={wind === null ? null : `${Math.round(wind)} km/h`} />
            <Reading
              label="Humidity"
              value={humidity === null ? null : `${Math.round(humidity)} %`}
            />
            <Reading label="Observed at" value={formatIst(weather.current.ts) + " IST"} />
          </div>
        </section>

        <section aria-labelledby="weather-next" className="flex flex-col gap-2">
          <h3 id="weather-next" className="text-small text-text-2 font-medium">
            Next three hours
          </h3>
          <HourlyBars weather={weather} />
          <p className="text-micro text-text-3">
            Rain in millimetres for each hour, with the source&apos;s own chance of any rain.
          </p>
        </section>
      </div>

      <div className="border-line flex flex-col gap-1.5 border-t pt-3">
        <p
          className={cn("num text-micro", weather.stale ? "text-status-degraded" : "text-text-2")}
          data-slot="weather-source-line"
        >
          {weather.stale
            ? `Cached ${ageLabel(weather.ageS)} · network unavailable`
            : sourceLine(weather)}
        </p>
        {weather.stale ? <p className="text-micro text-text-2">{sourceLine(weather)}</p> : null}
        {weather.notes.map((note) => (
          <p key={note} className="text-micro text-text-2 max-w-[68ch]">
            {note}
          </p>
        ))}
        {isGridDistant(weather) ? (
          <p className="text-micro text-text-2 max-w-[68ch]">
            This answer is for a model grid cell whose centre is{" "}
            <span className="num">{weather.gridOffsetKm} km</span> from the centre of the Mumbai
            area VARUNA has built, not for a thermometer on any street here.
          </p>
        ) : null}
        <p className="text-micro text-text-3">
          {weather.attribution}
          {weather.licenceUrl ? (
            <>
              {" · "}
              <a
                className="text-text-2 underline underline-offset-2"
                href={weather.licenceUrl}
                rel="noreferrer noopener"
                target="_blank"
              >
                Licence terms
              </a>
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}

/** Skeleton, never a spinner (SPEC.md 6.9). Same boxes the reading fills. */
function WeatherDialogSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2" data-slot="weather-skeleton" aria-busy="true">
      <span className="sr-only">Loading the live weather</span>
      <div className="flex flex-col gap-2">
        <div className="rounded-control bg-well h-8 w-32 animate-pulse motion-reduce:animate-none" />
        <div className="rounded-control bg-well h-24 w-full animate-pulse motion-reduce:animate-none" />
      </div>
      <div className="rounded-control bg-well h-32 w-full animate-pulse motion-reduce:animate-none" />
    </div>
  );
}
