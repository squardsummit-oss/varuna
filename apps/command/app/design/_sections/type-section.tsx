"use client";

import { DesignSection, Meta, type DemoProps } from "./section";

/** One row of the type scale: the preset class, its px / line-height, and a Mumbai sample. */
interface TypeRow {
  /** Tailwind preset from packages/tokens (never a raw font-size). */
  preset: string;
  name: string;
  /** "12 / 1.3" as SPEC.md section 6.3 writes it. */
  metrics: string;
  family: "Bricolage Grotesque" | "Geist Sans";
  use: string;
  sample: string;
}

/** The scale of SPEC.md section 6.3, smallest first. */
export const TYPE_ROWS: readonly TypeRow[] = [
  {
    preset: "type-micro",
    name: "Micro",
    metrics: "12 / 1.3",
    family: "Geist Sans",
    use: "chip labels, table meta, ticker rows",
    sample: "18:52 · Hindmata junction · BMC log",
  },
  {
    preset: "type-small",
    name: "Small",
    metrics: "13 / 1.4",
    family: "Geist Sans",
    use: "rail rows, panel descriptions, form labels",
    sample: "King's Circle: 38 cm at 08:20 (+40 min)",
  },
  {
    preset: "type-body",
    name: "Body",
    metrics: "15 / 1.5",
    family: "Geist Sans",
    use: "paragraphs, drawer copy; line length at most 72 characters",
    sample:
      "Sion Circle is forecast to pass 45 cm at 18:35, so buses lose the underpass about forty minutes before the peak.",
  },
  {
    preset: "type-h3",
    name: "Heading 3",
    metrics: "18 / 1.35",
    family: "Geist Sans",
    use: "panel titles, drawer sections",
    sample: "Why this junction floods",
  },
  {
    preset: "type-h2",
    name: "Heading 2",
    metrics: "24 / 1.2",
    family: "Geist Sans",
    use: "page titles, section headings",
    sample: "Why Hindmata floods",
  },
  {
    preset: "type-h1",
    name: "Heading 1",
    metrics: "32 / 1.15",
    family: "Bricolage Grotesque",
    use: "landing section headlines",
    sample: "Forecasts stop at 12 km. Streets flood at 30 m.",
  },
  {
    preset: "type-display",
    name: "Display",
    metrics: "48 / 1.05",
    family: "Bricolage Grotesque",
    use: "the big depth number in the hotspot drawer",
    sample: "55 cm",
  },
  {
    preset: "type-hero",
    name: "Hero",
    metrics: "72 / 1.0",
    family: "Bricolage Grotesque",
    use: "landing hero headline only",
    sample: "Every street.",
  },
];

interface FamilyRow {
  name: string;
  variable: string;
  preset: string;
  /** The type preset that already carries this family, so nothing fights over font-family. */
  samplePreset: string;
  use: string;
  sample: string;
}

const FAMILIES: readonly FamilyRow[] = [
  {
    name: "Display — Bricolage Grotesque",
    variable: "--font-display",
    preset: "font-display",
    samplePreset: "type-h1",
    use: "Landing headlines, page titles, the big depth number. Weights 500 to 700, tracking −0.02em.",
    sample: "Every street. Three hours early.",
  },
  {
    name: "UI and body — Geist Sans",
    variable: "--font-sans",
    preset: "font-sans",
    samplePreset: "type-h3",
    use: "Everything else. Every element that shows a number also carries the num class for tabular figures.",
    sample: "Hindmata junction · 55 cm · 82 % · 08:20 (+40 min)",
  },
  {
    name: "Mono — Geist Mono",
    variable: "--font-mono",
    preset: "font-mono",
    samplePreset: "type-mono",
    use: "Run ids, CAP XML, the API explorer and log streams only. Never for labels, never for small data.",
    sample: "MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked",
  },
];

/** A labelled sample of one scale step or family. */
function TypeSample({
  label,
  meta,
  note,
  children,
}: Pick<DemoProps, "label" | "note"> & { meta: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-panel border border-line bg-deep p-panel">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="type-small font-medium text-text">{label}</span>
        <Meta>{meta}</Meta>
      </div>
      {/* The type specimens scroll and hold no controls (WCAG 2.1.1). */}
      <div
        tabIndex={0}
        className="min-w-0 overflow-x-auto text-text focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-tide/50"
      >
        {children}
      </div>
      {note ? <span className="type-micro text-text-2">{note}</span> : null}
    </div>
  );
}

export function TypeSection() {
  return (
    <DesignSection
      id="type"
      title="Type scale"
      description="The eight steps and three families of the build spec, section 6.3, as the type-* presets components use. Sizes are never written inline."
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Scale</h3>
          <div className="flex flex-col gap-2">
            {TYPE_ROWS.map((row) => (
              <TypeSample
                key={row.preset}
                label={`${row.name} · ${row.preset}`}
                meta={`${row.metrics} · ${row.family}`}
                note={row.use}
              >
                <p className={`${row.preset} whitespace-nowrap`}>{row.sample}</p>
              </TypeSample>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="type-h3 text-text">Families</h3>
          <div className="flex flex-col gap-2">
            {FAMILIES.map((family) => (
              <TypeSample
                key={family.variable}
                label={family.name}
                meta={`${family.preset} · ${family.variable}`}
                note={family.use}
              >
                <p className={`${family.samplePreset} num`}>{family.sample}</p>
              </TypeSample>
            ))}
          </div>
        </div>
      </div>
    </DesignSection>
  );
}
