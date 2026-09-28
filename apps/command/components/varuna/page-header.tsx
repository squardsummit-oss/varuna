import type { NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  title: string;
  description?: string;
  /** Buttons rendered at the right of the title row. */
  actions?: React.ReactNode;
  /** Honesty label shown as a chip, e.g. "Inferred drain graph" (SPEC.md section 6.8). */
  honesty?: string;
  /**
   * The rail item a Sanskrit-named screen belongs to (ADR-0085). When it carries a gloss, a line
   * under the title gives the English name, the Devanagari and what the word means, so the name
   * invites the question and the page answers it. The h1's accessible name stays the title.
   */
  screen?: NavItem;
  className?: string;
}

/** Title block for full-page screens: display type, one sentence of context, optional honesty chip. */
export function PageHeader({
  title,
  description,
  actions,
  honesty,
  screen,
  className,
}: PageHeaderProps) {
  const glossId = screen?.gloss ? `page-gloss-${screen.id}` : undefined;
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1
            className="font-display text-h1 font-semibold tracking-display text-text"
            aria-describedby={glossId}
            translate={screen?.gloss ? "no" : undefined}
          >
            {title}
          </h1>
          {honesty ? (
            <span className="inline-flex items-center rounded-chip border border-line px-2.5 py-0.5 text-micro text-text-2">
              {honesty}
            </span>
          ) : null}
        </div>
        {screen?.gloss ? (
          <p id={glossId} className="flex flex-wrap items-baseline gap-x-2 text-small text-text-2">
            <span className="font-medium text-text">{screen.gloss}</span>
            {screen.deva ? (
              <span lang="sa" aria-hidden="true" className="text-text-3">
                {screen.deva}
              </span>
            ) : null}
            {screen.meaning ? <span className="text-text-3">{screen.meaning}</span> : null}
          </p>
        ) : null}
        {description ? <p className="max-w-[72ch] text-body text-text-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
