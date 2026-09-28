"use client";

import type { Route } from "next";
import { useRouter } from "next/navigation";
import {
  Copy,
  Cpu,
  Droplets,
  FireExtinguisher,
  History,
  Hospital,
  Keyboard,
  type LucideIcon,
  MapPin,
  Play,
  Settings,
  Waypoints,
  Wrench,
} from "lucide-react";
import { defaultFilter } from "cmdk";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd } from "@/components/varuna/kbd";
import { SkeletonRows } from "@/components/varuna/skeleton";
import { paletteHref, usePaletteData } from "@/lib/api/palette";
import { formatBeta, formatCm, formatDateTime } from "@/lib/format";
import { currentCity } from "@/lib/city";
import {
  NAV_ITEMS,
  PALETTE_ACTIONS,
  navHref,
  type NavItem,
  type PaletteActionId,
  type PaletteHotspot,
} from "@/lib/nav";
import { useReplayStore } from "@/lib/stores/replay";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

export interface CommandPaletteProps {
  /** Called when a hotspot is picked; defaults to opening the console on that hotspot. */
  onSelectHotspot?: (hotspot: PaletteHotspot) => void;
}

const ACTION_ICONS: Record<PaletteActionId, LucideIcon> = {
  "toggle-play": Play,
  "compute-live": Cpu,
  "copy-run-id": Copy,
  "dispatch-pumps": Droplets,
  "clean-top-pipes": Wrench,
  "open-settings": Settings,
  "show-shortcuts": Keyboard,
};

/**
 * Actions whose control exists but whose engine is not wired, each with its one sentence of plan
 * (SPEC.md 17: "coming in pilot", never a dead control). Compute live needs a cycle that runs
 * Sky, Twin and products on demand; the console serves baked runs only, so the palette says so
 * rather than opening a replay panel whose button cannot do it either.
 */
export const PILOT_PLANS: Partial<Record<PaletteActionId, string>> = {
  "compute-live":
    "Compute live will re-run this cycle through Sky, Twin and products and publish the new run to the console.",
};

async function copyRunId(runId: string | undefined): Promise<void> {
  if (!runId) return;
  try {
    await navigator.clipboard.writeText(runId);
    toast.success("Run id copied");
  } catch {
    toast.error("Copy failed. Select the run id in the top bar to copy it.");
  }
}

/** A line inside a group that is not a choice: an empty list or a failure, never selectable. */
function StatusRow({ children }: { children: ReactNode }) {
  return <div className="text-text-3 px-2 py-1.5 text-sm">{children}</div>;
}

/** Shimmer rows while a group loads (motion M22); the label is for screen readers. */
function LoadingRows({ label }: { label: string }) {
  return (
    <div role="status" className="px-2">
      <span className="sr-only">{label}</span>
      <SkeletonRows rows={3} />
    </div>
  );
}

/*
 * Item values, in one place: cmdk matches the search against them and the controlled selection
 * names an item by them, so the two must never drift apart. Each carries the id, so two facilities
 * that share a name ("Bandra Fire Brigade" is in the asset layer twice) stay two items.
 */
const hotspotValue = (h: { id: string; name: string }) => `hotspot ${h.name} ${h.id}`;
const facilityValue = (f: { id: string; name: string }) => `facility ${f.name} ${f.id}`;
const pipeValue = (p: { id: string; street: string | null }) => `pipe ${p.id} ${p.street ?? ""}`;
const screenValue = (item: { label: string }) => `screen ${item.label}`;
const runValue = (run: { run_id: string; cycle_ts: string }) =>
  `run ${run.run_id} ${formatDateTime(run.cycle_ts)}`;
const FACILITY_KEYWORDS = ["reachability"];
const PIPE_KEYWORDS = ["drain", "blockage"];

/**
 * What a screen answers to besides its name. Every screen carries a Sanskrit name (ADR-0085), and
 * an operator who types "console", "alerts", "pumps", "drain", "what-if" or "onboard" is looking
 * for the screen that does that, so the id, the English gloss, each word of it and the Devanagari
 * all find it.
 */
export function screenKeywords(item: NavItem): string[] {
  const words = item.gloss ? [item.gloss, ...item.gloss.toLowerCase().split(/\s+/)] : [];
  return [item.id, ...words, ...(item.deva ? [item.deva] : [])];
}

