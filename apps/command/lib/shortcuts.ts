"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { currentCity } from "./city";
import { NAV_ITEMS, navHref, type NavItem } from "./nav";
import { LEAD_COARSE, LEAD_FINE, LEAD_TICK, useReplayStore } from "./stores/replay";
import { useRunStore } from "./stores/run";
import { useUiStore } from "./stores/ui";

/** Shortcut groups shown in the overlay. */
export type ShortcutGroup = "Replay" | "Layers" | "Panels" | "Navigation";

export interface Shortcut {
  id: string;
  /** Keys as the overlay prints them; a chord is one entry, e.g. "Ctrl K". */
  keys: string[];
  label: string;
  group: ShortcutGroup;
  /** "always" works now; "run" is listed as available once a run is loaded. */
  availability: "always" | "run";
}

/**
 * The overlay's line for a rail chord. A Sanskrit name is a proper noun and keeps its capital,
 * with the English gloss beside it ("Go to Nadi (drain health)", "Go to Drishti (command
 * console)", ADR-0085); a screen without a gloss reads as the plain phrase ("Go to design").
 */
export function navShortcutLabel(item: NavItem): string {
  return item.gloss
    ? `Go to ${item.label} (${item.gloss.toLowerCase()})`
    : `Go to ${item.label.toLowerCase()}`;
}

/** Every shortcut from SPEC.md section 7.2 plus the rail chords. */
export const SHORTCUTS: readonly Shortcut[] = [
  {
    id: "play",
    keys: ["Space"],
    label: "Play or pause the replay",
    group: "Replay",
    availability: "always",
  },
  { id: "scrub", keys: ["←", "→"], label: "Scrub 15 min", group: "Replay", availability: "always" },
  {
    id: "scrub-coarse",
    keys: ["Shift ←", "Shift →"],
    label: "Scrub 60 min",
    group: "Replay",
    availability: "always",
  },
  {
    id: "scrub-fine",
    keys: ["Ctrl ←", "Ctrl →"],
    label: "Scrub 5 min",
    group: "Replay",
    availability: "always",
  },
  {
    id: "probability",
    keys: ["P"],
    label: "Probability mode",
    group: "Layers",
    availability: "run",
  },
  { id: "drains", keys: ["D"], label: "Drains (health)", group: "Layers", availability: "run" },
  {
    id: "surcharge",
    keys: ["S"],
    label: "Surcharge and backflow",
    group: "Layers",
    availability: "run",
  },
  { id: "routes", keys: ["R"], label: "Routes", group: "Layers", availability: "run" },
  { id: "isochrones", keys: ["I"], label: "Isochrones", group: "Layers", availability: "run" },
  { id: "ground-truth", keys: ["G"], label: "Ground truth", group: "Layers", availability: "run" },
  { id: "3d", keys: ["3"], label: "Photorealistic city", group: "Layers", availability: "run" },
  { id: "xray", keys: ["X"], label: "Drain X-ray", group: "Layers", availability: "run" },
  { id: "whatif", keys: ["W"], label: "What-if drawer", group: "Panels", availability: "run" },
  {
    id: "full-view",
    keys: ["F"],
    label: "Full view: the map on the whole screen",
    group: "Panels",
    availability: "run",
  },
  {
    id: "palette",
    keys: ["Ctrl K"],
    label: "Command palette",
    group: "Panels",
    availability: "always",
  },
  {
    id: "shortcuts",
    keys: ["?"],
    label: "Keyboard shortcuts",
    group: "Panels",
    availability: "always",
  },
  {
    id: "escape",
    keys: ["Esc"],
    label: "Close the open panel, or leave full view",
    group: "Panels",
    availability: "always",
  },
  ...NAV_ITEMS.map<Shortcut>((item) => ({
    id: `nav-${item.id}`,
    keys: [item.hint],
    label: navShortcutLabel(item),
    group: "Navigation",
    availability: "always",
  })),
];

/** Layer keys (lower case) that Phase 6 wires to map layers; nothing happens until a handler is registered. */
export const LAYER_KEYS = ["p", "d", "s", "r", "i", "g", "3", "x", "w", "f"] as const;
export type LayerKey = (typeof LAYER_KEYS)[number];

type LayerHandler = () => void;
const layerHandlers = new Map<LayerKey, LayerHandler>();

/**
 * Lets a screen register what a layer key does (e.g. the console maps "p" to probability mode).
 * Returns an unsubscribe. Keys with no handler do nothing, by design.
 */
export function registerLayerShortcut(key: LayerKey, handler: LayerHandler): () => void {
  layerHandlers.set(key, handler);
  return () => {
    if (layerHandlers.get(key) === handler) layerHandlers.delete(key);
  };
}

export function hasLayerShortcut(key: LayerKey): boolean {
  return layerHandlers.has(key);
}

