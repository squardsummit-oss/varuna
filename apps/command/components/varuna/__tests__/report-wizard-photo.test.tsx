import { createRequire } from "node:module";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MY_REPORTS_KEY } from "@/components/citizen/my-reports";
import {
  fitWithin,
  PHOTO_FILE_MAX_BYTES,
  photoOutcome,
  preparePhoto,
  REPORT_NOTE_MAX,
  ReportWizard,
} from "@/components/varuna/report-wizard";
import { REPORT_PHOTO_MAX_CHARS } from "@/lib/api/reports";
import { LOCALE_STORAGE_KEY } from "@/lib/i18n";
import { PublicI18nProvider } from "@/lib/i18n/provider";
import { stubFetch } from "@/lib/test-utils";

// next/font is a Next compile-time transform; under vitest it is a plain object with a class name.
vi.mock("next/font/google", () => ({
  Noto_Sans_Devanagari: () => ({ className: "noto", variable: "font-devanagari-var", style: {} }),
}));

/** A prepared photo short enough for the API, as a canvas would write it. */
const JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD";
/** One character over the API's `photo_data_url` limit. */
const TOO_LONG = `data:image/jpeg;base64,${"A".repeat(REPORT_PHOTO_MAX_CHARS)}`;

/** What the deployed API says of a photo it did not keep (`routers/reports.py`). */
const NOT_STORED_NOTE =
  "Photos are stored only on the demo laptop; this API kept your report without its photo.";

interface CanvasMock {
  drawImage: ReturnType<typeof vi.fn>;
  fillRect: ReturnType<typeof vi.fn>;
  toDataURL: ReturnType<typeof vi.fn>;
  sizes: { width: number; height: number }[];
}

/** Stub `createImageBitmap` and the 2D canvas jsdom does not implement. */
function mockCanvas(
  bitmap: { width: number; height: number } = { width: 4032, height: 3024 },
  encode: (quality: number) => string = () => JPEG,
): CanvasMock & { createImageBitmap: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> } {
  const close = vi.fn();
  const createImageBitmap = vi.fn(async () => ({ ...bitmap, close }));
  vi.stubGlobal("createImageBitmap", createImageBitmap);
  const drawImage = vi.fn();
  const fillRect = vi.fn();
  const sizes: { width: number; height: number }[] = [];
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (
    this: HTMLCanvasElement,
  ) {
    sizes.push({ width: this.width, height: this.height });
    return { drawImage, fillRect, fillStyle: "" } as unknown as CanvasRenderingContext2D;
  } as unknown as HTMLCanvasElement["getContext"]);
  const toDataURL = vi.fn((_type?: string, quality?: number) => encode(quality ?? 0));
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(
    toDataURL as unknown as HTMLCanvasElement["toDataURL"],
  );
  return { createImageBitmap, close, drawImage, fillRect, toDataURL, sizes };
}

function photoFile(bytes = 2_400_000): File {
  const file = new File([new Uint8Array(16)], "hindmata.jpg", { type: "image/jpeg" });
  Object.defineProperty(file, "size", { value: bytes });
  return file;
}

function renderWizard(i18n = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wizard = <ReportWizard />;
  return render(
    <QueryClientProvider client={queryClient}>
      {i18n ? <PublicI18nProvider>{wizard}</PublicI18nProvider> : wizard}
    </QueryClientProvider>,
  );
}

function click(name: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

function pick(file: File) {
  fireEvent.change(screen.getByLabelText("Add a photo"), { target: { files: [file] } });
}

/** The JSON body of the one `POST /v1/reports` the wizard made. */
function sentBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/v1/reports"));
  expect(call, "POST /v1/reports").toBeDefined();
  return JSON.parse(String((call?.[1] as RequestInit).body)) as Record<string, unknown>;
}

const STORED = {
  id: "rpt-1790000000000-a1b2c3",
  accepted: true,
  status: "received",
  feedback_streets: null,
  photo_attached: true,
  photo_stored: true,
  photo_note: null,
  photo_url: "/v1/reports/rpt-1790000000000-a1b2c3/photo?size=full",
  thumb_url: "/v1/reports/rpt-1790000000000-a1b2c3/photo?size=thumb",
  message: "Thanks. Your report is queued; the next cycle assimilates it.",
};

