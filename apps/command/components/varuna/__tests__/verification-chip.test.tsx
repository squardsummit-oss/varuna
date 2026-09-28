import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServedVerificationChip, VerificationChip } from "@/components/varuna/verification-chip";
import {
  headlineFromBody,
  loadVerificationHeadline,
  resetVerificationHeadlineCache,
} from "@/lib/api/verification";

/** The committed copy the page falls back to; the same file `/verification.json` serves. */
const committed = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../public/verification.json"), "utf-8"),
) as Record<string, unknown>;

/** What the chip prints from the committed copy, derived rather than typed, so re-scoring the
 * shipped runs (P9.7) cannot leave this test asserting last week's number. */
const COMMITTED_LINE = `CSI ${(committed.scores as { csi: number }).csi.toFixed(2)} at 15 cm on this event`;

/** A served body, shaped like `varuna_verify.event.sweep`, with numbers that differ from the
 * committed copy so a test can tell which one the chip printed. */
const served = {
  event: "MUM-2019-07-02",
  headline_threshold_cm: 30.0,
  threshold_cm: 30.0,
  scores: { csi: 0.111 },
  by_threshold: {
    "15": { threshold_cm: 15.0, scores: { csi: 0.5 } },
    "30": { threshold_cm: 30.0, scores: { csi: 0.333 } },
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("headlineFromBody", () => {
  it("reads the CSI at the threshold the scorer marks as headline", () => {
    expect(headlineFromBody(served, "api")).toEqual({
      kind: "scored",
      csi: 0.333,
      thresholdCm: 30,
      event: "MUM-2019-07-02",
      source: "api",
    });
  });

  it("reads the committed file's headline, 15 cm", () => {
    const headline = headlineFromBody(committed, "committed");
    expect(headline).toMatchObject({ kind: "scored", thresholdCm: 15, source: "committed" });
  });

  it("is unscored when the headline row has no CSI", () => {
    expect(headlineFromBody({ headline_threshold_cm: 15, by_threshold: {} }, "api").kind).toBe(
      "unscored",
    );
  });
});

describe("VerificationChip", () => {
  it("prints the score with its threshold", () => {
    render(<VerificationChip csi={0.219} thresholdCm={15} />);
    expect(screen.getByText(/CSI/)).toHaveTextContent("CSI 0.22 at 15 cm on this event");
  });

  it("shows a shimmer while loading, never 'Not scored yet'", () => {
    const { container } = render(<VerificationChip loading />);
    expect(container.querySelector('[data-state="loading"]')).not.toBeNull();
    expect(screen.queryByText("Not scored yet")).toBeNull();
  });

  it("says a score is unavailable and why", () => {
    render(<VerificationChip error="The API is unreachable." />);
    expect(screen.getByText("Score unavailable")).toBeInTheDocument();
    expect(screen.getByText(/The API is unreachable\./)).toBeInTheDocument();
  });
});

describe("ServedVerificationChip", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    resetVerificationHeadlineCache();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is a shimmer while the API has not answered", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    const { container } = render(<ServedVerificationChip />);
    expect(container.querySelector('[data-state="loading"]')).not.toBeNull();
    expect(screen.queryByText("Not scored yet")).toBeNull();
  });

  it("prints the served headline", async () => {
    fetchMock.mockResolvedValue(json(served));
    const { container } = render(<ServedVerificationChip />);
    expect(await screen.findByText(/CSI/)).toHaveTextContent("CSI 0.33 at 30 cm on this event");
    expect(container.querySelector('[data-source="api"]')).not.toBeNull();
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/v1/verification?event=MUM-2019-07-02");
  });

  it("falls back to the committed file when the API is unreachable, and says so", async () => {
    fetchMock.mockImplementation(async (input) => {
      if (String(input) === "/verification.json") return json(committed);
      throw new TypeError("Failed to fetch");
    });
    const { container } = render(<ServedVerificationChip />);
    expect(await screen.findByText(/CSI/)).toHaveTextContent(COMMITTED_LINE);
    const chip = container.querySelector('[data-slot="verification-chip"]');
    expect(chip).toHaveAttribute("data-source", "committed");
    expect(chip?.getAttribute("title")).toMatch(/committed/);
  });

  it("treats a gateway that got no answer from the app as unreachable", async () => {
    fetchMock.mockImplementation(async (input) =>
      String(input) === "/verification.json"
        ? json(committed)
        : new Response("Application failed to respond", { status: 502 }),
    );
    const headline = await loadVerificationHeadline();
    expect(headline).toMatchObject({ kind: "scored", source: "committed", thresholdCm: 15 });
  });

  it("believes a 404: the event has no ground truth, so it is not scored", async () => {
    fetchMock.mockResolvedValue(
      json(
        { error: { code: "no_ground_truth", message: "No ground truth for CHN-IDF-25yr." } },
        404,
      ),
    );
    render(<ServedVerificationChip event="CHN-IDF-25yr" />);
    expect(await screen.findByText("Not scored yet")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the committed copy while the API is still scoring, then the served score", async () => {
    let answer: (response: Response) => void = () => undefined;
    fetchMock.mockImplementation((input) =>
      String(input) === "/verification.json"
        ? Promise.resolve(json(committed))
        : new Promise<Response>((resolve) => (answer = resolve)),
    );
    const { container } = render(<ServedVerificationChip interimAfterMs={0} />);
    expect(await screen.findByText(/CSI/)).toHaveTextContent(COMMITTED_LINE);
    const chip = () => container.querySelector('[data-slot="verification-chip"]');
    expect(chip()).toHaveAttribute("data-source", "committed");
    expect(chip()?.getAttribute("title")).toMatch(/still scoring/);

    answer(json(served));
    await waitFor(() => expect(chip()).toHaveAttribute("data-source", "api"));
    expect(screen.getByText(/CSI/)).toHaveTextContent("CSI 0.33 at 30 cm on this event");
  });

  it("names the failure when neither the API nor the committed copy answers", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ServedVerificationChip />);
    expect(await screen.findByText("Score unavailable")).toBeInTheDocument();
    expect(screen.getByText(/The API is unreachable\./)).toBeInTheDocument();
  });
});
