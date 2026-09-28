import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AlertsScreen } from "@/app/alerts/alerts-screen";
import { TooltipProvider } from "@/components/ui/tooltip";
import { clearPassphrase, writePassphrase } from "@/lib/api/ops";

// The screen mounts the whole app shell; under a parallel run its first render has taken past
// the 1 s default, which failed a find that passes alone.
configure({ asyncUtilTimeout: 5000 });

vi.mock("sonner", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/alerts",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const ALERT_ID = `VARUNA-${RUN.toUpperCase()}-HINDMATA-SEVERE`;

const TIERS = [
  {
    id: "ward_officer",
    recipient: "Ward officer",
    trigger: "Watch raised",
    channel: "WhatsApp",
    levels: ["watch", "moderate", "severe"],
  },
  {
    id: "control_room",
    recipient: "Control room",
    trigger: "Moderate raised",
    channel: "Phone",
    levels: ["moderate", "severe"],
  },
  {
    id: "police_traffic",
    recipient: "Police and traffic",
    trigger: "Severe raised",
    channel: "SMS",
    levels: ["severe"],
  },
  {
    id: "transit",
    recipient: "Transit (buses, suburban rail)",
    trigger: "Escalated",
    channel: "GTFS-RT",
    levels: [],
  },
  { id: "public", recipient: "Public", trigger: "Escalated", channel: "CAP feed", levels: [] },
];

let senderConfigured = false;
const acts: { path: string; body: Record<string, unknown> }[] = [];
/** Every path the stub answered. The escalation matrix is no longer drawn, so a test that needs
 *  `config/escalation.yaml` to have been read waits on this instead of on a rendered tier. */
const served: string[] = [];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  senderConfigured = false;
  acts.length = 0;
  served.length = 0;
  clearPassphrase();
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://localhost:8000");
    served.push(url.pathname);
    if (init?.method === "POST") {
      acts.push({ path: url.pathname, body: JSON.parse(String(init.body ?? "{}")) });
      return json({ run_id: RUN, entry: { id: "e1", kind: "x", ts: "t" }, alert: {}, notes: [] });
    }
    if (url.pathname.endsWith("/v1/runs")) {
      return json({ runs: [{ run_id: RUN, cycle_ts: "2019-07-02T08:40:00+05:30" }] });
    }
    if (url.pathname.endsWith("/v1/alerts")) {
      return json({
        run_id: RUN,
        alerts: [
          {
            id: ALERT_ID,
            run_id: RUN,
            level: "severe",
            threshold_cm: 45,
            headline: "Hindmata junction: depth above 45 cm from 08:45 to 09:40",
            instruction: "Avoid Hindmata junction for the window",
            area_desc: "Ward F/S, Hindmata junction",
            scope: "hotspot",
            hotspot_id: "MUM-HS-01",
            raised_ts: "2019-07-02T08:10:00+05:30",
            sent_ts: "2019-07-02T08:40:00+05:30",
            persists_cycles: 2,
            persists_unit: "cycles",
            trigger_p: 1,
            notify: ["ward_officer", "control_room", "police_traffic"],
            pumps: ["P-05"],
            dispatch_note: "Pump P-05 dispatched.",
          },
        ],
        pending: [
          {
            id: "P1",
            level: "moderate",
            headline: "Sion Circle: depth above 30 cm from 09:00 to 09:30",
            area_desc: "Sion Circle",
            scope: "hotspot",
            peak_cm: 34,
            since_ts: "2019-07-02T08:40:00+05:30",
          },
        ],
        n_pending: 7,
        cleared: [],
        n_cleared: 0,
        hysteresis: { previous_run_id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked" },
      });
    }
    if (url.pathname.endsWith("/v1/alerts/escalation")) return json({ tiers: TIERS });
    if (url.pathname.endsWith("/v1/alerts/sender")) {
      return json(
        senderConfigured
          ? {
              configured: true,
              provider: "twilio",
              channel: "whatsapp",
              to_masked: "whatsapp:+91******2345",
            }
          : { configured: false, provider: null, channel: null, to_masked: null },
      );
    }
    if (url.pathname.endsWith("/v1/alerts/delivery")) {
      return json({
        run_id: RUN,
        n_real: 0,
        notes: ["No real sender is configured, so no message has been sent to any phone."],
        rows: [
          {
            id: "a-d",
            alert_id: ALERT_ID,
            label: "Dashboard",
            kind: "mock",
            status: "Shown on the alert queue",
            ts: "2019-07-02T08:40:00+05:30",
          },
          {
            id: "a-w",
            alert_id: ALERT_ID,
            label: "WhatsApp mock",
            kind: "mock",
            status: "Shown on the on-screen phone",
            ts: "2019-07-02T08:40:00+05:30",
            text: "VARUNA severe alert (exercise)\nHindmata junction: depth above 45 cm.\nPump P-05 dispatched.",
          },
          {
            id: "a-s",
            alert_id: ALERT_ID,
            label: "SMS mock",
            kind: "mock",
            status: "Rendered, not sent",
            ts: "2019-07-02T08:40:00+05:30",
            text: "VARUNA severe exercise: Hindmata junction: depth above 45 cm. Avoid the street.",
          },
        ],
      });
    }
    if (/\.cap$/.test(url.pathname)) return new Response("<alert/>", { status: 200 });
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

describe("AlertsScreen cross-cycle state", () => {
  it("says how long the alert has held, and lists what raises next cycle under watching", async () => {
    renderScreen();
    const row = await screen.findByRole("article");
    expect(within(row).getByText("Held 2 cycles")).toBeInTheDocument();
    // The panel states the rule the product computes, not a probability it never uses.
    expect(
      screen.getByText(/two 5-minute steps in a row, on two consecutive cycles/),
    ).toBeVisible();
    expect(screen.queryByText(/P ≥ 0\.6/)).toBeNull();
    // 7 places are pending; the run lists one, and it names a place the queue has no row for.
    expect(screen.getByText(/places raise a level next cycle if they hold/)).toBeInTheDocument();
    const watching = screen.getByRole("button", {
      name: "Watching, not raised yet (1)",
    });
    expect(screen.getByText("Sion Circle")).not.toBeVisible();
    fireEvent.click(watching);
    expect(screen.getByText("Sion Circle")).toBeVisible();
    expect(
      screen.getByText("This run lists 1 of the 7 places waiting to raise or go up a level."),
    ).toBeVisible();
  });

  it("puts the dispatched pumps on the phone", async () => {
    renderScreen();
    await screen.findByRole("article");
    const phone = screen.getByRole("figure", { name: "Ward officer's phone" });
    await waitFor(() =>
      expect(within(phone).getByText(/Pump P-05 dispatched\./)).toBeInTheDocument(),
    );
  });

  it("opens the details under the row: what to do, who was told, the timeline and the messages", async () => {
    renderScreen();
    const row = await screen.findByRole("article");
    const more = within(row).getByRole("button", { name: "See more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(within(row).queryByText("What to do")).toBeNull();

    fireEvent.click(more);
    expect(within(row).getByRole("button", { name: "See less" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(row).getByText("Avoid Hindmata junction for the window")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Plan a route around it" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/route\?run=/),
    );
    await waitFor(() =>
      expect(
        within(row).getByText("Told when raised: Ward officer, Control room, Police and traffic."),
      ).toBeInTheDocument(),
    );
    expect(within(row).getByText(/Next step: escalate to Transit/)).toBeInTheDocument();
    expect(within(row).getByText("Raised")).toBeInTheDocument();
    // The API's own renders, not text composed on this screen.
    await waitFor(() => expect(within(row).getByText(/Avoid the street\./)).toBeInTheDocument());
  });
});

describe("AlertsScreen escalation, sender and delivery", () => {
  it("escalates one step past the level's tiers in config/escalation.yaml", async () => {
    writePassphrase("monsoon desk 2026");
    renderScreen();
    await waitFor(() => expect(served).toContain("/v1/alerts/escalation"));
    const row = await screen.findByRole("article");
    fireEvent.click(within(row).getByRole("button", { name: "See more" }));
    fireEvent.click(within(row).getByRole("button", { name: "Escalate" }));
    await waitFor(() => expect(acts).toHaveLength(1));
    expect(acts[0]!.path).toBe(`/v1/alerts/${ALERT_ID}/escalate`);
    // Severe already reached ward officer, control room and police; the next step is transit.
    expect(acts[0]!.body.escalate_to).toBe("transit");
  });

  it("lists an alert's delivery rows only once its details are opened", async () => {
    renderScreen();
    const row = await screen.findByRole("article");
    await waitFor(() => expect(served).toContain("/v1/alerts/delivery"));
    expect(screen.queryByText("Rendered, not sent")).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "See more" }));
    // In the document rather than visible: the details open with M29, which starts transparent.
    expect(within(row).getByText("Rendered, not sent")).toBeInTheDocument();
  });

  it("keeps the escalation matrix shut until it is asked for", async () => {
    renderScreen();
    await screen.findByRole("article");
    const cell = await screen.findByText("Transit (buses, suburban rail)", { selector: "td" });
    expect(cell).not.toBeVisible();
    const show = screen
      .getAllByRole("button", { name: "Show" })
      .find((b) => b.getAttribute("aria-controls") === "escalation-matrix")!;
    fireEvent.click(show);
    expect(cell).toBeVisible();
    expect(screen.getAllByText("By escalation only")).toHaveLength(2);
  });

  it("draws no Send to my phone without a configured sender", async () => {
    renderScreen();
    await screen.findByRole("article");
    await screen.findByText(/no message has been sent to any phone/);
    expect(screen.queryByRole("button", { name: /Send to my phone/ })).toBeNull();
  });

  it("offers Send to my phone only when the API reports a sender, and sends through the gate", async () => {
    senderConfigured = true;
    writePassphrase("monsoon desk 2026");
    renderScreen();
    const button = await screen.findByRole("button", { name: /Send to my phone/ });
    expect(screen.getByText(/whatsapp:\+91\*\*\*\*\*\*2345/)).toBeInTheDocument();
    fireEvent.click(button);
    await waitFor(() => expect(acts.some((a) => a.path.endsWith("/send"))).toBe(true));
  });
});