const NOT_STORED = {
  ...STORED,
  id: "rpt-1790000000001-d4e5f6",
  photo_stored: false,
  photo_note: NOT_STORED_NOTE,
  photo_url: null,
  thumb_url: null,
  message: `Thanks. Your report is queued; the next cycle assimilates it. ${NOT_STORED_NOTE}`,
};

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("preparePhoto", () => {
  it("decodes with the EXIF orientation, caps the long edge at 1280 px and writes JPEG at 0.8", async () => {
    const canvas = mockCanvas({ width: 4032, height: 3024 });
    const file = photoFile();

    const result = await preparePhoto(file);

    expect(canvas.createImageBitmap).toHaveBeenCalledWith(file, { imageOrientation: "from-image" });
    expect(canvas.sizes).toEqual([{ width: 1280, height: 960 }]);
    expect(canvas.drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 1280, 960);
    expect(canvas.toDataURL).toHaveBeenCalledTimes(1);
    expect(canvas.toDataURL).toHaveBeenCalledWith("image/jpeg", 0.8);
    expect(result).toEqual({
      kind: "ready",
      dataUrl: JPEG,
      width: 1280,
      height: 960,
      quality: 0.8,
    });
    // The bitmap is released whatever happens; a phone holds a 12 MP decode otherwise.
    expect(canvas.close).toHaveBeenCalledTimes(1);
  });

  it("steps the quality down rather than refusing a busy photo", async () => {
    const canvas = mockCanvas(undefined, (quality) => (quality >= 0.8 ? TOO_LONG : JPEG));
    const result = await preparePhoto(photoFile());
    expect(canvas.toDataURL.mock.calls.map(([, q]) => q)).toEqual([0.8, 0.65]);
    expect(result).toMatchObject({ kind: "ready", quality: 0.65 });
  });

  it("refuses a photo still too long for the API at the lowest quality", async () => {
    mockCanvas(undefined, () => TOO_LONG);
    expect(await preparePhoto(photoFile())).toEqual({ kind: "too-big-encoded" });
  });

  it("refuses a file over 15 MB before decoding it", async () => {
    const canvas = mockCanvas();
    const result = await preparePhoto(photoFile(PHOTO_FILE_MAX_BYTES + 1));
    expect(result).toEqual({ kind: "too-big-file", bytes: PHOTO_FILE_MAX_BYTES + 1 });
    expect(canvas.createImageBitmap).not.toHaveBeenCalled();
  });

  it("says a photo the browser cannot decode is undecodable (HEIC on an older browser)", async () => {
    vi.stubGlobal(
      "createImageBitmap",
      vi.fn(async () => {
        throw new DOMException("The source image could not be decoded.", "InvalidStateError");
      }),
    );
    expect(await preparePhoto(photoFile())).toEqual({ kind: "undecodable" });

    vi.stubGlobal("createImageBitmap", undefined);
    expect(await preparePhoto(photoFile())).toEqual({ kind: "undecodable" });
  });

  it("treats a browser that writes PNG instead of JPEG as unable to prepare the photo", async () => {
    mockCanvas(undefined, () => "data:image/png;base64,iVBORw0KGgo");
    expect(await preparePhoto(photoFile())).toEqual({ kind: "undecodable" });
  });

  it("keeps a small photo at its own size and fits a portrait one by its height", () => {
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
    expect(fitWithin(3024, 4032)).toEqual({ width: 960, height: 1280 });
  });
});

describe("photoOutcome", () => {
  it("reads what the API said, never what the phone sent alone", () => {
    expect(photoOutcome({ photo_stored: true }, false)).toBeNull();
    expect(photoOutcome({ photo_stored: true }, true)).toBe("stored");
    expect(photoOutcome({ photo_stored: false }, true)).toBe("not-stored");
    expect(photoOutcome({ id: "rpt-1" }, true)).toBe("unconfirmed");
    expect(photoOutcome({ queued: true }, true)).toBe("saved-offline");
  });
});

