import { readFileSync } from "node:fs";
import path from "node:path";

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiExplorer } from "@/components/varuna/api-explorer";
import {
  DEMO_RUN_ID,
  PASSPHRASE_HEADER,
  operationsFromOpenApi,
  type OpenApiLike,
} from "@/components/varuna/api-explorer-model";
import { renderWithProviders } from "@/lib/test-utils";

const operations = operationsFromOpenApi(
  JSON.parse(
    readFileSync(path.resolve(__dirname, "../../../openapi.json"), "utf-8"),
  ) as OpenApiLike,
);

const BASE = "http://localhost:8154";

const FACILITIES = {
  facilities: [
    {
      asset_id: "hospital-001",
      name: "King Edward Memorial (KEM) Hospital, Parel",
      kind: "hospital",
      lon: 72.84218,
      lat: 19.001551,
    },
    {
      asset_id: "hospital-002",
      name: "Lokmanya Tilak Municipal General (LTMG) Hospital, Sion",
      kind: "hospital",
      lon: 72.858231,
      lat: 19.034843,
    },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A fetch that answers the facility lookup and records every other request it is asked for. */
function fakeApi(answer: (url: string, init?: RequestInit) => Response) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/v1/route/facilities")) return json(FACILITIES);
    calls.push({ url, init });
    return answer(url, init);
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

describe("ApiExplorer", () => {
  it("opens on the ambulance preset with KEM and Sion read from the register, and sends it", async () => {
    const api = fakeApi(() => json({ run_id: DEMO_RUN_ID, naive: { minutes: 5.4 } }));
    renderWithProviders(<ApiExplorer operations={operations} base={BASE} fetchImpl={api.impl} />);

    const body = (await screen.findByLabelText(/Request body/)) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("72.84218"));
    expect(body.value).toContain(DEMO_RUN_ID);

    fireEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByTestId("api-status")).toHaveTextContent("200");
    expect(screen.getByTestId("api-time")).toHaveTextContent(/ms$/);
    expect(api.calls).toHaveLength(1);
    expect(api.calls[0].url).toBe(`${BASE}/v1/route`);
    const headers = api.calls[0].init?.headers as Record<string, string>;
    expect(Object.keys(headers).map((h) => h.toLowerCase())).not.toContain(PASSPHRASE_HEADER);
    const sent = JSON.parse(String(api.calls[0].init?.body)) as Record<string, unknown>;
    expect(sent.destination).toEqual([72.858231, 19.034843]);
  });

  it("shows a refusal in the API's own error envelope", async () => {
    const api = fakeApi(() =>
      json(
        {
          error: {
            code: "unsupported_scenario",
            message: "A tide offset has no representation in the emulator.",
            run_id: DEMO_RUN_ID,
          },
        },
        422,
      ),
    );
    renderWithProviders(<ApiExplorer operations={operations} base={BASE} fetchImpl={api.impl} />);
    fireEvent.click(screen.getByRole("button", { name: "What-if at 1.3x rain" }));
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));

    expect(await screen.findByTestId("api-status")).toHaveTextContent("422");
    const envelope = screen.getByTestId("api-envelope");
    expect(envelope).toHaveTextContent("unsupported_scenario");
    expect(envelope).toHaveTextContent("A tide offset has no representation in the emulator.");
    expect(api.calls[0].url).toBe(`${BASE}/v1/whatif`);
  });

  it("sends the segments preset as a GET with the demo run and bounding box", async () => {
    const api = fakeApi(() => json({ run_id: DEMO_RUN_ID, n_segments_wet: 6492 }));
    renderWithProviders(<ApiExplorer operations={operations} base={BASE} fetchImpl={api.impl} />);
    fireEvent.click(screen.getByRole("button", { name: "Segments in a bounding box" }));
    expect(screen.getByText(/reads only run_id, city and min_depth_cm/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByTestId("api-status")).toHaveTextContent("200");
    const url = new URL(api.calls[0].url);
    expect(url.pathname).toBe("/v1/nowcast/segments");
    expect(url.searchParams.get("run_id")).toBe(DEMO_RUN_ID);
    expect(url.searchParams.get("bbox")).toBe("72.835,19.005,72.850,19.020");
    expect(api.calls[0].init?.method).toBe("GET");
  });

  it("shows a desk write without a send button or a passphrase field", async () => {
    const api = fakeApi(() => json({}));
    renderWithProviders(<ApiExplorer operations={operations} base={BASE} fetchImpl={api.impl} />);
    fireEvent.change(screen.getByLabelText("Filter operations"), { target: { value: "closures" } });
    const row = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("POST") && b.textContent.includes("/v1/ops/closures"));
    expect(row).toBeDefined();
    fireEvent.click(row as HTMLElement);

    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
    expect(screen.getByText(/never sends or stores one/)).toBeInTheDocument();
    expect(screen.queryByLabelText(new RegExp(PASSPHRASE_HEADER, "i"))).toBeNull();
    expect(screen.getByLabelText("The request as curl")).toHaveTextContent(
      "$VARUNA_OPS_PASSPHRASE",
    );
    expect(api.calls).toHaveLength(0);
  });

  it("says so when the API cannot be reached", async () => {
    const impl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    renderWithProviders(<ApiExplorer operations={operations} base={BASE} fetchImpl={impl} />);
    expect(await screen.findByText(/KEM and Sion could not be looked up/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "What-if at 1.3x rain" }));
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByTestId("api-status")).toHaveTextContent("Not reached");
    expect(screen.getByText(/never reached http:\/\/localhost:8154/)).toBeInTheDocument();
  });
});