type PlayToggle = () => void;
let playToggle: PlayToggle | null = null;

/**
 * Lets the mounted time bar own Space, so the shortcut drives the API's replay clock instead of
 * only the local store. Without a registration (the landing page, `/design`) Space still toggles
 * the store, and nothing reaches the network.
 */
export function registerPlayToggle(handler: PlayToggle): () => void {
  playToggle = handler;
  return () => {
    if (playToggle === handler) playToggle = null;
  };
}

/** True when the key event started in a field that owns its own keys. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  // `=== true`: jsdom leaves `isContentEditable` undefined, and a predicate answers a boolean.
  return target.isContentEditable === true;
}

/**
 * Inputs that take a value by pointer or arrow and never by typing: a letter pressed on one of
 * them is not text, so it can still be a shortcut. The console's scrub is a range input, and it is
 * the control holding focus after a drag - without this, F and Escape did nothing from it, though
 * the full-view caption promises both.
 */
const VALUE_CONTROL_TYPES = new Set([
  "range",
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "color",
]);

/** True for an input that takes a value without typing (a range, a checkbox), see above. */
export function isValueControl(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement && VALUE_CONTROL_TYPES.has(target.type);
}

/** True when the event started where typed characters are text: every editable target except the
 *  value controls above. Letters and Escape are left alone there, and only there. */
export function isTextEntry(target: EventTarget | null): boolean {
  return isEditableTarget(target) && !isValueControl(target);
}

/** True for controls that already act on Space or Enter. */
function isActivatableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "BUTTON" || tag === "A" || tag === "SUMMARY") return true;
  const role = target.getAttribute("role");
  return (
    role === "button" ||
    role === "tab" ||
    role === "menuitem" ||
    role === "option" ||
    role === "switch" ||
    role === "slider"
  );
}

/** Scrub step for an arrow key event: 60 min with Shift, 5 min with Ctrl, otherwise 15. */
export function scrubStepFor(e: Pick<KeyboardEvent, "shiftKey" | "ctrlKey" | "metaKey">): number {
  if (e.shiftKey) return LEAD_COARSE;
  if (e.ctrlKey || e.metaKey) return LEAD_FINE;
  return LEAD_TICK;
}

/**
 * Global keyboard shortcuts (SPEC.md section 7.2). Mount once, in Providers.
 * Dispatches to the stores that exist now; layer keys go through registerLayerShortcut.
 */
export function useGlobalShortcuts(): void {
  const router = useRouter();

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const ui = useUiStore.getState();
      const replay = useReplayStore.getState();
      const key = e.key;
      const lower = key.toLowerCase();
      const mod = e.ctrlKey || e.metaKey;

      // Command palette: works everywhere, even inside fields.
      if (mod && lower === "k") {
        e.preventDefault();
        ui.toggleCommandPalette();
        return;
      }

      if (isTextEntry(e.target)) return;
      // A range or a checkbox keeps its own Space and arrows; letters, `?` and Escape still work.
      const valueControl = isValueControl(e.target);

      // Escape closes whatever overlay is open; dialogs handle their own Escape too.
      if (key === "Escape") {
        if (ui.commandPaletteOpen || ui.shortcutsOpen || ui.settingsOpen) {
          ui.closeOverlays();
        }
        return;
      }

      // Nothing else fires while a modal overlay is open.
      if (ui.commandPaletteOpen || ui.shortcutsOpen || ui.settingsOpen) return;

      if (key === "?" || (e.shiftKey && key === "/")) {
        e.preventDefault();
        ui.toggleShortcuts();
        return;
      }

      // Rail chords: Alt+1 ... Alt+9.
      if (e.altKey && !mod && /^[1-9]$/.test(key)) {
        const item = NAV_ITEMS[Number(key) - 1];
        if (item) {
          e.preventDefault();
          // The city travels with the chord as it does with the rail's links.
          router.push(navHref(item, currentCity()));
        }
        return;
      }

      if (key === " " && !mod && !e.altKey) {
        if (valueControl || isActivatableTarget(e.target)) return;
        e.preventDefault();
        if (playToggle) playToggle();
        else replay.togglePlaying();
        return;
      }

      if (key === "ArrowLeft" || key === "ArrowRight") {
        if (e.altKey || valueControl) return;
        const role = e.target instanceof HTMLElement ? e.target.getAttribute("role") : null;
        if (role === "slider" || role === "tab" || role === "menuitem") return;
        e.preventDefault();
        const step = scrubStepFor(e);
        replay.stepLead(key === "ArrowLeft" ? -step : step);
        return;
      }

      if (!mod && !e.altKey && (LAYER_KEYS as readonly string[]).includes(lower)) {
        const handler = layerHandlers.get(lower as LayerKey);
        if (handler && useRunStore.getState().currentRun) {
          e.preventDefault();
          handler();
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router]);
}
