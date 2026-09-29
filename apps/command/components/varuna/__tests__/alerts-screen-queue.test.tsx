import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AlertsScreen } from "@/app/alerts/alerts-screen";
import { TooltipProvider } from "@/components/ui/tooltip";
import { writePassphrase } from "@/lib/api/ops";

// The screen mounts the whole app shell; under a parallel run its first render has taken past
// the 1 s default, which failed a find that passes alone.
configure({ asyncUtilTimeout: 5000 });

vi.mock("sonner", () => ({ toast: vi.fn() }));

/** The query string the screen reads; a test sets it before rendering. */
let query = "";
vi.mock("next/navigation", () => ({
  usePathname: () => "/alerts",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
}));

const RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const CYCLE = "2019-07-02T08:40:00+05:30";
const HORIZON = "2019-07-02T11:40:00+05:30";

const THRESHOLD = { severe: 45, moderate: 30, watch: 15 } as const;
type Level = keyof typeof THRESHOLD;

function alert(
  code: string,
  name: string,
  level: Level,
  from: string,
  peak: number,
  extra: Record<string, unknown> = {},
) {
  return {
    id: `VARUNA-${RUN.toUpperCase()}-STREET-${code}-${level.toUpperCase()}`,
    run_id: RUN,
    level,
    threshold_cm: THRESHOLD[level],
    headline: `${name}: depth above ${THRESHOLD[level]} cm`,
    instruction: `Avoid ${name}.`,
    area_desc: name,
    name,
    locality: null,
    scope: "segment",
    hotspot_id: null,
    lon: 72.84,
    lat: 19.01,
    peak_cm: peak,
    window_from: `2019-07-02T${from}:00+05:30`,
    window_to: HORIZON,
    window_open_ended: true,
    raised_ts: "2019-07-02T08:10:00+05:30",
    persists_cycles: 2,
    persists_unit: "cycles",
    ...extra,
  };
}

/**
 * The 08:40 shape in miniature: one severe, seven moderate (more than a level previews), two
 * watch. One moderate street is pending severe; one pending place has no row at all.
 */
const ALERTS = [
  alert("0926", "Sant Shitolebaba Maharaj Marg", "severe", "09:05", 100, {
    locality: "near Hindmata junction",
  }),
  alert("0560", "Mahatma Gandhi Road", "moderate", "09:30", 40),
  alert("0561", "Pipeline Road", "moderate", "09:10", 38, {
    members_above: 38,
    members_total: 50,
  }),
  alert("0562", "Tulsi Pipe Road", "moderate", "09:10", 44),
  alert("0563", "Dr Babasaheb Ambedkar Marg", "moderate", "09:40", 31),
  alert("0564", "Lady Jamshedji Road", "moderate", "09:50", 33),
  alert("0565", "Senapati Bapat Marg", "moderate", "10:00", 35),
  alert("0566", "LBS Marg", "moderate", "10:05", 36, {
    window_to: "2019-07-02T10:40:00+05:30",
    window_open_ended: false,
    acknowledged_by: "ward officer F/S",
    acknowledged_ts: "2019-07-02T08:52:00+05:30",
    state: "acknowledged",
  }),
  alert("0567", "Khan Abdul Gaffar Khan Road", "watch", "09:15", 18),
  alert("0568", "Veer Savarkar Marg", "watch", "09:20", 20),
];

