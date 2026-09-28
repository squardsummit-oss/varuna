import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { mergeCredits } from "@/lib/maps/photoreal";
import {
  attributionLine,
  attributionProviders,
  DATA_SOURCES_LABEL,
  GOOGLE_WORDMARK,
  MapAttribution,
} from "../map-attribution";

/**
 * The provider list off real Mumbai tiles, as `mergeCredits` hands it over: de-duplicated,
 * sorted, semicolon-separated. "Data SIO, NOAA, U.S. Navy, NGA, GEBCO" is one provider whose own
 * name carries four commas, which is why nothing in the component splits on one.
 */
const LONG_CREDITS =
  "Airbus; Data SIO, NOAA, U.S. Navy, NGA, GEBCO; Landsat / Copernicus; Maxar Technologies";

/**
 * jsdom lays nothing out: every element reports `scrollWidth` and `clientWidth` as 0, so the
 * component's overflow test is false there and the "Data sources" control never appears. These
 * two helpers shadow the widths on `HTMLElement.prototype` so a test can reach either branch.
 *
 * They prove the behaviour *around* the measurement, not the measurement: whether a real line at
 * a real console width clips is a browser question and was answered in one.
 */
function pretendTheLineClips(): void {
  defineWidth("scrollWidth", 520);
  defineWidth("clientWidth", 180);
}

function pretendTheLineFits(): void {
  defineWidth("scrollWidth", 180);
  defineWidth("clientWidth", 180);
}

function defineWidth(property: "scrollWidth" | "clientWidth", value: number): void {
  Object.defineProperty(HTMLElement.prototype, property, {
    configurable: true,
    get: () => value,
  });
}

afterEach(() => {
  // Back to jsdom's own (inherited) getters, so a test that does not stub sees the real zeroes.
  // `Reflect.deleteProperty` rather than `delete`, which TypeScript refuses on a readonly DOM
  // property even when the descriptor this file installed is configurable.
  Reflect.deleteProperty(HTMLElement.prototype, "scrollWidth");
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
});

describe("attributionProviders", () => {
  it("has nothing to list before the first tiles arrive", () => {
    expect(attributionProviders()).toEqual([]);
    expect(attributionProviders("")).toEqual([]);
    expect(attributionProviders("  ;  ")).toEqual([]);
  });

  it("keeps a provider whose own name contains commas whole", () => {
    expect(attributionProviders(LONG_CREDITS)).toEqual([
      "Airbus",
      "Data SIO, NOAA, U.S. Navy, NGA, GEBCO",
      "Landsat / Copernicus",
      "Maxar Technologies",
    ]);
  });
});

describe("attributionLine", () => {
  it("shows the wordmark alone while the first tiles are still arriving", () => {
    expect(attributionLine()).toBe(GOOGLE_WORDMARK);
    expect(attributionLine("")).toBe(GOOGLE_WORDMARK);
    expect(attributionLine("   ")).toBe(GOOGLE_WORDMARK);
  });

  it("joins the providers to the wordmark the way Google's own sample does", () => {
    expect(attributionLine("Airbus; Maxar Technologies")).toBe(
      "Google Maps; Airbus; Maxar Technologies",
    );
  });

  it("reads a merged credit line end to end", () => {
    const credits = mergeCredits(["Maxar Technologies;Airbus", "Airbus"]);
    expect(attributionLine(credits)).toBe("Google Maps; Airbus; Maxar Technologies");
  });
});

