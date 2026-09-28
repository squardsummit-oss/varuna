import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import {
  BadgeCheck,
  Bell,
  Droplets,
  FlaskConical,
  LayoutDashboard,
  MapPinned,
  Play,
  Route as RouteIcon,
  Waypoints,
} from "lucide-react";

import { DEFAULT_CITY } from "./city";

/** One icon-rail entry (SPEC.md section 7.2, in order). */
export interface NavItem {
  id: string;
  href: Route;
  /**
   * The screen's name as the product shows it. Every screen carries a Sanskrit name (ADR-0085 named
   * six; Drishti, Sanket and Pravesh followed), in plain romanised spelling because Geist and
   * Bricolage have no glyphs for the IAST dots (measured: no ṛ, ṇ or ḍ), and a mixed-font "Smṛti"
   * or "Dṛṣṭi" would read as a rendering fault.
   */
  label: string;
  /**
   * The English name of what the screen does, in section 6.8's vocabulary. Shown beside a
   * Sanskrit label and part of the accessible name, so a screen reader and a first-time reader
   * both know where the link goes. Every screen has one now; it stays optional so a future screen
   * with an English name needs none.
   */
  gloss?: string;
  /** The Sanskrit name in Devanagari, rendered with `lang="sa"` as an accent, never alone. */
  deva?: string;
  /** One line on why the name fits, for the page header and the tooltip. */
  meaning?: string;
  icon: LucideIcon;
  /** Keyboard hint shown in the tooltip and the palette, e.g. "Alt+1". */
  hint: string;
}

/*
 * Routes other than /console and /design are built by other contributors; they are typed as Route so
 * the rail and the palette compile before every page exists. Typed routes still guard `Link` in
 * the pages themselves once they land.
 */
const route = (path: string) => path as Route;

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "console",
    href: route("/console"),
    label: "Drishti",
    gloss: "Command console",
    deva: "दृष्टि",
    meaning: "Sight; the whole city's water at a glance",
    icon: LayoutDashboard,
    hint: "Alt+1",
  },
  {
    id: "drains",
    href: route("/drains"),
    label: "Nadi",
    gloss: "Drain health",
    deva: "नाडी",
    meaning:
      "The channels that carry a body's pulse; here, the drains VARUNA learns from every flood",
    icon: Waypoints,
    hint: "Alt+2",
  },
  {
    id: "route",
    href: route("/route"),
    label: "Marga",
    gloss: "Route planner",
    deva: "मार्ग",
    meaning: "The way through; the route an ambulance can still take",
    icon: RouteIcon,
    hint: "Alt+3",
  },
  {
    id: "alerts",
    href: route("/alerts"),
    label: "Sanket",
    gloss: "Alert centre",
    deva: "सङ्केत",
    meaning: "A signal; the warning that reaches the ward officer's phone",
    icon: Bell,
    hint: "Alt+4",
  },
  {
    id: "pumps",
    href: route("/pumps"),
    label: "Jalayantra",
    gloss: "Pump dispatch",
    deva: "जलयन्त्र",
    meaning: "Water engine; where the city's mobile pumps are sent",
    icon: Droplets,
    hint: "Alt+5",
  },
  {
    id: "whatif",
    href: route("/whatif"),
    label: "Kalpana",
    gloss: "What-if lab",
    deva: "कल्पना",
    meaning: "Imagining; the city under a storm, a tide or a clean drain that has not happened",
    icon: FlaskConical,
    hint: "Alt+6",
  },
  {
    id: "replay",
    href: route("/replay"),
    label: "Smriti",
    gloss: "Replay",
    deva: "स्मृति",
    // Names no event and claims no fidelity: /replay also plays two design storms, and the 2 July
    // 2019 bundle is reconstructed, its radar, gauges, traffic and reports synthetic (6.8, rule 7).
    meaning:
      "Memory; a storm run through the pipeline again, reconstructed from a past event or designed",
    icon: Play,
    hint: "Alt+7",
  },
  {
    id: "verify",
    href: route("/verify"),
    label: "Pramana",
    gloss: "Verification",
    deva: "प्रमाण",
    meaning: "Proof, a valid means of knowing; how VARUNA scores itself",
    icon: BadgeCheck,
    hint: "Alt+8",
  },
  {
    id: "onboard",
    href: route("/onboard"),
    label: "Pravesh",
    gloss: "City onboarding",
    deva: "प्रवेश",
    meaning: "Entry; a new city brought into VARUNA in minutes",
    icon: MapPinned,
    hint: "Alt+9",
  },
];

