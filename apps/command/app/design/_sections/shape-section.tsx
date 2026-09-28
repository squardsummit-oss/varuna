"use client";

import { DesignSection, Meta } from "./section";

interface RadiusRow {
  name: string;
  className: string;
  px: string;
  use: string;
}

/** Radii of SPEC.md section 6.4; components use the class, never the number. */
const RADII: readonly RadiusRow[] = [
  { name: "Panel", className: "rounded-panel", px: "12 px", use: "panels, cards, drawers" },
  { name: "Control", className: "rounded-control", px: "8 px", use: "buttons, inputs, rows" },
  { name: "Chip", className: "rounded-chip", px: "999 px", use: "depth chips, level chips, pills" },
  { name: "Popover", className: "rounded-popover", px: "10 px", use: "map popovers, tooltips" },
  { name: "Phone", className: "rounded-phone", px: "40 px", use: "the WhatsApp phone mock only" },
];

interface SpacingRow {
  name: string;
  className: string;
  px: number;
  use: string;
}

/** The 4-pt grid, with the two spacing tokens the shell depends on. */
const SPACING: readonly SpacingRow[] = [
  { name: "4", className: "gap-1", px: 4, use: "icon to label" },
  { name: "8", className: "gap-2", px: 8, use: "inside a row" },
  { name: "12", className: "gap-3", px: 12, use: "between rows" },
  { name: "16", className: "p-panel", px: 16, use: "panel padding (p-panel)" },
  { name: "24", className: "gap-section", px: 24, use: "between sections (gap-section)" },
  { name: "32", className: "gap-8", px: 32, use: "between screen blocks" },
];

interface HeightRow {
  name: string;
  className: string;
  px: number;
  use: string;
}

const ROW_HEIGHTS: readonly HeightRow[] = [
  { name: "Row", className: "h-row", px: 40, use: "hotspot rail, alert queue, pump cards" },
  { name: "Dense row", className: "h-row-dense", px: 32, use: "drain-health and cycle-log tables" },
];

interface LayoutRow {
  name: string;
  className: string;
  px: number;
  axis: "height" | "width";
  use: string;
}

/** The four fixed sizes of the console shell (SPEC.md section 6.5), drawn to scale. */
const LAYOUT: readonly LayoutRow[] = [
  { name: "Top bar", className: "h-top-bar", px: 52, axis: "height", use: "wordmark, mode banner, run stamp" },
  { name: "Icon rail", className: "w-icon-rail", px: 56, axis: "width", use: "screen navigation" },
  { name: "Time bar", className: "h-time-bar", px: 96, axis: "height", use: "scrub, play, speed, spread band" },
  { name: "Right rail", className: "w-right-rail", px: 360, axis: "width", use: "hotspots, alerts, pumps, reachability" },
];

export function ShapeSection() {
  return (
    <DesignSection
      id="shape"
      title="Shape and spacing"
      description="Radius, the 4-pt grid, row heights and the fixed shell sizes of the build spec, sections 6.4 and 6.5. There are no drop shadows in the dark UI: depth is deep on ink with a 1 px line border."
    >
      <div className="flex flex-col gap-8">
        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Radius</h3>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {RADII.map((r) => (
              <li key={r.className} className="flex items-center gap-3 rounded-control border border-line bg-deep p-2">
                <span
                  aria-hidden="true"
                  className={`size-14 shrink-0 border border-line-strong bg-well ${r.className}`}
                />
                <div className="flex min-w-0 flex-col">
                  <span className="type-small font-medium text-text">{r.name}</span>
                  <Meta>{`${r.className} · ${r.px}`}</Meta>
                  <span className="type-micro text-text-2">{r.use}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Spacing</h3>
          <p className="type-small max-w-[72ch] text-text-2">
            Everything sits on a 4-pt grid. Panel padding and section gap have their own tokens so
            the console keeps control-room density.
          </p>
          <ul className="flex flex-col gap-2">
            {SPACING.map((s) => (
              <li key={s.name} className="flex items-center gap-3 rounded-control border border-line bg-deep p-2">
                <span className="type-small num w-10 shrink-0 text-text">{s.px}</span>
                <span
                  aria-hidden="true"
                  className="h-4 rounded-chip bg-tide"
                  style={{ width: `${s.px}px` }}
                />
                <Meta>{s.className}</Meta>
                <span className="type-micro text-text-2">{s.use}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Row heights</h3>
          <ul className="flex flex-col gap-2">
            {ROW_HEIGHTS.map((row) => (
              <li key={row.className} className="flex flex-col gap-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="type-small font-medium text-text">{row.name}</span>
                  <Meta>{`${row.className} · ${row.px} px`}</Meta>
                  <span className="type-micro text-text-2">{row.use}</span>
                </div>
                <div
                  className={`flex items-center rounded-control border border-line bg-deep px-3 ${row.className}`}
                >
                  <span className="type-small text-text">Hindmata junction</span>
                  <span className="type-small num ml-auto text-text-2">55 cm</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Shell sizes</h3>
          <p className="type-small max-w-[72ch] text-text-2">
            Drawn to scale: the console shell is a 52 px top bar, a 56 px icon rail, a 360 px right
            rail and a 96 px time bar around the map.
          </p>
          <ul className="flex flex-col gap-2">
            {LAYOUT.map((item) => (
              <li key={item.className} className="flex flex-col gap-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="type-small font-medium text-text">{item.name}</span>
                  <Meta>{`${item.className} · ${item.px} px ${item.axis}`}</Meta>
                  <span className="type-micro text-text-2">{item.use}</span>
                </div>
                {item.axis === "height" ? (
                  <div
                    className={`flex items-center rounded-control border border-line bg-deep px-3 ${item.className}`}
                  >
                    <span className="type-micro num text-text-3">{`${item.px} px`}</span>
                  </div>
                ) : (
                  <div className="flex h-16 items-stretch gap-2">
                    <div
                      className={`flex items-center justify-center rounded-control border border-line bg-deep ${item.className}`}
                    >
                      <span className="type-micro num text-text-3">{`${item.px} px`}</span>
                    </div>
                    <div className="flex flex-1 items-center rounded-control border border-line border-dashed bg-ink px-3">
                      <span className="type-micro text-text-3">Map canvas takes the rest</span>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </DesignSection>
  );
}