describe("MapAttribution", () => {
  it("names Google whether or not any provider has been harvested yet", () => {
    render(<MapAttribution />);
    expect(screen.getByText(GOOGLE_WORDMARK)).toBeInTheDocument();
  });

  it("keeps every provider in the DOM although the line is clipped to one", () => {
    // The licence is satisfied by the text being displayed and present, not by it fitting: CSS
    // truncation clips pixels, so assistive technology and a page copy still get the whole list.
    render(<MapAttribution credits={LONG_CREDITS} />);

    const chip = screen.getByText(`${GOOGLE_WORDMARK}; ${LONG_CREDITS}`);
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveAttribute("title", `${GOOGLE_WORDMARK}; ${LONG_CREDITS}`);
    expect(chip.className).toContain("truncate");
  });

  it("never eats a map drag: the wrapper is inert and the line adds no hittable pixel", () => {
    pretendTheLineFits();
    render(<MapAttribution credits="Airbus" />);

    const wrapper = document.querySelector('[data-slot="map-attribution"]');
    expect(wrapper?.className).toContain("pointer-events-none");
    // Nothing takes the pointer back while the whole line is readable, so at a comfortable width
    // this component is exactly as transparent to a drag as the `<p>` it replaced.
    expect(wrapper?.querySelectorAll(".pointer-events-auto")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: DATA_SOURCES_LABEL })).not.toBeInTheDocument();
  });

  it("offers the policy's own control, and only it takes the pointer, once the line clips", () => {
    pretendTheLineClips();
    render(<MapAttribution credits={LONG_CREDITS} />);

    const button = screen.getByRole("button", { name: DATA_SOURCES_LABEL });
    expect(button.className).toContain("pointer-events-auto");
    expect(button).toHaveAttribute("aria-expanded", "false");
    // The hover half of "hover-over or clickable": the whole line without a press.
    expect(button).toHaveAttribute("title", `${GOOGLE_WORDMARK}; ${LONG_CREDITS}`);
  });

  it("lifts the control out of the map's stacking order without lifting the wrapper", async () => {
    // The console's scrub bar is an in-map panel at `z-30` and reaches the bottom-right corner on
    // a narrow console: measured at 1024x768 it spanned x 16-648 against a control at x 562-647,
    // and `elementsFromPoint` over the control returned the bar. A `z-` on the wrapper cannot fix
    // that - a positioned element with a z-index opens a stacking context and traps its children
    // under the bar with it - so the wrapper stays at `z-index: auto` and only the two things
    // that have to be reachable lift themselves out.
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    const wrapper = document.querySelector('[data-slot="map-attribution"]');
    expect(wrapper?.className).not.toMatch(/(^|\s)-?z-/);

    const button = screen.getByRole("button", { name: DATA_SOURCES_LABEL });
    expect(button.className).toContain("z-40");
    await user.click(button);
    expect(screen.getByRole("group", { name: DATA_SOURCES_LABEL }).className).toContain("z-40");
  });

  it("reveals every provider on its own line when the control is pressed", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    await user.click(screen.getByRole("button", { name: DATA_SOURCES_LABEL }));

    const panel = screen.getByRole("group", { name: DATA_SOURCES_LABEL });
    const rows = screen.getAllByRole("listitem").map((row) => row.textContent);
    expect(rows).toEqual([
      GOOGLE_WORDMARK,
      "Airbus",
      "Data SIO, NOAA, U.S. Navy, NGA, GEBCO",
      "Landsat / Copernicus",
      "Maxar Technologies",
    ]);
    expect(screen.getByRole("button", { name: DATA_SOURCES_LABEL })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    // Not a dialog: it takes no focus of its own and traps none (SPEC.md 6.10).
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a single provider the same way", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits="Airbus" />);

    await user.click(screen.getByRole("button", { name: DATA_SOURCES_LABEL }));

    expect(screen.getAllByRole("listitem").map((row) => row.textContent)).toEqual([
      GOOGLE_WORDMARK,
      "Airbus",
    ]);
  });

  it("offers no control when there is no provider behind it, however narrow the chip", () => {
    pretendTheLineClips();
    render(<MapAttribution credits="" />);

    // A control that opens onto a copy of its own label is furniture, and the wordmark is already
    // on screen.
    expect(screen.queryByRole("button", { name: DATA_SOURCES_LABEL })).not.toBeInTheDocument();
    expect(screen.getByText(GOOGLE_WORDMARK)).toBeInTheDocument();
  });

  it("closes on Escape and hands focus back to the control", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    const button = screen.getByRole("button", { name: DATA_SOURCES_LABEL });
    await user.click(button);
    expect(screen.getByRole("group", { name: DATA_SOURCES_LABEL })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("group", { name: DATA_SOURCES_LABEL })).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it("closes when a press lands on the map underneath, because that press is a pan", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    await user.click(screen.getByRole("button", { name: DATA_SOURCES_LABEL }));
    fireEvent.pointerDown(document.body);

    expect(screen.queryByRole("group", { name: DATA_SOURCES_LABEL })).not.toBeInTheDocument();
  });

  it("closes on a second press of the control, which keeps its name", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    const button = screen.getByRole("button", { name: DATA_SOURCES_LABEL });
    await user.click(button);
    await user.click(button);

    expect(screen.queryByRole("group", { name: DATA_SOURCES_LABEL })).not.toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("is reachable by keyboard alone, and the panel it opens is announced as its own", async () => {
    pretendTheLineClips();
    const user = userEvent.setup();
    render(<MapAttribution credits={LONG_CREDITS} />);

    await user.tab();
    const button = screen.getByRole("button", { name: DATA_SOURCES_LABEL });
    expect(button).toHaveFocus();
    expect(button.className).toContain("focus-visible:ring-tide");

    await user.keyboard("{Enter}");
    const panel = screen.getByRole("group", { name: DATA_SOURCES_LABEL });
    expect(button).toHaveAttribute("aria-controls", panel.id);
  });

  it("uses tokens for every colour, so `pnpm lint:design` has nothing to find", () => {
    pretendTheLineClips();
    render(<MapAttribution credits="Airbus" />);

    const chip = screen.getByText("Google Maps; Airbus");
    expect(chip.className).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(chip.className).toContain("text-text-2");
    expect(chip.className).toContain("type-micro");
    expect(chip.parentElement?.className).toContain("bg-ink/80");
    expect(chip.parentElement?.className).toContain("border-line");
  });

  it("takes a class from its host without losing its own", () => {
    render(<MapAttribution credits="Airbus" className="left-2" />);

    const wrapper = document.querySelector('[data-slot="map-attribution"]');
    expect(wrapper?.className).toContain("left-2");
    expect(wrapper?.className).toContain("bottom-2");
  });
});
