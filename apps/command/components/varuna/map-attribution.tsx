"use client";

/**
 * The attribution line for the photorealistic basemap: Google's wordmark and the providers of
 * the tiles currently on screen, plus a "Data sources" control on the widths where that line
 * does not fit.
 *
 * **This is a licence term, not decoration.** Google's Map Tiles policy requires both the Google
 * attribution and the data providers of the tiles being displayed, and - where space does not
 * allow the whole line - a hover-over or clickable element labelled "Data sources" that reveals
 * it. That wording is why the control below is spelled exactly that way rather than "Credits" or
 * "Sources". The providers change as the camera moves - flying from Dadar to the coast can swap
 * Airbus for Maxar - so the line is fed from `createCreditStore()` in `lib/maps/photoreal.ts`,
 * which merges each traversal's `asset.copyright` strings and notifies only when the merged line
 * actually changes. A component that re-rendered per traversal would cost the console frames it
 * does not have: section 14's budget is 55 fps and 3D already measures 50.4 (ADR-0065).
 *
 * The strings are semicolon-separated, which is a fact about the tiles rather than a guess: the
 * ones traversed down to Hindmata on 2026-09-23 carried `"Google;Airbus"` and `"Google;Data SIO,
 * NOAA, U.S. Navy, NGA, GEBCO"`. A provider's own name may therefore contain commas, so nothing
 * here splits on one.
 *
 * **What the audit found.** The chip truncated to a single line and was `pointer-events-none`
 * throughout. The full list stayed in the DOM - CSS truncation clips pixels, not nodes - so
 * assistive technology and a page copy got every provider, but a sighted reader at a narrow
 * console width saw an ellipsis, and `pointer-events-none` meant the `title` could not be hovered
 * either. On a narrow console the providers were reachable by nobody using a mouse, which is the
 * one failure mode the policy names.
 *
 * **When the control appears.** Only when the line is really clipped: `scrollWidth >
 * clientWidth` on the credit paragraph, re-measured by a `ResizeObserver` on the paragraph
 * itself, so a console dragged narrower grows the control and one dragged wider drops it again.
 * That cannot oscillate, because removing the button only ever gives the line *more* room: a line
 * that fits without the button keeps fitting once it is gone. It also takes at least one
 * provider - a chip reading "Google Maps" on its own has nothing behind it to reveal, and a
 * control that opens onto a copy of its own label is furniture.
 *
 * **How it stays out of the map's way.** The wrapper keeps `pointer-events-none`, and only the
 * button and the open panel take `pointer-events-auto` back. A drag that begins anywhere on the
 * credit text still pans the map, exactly as before; the one small control in the corner is the
 * only hittable pixel this component adds, and while the line fits it adds none at all. Nothing
 * here is a dialog: the panel does not trap focus, and it closes on Escape (returning focus to
 * the button), on a pointer press outside it, and on a second press of the button. Escape is not
 * swallowed either - `useGlobalShortcuts` acts on it only when a modal overlay is open, and this
 * is not one.
 *
 * **Why the wrapper carries no `z-`, and the button carries `z-40`.** The console's scrub bar
 * (`app/console/console-screen.tsx:664`) is an in-map panel at `bottom-4 z-30`, up to 680 px wide
 * and centred, and it reaches the bottom-right corner whenever the map is narrow. Measured on
 * 2026-09-23 at 1024x768: the bar occupied x 16-648 and the control x 562-647, so
 * `elementsFromPoint` over the control returned *the bar*, and a press would have scrubbed the
 * forecast instead of opening the list. A `z-` on the wrapper cannot fix that, because a
 * positioned element with a z-index opens a stacking context and traps its children beneath the
 * bar with it; so the wrapper and the chip stay at `z-index: auto` - painting exactly where they
 * painted before, under the console's own panels - and only the two things that must be reachable
 * lift themselves out. The credit *text* is therefore still partly behind the scrub bar at narrow
 * widths (365 px of horizontal and 22 px of vertical overlap at 1440x900); that is the scrub
 * bar's geometry rather than this chip's, and moving it is the console's call - it already passes
 * a `className` through. Noted in `.wf/ATTRIBUTION-requests.md`.
 *
 * **It opens upward, over the depth legend.** There is nowhere else for it to go: the chip sits
 * in the bottom-right corner and section 6.7 puts the legend just above it. That is an argument
 * for making it easy to dismiss rather than for moving it - a corner panel that covers the legend
 * for exactly as long as the reader wants it is better than a provider list nobody can read.
 *
 * **No motion.** Section 8 has no row for this panel, so it cuts rather than animating, which
 * also leaves `prefers-reduced-motion` nothing to flatten.
 *
 * **Text, not a logo.** Google permits a text-only "Google Maps" attribution where space is
 * tight, and text is what this renders - no bitmap, so there is no third-party image asset in
 * `public/` to license, to keep in `make pack`, or to get wrong at 2x.
 *
 * **What the tests cannot prove, and what a browser said instead.** jsdom reports both
 * `scrollWidth` and `clientWidth` as 0, so the measurement never fires there and the tests stub
 * the two widths to reach the clipped branch: they pin the behaviour around the measurement, not
 * the measurement itself. Driven on `/console` with the photorealistic city on, 2026-09-23, the
 * camera over Mumbai harvesting `"Google Maps; Airbus; Data SIO, NOAA, U.S. Navy, NGA, GEBCO;
 * Google; Landsat / Copernicus"`:
 *
 * That line measures **510 px**, and the box it has to fit in is `min(70 % of the map area,
 * 56ch)` - 557 px at this type size - less the button and the chip's padding when the button is
 * there. So the answer depends on how wide the console leaves the *map*, not on how wide the
 * window is:
 *
 * - 1440x900 with the map 1080 px wide - cap 557 px, so the 510 px line fits and no control is
 *   drawn.
 * - 1440x900 and 1366x768 in a narrower console state, text box 448 px - clipped, control at
 *   x 978-1063 and x 904-989, with the scrub bar ending at x 880 and x 843. `elementsFromPoint`
 *   over the control returns the button.
 * - 1024x768, map 664 px wide, text box 356 px (below the supported floor, kept because it is
 *   where the collision showed) - control at x 562-647 against a bar spanning x 16-648. Before
 *   the stacking change `elementsFromPoint` returned the bar and the control could not be pressed
 *   at all; after it, the button.
 * - Over the credit text itself, at every width, the hit stack is scrub bar, Esri credit line,
 *   canvas - this chip is not in it. The line is as transparent to a drag as the `<p>` it
 *   replaced.
 *
 * A press opened the panel on all five providers with the commas inside one of them intact,
 * Escape closed it and put focus back on the control, and a press on the map closed it.
 *
 * An earlier camera, before the traversal had reached Airbus, produced a 469 px line and no
 * control. What is on screen decides, which is the point of a licence term that names the tiles
 * *currently* displayed.
 */

import { useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/** The wordmark. Google's own text attribution, spelled once. */
export const GOOGLE_WORDMARK = "Google Maps";

/** The control's label, in the policy's own words. Sentence case, no arrow (SPEC.md 6.8). */
export const DATA_SOURCES_LABEL = "Data sources";

/** The one sentence under the panel heading: what this list is a list of. */
export const DATA_SOURCES_HINT = "Providers of the tiles on screen.";

export interface MapAttributionProps {
  /**
   * The merged provider line from `useMapCredits(store)` - "Airbus; Maxar Technologies". Empty
   * while the first tiles are still arriving, which is a chip reading only the wordmark rather
   * than a chip that pops into existence a second after the city does.
   */
  credits?: string;
  className?: string;
}

/**
 * The providers on their own, in the order the merged line carries them.
 *
 * Split on the semicolon only. `mergeCredits` has already de-duplicated and sorted them, and a
 * provider's name may contain commas ("Data SIO, NOAA, U.S. Navy, NGA, GEBCO" is one provider,
 * not five).
 */
export function attributionProviders(credits?: string): string[] {
  return (credits ?? "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean);
}

/** The whole line as one string: what the DOM carries and what `title` shows. */
export function attributionLine(credits?: string): string {
  const providers = attributionProviders(credits);
  return providers.length ? `${GOOGLE_WORDMARK}; ${providers.join("; ")}` : GOOGLE_WORDMARK;
}

export function MapAttribution({ credits, className }: MapAttributionProps) {
  const providers = attributionProviders(credits);
  const line = attributionLine(credits);

  const rootRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<HTMLParagraphElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const headingId = useId();

  const [clipped, setClipped] = useState(false);
  const [open, setOpen] = useState(false);

  // The measurement. It runs on mount and on every change to the line, and then whenever the
  // paragraph is resized - which covers the console being dragged narrower, the right rail
  // opening, and the button appearing or leaving beside the text. A console dragged wide enough
  // to show the whole line loses the control the panel is closed with, so the panel goes with it.
  useEffect(() => {
    const el = lineRef.current;
    if (!el) return;
    const measure = () => {
      const overflowing = el.scrollWidth > el.clientWidth + 1;
      setClipped(overflowing);
      if (!overflowing) setOpen(false);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [line]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    // A press anywhere else closes it, including on the map underneath: the wrapper is inert, so
    // that press is also the first pixel of a pan, and getting out of the way is what someone who
    // has started dragging the city wants.
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && root.contains(event.target)) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const showDisclosure = clipped && providers.length > 0;

  return (
    <div
      ref={rootRef}
      data-slot="map-attribution"
      className={cn(
        "pointer-events-none absolute right-2 bottom-2 flex max-w-[min(70%,56ch)] flex-col items-end gap-1",
        className,
      )}
    >
      {open && showDisclosure ? (
        <div
          id={panelId}
          role="group"
          aria-labelledby={headingId}
          className="rounded-panel border-line bg-ink/95 pointer-events-auto relative z-40 max-w-full min-w-[12rem] border p-3"
        >
          <p id={headingId} className="type-small text-text">
            {DATA_SOURCES_LABEL}
          </p>
          <p className="type-micro text-text-3 mt-0.5">{DATA_SOURCES_HINT}</p>
          {/* Focusable: the list scrolls when a traversal has harvested more providers than fit,
              and its contents are names rather than controls, so there is nothing inside to tab
              to (WCAG 2.1.1). */}
          <ul
            tabIndex={0}
            className="type-micro text-text-2 focus-visible:ring-tide/50 mt-2 flex max-h-[40vh] flex-col gap-1 overflow-y-auto focus-visible:ring-3 focus-visible:outline-none"
          >
            <li className="break-words">{GOOGLE_WORDMARK}</li>
            {providers.map((provider) => (
              <li key={provider} className="break-words">
                {provider}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rounded-control border-line bg-ink/80 flex max-w-full items-center gap-1.5 border px-2 py-1">
        <p ref={lineRef} title={line} className="text-text-2 type-micro min-w-0 truncate">
          {line}
        </p>
        {showDisclosure ? (
          <button
            ref={buttonRef}
            type="button"
            onClick={() => setOpen((wasOpen) => !wasOpen)}
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            // The hover half of the policy's "hover-over or clickable": a mouse that only rests on
            // the control gets the whole line without a press, and a press keeps it on screen
            // where it can be read and selected.
            title={line}
            className="rounded-control border-line-strong bg-ink text-text-2 hover:text-text hover:bg-well focus-visible:ring-tide type-micro pointer-events-auto relative z-40 inline-flex h-5 shrink-0 items-center border px-1.5 whitespace-nowrap focus-visible:ring-2 focus-visible:outline-none"
          >
            {DATA_SOURCES_LABEL}
          </button>
        ) : null}
      </div>
    </div>
  );
}