/** Words of a value or a query: letters, digits and combining marks, so Devanagari stays whole. */
function words(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^\p{L}\p{N}\p{M}]+/u)
    .filter(Boolean);
}

/*
 * Named places are matched on words, never on scattered letters. cmdk's own score is a subsequence
 * match, and over 368 hospital and fire-station names a subsequence of "console", "alerts" or
 * "drishti" turns up dozens of times: measured on 2026-09-28, typing "console" put Drishti 40th of
 * 44 under hospitals, and Enter opened an eye hospital.
 */
const STRICT_KINDS = new Set(["hotspot", "facility", "pipe", "run"]);

/*
 * Between entries that match equally well, a screen comes first, then a hotspot, then a facility or
 * a pipe: a screen is where an operator goes, and a hotspot is what the console is about. Kept
 * below 10 so it only ever breaks a tie inside a tier.
 */
const KIND_WEIGHT: Record<string, number> = { screen: 3, hotspot: 2, facility: 1, pipe: 1 };

/**
 * The palette's filter and ranking, passed to cmdk and used to order the groups (cmdk's own group
 * sort looks groups up by an id it never renders, so it never moves one). Every entry's value
 * starts with its kind ("screen", "hotspot", "facility", "pipe", "run", "action"). The score is
 * tiered, so a better kind of match always outranks a worse one:
 *
 * - 30s: every query word starts a word of the value or its keywords ("sion" in "Sion Circle");
 * - 20s: every query word is inside the text ("0310" in a run id);
 * - 10s: cmdk's scattered-letter match, for screens and actions only, so "jlyntr" still finds
 *   Jalayantra but "console" never finds a hospital;
 * - 0: no match, and the entry is hidden.
 *
 * Inside a tier the kind weight and then cmdk's own score order the entries.
 */
export function paletteScore(value: string, search: string, keywords: string[] = []): number {
  const query = words(search);
  if (query.length === 0) return 1;
  const haystack = words([value, ...keywords].join(" "));
  const kind = haystack[0] ?? "";
  const fuzzy = defaultFilter(value, search, keywords);
  let tier = 0;
  if (query.every((q) => haystack.some((word) => word.startsWith(q)))) tier = 3;
  else if (query.every((q) => haystack.join(" ").includes(q))) tier = 2;
  else if (!STRICT_KINDS.has(kind) && fuzzy > 0) tier = 1;
  if (tier === 0) return 0;
  return tier * 10 + (KIND_WEIGHT[kind] ?? 0) + Math.min(fuzzy, 0.99);
}

/** One selectable entry as the filter sees it; disabled entries never lead a group. */
interface ScoredEntry {
  value: string;
  keywords?: string[];
  disabled?: boolean;
}

/** An action as the palette draws it and ranks it. */
interface ActionRow extends ScoredEntry {
  key: string;
  id: PaletteActionId;
  label: string;
  /** The plan or the reason under a disabled action. */
  note?: string;
  hint?: string;
  href?: Route;
  onSelect: () => void;
}

/** One group of the palette: its rows as the filter sees them, and the group as drawn. */
interface PaletteGroup {
  key: string;
  entries: readonly ScoredEntry[];
  node: ReactNode;
}

function bestScore(entries: readonly ScoredEntry[], search: string): number {
  let best = 0;
  for (const entry of entries) {
    if (entry.disabled) continue;
    best = Math.max(best, paletteScore(entry.value.trim(), search, entry.keywords));
  }
  return best;
}

function failure(what: string, error: Error | null): string {
  const reason = error?.message ? ` (${error.message.replace(/\.$/, "")})` : "";
  return `${what} did not load${reason}. Screens still open; open the palette again to retry.`;
}

/**
 * Command palette (Ctrl K, SPEC.md 7.13): jump to a hotspot, facility, pipe, screen or run, or
 * start an action. Open state lives in the ui store so the global shortcut and the top-bar button
 * share it.
 *
 * It loads its own lists (`usePaletteData`) for the run the operator is looking at - the run store's
 * run, which the console sets from the cycle it draws - or the newest run when there is none. The
 * lists load on first open, not on page load.
 */
