/**
 * SPEC.md 7.4 AC3: once a route has been asked for, changing the departure time or the profile
 * routes again - and the profile reaches the API under the key the API knows it by.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Place, RoutePlan, RouteQuery } from "@/lib/api/route";

const places: Place[] = [
  {
    id: "kem",
    name: "King Edward Memorial (KEM) Hospital, Parel",
    kind: "hospital",
    lon: 72.84218,
    lat: 19.001551,
  },
  {
    id: "ltmg",
    name: "Lokmanya Tilak Municipal General (LTMG) Hospital, Sion",
    kind: "hospital",
    lon: 72.858231,
    lat: 19.034843,
  },
];

const asked: RouteQuery[] = [];

function answer(query: RouteQuery): RoutePlan {
  return {
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    profile: query.profile,
    departAt: query.departAt,
    naive: null,
    varuna: null,
    alternates: [],
    avoided: [],
    corridors: [],
    reasons: [],
    tripId: null,
    notes: [],
    ms: 70,
  };
}

vi.mock("@/lib/api/route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/route")>();
  return {
    ...actual,
    loadPlaces: vi.fn(async () => places),
    planRoute: vi.fn(async (query: RouteQuery) => {
      asked.push(query);
      return answer(query);
    }),
  };
});

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));
vi.mock("@/components/map/city-map", () => ({ CityMap: () => null }));

const { RouteScreen, REROUTE_DEBOUNCE_MS } = await import("../route-screen");

beforeEach(() => {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ features: [] }), { status: 200 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function findRoute() {
  render(<RouteScreen />);
  const button = await screen.findByRole("button", { name: "Find route" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(asked).toHaveLength(1));
}

describe("RouteScreen re-routes on a changed trip", () => {
  it("routes the demo trip by ambulance when asked, and not before", async () => {
    render(<RouteScreen />);
    await screen.findByRole("button", { name: "Find route" });
    await act(() => new Promise((r) => setTimeout(r, REROUTE_DEBOUNCE_MS * 2)));
    expect(asked).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Find route" }));
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0].origin.id).toBe("kem");
    expect(asked[0].destination.id).toBe("ltmg");
    expect(asked[0].profile).toBe("ambulance");
  });

  it("changing the profile routes again, under the API's key for it", async () => {
    await findRoute();
    fireEvent.change(screen.getByLabelText("Vehicle profile"), {
      target: { value: "two-wheeler" },
    });
    await waitFor(() => expect(asked).toHaveLength(2));
    // The picker says "two-wheeler"; varuna_route.profiles keys it "two_wheeler". Sent as the
    // picker spells it, the API answered 422.
    expect(asked[1].profile).toBe("two_wheeler");
    expect(asked[1].riskTolerance).toBe(0.5);

    fireEvent.change(screen.getByLabelText("Vehicle profile"), {
      target: { value: "pedestrian" },
    });
    await waitFor(() => expect(asked).toHaveLength(3));
    expect(asked[2].profile).toBe("pedestrian");
  });

  it("changing the departure time routes again at that time", async () => {
    await findRoute();
    const before = asked[0].departAt;
    fireEvent.change(screen.getByLabelText("Departure time"), { target: { value: "10:40" } });
    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1].departAt).toBe(`${before.slice(0, 10)}T10:40:00+05:30`);
    expect(asked[1].profile).toBe("ambulance");
  });

  it("a burst of changes asks once, for the last of them", async () => {
    await findRoute();
    const time = screen.getByLabelText("Departure time");
    fireEvent.change(time, { target: { value: "09:00" } });
    fireEvent.change(time, { target: { value: "09:30" } });
    fireEvent.change(time, { target: { value: "10:00" } });
    await waitFor(() => expect(asked).toHaveLength(2));
    await act(() => new Promise((r) => setTimeout(r, REROUTE_DEBOUNCE_MS * 2)));
    expect(asked).toHaveLength(2);
    expect(asked[1].departAt.slice(11, 16)).toBe("10:00");
  });
});