/** A rail item by id; throws on an id the rail does not carry, so a typo fails a test, not a page. */
export function navItem(id: string): NavItem {
  const item = NAV_ITEMS.find((candidate) => candidate.id === id);
  if (!item) throw new Error(`No rail item "${id}"`);
  return item;
}

/**
 * The accessible name of a rail item: the label and, for a Sanskrit label, its English gloss
 * ("Nadi, drain health"), so a screen reader never announces only a word it cannot translate.
 */
export function navAccessibleName(item: NavItem): string {
  return item.gloss ? `${item.label}, ${item.gloss.toLowerCase()}` : item.label;
}

/**
 * A rail item's href on `city`. The city travels with every screen link - a Chennai console that
 * opens Nadi opens Chennai's drains - and nothing else does: a `?run=` names one city's cycle and a
 * `?hotspot=` one screen's selection, so neither belongs on the next screen. Mumbai, the default,
 * adds nothing, so its links read as they always have. Callers pass a city already validated by
 * `cityFromSearch`; it is encoded regardless.
 */
export function navHref(item: NavItem, city: string | null | undefined): Route {
  if (!city || city === DEFAULT_CITY) return item.href;
  return `${item.href}?city=${encodeURIComponent(city)}` as Route;
}

/** True when `pathname` is on or under a rail item's href. */
export function isNavActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  const base = href.split("?")[0] ?? href;
  return pathname === base || pathname.startsWith(`${base}/`);
}

/** Rail item for a pathname, or null on pages outside the rail (landing, design). */
export function activeNavItem(pathname: string | null): NavItem | null {
  return NAV_ITEMS.find((item) => isNavActive(pathname, item.href)) ?? null;
}

/** Command palette entries. Navigation is derived from NAV_ITEMS; actions are listed here. */
export type PaletteActionId =
  | "toggle-play"
  | "compute-live"
  | "copy-run-id"
  | "dispatch-pumps"
  | "clean-top-pipes"
  | "open-settings"
  | "show-shortcuts";

export interface PaletteAction {
  id: PaletteActionId;
  label: string;
  /** Keyboard hint, if the action has one. */
  hint?: string;
  /** When true the action only works with a loaded run and is disabled until then. */
  needsRun: boolean;
  /** Plain-language reason shown while disabled. */
  disabledReason?: string;
}

export const PALETTE_ACTIONS: readonly PaletteAction[] = [
  { id: "toggle-play", label: "Play or pause the replay", hint: "Space", needsRun: false },
  {
    id: "compute-live",
    label: "Compute live",
    needsRun: true,
    disabledReason: "Available once a run is loaded",
  },
  {
    id: "copy-run-id",
    label: "Copy run id",
    needsRun: true,
    disabledReason: "Available once a run is loaded",
  },
  /*
   * The two deep-link actions below are gated on a loaded run and nothing else - the palette cannot
   * see a run's hotspots or attribution - so their reasons say exactly that. An earlier "once a run
   * has attribution" read as a condition the palette checked; it checked only `currentRun`.
   *
   * The label was "Clean top pipes in what-if". Nothing ranks a junction's pipes yet (P7.7) and the
   * lab refuses the top-14-by-beta lever with its reason, so the palette no longer names a
   * selection it cannot make: it opens the lab on the loaded run and the operator picks there.
   */
  {
    id: "dispatch-pumps",
    label: "Dispatch pumps at a hotspot",
    needsRun: true,
    disabledReason: "Available once a run is loaded",
  },
  {
    id: "clean-top-pipes",
    label: "Clean pipes in what-if",
    needsRun: true,
    disabledReason: "Available once a run is loaded",
  },
  { id: "open-settings", label: "Open settings", needsRun: false },
  { id: "show-shortcuts", label: "Show keyboard shortcuts", hint: "?", needsRun: false },
];

/** A hotspot as the palette lists it; the console passes these once a run is loaded. */
export interface PaletteHotspot {
  id: string;
  name: string;
  /** p50 depth in cm at the selected time, if known. */
  depthCm?: number;
}
