/**
 * The "Today, next 3 h" card in every state it can be in: collapsed and not yet asked, loading,
 * ready (fresh, stale, past its window, dry), refused for this city, and unavailable. The bodies
 * come from `outlook.fixture.ts`, a body the API produced, so the card is tested against the
 * shape it will actually be sent.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HELD_STALE_AFTER_S,
  LiveOutlookCard,
  LiveOutlookView,
  nextOutlookState,
  outlookFreshness,
} from "@/components/varuna/live-outlook-card";
import { OutlookSchema, type Outlook } from "@/lib/api/outlook";
import { outlookBody } from "@/lib/api/outlook.fixture";
import { useReplayStore } from "@/lib/stores/replay";
import { stubFetch } from "@/lib/test-utils";

const FETCHED = Date.parse("2026-09-26T14:12:00+05:30");

function outlook(overrides: Record<string, unknown> = {}): Outlook {
  return OutlookSchema.parse(outlookBody(overrides)) as Outlook;
}

function dry(): Outlook {
  return outlook({
    rain_mm_h: Array.from({ length: 36 }, () => 0),
    rain_total_mm: 0,
    segments: [],
    n_segments_over_1cm: 0,
    truncated: false,
    summary: {
      max_cm: 0,
      max_p90_cm: 0,
      n_ge_5: 0,
      n_ge_15: 0,
      n_ge_30: 0,
      worst: [],
      sentence: "No rain forecast and no street above 5 cm expected in the next 3 h.",
    },
  });
}

describe("LiveOutlookView", () => {
  it("shows a skeleton while loading, never a spinner", () => {
    const { container } = render(<LiveOutlookView state={{ kind: "loading" }} nowMs={FETCHED} />);
    expect(screen.getByText("Loading today's outlook")).toBeInTheDocument();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it("puts today's window first, then the API's sentence, the streets, source, method and skill", () => {
    render(
      <LiveOutlookView state={{ kind: "ready", outlook: outlook() }} nowMs={FETCHED + 240_000} />,
    );
    expect(
      screen.getByText(/^Today, not the replay: 14:10 to 17:10 IST, 26 Sept? 2026$/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^393 streets expected above 5 cm in the next 3 h/),
    ).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Deepest streets in the next 3 h" })).toHaveTextContent(
      "Mathuradas Vasanji Road (Andheri Kurla Road)",
    );
    expect(screen.getByLabelText(/^32 cm, 30-45 cm/)).toBeInTheDocument();
    expect(
      screen.getByText(/Source: Open-Meteo hourly rain, fetched 4 min ago \(CC BY 4\.0\)\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Reduced-order emulator \(Flash-lite\) on Open-Meteo NWP rain, AOI-uniform; not a radar nowcast\. Measured skill against VARUNA-Twin on runs it never saw: RMSE 5\.7 cm, CSI 0\.085 at 30\s+cm\.$/,
      ),
    ).toBeInTheDocument();
    // The API's notes are printed verbatim under "How this is computed", not paraphrased.
    expect(screen.getByText("How this is computed")).toBeInTheDocument();
    expect(
      screen.getByText("Today, not the replay: rain for 14:10 to 17:10 IST on 26 Sep 2026."),
    ).toBeInTheDocument();
  });

  it("says no street above 5 cm when that is the answer, and names no street", () => {
    render(<LiveOutlookView state={{ kind: "ready", outlook: dry() }} nowMs={FETCHED} />);
    expect(screen.getByText(/no street above 5 cm expected/i)).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.getByText(/^0 mm of rain over the next 3 h/)).toBeInTheDocument();
  });

  it("says the window the API sent when it is shorter than 3 h, never three hours", () => {
    // `_rain` stops at the first step no hourly value covers: 20 steps of 5 min here.
    const steps = 20;
    const short = outlook({
      valid_to: "2026-09-26T15:50:00+05:30",
      n_steps: steps,
      valid_ts: Array.from({ length: steps }, (_, i) => `step-${i}`),
      rain_mm_h: Array.from({ length: steps }, (_, i) => (i < 10 ? 12 : 3)),
      rain_total_mm: 12.5,
      segments: [],
      truncated: false,
      summary: {
        ...(outlookBody().summary as object),
        worst: [
          {
            segment_id: "S1267122772-000",
            name: "Mathuradas Vasanji Road (Andheri Kurla Road)",
            peak_p50_cm: 21.4,
            peak_p90_cm: 24.0,
            peak_ts: "2026-09-26T15:30:00+05:30",
          },
        ],
        sentence:
          "212 streets expected above 5 cm in the next 100 min, 4 above 15 cm; deepest 21 cm on Mathuradas Vasanji Road (Andheri Kurla Road) around 15:30.",
      },
    });
    const { container } = render(
      <LiveOutlookView state={{ kind: "ready", outlook: short }} nowMs={FETCHED + 60_000} />,
    );
    expect(
      screen.getByText(/^Today, not the replay: 14:10 to 15:50 IST, 26 Sept? 2026$/),
    ).toBeInTheDocument();
    expect(screen.getByText(/in the next 100 min, 4 above 15 cm/)).toBeInTheDocument();
    expect(
      screen.getByText(/^13 mm of rain over the next 100 min, for one grid cell/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Deepest streets in the next 100 min" }),
    ).toHaveTextContent("Mathuradas Vasanji Road (Andheri Kurla Road)");
    // Nothing in the body claims the three hours this outlook does not cover.
    expect(container.textContent).not.toMatch(/3 h/);
  });

  it("says when it is running on a stale copy, and how old", () => {
    const stale = outlook({
      source: { ...(outlookBody().source as object), stale: true, age_s: 2400 },
    });
    render(
      <LiveOutlookView state={{ kind: "ready", outlook: stale }} nowMs={FETCHED + 2_400_000} />,
    );
    expect(
      screen.getByText(
        "This runs on a weather copy fetched 40 min ago; no newer copy has arrived.",
      ),
    ).toBeInTheDocument();
  });

  it("names no cause for a stale copy under VARUNA_OFFLINE=1, where nothing was asked", () => {
    // The API's own stale note for the offline finale (outlook `_stamp` over the weather note).
    const offlineNote =
      "The rain is from a weather copy 2 h old. VARUNA_OFFLINE=1, so Open-Meteo was not contacted. This is the last copy fetched before the network was switched off. This outlook covers 14:10 to 17:10 IST and is not today's latest.";
    const stale = outlook({
      source: { ...(outlookBody().source as object), stale: true, age_s: 7200 },
      notes: [offlineNote, ...(outlookBody().notes as string[])],
    });
    const { container } = render(
      <LiveOutlookView state={{ kind: "ready", outlook: stale }} nowMs={FETCHED + 7_200_000} />,
    );
    expect(
      screen.getByText("This runs on a weather copy fetched 2 h ago; no newer copy has arrived."),
    ).toBeInTheDocument();
    // No sentence on the card claims the service was tried and failed.
    expect(container.textContent).not.toMatch(/could not be reached/i);
    // The real cause is printed, verbatim, under "How this is computed".
    expect(screen.getByText(offlineNote)).toBeInTheDocument();
  });

  it("measures the grid cell from the mapped area's centre, as the API does", () => {
    render(<LiveOutlookView state={{ kind: "ready", outlook: outlook() }} nowMs={FETCHED} />);
    expect(
      screen.getByText(/for one grid cell 2\.5 km from the centre of the mapped area\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/city centre/)).toBeNull();
  });

  it("says when its window has passed rather than presenting it as the next 3 h", () => {
    render(
      <LiveOutlookView
        state={{ kind: "ready", outlook: outlook({ expired: true }) }}
        nowMs={FETCHED + 4 * 3_600_000}
      />,
    );
    expect(
      screen.getByText(/window has passed and no newer weather has arrived/),
    ).toHaveTextContent("the weather copy was fetched 4 h ago.");
  });

  it("carries the API's refusal for a city with no emulator", () => {
    const reason =
      "There is no live outlook for Chennai: Flash-lite, the emulator it runs on, is fitted to Mumbai's streets only.";
    render(<LiveOutlookView state={{ kind: "refused", reason }} nowMs={FETCHED} />);
    expect(screen.getByText("No live outlook for this city.")).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says what failed and offers to try again", () => {
    const onRetry = vi.fn();
    render(
      <LiveOutlookView
        state={{ kind: "unavailable", reason: "Open-Meteo did not answer." }}
        nowMs={FETCHED}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("Today's outlook could not be loaded.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("outlookFreshness", () => {
  const ready = (o: Outlook) => ({ kind: "ready" as const, outlook: o });

  it("is live while the copy is young and the window is ahead", () => {
    expect(outlookFreshness(ready(outlook()), FETCHED + 240_000)).toBe("live");
  });

  it("ages a held answer into stale once it has missed a refresh, although the API said fresh", () => {
    const held = FETCHED + (HELD_STALE_AFTER_S + 60) * 1000;
    expect(outlookFreshness(ready(outlook()), held)).toBe("stale");
  });

  it("calls a held answer expired once its window has passed on the card's own clock", () => {
    // 17:11 IST: one minute past valid_to, with the API's `expired` still false.
    expect(outlookFreshness(ready(outlook()), Date.parse("2026-09-26T17:11:00+05:30"))).toBe(
      "expired",
    );
  });

  it("claims nothing for a state with no answer", () => {
    expect(outlookFreshness({ kind: "loading" }, FETCHED)).toBe("none");
    expect(outlookFreshness({ kind: "refused", reason: "No." }, FETCHED)).toBe("none");
  });

  it("prints the stale line for a held answer, with its real age", () => {
    render(
      <LiveOutlookView
        state={{ kind: "ready", outlook: outlook() }}
        nowMs={FETCHED + 25 * 60_000}
      />,
    );
    expect(
      screen.getByText(
        "This runs on a weather copy fetched 25 min ago; no newer copy has arrived.",
      ),
    ).toBeInTheDocument();
  });
});

describe("nextOutlookState", () => {
  const held = { kind: "ready" as const, outlook: outlook() };

  it("keeps the answer on screen when a refresh for the same city fails", () => {
    const failed = { kind: "unavailable" as const, reason: "Open-Meteo did not answer." };
    expect(nextOutlookState(held, failed, "mumbai")).toBe(held);
  });

  it("never keeps another city's answer", () => {
    const failed = { kind: "unavailable" as const, reason: "No answer." };
    expect(nextOutlookState(held, failed, "chennai")).toBe(failed);
  });

  it("lets a refusal and a newer answer replace it", () => {
    const refused = { kind: "refused" as const, reason: "Not fitted to this city." };
    expect(nextOutlookState(held, refused, "mumbai")).toBe(refused);
    const newer = { kind: "ready" as const, outlook: dry() };
    expect(nextOutlookState(held, newer, "mumbai")).toBe(newer);
  });
});

describe("LiveOutlookCard", () => {
  beforeEach(() => {
    useReplayStore.getState().reset();
    // Only the date is faked, four minutes after the fixture's copy was fetched, so its window is
    // still ahead; timers stay real for the card's requests.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(FETCHED + 240_000);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("starts collapsed and asks the API nothing until it is opened", async () => {
    const fetchMock = vi.fn(stubFetch({ "/v1/outlook": outlookBody() }));
    vi.stubGlobal("fetch", fetchMock);
    render(<LiveOutlookCard />);

    const toggle = screen.getByRole("button", { name: /Today, next 3 h/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(fetchMock).not.toHaveBeenCalled();
    // Nothing has been asked, so the header claims no freshness it has not seen.
    expect(toggle).toHaveTextContent("Not loaded");
    expect(toggle).not.toHaveTextContent("Live weather");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await screen.findByText(/^393 streets expected above 5 cm/);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/v1/outlook?city=mumbai");
    expect(toggle).toHaveTextContent("Live weather");
  });

  it("brings its answer into view when the reader opens it, and only then", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/outlook": outlookBody() })));
    const scroll = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scroll;
    try {
      const { rerender } = render(<LiveOutlookCard />);
      expect(scroll).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: /Today, next 3 h/ }));
      await screen.findByText(/^393 streets expected/);
      // Instant, never smooth: no motion outside the catalogue.
      expect(scroll).toHaveBeenLastCalledWith({ block: "nearest" });
      const calls = scroll.mock.calls.length;

      // A re-render with the answer already on screen does not scroll the column again.
      rerender(<LiveOutlookCard className="w-[248px]" />);
      expect(scroll).toHaveBeenCalledTimes(calls);
      expect(screen.getByTestId("live-outlook-card")).toHaveClass("w-[248px]");
      expect(screen.getByTestId("live-outlook-card")).not.toHaveClass("w-full");
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("never moves the replay: opening it leaves the clock and the scrub where they were", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/outlook": outlookBody() })));
    act(() => {
      useReplayStore.getState().setLeadMin(45);
    });
    const before = { ...useReplayStore.getState() };
    render(<LiveOutlookCard />);
    fireEvent.click(screen.getByRole("button", { name: /Today, next 3 h/ }));
    await screen.findByText(/^393 streets expected/);

    const after = useReplayStore.getState();
    expect(after.leadMin).toBe(45);
    expect(after.simTime).toBe(before.simTime);
    expect(after.playing).toBe(before.playing);
    expect(after.bundleId).toBe(before.bundleId);
  });

  it("says Stale copy, never Live weather, once a stale answer arrives", async () => {
    const stale = outlookBody({
      source: { ...(outlookBody().source as object), stale: true, age_s: 7200 },
    });
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/outlook": stale })));
    render(<LiveOutlookCard />);
    const toggle = screen.getByRole("button", { name: /Today, next 3 h/ });
    expect(toggle).toHaveTextContent("Not loaded");

    fireEvent.click(toggle);
    await screen.findByText(/no newer copy has arrived/);
    expect(toggle).toHaveTextContent("Stale copy");
    expect(toggle).not.toHaveTextContent("Live weather");
  });

  it("asks for the console's city and shows Chennai's refusal", async () => {
    const message =
      "There is no live outlook for Chennai: Flash-lite, the emulator it runs on, is fitted to Mumbai's streets only.";
    const fetchMock = vi.fn(
      stubFetch({
        "/v1/outlook": { status: 422, body: { error: { code: "no_emulator_for_city", message } } },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<LiveOutlookCard city="chennai" collapsible={false} />);

    await screen.findByText("No live outlook for this city.");
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("Not for this city")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("city=chennai");
  });

  it("drops the previous city's answer when the city changes", async () => {
    const message = "There is no live outlook for Chennai.";
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost:8000");
      const route =
        url.searchParams.get("city") === "chennai"
          ? {
              "/v1/outlook": {
                status: 422,
                body: { error: { code: "no_emulator_for_city", message } },
              },
            }
          : { "/v1/outlook": outlookBody() };
      return stubFetch(route)(input);
    });
    const { rerender } = render(<LiveOutlookCard city="mumbai" collapsible={false} />);
    await screen.findByText(/^393 streets expected/);

    rerender(<LiveOutlookCard city="chennai" collapsible={false} />);
    await screen.findByText("No live outlook for this city.");
    expect(screen.queryByText(/^393 streets expected/)).toBeNull();
    expect(screen.queryByText("Mathuradas Vasanji Road (Andheri Kurla Road)")).toBeNull();
  });

  it("recovers from an error when asked to try again", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      calls += 1;
      const route =
        calls === 1
          ? {
              "/v1/outlook": {
                status: 503,
                body: {
                  error: {
                    code: "weather_unavailable",
                    message: "Open-Meteo did not answer and no copy is kept. Nothing was invented.",
                  },
                },
              },
            }
          : { "/v1/outlook": dry() };
      return stubFetch(route)(input);
    });
    render(<LiveOutlookCard collapsible={false} />);

    await screen.findByText("Today's outlook could not be loaded.");
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(screen.getByText(/no street above 5 cm expected/i)).toBeInTheDocument(),
    );
    expect(calls).toBe(2);
  });
});