const PENDING = [
  {
    id: "P-0560",
    level: "severe",
    headline: "Mahatma Gandhi Road: depth above 45 cm",
    area_desc: "Mahatma Gandhi Road",
    name: "Mahatma Gandhi Road",
    scope: "segment",
    peak_cm: 47,
    since_ts: CYCLE,
  },
  {
    id: "P-0999",
    level: "watch",
    headline: "Dharavi Main Road: depth above 15 cm",
    area_desc: "Dharavi Main Road",
    name: "Dharavi Main Road",
    scope: "segment",
    peak_cm: 16,
    since_ts: CYCLE,
  },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  query = "";
  window.history.replaceState(null, "", "/alerts");
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://localhost:8000");
    if (url.pathname.endsWith("/v1/runs")) {
      return json({
        runs: [
          { run_id: RUN, cycle_ts: CYCLE },
          {
            run_id: "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
            cycle_ts: "2019-07-02T09:10:00+05:30",
          },
        ],
      });
    }
    if (url.pathname.endsWith("/v1/alerts")) {
      return json({
        run_id: RUN,
        cycle_ts: CYCLE,
        alerts: ALERTS,
        pending: PENDING,
        n_pending: 175,
        cleared: [],
        n_cleared: 0,
        hysteresis: { previous_run_id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked" },
      });
    }
    if (url.pathname.endsWith("/v1/alerts/escalation")) return json({ tiers: [] });
    if (url.pathname.endsWith("/v1/alerts/sender")) return json({ configured: false });
    if (url.pathname.endsWith("/v1/alerts/delivery")) {
      return json({ run_id: RUN, n_real: 0, notes: [], rows: [] });
    }
    return json({ error: { code: "not_found", message: `No stub for ${url.pathname}` } }, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderScreen() {
  return render(
    <TooltipProvider>
      <AlertsScreen />
    </TooltipProvider>,
  );
}

function level(name: string): HTMLElement {
  return screen.getByRole("region", { name: `${name} alerts` });
}

function rowNames(section: HTMLElement): string[] {
  return within(section)
    .queryAllByRole("article")
    .filter((row) => row.closest("[hidden]") === null)
    .map((row) => row.getAttribute("aria-label") ?? "");
}

describe("AlertsScreen summary strip", () => {
  it("counts each level, the deepest place, the first onset and what raises next cycle", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const strip = screen.getByRole("region", { name: "This cycle's alerts" });
    const filters = within(strip).getByRole("group", { name: "Filter by level" });
    expect(within(filters).getByRole("button", { name: /Severe.*1/ })).toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: /Moderate.*7/ })).toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: /Watch.*2/ })).toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: /Unacknowledged.*9/ })).toBeInTheDocument();
    expect(strip).toHaveTextContent("Deepest Sant Shitolebaba Maharaj Marg, 100 cm at peak");
    expect(strip).toHaveTextContent("First street over its threshold 09:05 (+25 min)");
    expect(strip).toHaveTextContent("175 places raise a level at 09:10 if they hold");
    expect(within(strip).getByText("Exercise: replay alerts are drills")).toBeVisible();
  });

  it("filters the queue by level, and by what nobody has acknowledged", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const filters = screen.getByRole("group", { name: "Filter by level" });

    fireEvent.click(within(filters).getByRole("button", { name: /Severe/ }));
    expect(screen.getAllByRole("article").map((r) => r.getAttribute("aria-label"))).toEqual([
      "Severe: Sant Shitolebaba Maharaj Marg",
    ]);
    fireEvent.click(within(filters).getByRole("button", { name: /Severe/ }));

    fireEvent.click(within(filters).getByRole("button", { name: /Unacknowledged/ }));
    expect(screen.queryByRole("article", { name: "Moderate: LBS Marg" })).toBeNull();
  });
});

