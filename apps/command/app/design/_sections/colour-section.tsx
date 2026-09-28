"use client";

import { colors, tokens } from "@varuna/tokens";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

import { AA_TEXT_RATIO, contrastRatio, formatRatio, passesAa } from "./contrast";
import { DesignSection } from "./section";

/** The shape shared by every entry of tokens.color, whichever group it sits in. */
interface TokenEntry {
  value: string;
  use?: string;
  opacity?: number;
  minutes?: number;
  label?: string;
  meaning?: string;
  min_beta?: number;
  max_beta?: number;
}

export interface SwatchRow {
  /** Custom-property name without the dashes, e.g. "depth-3". */
  name: string;
  hex: string;
  use?: string;
}

export interface ColourGroup {
  id: string;
  title: string;
  description: string;
  rows: SwatchRow[];
}

/** Uses for the groups whose tokens.json entries carry only a value. */
const USES: Readonly<Record<string, string>> = {
  "obs-traffic": "Pulse: traffic anomaly observations",
  "obs-report": "Pulse: citizen reports",
  "obs-sensor": "Pulse: water-level sensors",
  "obs-cctv": "Pulse: traffic camera observations",
  "obs-sar": "Pulse: satellite radar observations",
  "status-live": "mode banner: live",
  "status-replay": "mode banner: replay",
  "status-baked": "run stamp: baked products",
  "status-degraded": "mode banner: degraded feeds",
  "chart-1": "charts, first series (p50 line)",
  "chart-2": "charts, second series",
  "chart-3": "charts, third series",
  "chart-4": "charts, fourth series",
  "chart-5": "charts, fifth series",
};

function describe(name: string, entry: TokenEntry): string | undefined {
  if (entry.use) return entry.use;
  if (entry.label && entry.meaning) return `${entry.label}: ${entry.meaning}`;
  if (typeof entry.min_beta === "number" && typeof entry.max_beta === "number") {
    return `posterior blockage beta ${entry.min_beta}-${entry.max_beta}`;
  }
  if (typeof entry.minutes === "number" && typeof entry.opacity === "number") {
    return `${entry.minutes}-min reachability isochrone at ${Math.round(entry.opacity * 100)} % opacity`;
  }
  return USES[name];
}

function rowsOf(group: Readonly<Record<string, TokenEntry>>, prefix?: string): SwatchRow[] {
  const rows = Object.entries(group).map(([key, entry]) => {
    const name = prefix ? `${prefix}-${key}` : key;
    return { name, hex: entry.value, use: describe(name, entry) };
  });
  // The ramp reads dry first; JSON key order puts the numeric keys first.
  return rows.sort((a, b) => (a.name.endsWith("-dry") ? -1 : b.name.endsWith("-dry") ? 1 : 0));
}

/** Every colour group of tokens.json, in the order SPEC.md section 6.2 lists them. */
export const COLOUR_GROUPS: readonly ColourGroup[] = [
  {
    id: "base",
    title: "Base",
    description: "The monsoon night: deep indigo, never black. Tide is the only brand accent.",
    rows: rowsOf(tokens.color.base),
  },
  {
    id: "depth",
    title: "Depth ramp",
    description:
      "Fixed meaning everywhere: map segments, rasters, chips, charts and alerts. Never used for anything that is not water depth.",
    rows: rowsOf(tokens.color.depth, "depth"),
  },
  {
    id: "drain",
    title: "Drain ramp",
    description: "Posterior blockage beta per pipe; magenta means blocked.",
    rows: rowsOf(tokens.color.drain, "drain"),
  },
  {
    id: "semantic",
    title: "Semantic",
    description: "Surcharge, the naive route, ground truth and the one destructive colour, which never reaches the map.",
    rows: rowsOf(tokens.color.semantic),
  },
  {
    id: "reach",
    title: "Reachability",
    description: "Tide at three opacities for the 5, 10 and 15 minute isochrones.",
    rows: rowsOf(tokens.color.reach, "reach"),
  },
  {
    id: "obs",
    title: "Observation types",
    description: "One colour per observation type Pulse assimilates.",
    rows: rowsOf(tokens.color.obs, "obs"),
  },
  {
    id: "status",
    title: "Status",
    description: "Mode banner and run stamp colours.",
    rows: rowsOf(tokens.color.status, "status"),
  },
  {
    id: "chart",
    title: "Charts",
    description: "Series colours in order; the p10-p90 band is the line colour at 20 % opacity.",
    rows: rowsOf(tokens.color.chart, "chart"),
  },
];

