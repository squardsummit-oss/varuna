/**
 * What the console reads out of its own address bar (chunk INTEGRATE, defects 1 and 2).
 *
 * Two defects, one cause: the query string was read once, from `window.location`, in a `useState`
 * initialiser, and the run registry was asked without a city.
 *
 *   1. `/console?city=chennai` looked up "the cycle at 06:40" across every city's runs, found the
 *      Mumbai one and pinned it to a Chennai map. Nothing looked broken - which is what made it
 *      worth a test rather than a glance.
 *   2. A `next/link` to `/console?run=<id>` landed with no run, because an initialiser that reads
 *      `window.location` runs before the router has finished the navigation, and never runs again.
 *
 * The map is mocked so the assertions are about the two props that carry the answer - `city` and
 * `runId` - rather than about deck.gl, which jsdom has no GPU for anyway.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleScreen } from "@/app/console/console-screen";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/console",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

/** The map, reduced to the two props this test is about. */
const map = vi.hoisted(() => ({ calls: [] as { city?: string; runId?: string }[] }));

vi.mock("@/components/map/flood-map", () => ({
  FloodMap: (props: { city?: string; runId?: string; deferLoad?: boolean }) => {
    map.calls.push({ city: props.city, runId: props.runId });
    return (
      <div
        data-testid="flood-map"
        data-city={props.city ?? ""}
        data-run={props.runId ?? ""}
        data-defer={props.deferLoad ? "yes" : "no"}
      />
    );
  },
}));

const MUMBAI_0640 = "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked";
const CHENNAI_LATEST = "CHN-20260701T0120Z-sky1.0-twin1.0-flash0.0-baked";

/** Every city's runs live in one registry; `CHN-` sorts after `MUM-` for the same instant. */
const REGISTRY: Record<string, { run_id: string; cycle_ts: string }[]> = {
  mumbai: [
    {
      run_id: "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
      cycle_ts: "2019-07-02T09:10:00+05:30",
    },
    { run_id: MUMBAI_0640, cycle_ts: "2019-07-02T06:40:00+05:30" },
  ],
  // Chennai's design storm is a different day, so no cycle sits at the replay's 06:40 opening.
  chennai: [{ run_id: CHENNAI_LATEST, cycle_ts: "2026-07-01T06:50:00+05:30" }],
  nowhere: [],
};

/** Which city `/v1/runs` was asked about, in call order. */
let runsQueriedFor: (string | null)[] = [];

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/v1/runs") {
        const city = url.searchParams.get("city");
        runsQueriedFor.push(city);
        return Promise.resolve(
          new Response(JSON.stringify({ runs: REGISTRY[city ?? ""] ?? REGISTRY.mumbai }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      // Everything else the chrome asks for: an honest empty answer, not a crash.
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
}

/** The console inside the providers the app shell gives it. Retries off: a 404 in this test is an
 * answer, not something to wait out. */
function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={client}>
      <ConsoleScreen />
    </QueryClientProvider>
  );
  const view = render(tree());
  // A fresh element every time: React bails out of a re-render with the identical element, which
  // is exactly the "nothing happened" this test exists to catch.
  /** Re-render in place: what a client-side navigation does, with a new query string. */
  return { ...view, again: () => view.rerender(tree()) };
}

/** The run the map was last handed, once it has stopped deferring its load. */
async function settledMap() {
  const node = await screen.findByTestId("flood-map");
  await waitFor(() => expect(node.getAttribute("data-defer")).toBe("no"));
  return node;
}

describe("the console's address bar", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams();
    map.calls = [];
    runsQueriedFor = [];
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
    stubFetch();
  });

  it("opens Mumbai on the 06:40 cycle the demo script starts from", async () => {
    renderConsole();
    const node = await settledMap();
    expect(node).toHaveAttribute("data-city", "mumbai");
    expect(node).toHaveAttribute("data-run", MUMBAI_0640);
    expect(runsQueriedFor).toContain("mumbai");
  });

  it("asks a Chennai console about Chennai's runs, and never pins a Mumbai one", async () => {
    nav.params = new URLSearchParams("city=chennai");
    renderConsole();
    const node = await settledMap();
    expect(node).toHaveAttribute("data-city", "chennai");
    expect(runsQueriedFor).toContain("chennai");
    // No Chennai cycle sits at the replay's opening instant, so no run is pinned and the API's
    // own newest-for-Chennai answers. What must never happen is the Mumbai run appearing here.
    expect(node).toHaveAttribute("data-run", "");
    expect(map.calls.some((call) => call.runId === MUMBAI_0640)).toBe(false);
  });

  it("lets a city with no runs reach its own empty state rather than another city's water", async () => {
    nav.params = new URLSearchParams("city=nowhere");
    renderConsole();
    const node = await settledMap();
    expect(node).toHaveAttribute("data-city", "nowhere");
    expect(node).toHaveAttribute("data-run", "");
  });

  it("hides the Mumbai cycle chips on another city's console", async () => {
    renderConsole();
    await settledMap();
    expect(screen.queryByText("Cycle")).not.toBeNull();

    nav.params = new URLSearchParams("city=chennai");
    renderConsole();
    await waitFor(() => expect(screen.queryAllByTestId("flood-map")).toHaveLength(2));
    // One console is Mumbai's, so one row of chips: the Chennai console adds none.
    expect(screen.queryAllByText("Cycle")).toHaveLength(1);
  });

  it("follows a navigation to a pinned run instead of reading the URL once at mount", async () => {
    // Mount with no run, the way a link from another screen arrives while the router is still
    // mid-navigation: the old initialiser read `window.location` here and got nothing.
    const view = renderConsole();
    await settledMap();

    const target = "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked";
    nav.params = new URLSearchParams(`run=${target}`);
    view.again();

    await waitFor(() =>
      expect(screen.getByTestId("flood-map")).toHaveAttribute("data-run", target),
    );
    // A pinned run needs no registry lookup, and must not be overwritten by one already in flight.
    expect(screen.getByTestId("flood-map")).toHaveAttribute("data-defer", "no");
  });

  it("lets a second navigation change the pinned run", async () => {
    nav.params = new URLSearchParams(`run=${MUMBAI_0640}`);
    const view = renderConsole();
    expect(await settledMap()).toHaveAttribute("data-run", MUMBAI_0640);

    nav.params = new URLSearchParams(`run=${CHENNAI_LATEST}&city=chennai`);
    view.again();
    await waitFor(() =>
      expect(screen.getByTestId("flood-map")).toHaveAttribute("data-run", CHENNAI_LATEST),
    );
    expect(screen.getByTestId("flood-map")).toHaveAttribute("data-city", "chennai");
  });
});