describe("AlertsScreen queue", () => {
  it("previews five per level in onset order, then shows all of that level", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const moderate = level("Moderate");
    // Onset first; at the same onset, the deeper street first.
    expect(rowNames(moderate)).toEqual([
      "Moderate: Tulsi Pipe Road",
      "Moderate: Pipeline Road",
      "Moderate: Mahatma Gandhi Road",
      "Moderate: Dr Babasaheb Ambedkar Marg",
      "Moderate: Lady Jamshedji Road",
    ]);
    fireEvent.click(within(moderate).getByRole("button", { name: "Show all 7 moderate" }));
    expect(rowNames(moderate)).toHaveLength(7);
  });

  it("keeps Watch shut while Severe or Moderate hold alerts", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const watch = level("Watch");
    const header = within(watch).getByRole("button", { expanded: false });
    expect(rowNames(watch)).toHaveLength(0);
    fireEvent.click(header);
    expect(rowNames(watch)).toHaveLength(2);
  });

  it("puts a pending level up on the row the place already has, not in a row of its own", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const row = screen.getByRole("article", { name: "Moderate: Mahatma Gandhi Road" });
    expect(within(row).getByText("Severe next cycle if it holds")).toBeInTheDocument();
    // Only the place with no row waits under watching.
    const watching = screen.getByRole("button", {
      name: "Watching, not raised yet (1)",
    });
    fireEvent.click(watching);
    // The listed entry that steps a row up is counted, not listed a second time.
    expect(
      screen.getByText("2 of 175 waiting places listed. 1 already has a lower-level row above."),
    ).toBeVisible();
  });

  it("gives each row its window, lead time, peak and status, and nothing else until asked", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const open = screen.getByRole("article", { name: "Severe: Sant Shitolebaba Maharaj Marg" });
    expect(within(open).getByText("09:05 (+25 min) until the end of the forecast")).toBeVisible();
    expect(within(open).getByText("near Hindmata junction")).toBeVisible();
    expect(within(open).getByText("100 cm")).toBeVisible();
    expect(within(open).getByText("Held 2 cycles")).toBeVisible();
    expect(within(open).queryByText(/trigger/i)).toBeNull();
    expect(within(open).queryByText(/WhatsApp mock/)).toBeNull();
    expect(within(open).queryByRole("button", { name: "Acknowledge" })).toBeNull();

    fireEvent.click(within(level("Moderate")).getByRole("button", { name: "Show all 7 moderate" }));
    const closed = screen.getByRole("article", { name: "Moderate: LBS Marg" });
    expect(within(closed).getByText("10:05 (+85 min) to 10:40")).toBeVisible();
    expect(within(closed).getByText("Acknowledged 08:52")).toBeVisible();
  });

  it("opens one alert at a time and keeps the open one in the URL", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const first = screen.getByRole("article", { name: "Severe: Sant Shitolebaba Maharaj Marg" });
    const second = screen.getByRole("article", { name: "Moderate: Pipeline Road" });

    fireEvent.click(within(first).getByRole("button", { name: "See more" }));
    expect(new URLSearchParams(window.location.search).get("alert")).toBe(ALERTS[0]!.id);
    expect(new URLSearchParams(window.location.search).get("run")).toBe(RUN);

    fireEvent.click(within(second).getByRole("button", { name: "See more" }));
    expect(within(first).getByRole("button", { name: "See more" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(new URLSearchParams(window.location.search).get("alert")).toBe(ALERTS[2]!.id);

    fireEvent.click(within(second).getByRole("button", { name: "See less" }));
    expect(new URLSearchParams(window.location.search).get("alert")).toBeNull();
  });

  it("opens the alert the URL names, even past a level's preview", async () => {
    query = `run=${RUN}&alert=${ALERTS[7]!.id}`;
    renderScreen();
    const row = await screen.findByRole("article", { name: "Moderate: LBS Marg" });
    expect(within(row).getByRole("button", { name: "See less" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(row).getByText("What to do")).toBeInTheDocument();
    // The phone leads with the open alert.
    const phone = screen.getByRole("figure", { name: "Ward officer's phone" });
    await waitFor(() =>
      expect(within(phone).getAllByRole("listitem")[0]).toHaveTextContent("LBS Marg"),
    );
  });

  it("links the open alert to a route around it and to the map, carrying the place", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const row = screen.getByRole("article", { name: "Severe: Sant Shitolebaba Maharaj Marg" });
    fireEvent.click(within(row).getByRole("button", { name: "See more" }));
    const route = within(row).getByRole("link", { name: "Plan a route around it" });
    const href = new URL(route.getAttribute("href")!, "http://localhost");
    expect(href.pathname).toBe("/route");
    expect(href.searchParams.get("to")).toBe("72.84,19.01");
    expect(href.searchParams.get("place")).toBe("Sant Shitolebaba Maharaj Marg");
    const map = within(row).getByRole("link", { name: "Show on the map" });
    expect(new URL(map.getAttribute("href")!, "http://localhost").searchParams.get("focus")).toBe(
      "72.84,19.01",
    );
    expect(within(row).getByRole("link", { name: "Open pump dispatch" })).toHaveAttribute(
      "href",
      "/pumps",
    );
  });

  it("reports how many members agree when the run carries it, and says nothing when it does not", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const counted = screen.getByRole("article", { name: "Moderate: Pipeline Road" });
    fireEvent.click(within(counted).getByRole("button", { name: "See more" }));
    // In the document, not "visible": the details open with M29, which jsdom never finishes.
    expect(within(counted).getByText("38 of 50")).toBeInTheDocument();
    expect(within(counted).getByText(/members above 30 cm/)).toBeInTheDocument();

    const uncounted = screen.getByRole("article", {
      name: "Severe: Sant Shitolebaba Maharaj Marg",
    });
    fireEvent.click(within(uncounted).getByRole("button", { name: "See more" }));
    expect(within(uncounted).queryByText(/members above/)).toBeNull();
  });
});