/** Tokens used as text; each is checked against both backgrounds. */
const TEXT_ROLES = ["text", "text-2", "text-3", "tide", "danger"] as const;
const BACKGROUNDS = ["ink", "deep"] as const;

const ROLE_NOTES: Record<(typeof TEXT_ROLES)[number], string> = {
  text: "primary text",
  "text-2": "secondary text",
  "text-3": "muted text and placeholders only; never body copy",
  tide: "links, primary buttons (ink text on tide), focus rings",
  danger: "destructive actions only",
};

function Swatch({ row }: { row: SwatchRow }) {
  return (
    <li className="flex items-center gap-3 rounded-control border border-line bg-deep p-2">
      <span
        aria-hidden="true"
        className="size-10 shrink-0 rounded-control border border-line"
        style={{ backgroundColor: `var(--${row.name})` }}
      />
      <div className="flex min-w-0 flex-col">
        <span className="type-small font-medium text-text">{`--${row.name}`}</span>
        <span className="type-micro num text-text-3">{row.hex}</span>
        {row.use ? <span className="type-micro text-text-2">{row.use}</span> : null}
      </div>
    </li>
  );
}

function ContrastCell({ fg, bg }: { fg: (typeof TEXT_ROLES)[number]; bg: (typeof BACKGROUNDS)[number] }) {
  const ratio = contrastRatio(colors[fg], colors[bg]);
  const pass = passesAa(ratio);
  return (
    <TableCell>
      <div className="flex items-center gap-2">
        <span
          className="type-small rounded-control px-2 py-1"
          style={{ color: `var(--${fg})`, backgroundColor: `var(--${bg})` }}
        >
          Hindmata 55 cm
        </span>
        <span className="type-small num text-text">{formatRatio(ratio)}</span>
        <span
          className={cn(
            "type-micro rounded-chip border px-1.5 py-0.5",
            pass ? "border-tide text-tide" : "border-danger text-danger",
          )}
        >
          {pass ? "Pass" : "Fail"}
        </span>
      </div>
    </TableCell>
  );
}

/** Contrast report: every text token against both backgrounds, computed at render time. */
export function ContrastReport() {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="type-h3 text-text">Contrast report</h3>
      <p className="type-small max-w-[72ch] text-text-2">
        Relative luminance and ratio computed from tokens.json in the browser. Text must reach{" "}
        <span className="num">{AA_TEXT_RATIO}:1</span>; a fail is a real finding, not a footnote.
      </p>
      <Table aria-label="Contrast report">
        <TableHeader>
          <TableRow>
            <TableHead>Token</TableHead>
            <TableHead>On ink</TableHead>
            <TableHead>On deep</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {TEXT_ROLES.map((role) => (
            <TableRow key={role}>
              <TableCell>
                <div className="flex flex-col">
                  <span className="type-small font-medium text-text">{`--${role}`}</span>
                  <span className="type-micro text-text-3">{ROLE_NOTES[role]}</span>
                </div>
              </TableCell>
              {BACKGROUNDS.map((bg) => (
                <ContrastCell key={bg} fg={role} bg={bg} />
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function ColourSection() {
  return (
    <DesignSection
      id="colour"
      title="Colour tokens"
      description="Every entry of packages/tokens/tokens.json, read from the built package. Components never carry a hex; they use the custom property or the Tailwind colour utility."
    >
      <div className="flex flex-col gap-8">
        {COLOUR_GROUPS.map((group) => (
          <div key={group.id} className="flex flex-col gap-2">
            <h3 className="type-h3 text-text">{group.title}</h3>
            <p className="type-small max-w-[72ch] text-text-2">{group.description}</p>
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {group.rows.map((row) => (
                <Swatch key={row.name} row={row} />
              ))}
            </ul>
          </div>
        ))}
        <ContrastReport />
      </div>
    </DesignSection>
  );
}
