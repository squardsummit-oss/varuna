"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Kbd } from "@/components/varuna/kbd";
import { type Shortcut, type ShortcutGroup, SHORTCUTS } from "@/lib/shortcuts";
import { useUiStore } from "@/lib/stores/ui";

interface GroupedShortcuts {
  name: ShortcutGroup;
  items: Shortcut[];
}

/** Groups in order of first appearance in SHORTCUTS (Replay, Layers, Panels, Navigation). */
export function groupShortcuts(shortcuts: readonly Shortcut[] = SHORTCUTS): GroupedShortcuts[] {
  const groups: GroupedShortcuts[] = [];
  for (const shortcut of shortcuts) {
    let group = groups.find((g) => g.name === shortcut.group);
    if (!group) {
      group = { name: shortcut.group, items: [] };
      groups.push(group);
    }
    group.items.push(shortcut);
  }
  return groups;
}

const RUN_NOTE = "Available once a run is loaded";

/**
/** The "?" overlay: every shortcut from SPEC.md section 7.2, grouped, with key caps. */
export function ShortcutsOverlay() {
  const open = useUiStore((s) => s.shortcutsOpen);
  const setOpen = useUiStore((s) => s.setShortcutsOpen);
  const groups = groupShortcuts();

  return (
    <Dialog open={open} onOpenChange={(next) => setOpen(next)}>
      <DialogContent className="motion-reduce:animate-none sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Shortcuts work anywhere on the console except inside a text field. Press Esc to close.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-6 sm:grid-cols-2">
          {groups.map((group) => {
            const headingId = `shortcuts-${group.name.toLowerCase()}`;
            return (
              <section key={group.name} aria-labelledby={headingId}>
                <h3 id={headingId} className="type-small text-text-2 mb-1 font-medium">
                  {group.name}
                </h3>
                <ul className="flex flex-col">
                  {group.items.map((shortcut) => (
                    <li
                      key={shortcut.id}
                      className="border-line flex min-h-10 items-center justify-between gap-4 border-b py-1.5 last:border-b-0"
                    >
                      <span className="flex flex-col">
                        <span className="type-body text-text">{shortcut.label}</span>
                        {shortcut.availability === "run" ? (
                          <span className="type-micro text-text-3">{RUN_NOTE}</span>
                        ) : null}
                      </span>
                      <span className="flex shrink-0 items-center gap-1">
                        {shortcut.keys.map((key) => (
                          <Kbd key={key}>{key}</Kbd>
                        ))}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