describe("AlertsScreen phone and city", () => {
  it("sets the phone's clock to the cycle on screen, not the replay clock", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const phone = screen.getByRole("figure", { name: "Ward officer's phone" });
    // The replay store opens at 06:40; the queue is 08:40's, and the messages were raised at 08:10.
    await waitFor(() => expect(within(phone).getByText("08:40")).toBeInTheDocument());
    expect(within(phone).queryByText("06:40")).toBeNull();
  });

  it("asks the API for the city in the address", async () => {
    const seen: string[] = [];
    const stub = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      seen.push(raw);
      return stub(input, init);
    });
    query = "city=chennai";
    renderScreen();
    await screen.findAllByRole("article");
    const alertReads = seen
      .map((raw) => new URL(raw, "http://localhost:8000"))
      .filter((url) => url.pathname.endsWith("/v1/alerts"));
    expect(alertReads.length).toBeGreaterThan(0);
    for (const url of alertReads) expect(url.searchParams.get("city")).toBe("chennai");
  });
});

describe("AlertsScreen on a capped queue", () => {
  /** The 08:40 counts over the fixture: 213 raised, of which the product listed the worst few. */
  function stubCapped(pending: unknown[]) {
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, "http://localhost:8000");
      if (url.pathname.endsWith("/v1/alerts")) {
        return json({
          run_id: RUN,
          cycle_ts: CYCLE,
          alerts: ALERTS,
          n_raised: 213,
          n_raised_by_level: { severe: 13, moderate: 35, watch: 165 },
          first_onset: { ts: "2019-07-02T08:45:00+05:30", name: "Step Marg", level: "watch" },
          pending,
          n_pending: 175,
          n_pending_new: 109,
          n_pending_step_up: 66,
          cleared: [],
          n_cleared: 0,
          hysteresis: { previous_run_id: null },
        });
      }
      return base(input, init);
    });
  }

  const STEP_UP = {
    id: "P-0777",
    level: "moderate",
    headline: "Pipeline Link Road: depth above 30 cm",
    area_desc: "Pipeline Link Road",
    name: "Pipeline Link Road",
    scope: "segment",
    peak_cm: 33,
    since_ts: CYCLE,
    raised_level: "watch",
  };

  it("counts every alert raised, says the list is the worst of them, and names the first onset", async () => {
    stubCapped([
      { ...PENDING[0], raised_level: "moderate" },
      { ...PENDING[1], raised_level: null },
      STEP_UP,
    ]);
    renderScreen();
    await screen.findAllByRole("article");
    const strip = screen.getByRole("region", { name: "This cycle's alerts" });
    const filters = within(strip).getByRole("group", { name: "Filter by level" });
    expect(within(filters).getByRole("button", { name: /Severe.*13.*1 listed/ })).toBeVisible();
    expect(within(filters).getByRole("button", { name: /Watch.*165.*2 listed/ })).toBeVisible();
    expect(strip).toHaveTextContent("Worst 10 of 213 alerts listed. Filters cover the 10 listed.");
    // The earliest window over all 213, which the listed ten do not hold.
    expect(strip).toHaveTextContent("First street over its threshold 08:45 (+5 min), Step Marg");
    expect(strip).toHaveTextContent("109 new places raise at 09:10 if they hold");
    expect(strip).toHaveTextContent("66 raised places go up a level at 09:10 if they hold");
  });

  it("puts a place raised beyond the list under its own heading, never under not raised yet", async () => {
    stubCapped([
      { ...PENDING[0], raised_level: "moderate" },
      { ...PENDING[1], raised_level: null },
      STEP_UP,
    ]);
    renderScreen();
    await screen.findAllByRole("article");
    const watching = screen.getByRole("region", {
      name: "Watching, not raised yet (1 shown of 109)",
    });
    expect(within(watching).queryByText("Pipeline Link Road")).toBeNull();
    const stepping = screen.getByRole("button", {
      name: "Raised, not among the 10 listed, going up a level (1)",
    });
    fireEvent.click(stepping);
    expect(screen.getByText("Raised at Watch; Moderate at 09:10 if it holds")).toBeVisible();
  });

  it("says it cannot tell when the API does not name the raised level", async () => {
    stubCapped([{ ...STEP_UP, raised_level: undefined }]);
    renderScreen();
    await screen.findAllByRole("article");
    expect(screen.queryByRole("region", { name: /Watching, not raised yet/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Crossed a level this cycle, may already be raised (1)" }),
    ).toBeInTheDocument();
  });
});

