import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportWizard } from "@/components/varuna/report-wizard";
import { stubFetch } from "@/lib/test-utils";

/**
 * What `POST /v1/reports` actually answered for a knee-deep report at Hindmata on 2026-09-13,
 * copied from the response verbatim. `feedback_streets` is null because the count SPEC.md 11.6
 * defines - segments whose p50 moves by more than 3 cm - belongs to the EnKF, which runs on the
 * next cycle and not inside this request.
 */
const QUEUED = {
  id: "rpt-1789280743240-3aff80",
  accepted: true,
  run_id: null,
  streets_nearby: 0,
  feedback_streets: null,
  message: "Thanks. Your report is queued; the next cycle assimilates it.",
};

function renderWizard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReportWizard />
    </QueryClientProvider>,
  );
}

function click(name: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ReportWizard", () => {
  it("advances through location, photo and depth", () => {
    renderWizard();

    expect(screen.getByRole("heading", { name: "Where is the water?" })).toBeInTheDocument();
    expect(screen.getByLabelText("Latitude")).toHaveValue("19.012");
    expect(screen.getByLabelText("Longitude")).toHaveValue("72.841");

    click("Continue to photo");
    expect(screen.getByRole("heading", { name: "Add a photo" })).toBeInTheDocument();

    click("Skip photo");
    expect(screen.getByRole("heading", { name: "How deep is the water?" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "How deep is the water" })).toBeInTheDocument();
  });

  it("refuses to send without a depth chip", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    renderWizard();

    click("Continue to photo");
    click("Skip photo");

    const send = screen.getByRole("button", { name: "Send report" });
    expect(send).toBeDisabled();
    expect(screen.getByText("Pick a depth to send the report.")).toBeInTheDocument();

    fireEvent.click(send);
    expect(fetchSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    expect(screen.getByRole("button", { name: "Send report" })).not.toBeDisabled();

    fetchSpy.mockRestore();
  });

  it("shows the API's queued message rather than a count nothing has computed yet", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/reports": { status: 202, body: QUEUED } })));
    const { container } = renderWizard();

    click("Continue to photo");
    click("Skip photo");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");

    expect(await screen.findByRole("heading", { name: "Report sent" })).toBeInTheDocument();
    expect(screen.getByText(QUEUED.message)).toBeInTheDocument();
    // The defect this test exists for: `feedback_streets ?? 0` headlined an improved forecast for
    // a count of zero streets, a claim of effect over a number nobody measured (SPEC.md rule 6).
    // `\b` so "10 streets" is not a zero; the regex is proven to catch the defect first, so it
    // can never pass by matching nothing (it once held a literal backspace and always passed).
    const zeroStreets = /\b0 streets?\b/;
    expect("improved the forecast for 0 streets").toMatch(zeroStreets);
    expect("improved the forecast for 10 streets").not.toMatch(zeroStreets);
    expect(container.textContent).not.toMatch(zeroStreets);
    expect(container.textContent).not.toContain("improved the forecast");
  });

  it("keeps the count once Pulse has one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          "/v1/reports": { status: 202, body: { ...QUEUED, feedback_streets: 3, message: null } },
        }),
      ),
    );
    renderWizard();

    click("Continue to photo");
    click("Skip photo");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");

    expect(
      await screen.findByRole("heading", { name: /improved the forecast for 3 streets/ }),
    ).toBeInTheDocument();
  });
});
