"use client";

import { cn } from "@/lib/utils";

/** The page index, in reading order; ids double as anchor targets. */
export const DESIGN_SECTIONS = [
  { id: "colour", label: "Colour tokens" },
  { id: "ramps", label: "Ramps" },
  { id: "type", label: "Type scale" },
  { id: "shape", label: "Shape and spacing" },
  { id: "components", label: "Components" },
  { id: "motion", label: "Motion" },
] as const;

export type DesignSectionId = (typeof DESIGN_SECTIONS)[number]["id"];

export interface DesignSectionProps {
  id: DesignSectionId;
  title: string;
  /** One sentence on what the section proves; cites the spec section. */
  description?: string;
  children: React.ReactNode;
}

/** A titled, anchored block of the design page. */
export function DesignSection({ id, title, description, children }: DesignSectionProps) {
  const headingId = `${id}-heading`;
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-6 flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id={headingId} className="type-h2 text-text">
          {title}
        </h2>
        {description ? <p className="type-small max-w-[72ch] text-text-2">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

export interface DemoProps {
  /** The state or variant this cell shows, e.g. "Loading", "No data", "Small". */
  label: string;
  /** A second line when the state needs a word of explanation. */
  note?: string;
  children: React.ReactNode;
  className?: string;
  /** Skip the bordered frame when the child draws its own box. */
  bare?: boolean;
}

/** One labelled cell of a component demo: the state label above, the component below. */
export function Demo({ label, note, children, className, bare = false }: DemoProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <div className="flex flex-col gap-0.5">
        <span className="type-micro font-medium text-text-2">{label}</span>
        {note ? <span className="type-micro text-text-3">{note}</span> : null}
      </div>
      {bare ? children : <div className="rounded-control border border-line bg-ink p-3">{children}</div>}
    </div>
  );
}

/** Small label beside a value, e.g. "12 / 1.3". */
export function Meta({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("type-micro num text-text-3", className)}>{children}</span>;
}