describe("ReportWizard photo and note", () => {
  it("sends the prepared JPEG and the note, says the photo is stored and remembers the id", async () => {
    mockCanvas();
    const fetchMock = vi.fn(stubFetch({ "/v1/reports": { status: 202, body: STORED } }));
    vi.stubGlobal("fetch", fetchMock);
    renderWizard();

    click("Continue to photo");
    expect(
      screen.getByText(
        /stored with your report and shown to the ward officer and on the citizen dashboard/,
      ),
    ).toBeInTheDocument();
    pick(photoFile());
    expect(await screen.findByAltText("The photo you picked")).toHaveAttribute("src", JPEG);
    expect(
      screen.getByText(/1280 × 960 px, without the location and camera details/),
    ).toBeVisible();

    click("Continue to depth");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    fireEvent.change(screen.getByLabelText("Anything to add? (optional)"), {
      target: { value: "  Water over the divider at Hindmata, buses turning back.  " },
    });
    click("Send report");

    expect(await screen.findByRole("heading", { name: "Report sent" })).toBeInTheDocument();
    const body = sentBody(fetchMock);
    expect(body.photo_data_url).toBe(JPEG);
    expect(body.text).toBe("Water over the divider at Hindmata, buses turning back.");
    expect(screen.getByText("Your photo is stored with the report.")).toBeInTheDocument();

    const stored = JSON.parse(window.localStorage.getItem(MY_REPORTS_KEY) ?? "[]") as {
      id: string;
    }[];
    expect(stored.map((entry) => entry.id)).toEqual([STORED.id]);

    expect(screen.getByRole("link", { name: "Back to the dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Back to the map" })).toHaveAttribute("href", "/map");
  });

  it("says plainly when the API kept the report without its photo, in the API's words once", async () => {
    mockCanvas();
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/reports": { status: 202, body: NOT_STORED } })));
    const { container } = renderWizard();

    click("Continue to photo");
    pick(photoFile());
    await screen.findByAltText("The photo you picked");
    click("Continue to depth");
    fireEvent.click(screen.getByRole("radio", { name: /Ankle/ }));
    click("Send report");

    expect(
      await screen.findByText("Your photo was not stored. The report was kept without it."),
    ).toBeInTheDocument();
    // The English body is the API's message, which already carries the reason; printing it a
    // second time under the photo line would say the same sentence twice.
    expect(container.textContent?.split(NOT_STORED_NOTE).length).toBe(2);
    expect(container.textContent).not.toContain("Your photo is stored");
  });

  it("gives the API's reason under the translated line in Marathi", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "mr");
    mockCanvas();
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/reports": { status: 202, body: NOT_STORED } })));
    const { container } = renderWizard(true);

    fireEvent.click(
      await screen.findByRole("button", { name: "फोटोकडे पुढे चला" }, { timeout: 5_000 }),
    );
    fireEvent.change(screen.getByLabelText("फोटो जोडा"), { target: { files: [photoFile()] } });
    await screen.findByAltText("तुम्ही निवडलेला फोटो");
    fireEvent.click(screen.getByRole("button", { name: "खोलीकडे पुढे चला" }));
    fireEvent.click(screen.getByRole("radio", { name: /गुडघा/ }));
    fireEvent.click(screen.getByRole("button", { name: "नोंद पाठवा" }));

    expect(
      await screen.findByText(/तुमचा फोटो जतन झाला नाही/, { selector: "p" }),
    ).toBeInTheDocument();
    const reason = container.querySelector('span[lang="en"]');
    expect(reason?.textContent).toBe(NOT_STORED_NOTE);
    expect(screen.getByRole("link", { name: "डॅशबोर्डकडे परत" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("refuses a photo over 15 MB and still sends the report without it", async () => {
    const canvas = mockCanvas();
    const fetchMock = vi.fn(
      stubFetch({
        "/v1/reports": {
          status: 202,
          body: { ...STORED, photo_attached: false, photo_stored: false },
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWizard();

    click("Continue to photo");
    pick(photoFile(18_400_000));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This photo is 18.4 MB, over the 15 MB limit, so it was not used.",
    );
    expect(canvas.createImageBitmap).not.toHaveBeenCalled();
    expect(screen.queryByAltText("The photo you picked")).toBeNull();

    click("Continue to depth");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");
    await screen.findByRole("heading", { name: "Report sent" });
    expect(sentBody(fetchMock)).not.toHaveProperty("photo_data_url");
    // No photo went, so nothing is said about one.
    expect(screen.queryByText(/Your photo/)).toBeNull();
  });

  it("says a photo it cannot open was not used, and names HEIC", async () => {
    vi.stubGlobal(
      "createImageBitmap",
      vi.fn(async () => {
        throw new DOMException("The source image could not be decoded.", "InvalidStateError");
      }),
    );
    renderWizard();

    click("Continue to photo");
    pick(photoFile());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not open that photo, so it was not used\. Some phones save photos as HEIC/,
    );
    expect(screen.getByRole("button", { name: "Continue to depth" })).toBeEnabled();
  });

  it("removes a prepared photo on request", async () => {
    mockCanvas();
    renderWizard();

    click("Continue to photo");
    pick(photoFile());
    await screen.findByAltText("The photo you picked");
    click("Remove photo");
    expect(screen.queryByAltText("The photo you picked")).toBeNull();
    expect(screen.getByText("No photo yet.")).toBeInTheDocument();
  });

  it("holds the note to 280 characters and counts them", async () => {
    const fetchMock = vi.fn(stubFetch({ "/v1/reports": { status: 202, body: STORED } }));
    vi.stubGlobal("fetch", fetchMock);
    renderWizard();

    click("Continue to photo");
    click("Skip photo");
    const note = screen.getByLabelText("Anything to add? (optional)");
    expect(note).toHaveAttribute("maxLength", String(REPORT_NOTE_MAX));
    expect(screen.getByText("0 of 280 characters")).toBeInTheDocument();

    fireEvent.change(note, { target: { value: "x".repeat(300) } });
    expect(note).toHaveValue("x".repeat(REPORT_NOTE_MAX));
    expect(screen.getByText("280 of 280 characters")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Waist/ }));
    click("Send report");
    await screen.findByRole("heading", { name: "Report sent" });
    expect(sentBody(fetchMock).text).toHaveLength(REPORT_NOTE_MAX);
  });

  it("sends no text when the note is left empty", async () => {
    const fetchMock = vi.fn(stubFetch({ "/v1/reports": { status: 202, body: STORED } }));
    vi.stubGlobal("fetch", fetchMock);
    renderWizard();

    click("Continue to photo");
    click("Skip photo");
    fireEvent.change(screen.getByLabelText("Anything to add? (optional)"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");
    await screen.findByRole("heading", { name: "Report sent" });
    expect(sentBody(fetchMock)).not.toHaveProperty("text");
  });

  it("keeps an offline report on the phone and remembers no id for it", async () => {
    mockCanvas();
    // What the service worker answers for a POST it could not deliver: no id exists yet.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          "/v1/reports": {
            status: 202,
            body: { queued: true, accepted: false, feedback_streets: null, pending: 1 },
          },
        }),
      ),
    );
    renderWizard();

    click("Continue to photo");
    pick(photoFile());
    await screen.findByAltText("The photo you picked");
    click("Continue to depth");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");

    expect(
      await screen.findByRole("heading", { name: "Report saved on this phone" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Your photo is saved on this phone with the report."),
    ).toBeInTheDocument();
    expect(window.localStorage.getItem(MY_REPORTS_KEY)).toBeNull();
  });

  it("keeps the report sent when storage refuses the id", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/reports": { status: 202, body: STORED } })));
    renderWizard();

    click("Continue to photo");
    click("Skip photo");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    click("Send report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Report sent" })).toBeInTheDocument(),
    );
  });
});