export function CommandPalette({ onSelectHotspot }: CommandPaletteProps) {
  const router = useRouter();
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const currentRun = useRunStore((s) => s.currentRun);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const { runs, hotspots, facilities, pipes } = usePaletteData({
    enabled: open,
    runId: currentRun?.run_id,
    city: currentRun?.city,
  });

  const runList = runs.data ?? [];
  const hotspotList = hotspots.data?.hotspots ?? [];
  // The run every deep link carries: the one on screen, else the one the ranking was read from,
  // else the newest in the registry.
  const activeRunId = currentRun?.run_id || hotspots.data?.runId || runList[0]?.run_id || undefined;

  /*
   * The lists arrive in whatever order the API answers, and cmdk selects the first item that
   * registers - Drishti, the console screen, or a fire station if facilities come back first - then keeps
   * that selection while the hotspot ranking lands above it, scrolled out of view. With nothing typed
   * the selection follows the first item in list order instead, so Enter on a fresh palette is the
   * top-ranked hotspot. Adjusted during render rather than in an effect (React's "adjusting state
   * when a prop changes").
   */
  const facilityList = facilities.data ?? [];
  const pipeList = pipes.data ?? [];
  const firstValue = hotspotList[0]
    ? hotspotValue(hotspotList[0])
    : facilityList[0]
      ? facilityValue(facilityList[0])
      : pipeList[0]
        ? pipeValue(pipeList[0])
        : screenValue(NAV_ITEMS[0]!);
  const [anchor, setAnchor] = useState(firstValue);
  if (anchor !== firstValue) {
    setAnchor(firstValue);
    if (search === "") setSelected(firstValue);
  }
  useEffect(() => {
    if (open && search === "" && listRef.current) listRef.current.scrollTop = 0;
  }, [open, search, firstValue]);

  // Every session of the palette starts with an empty query on the first item.
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setSearch("");
      setSelected(firstValue);
    }
    setOpen(next);
  };

  const close = () => handleOpenChange(false);

  const go = (href: Route) => {
    close();
    router.push(href);
  };

  const pickHotspot = (hotspot: PaletteHotspot) => {
    if (onSelectHotspot) {
      close();
      onSelectHotspot(hotspot);
      return;
    }
    go(paletteHref.hotspot(hotspot.id, activeRunId));
  };

  const runAction = (id: PaletteActionId) => {
    const ui = useUiStore.getState();
    switch (id) {
      case "toggle-play":
        close();
        useReplayStore.getState().togglePlaying();
        return;
      case "open-settings":
        close();
        ui.setSettingsOpen(true);
        return;
      case "show-shortcuts":
        close();
        ui.setShortcutsOpen(true);
        return;
      case "copy-run-id":
        close();
        void copyRunId(activeRunId);
        return;
      case "compute-live":
        // Disabled with its plan (PILOT_PLANS); nothing to run.
        return;
      case "dispatch-pumps":
        go(paletteHref.dispatch(undefined, activeRunId));
        return;
      case "clean-top-pipes":
        // Opens the lab on this cycle and nothing more: no pipe is preselected and no effect is
        // named, because nothing ranks a junction's pipes yet (ADR-0042).
        go(paletteHref.whatif(activeRunId));
        return;
    }
  };

  const noRunsYet = runs.isSuccess && runList.length === 0;

  /*
   * Every action as one row, so the rows drawn and the rows ranked are the same list: one dispatch
   * per ranked hotspot once the ranking is in, the generic entry until then.
   */
  const actionRows = PALETTE_ACTIONS.flatMap((action): ActionRow[] => {
    if (action.id === "dispatch-pumps" && hotspotList.length > 0) {
      return hotspotList.map((hotspot) => {
        const href = paletteHref.dispatch(hotspot.id, activeRunId);
        return {
          key: `dispatch-${hotspot.id}`,
          id: action.id,
          value: `action dispatch pumps at ${hotspot.name} ${hotspot.id}`,
          label: `Dispatch pumps at ${hotspot.name}`,
          href,
          onSelect: () => go(href),
        };
      });
    }
    const plan = PILOT_PLANS[action.id];
    const waitingForRun = action.needsRun && !activeRunId;
    return [
      {
        key: action.id,
        id: action.id,
        value: `action ${action.label}`,
        keywords: [action.id],
        label: action.label,
        note: plan ? `Coming in pilot. ${plan}` : waitingForRun ? action.disabledReason : undefined,
        hint: action.hint,
        disabled: Boolean(plan) || waitingForRun,
        onSelect: () => runAction(action.id),
      },
    ];
  });

  const groups: PaletteGroup[] = [
    {
      key: "hotspots",
      entries: hotspotList.map((hotspot) => ({ value: hotspotValue(hotspot) })),
      node: (
        <CommandGroup key="hotspots" heading="Hotspots" forceMount={hotspots.isError || undefined}>
          {hotspots.isLoading ? <LoadingRows label="Loading hotspots" /> : null}
          {hotspots.isError ? <StatusRow>{failure("Hotspots", hotspots.error)}</StatusRow> : null}
          {hotspots.isSuccess && hotspotList.length === 0 ? (
            <StatusRow>
              {noRunsYet
                ? "No hotspots yet — press Play on the replay"
                : "This run has no hotspot ranking. Pick another run below."}
            </StatusRow>
          ) : null}
          {hotspotList.map((hotspot) => (
            <CommandItem
              key={hotspot.id}
              value={hotspotValue(hotspot)}
              data-href={paletteHref.hotspot(hotspot.id, activeRunId)}
              onSelect={() =>
                pickHotspot({ id: hotspot.id, name: hotspot.name, depthCm: hotspot.peakDepthCm })
              }
            >
              <MapPin className="text-text-2 size-4" strokeWidth={1.75} />
              <span className="min-w-0 truncate">{hotspot.name}</span>
              <CommandShortcut className="num tracking-normal">
                Peak {formatCm(hotspot.peakDepthCm)}
              </CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
      ),
    },
    {
      key: "facilities",
      entries: facilityList.map((facility) => ({
        value: facilityValue(facility),
        keywords: FACILITY_KEYWORDS,
      })),
      node: (
        <CommandGroup
          key="facilities"
          heading="Facilities"
          forceMount={facilities.isError || undefined}
        >
          {facilities.isLoading ? <LoadingRows label="Loading facilities" /> : null}
          {facilities.isError ? (
            <StatusRow>{failure("Facilities", facilities.error)}</StatusRow>
          ) : null}
          {facilities.isSuccess && facilityList.length === 0 ? (
            <StatusRow>This city has no hospitals or fire stations in its asset layer.</StatusRow>
          ) : null}
          {facilityList.map((facility) => {
            const Icon = facility.kind === "fire_station" ? FireExtinguisher : Hospital;
            return (
              <CommandItem
                key={facility.id}
                value={facilityValue(facility)}
                keywords={FACILITY_KEYWORDS}
                data-href={paletteHref.facility(facility.id, activeRunId)}
                onSelect={() => go(paletteHref.facility(facility.id, activeRunId))}
              >
                <Icon className="text-text-2 size-4" strokeWidth={1.75} />
                <span className="min-w-0 truncate">{facility.name}</span>
                <CommandShortcut className="tracking-normal">
                  {facility.kind === "fire_station" ? "Fire station" : "Hospital"}
                </CommandShortcut>
              </CommandItem>
            );
          })}
        </CommandGroup>
      ),
    },
    {
      key: "pipes",
      entries: pipeList.map((pipe) => ({ value: pipeValue(pipe), keywords: PIPE_KEYWORDS })),
      node: (
        <CommandGroup
          key="pipes"
          heading="Pipes by blockage"
          forceMount={pipes.isError || undefined}
        >
          {pipes.isLoading ? <LoadingRows label="Loading pipes" /> : null}
          {pipes.isError ? <StatusRow>{failure("Pipes", pipes.error)}</StatusRow> : null}
          {pipes.isSuccess && pipeList.length === 0 ? (
            <StatusRow>This run has no drain-health product, so no pipe is ranked.</StatusRow>
          ) : null}
          {pipeList.map((pipe) => (
            <CommandItem
              key={pipe.id}
              value={pipeValue(pipe)}
              keywords={PIPE_KEYWORDS}
              data-href={paletteHref.pipe(pipe.id, activeRunId)}
              onSelect={() => go(paletteHref.pipe(pipe.id, activeRunId))}
            >
              <Waypoints className="text-text-2 size-4" strokeWidth={1.75} />
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{pipe.street ?? `Pipe ${pipe.id}`}</span>
                {pipe.street ? (
                  <span className="type-micro text-text-3">Pipe {pipe.id}</span>
                ) : null}
              </span>
              <CommandShortcut className="num tracking-normal">
                β {formatBeta(pipe.betaMean)}
              </CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
      ),
    },
    {
      key: "screens",
      entries: NAV_ITEMS.map((item) => ({
        value: screenValue(item),
        keywords: screenKeywords(item),
      })),
      node: (
        <CommandGroup key="screens" heading="Screens">
          {NAV_ITEMS.map((item) => (
            <CommandItem
              key={item.id}
              value={screenValue(item)}
              keywords={screenKeywords(item)}
              data-href={item.href}
              // The city travels with a screen, as it does on the rail's links; read when the
              // item is picked, so it is the address bar the operator is looking at.
              onSelect={() => go(navHref(item, currentCity()))}
            >
              <item.icon className="text-text-2 size-4" strokeWidth={1.75} />
              <span className="flex min-w-0 items-baseline gap-2">
                <span translate={item.gloss ? "no" : undefined}>{item.label}</span>
                {item.gloss ? (
                  <span className="type-micro text-text-3 truncate">{item.gloss}</span>
                ) : null}
              </span>
              <CommandShortcut className="tracking-normal">
                <Kbd>{item.hint}</Kbd>
              </CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
      ),
    },
    {
      key: "runs",
      entries: runList.map((run) => ({ value: runValue(run) })),
      node: (
        <CommandGroup key="runs" heading="Runs" forceMount={runs.isError || undefined}>
          {runs.isLoading ? <LoadingRows label="Loading runs" /> : null}
          {runs.isError ? <StatusRow>{failure("Runs", runs.error)}</StatusRow> : null}
          {noRunsYet ? (
            <StatusRow>No runs yet — press Play on the replay, or bake the bundle.</StatusRow>
          ) : null}
          {runList.map((run) => (
            <CommandItem
              key={run.run_id}
              value={runValue(run)}
              data-href={paletteHref.run(run.run_id)}
              onSelect={() => go(paletteHref.run(run.run_id))}
            >
              <History className="text-text-2 size-4" strokeWidth={1.75} />
              <span className="flex min-w-0 flex-col">
                <span className="num">{formatDateTime(run.cycle_ts)}</span>
                <span className="type-micro text-text-3 truncate font-mono">{run.run_id}</span>
              </span>
              <CommandShortcut className="tracking-normal">
                {run.run_id === activeRunId ? "Showing" : run.mode}
              </CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
      ),
    },
    {
      key: "actions",
      entries: actionRows,
      node: (
        <CommandGroup key="actions" heading="Actions">
          {actionRows.map((row) => {
            const Icon = ACTION_ICONS[row.id];
            return (
              <CommandItem
                key={row.key}
                value={row.value}
                keywords={row.keywords}
                disabled={row.disabled}
                data-href={row.href}
                onSelect={row.onSelect}
              >
                <Icon className="text-text-2 size-4" strokeWidth={1.75} />
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">{row.label}</span>
                  {row.note ? <span className="type-micro text-text-3">{row.note}</span> : null}
                </span>
                {row.hint ? (
                  <CommandShortcut className="tracking-normal">
                    <Kbd>{row.hint}</Kbd>
                  </CommandShortcut>
                ) : null}
              </CommandItem>
            );
          })}
        </CommandGroup>
      ),
    },
  ];

  /*
   * With a query, the group holding the best match is drawn first, so the first row - the one
   * Enter opens - is the best match anywhere in the palette, not the best hotspot. The sort is
   * stable, so groups that tie keep the order above. With nothing typed the order is fixed.
   */
  const ordered =
    search.trim() === ""
      ? groups
      : groups
          .map((group) => ({ group, score: bestScore(group.entries, search) }))
          .sort((a, b) => b.score - a.score)
          .map(({ group }) => group);

  return (
    <CommandDialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Command palette"
      description="Jump to a hotspot, facility, pipe, screen or run, or start an action"
      className="motion-reduce:animate-none sm:max-w-lg"
    >
      <Command
        label="Command palette"
        loop
        value={selected}
        onValueChange={setSelected}
        filter={paletteScore}
      >
        <CommandInput
          placeholder="Jump to a hotspot, facility, pipe, screen or action"
          value={search}
          onValueChange={setSearch}
        />
        <CommandList ref={listRef} className="max-h-96">
          <CommandEmpty className="text-text-3">
            Nothing matches. Try a hotspot, a hospital, a pipe id or a screen name.
          </CommandEmpty>
          {ordered.map((group) => group.node)}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
