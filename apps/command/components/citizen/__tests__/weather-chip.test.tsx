/**
 * The weather chip and dialog (task D-13, UI_SPEC 5).
 *
 * What these tests defend, in order of how badly it would go wrong on stage: that the chip never
 * prints a temperature it was not given, that the dialog always carries the sentence separating
 * today's sky from the replayed storm, and that the licence attribution the data's terms require
 * is on screen whenever the data is.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { WeatherChip } from "@/components/citizen/weather-chip";
import { DIFFERENTIATOR, WeatherDialog } from "@/components/citizen/weather-dialog";
import { weatherFromBody, type WeatherState } from "@/lib/api/weather";

function reading(overrides: Record<string, unknown> = {}) {
  return weatherFromBody({
    city: "mumbai",
    point: { lon: 72.86, lat: 19.065 },
    grid_point: { lon: 72.875, lat: 19.0 },
    grid_offset_km: 7.29,
    current: {
      ts: "2026-09-19T14:15:00+05:30",
      temperature_c: 28.4,
      humidity_pct: 79,
      precipitation_mm: 0.3,
      wind_kmh: 17.6,
      weather_code: 63,
      weather: "Moderate rain",
    },
    hourly: [
      { ts: "2026-09-19T15:00:00+05:30", precipitation_mm: 1.2, precipitation_probability_pct: 62 },
      { ts: "2026-09-19T16:00:00+05:30", precipitation_mm: 4.4, precipitation_probability_pct: 71 },
      { ts: "2026-09-19T17:00:00+05:30", precipitation_mm: 0, precipitation_probability_pct: 18 },
    ],
    fetched_at: "2026-09-19T14:13:00+05:30",
    age_s: 124,
    stale: false,
    ttl_s: 900,
    source: "open-meteo",
    source_url: "https://open-meteo.com/",
    licence: "CC BY 4.0",
    licence_url: "https://creativecommons.org/licenses/by/4.0/",
    attribution: "Weather data by Open-Meteo.com (CC BY 4.0)",
    notes: [],
    ...overrides,
  });
}

const ready: WeatherState = { kind: "ready", weather: reading() };

function chipOf(container: HTMLElement): HTMLElement {
  const chip = container.querySelector<HTMLElement>('[data-slot="weather-chip"]');
  if (!chip) throw new Error("no chip rendered");
  return chip;
}

describe("WeatherChip", () => {
  it("prints the temperature and the word Live", () => {
    const { container } = render(<WeatherChip state={ready} />);
    const chip = chipOf(container);
    expect(chip).toHaveAttribute("data-state", "ready");
    expect(chip).toHaveTextContent("28 °C");
    expect(chip).toHaveTextContent("Live");
  });

  it("claims no temperature when the source is unavailable, and keeps the reason", () => {
    const { container } = render(
      <WeatherChip
        state={{ kind: "unavailable", reason: "The VARUNA API is unreachable at :8000." }}
      />,
    );
    const chip = chipOf(container);
    expect(chip).toHaveTextContent("No live weather");
    expect(chip.textContent).not.toMatch(/°C/);
    expect(chip.getAttribute("aria-label")).toContain("unreachable");
  });

  it("shows an age in place of Live once the copy is stale", () => {
    const { container } = render(
      <WeatherChip state={{ kind: "ready", weather: reading({ stale: true, age_s: 2460 }) }} />,
    );
    const chip = chipOf(container);
    expect(chip).toHaveTextContent("41 min ago");
    expect(chip.textContent).not.toContain("Live");
    expect(chip.querySelector(".text-status-degraded")).not.toBeNull();
  });

  it("reserves the same box in every state, so the header cannot shift", () => {
    const boxes = (
      [
        { kind: "loading" },
        ready,
        { kind: "ready", weather: reading({ stale: true, age_s: 2460 }) },
        { kind: "unavailable", reason: "No live weather source configured." },
      ] as WeatherState[]
    ).map((state) => {
      const { container, unmount } = render(<WeatherChip state={state} />);
      const className = chipOf(container).className;
      unmount();
      return className;
    });
    for (const className of boxes) {
      expect(className).toContain("h-11");
      expect(className).toContain("min-w-[9.5rem]");
    }
  });

  it("shimmers rather than spinning while it loads, and says so to a screen reader", () => {
    const { container } = render(<WeatherChip state={{ kind: "loading" }} />);
    const chip = chipOf(container);
    expect(chip.getAttribute("aria-label")).toBe("Loading the live weather");
    expect(chip.querySelector(".animate-pulse")).not.toBeNull();
    expect(chip.textContent).not.toMatch(/°C/);
  });

  it("opens the dialog on click", () => {
    const { container } = render(<WeatherChip state={ready} />);
    fireEvent.click(chipOf(container));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("WeatherDialog", () => {
  function open(state: WeatherState) {
    render(<WeatherDialog open onOpenChange={() => undefined} state={state} />);
    return screen.getByRole("dialog");
  }

  it("keeps today's sky and the replayed storm apart, in its own block", () => {
    const dialog = open(ready);
    const note = within(dialog).getByRole("heading", { name: "What is on the map is not this." });
    expect(note).toBeInTheDocument();
    expect(dialog).toHaveTextContent("reconstructed 2 July 2019 replay");
    expect(dialog).toHaveTextContent(DIFFERENTIATOR);
  });

  it("carries the separation block even when there is no reading at all", () => {
    const dialog = open({ kind: "unavailable", reason: "Open-Meteo could not be reached." });
    expect(
      within(dialog).getByRole("heading", { name: "What is on the map is not this." }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("The live weather source is unavailable.");
    expect(dialog).toHaveTextContent("Open-Meteo could not be reached.");
    expect(dialog.textContent).not.toMatch(/°C/);
  });

  it("prints the attribution the licence requires, from the body", () => {
    const dialog = open(ready);
    expect(dialog).toHaveTextContent("Weather data by Open-Meteo.com (CC BY 4.0)");
    expect(within(dialog).getByRole("link", { name: "Licence terms" })).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by/4.0/",
    );
  });

  it("shows the source line with the copy's age", () => {
    const dialog = open(ready);
    expect(dialog).toHaveTextContent("Open-Meteo · CC BY 4.0 · fetched 2 min ago");
  });

  it("says the answer is for a model grid cell when the cell is far from the city centre", () => {
    const dialog = open(ready);
    expect(dialog).toHaveTextContent("model grid cell");
    expect(dialog).toHaveTextContent("7.29 km");
  });

  it("does not raise the grid caveat when the cell is on top of the city", () => {
    const dialog = open({ kind: "ready", weather: reading({ grid_offset_km: 0.6 }) });
    expect(dialog.textContent).not.toContain("model grid cell");
  });

  it("marks a stale copy with its age in the degraded colour and repeats the API's note", () => {
    const dialog = open({
      kind: "ready",
      weather: reading({
        stale: true,
        age_s: 2460,
        notes: ["Open-Meteo could not be reached (timeout); showing the last good copy."],
      }),
    });
    const line = dialog.querySelector('[data-slot="weather-source-line"]');
    expect(line).not.toBeNull();
    expect(line).toHaveTextContent("Cached 41 min ago · network unavailable");
    expect(line?.className).toContain("text-status-degraded");
    expect(dialog).toHaveTextContent("showing the last good copy");
  });

  it("draws one bar per hour with its rain and chance, and never invents a missing one", () => {
    const dialog = open({
      kind: "ready",
      weather: reading({
        hourly: [
          {
            ts: "2026-09-19T15:00:00+05:30",
            precipitation_mm: 1.2,
            precipitation_probability_pct: 62,
          },
          { ts: "2026-09-19T16:00:00+05:30", precipitation_mm: null },
        ],
      }),
    });
    const bars = within(dialog).getByRole("list", {
      name: "Rain expected over the next hours",
    });
    expect(within(bars).getAllByRole("listitem")).toHaveLength(2);
    expect(bars).toHaveTextContent("1.2 mm");
    expect(bars).toHaveTextContent("62 %");
    expect(bars).toHaveTextContent("—");
  });
});