describe("ReportWizard accessibility", () => {
  interface AxeCore {
    run(
      context: Element,
      options: { rules: Record<string, { enabled: boolean }> },
    ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
  }

  async function violationsIn(container: HTMLElement) {
    const here = createRequire(import.meta.url);
    const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
    // jsdom cannot measure colour, so contrast is left to the browser-level axe pass.
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.passes.length).toBeGreaterThan(0);
    return results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`);
  }

  it("finds nothing on the prepared photo, the refused photo, the note and the confirmation", async () => {
    mockCanvas();
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/reports": { status: 202, body: NOT_STORED } })));
    const { container } = renderWizard();

    click("Continue to photo");
    pick(photoFile());
    await screen.findByAltText("The photo you picked");
    expect(await violationsIn(container)).toEqual([]);

    pick(photoFile(PHOTO_FILE_MAX_BYTES + 1));
    await screen.findByRole("alert");
    expect(await violationsIn(container)).toEqual([]);

    click("Continue to depth");
    fireEvent.click(screen.getByRole("radio", { name: /Knee/ }));
    fireEvent.change(screen.getByLabelText("Anything to add? (optional)"), {
      target: { value: "Knee deep outside Hindmata cinema." },
    });
    expect(await violationsIn(container)).toEqual([]);

    click("Send report");
    await screen.findByRole("heading", { name: "Report sent" });
    expect(await violationsIn(container)).toEqual([]);
  });
});
