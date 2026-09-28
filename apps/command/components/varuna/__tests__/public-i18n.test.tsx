import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageToggle } from "@/components/varuna/language-toggle";
import { PublicLegend } from "@/components/varuna/public-legend";
import { ReportWizard, reportOutcome } from "@/components/varuna/report-wizard";
import { VehicleSelector } from "@/components/varuna/vehicle-selector";
import { LOCALE_STORAGE_KEY } from "@/lib/i18n";
import { PublicI18nProvider } from "@/lib/i18n/provider";
import { stubFetch } from "@/lib/test-utils";

// next/font is a Next compile-time transform; under vitest it is a plain object with a class name.
vi.mock("next/font/google", () => ({
  Noto_Sans_Devanagari: () => ({ className: "noto", variable: "font-devanagari-var", style: {} }),
}));

function withQuery(children: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.lang = "en";
});

/**
 * Hindi and Marathi are lazy imports (`lib/i18n/messages.ts`), so the first text in either waits on
 * a module load. Testing-library's one-second default lost that race in the full suite on
 * 2026-09-26 while this file alone passed three runs out of three.
 */
const DICTIONARY = { timeout: 5_000 };

describe("components shared with /dashboard, with no language provider", () => {
  it("render in English", () => {
    render(
      <>
        <VehicleSelector value="car" onValueChange={() => {}} />
        <PublicLegend profile="two-wheeler" />
      </>,
    );
    expect(screen.getByRole("group", { name: "Vehicle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Two-wheeler" })).toBeInTheDocument();
    expect(screen.getByText("Passable")).toBeInTheDocument();
    expect(screen.getByText("for a two-wheeler")).toBeInTheDocument();
  });

  it("keep Hindi and Marathi disabled with the reason on the toggle", () => {
    render(<LanguageToggle />);
    expect(screen.getByRole("button", { name: /^HI / })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^MR / })).toBeDisabled();
    expect(screen.getByText("Hindi and Marathi are coming in pilot")).toBeInTheDocument();
  });
});

describe("PublicI18nProvider", () => {
  it("switches to Hindi, remembers it, and marks the text as Devanagari", async () => {
    const { container } = render(
      <PublicI18nProvider>
        <LanguageToggle />
        <VehicleSelector value="car" onValueChange={() => {}} />
      </PublicI18nProvider>,
    );
    const hindi = screen.getByRole("button", { name: /^HI / });
    expect(hindi).toBeEnabled();
    await act(async () => {
      fireEvent.click(hindi);
    });

    expect(await screen.findByRole("button", { name: "दोपहिया" }, DICTIONARY)).toBeInTheDocument();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("hi");
    expect(document.documentElement.lang).toBe("hi");
    const root = container.querySelector("[data-script]");
    expect(root?.getAttribute("data-script")).toBe("deva");
    expect(root?.getAttribute("lang")).toBe("hi");
    expect(root?.className).toContain("font-devanagari-var");
    expect(screen.getByRole("button", { name: /^HI / })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens in the stored language and applies no Devanagari font to English", async () => {
    const { container, unmount } = render(
      <PublicI18nProvider>
        <PublicLegend profile="bus" />
      </PublicI18nProvider>,
    );
    expect(screen.getByText("Passable")).toBeInTheDocument();
    expect(container.querySelector("[data-script]")).toBeNull();
    unmount();

    window.localStorage.setItem(LOCALE_STORAGE_KEY, "mr");
    render(
      <PublicI18nProvider>
        <PublicLegend profile="bus" />
      </PublicI18nProvider>,
    );
    expect(await screen.findByText("पार करण्यायोग्य", {}, DICTIONARY)).toBeInTheDocument();
    expect(screen.getByText("बस साठी")).toBeInTheDocument();
  });

  it("ignores a stored value that is not one of the three languages", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "ta");
    render(
      <PublicI18nProvider>
        <PublicLegend />
      </PublicI18nProvider>,
    );
    expect(screen.getByText("Passable")).toBeInTheDocument();
  });
});

describe("report confirmation", () => {
  it("classifies each answer without inventing a count", () => {
    expect(reportOutcome({ queued: true })).toBe("offline");
    expect(reportOutcome({ queued: true, feedback_streets: 4 })).toBe("offline");
    expect(reportOutcome({ accepted: true, feedback_streets: null })).toBe("queued");
    expect(reportOutcome({ feedback_streets: 0 })).toBe("assimilated");
    expect(reportOutcome({ feedback_streets: 3 })).toBe("improved");
  });

  async function fileKneeReport() {
    fireEvent.click(screen.getByRole("button", { name: "Continue to photo" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip photo" }));
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    fireEvent.click(screen.getByRole("button", { name: "Send report" }));
  }

  it("says an offline report is saved on the phone and has not changed any forecast", async () => {
    // What the service worker answers for a POST it could not deliver.
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetch({ "/v1/reports": { status: 202, body: { queued: true } } })),
    );
    const { container } = render(withQuery(<ReportWizard />));
    await fileKneeReport();

    expect(
      await screen.findByRole("heading", { name: "Report saved on this phone" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/will be sent when the phone is back online/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("improved the forecast");
    expect(container.textContent).not.toContain("Report sent");
  });

  it("says the same in Marathi", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "mr");
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetch({ "/v1/reports": { status: 202, body: { queued: true } } })),
    );
    render(withQuery(<PublicI18nProvider>{<ReportWizard />}</PublicI18nProvider>));
    await screen.findByRole("button", { name: "फोटोकडे पुढे चला" }, DICTIONARY);

    fireEvent.click(screen.getByRole("button", { name: "फोटोकडे पुढे चला" }));
    fireEvent.click(screen.getByRole("button", { name: "फोटो वगळा" }));
    fireEvent.click(screen.getByRole("radio", { name: /गुडघा/ }));
    expect(screen.getByText(/सुमारे 45 cm/, { selector: "p" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "नोंद पाठवा" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "नोंद या फोनवर जतन केली" })).toBeInTheDocument(),
    );
  });

  it("prints an assimilated count in Hindi with Latin digits", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "hi");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          "/v1/reports": { status: 202, body: { accepted: true, feedback_streets: 3 } },
        }),
      ),
    );
    render(withQuery(<PublicI18nProvider>{<ReportWizard />}</PublicI18nProvider>));
    fireEvent.click(await screen.findByRole("button", { name: "फ़ोटो पर आगे बढ़ें" }, DICTIONARY));
    fireEvent.click(screen.getByRole("button", { name: "फ़ोटो छोड़ें" }));
    fireEvent.click(screen.getByRole("radio", { name: /घुटना/ }));
    fireEvent.click(screen.getByRole("button", { name: "रिपोर्ट भेजें" }));

    expect(await screen.findByRole("heading", { name: /3 सड़कों/ })).toBeInTheDocument();
  });
});