describe("AlertsScreen level controls", () => {
  it("shuts a level and the open alert in it together, so the header never goes dead", async () => {
    renderScreen();
    await screen.findAllByRole("article");
    const severe = level("Severe");
    const row = screen.getByRole("article", { name: "Severe: Sant Shitolebaba Maharaj Marg" });
    fireEvent.click(within(row).getByRole("button", { name: "See more" }));
    const header = severe.querySelector<HTMLButtonElement>(
      'button[aria-controls="alert-level-severe"]',
    )!;
    expect(header).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(rowNames(severe)).toHaveLength(0);
    expect(new URLSearchParams(window.location.search).get("alert")).toBeNull();
    fireEvent.click(header);
    expect(within(row).getByRole("button", { name: "See more" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("shows fewer, closing an open alert past the preview rather than ignoring the press", async () => {
    query = `run=${RUN}&alert=${ALERTS[7]!.id}`;
    renderScreen();
    await screen.findByRole("article", { name: "Moderate: LBS Marg" });
    const moderate = level("Moderate");
    expect(rowNames(moderate)).toHaveLength(7);
    fireEvent.click(within(moderate).getByRole("button", { name: "Show fewer" }));
    expect(rowNames(moderate)).toHaveLength(5);
    expect(new URLSearchParams(window.location.search).get("alert")).toBeNull();
  });
});

describe("AlertsScreen send to my phone", () => {
  afterEach(() => {
    writePassphrase("");
  });

  it("records a real send under the city on screen and the run it came from", async () => {
    const sends: { path: string; runId: string | null; body: Record<string, unknown> }[] = [];
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, "http://localhost:8000");
      if (url.pathname.endsWith("/v1/alerts/sender")) {
        return json({
          configured: true,
          provider: "twilio",
          channel: "sms",
          to_masked: "+91***45",
        });
      }
      if (init?.method === "POST" && url.pathname.endsWith("/send")) {
        sends.push({
          path: url.pathname,
          runId: url.searchParams.get("run_id"),
          body: JSON.parse(String(init.body ?? "{}")) as Record<string, unknown>,
        });
        return json({ run_id: RUN, delivery: { status: "Sent", to_masked: "+91***45" } });
      }
      return base(input, init);
    });
    writePassphrase("monsoon desk 2026");
    query = "city=chennai";
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /Send to my phone/ }));
    await waitFor(() => expect(sends).toHaveLength(1));
    expect(sends[0]!.body.city).toBe("chennai");
    expect(sends[0]!.runId).toBe(RUN);
  });
});
