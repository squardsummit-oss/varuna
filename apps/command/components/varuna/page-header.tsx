import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  title: string;
  /** One short line at most; the gloss already says what the screen is. */
  description?: string;
  /** Buttons rendered at the right of the title row. */
  actions?: React.ReactNode;
  /** Honesty label shown as a chip, e.g. "Inferred drain graph" (SPEC.md section 6.8). */
  honesty?: string;
  /**
   * The rail item a Sanskrit-named screen belongs to (ADR-0085). When it carries a gloss, a line
   * under the title gives the English name and the Devanagari. What the word means is a tooltip on
   * that line (hover and focus) and screen-reader text, not a paragraph, so the header stays two
   * lines. The h1's accessible name stays the title.
   */
  screen?: NavItem;
  className?: string;
}

/** The rail's tooltip look (`DARK_TOOLTIP_CLASS`), kept here so this module imports no client code. */
const GLOSS_TOOLTIP_CLASS = cn(
  "max-w-[36ch] border border-line-strong bg-well text-text",
  "[&>:last-child]:bg-well [&>:last-child]:fill-well",
);

/** Title block for full-page screens: display type, the English gloss, optional honesty chip. */
export function PageHeader({
  title,
  description,
  actions,
  honesty,
  screen,
  className,
}: PageHeaderProps) {
  const glossId = screen?.gloss ? `page-gloss-${screen.id}` : undefined;
  const gloss = screen?.gloss ? (
    <>
      <span className="text-text font-medium">{screen.gloss}</span>
      {screen.deva ? (
        <span lang="sa" aria-hidden="true" className="text-text-3">
          {screen.deva}
        </span>
      ) : null}
      {screen.meaning ? <span className="sr-only">{screen.meaning}</span> : null}
    </>
  ) : null;
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
      <div className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-3">
          <h1
            className="font-display text-h1 tracking-display text-text font-semibold"
            aria-describedby={glossId}
            translate={screen?.gloss ? "no" : undefined}
          >
            {title}
          </h1>
          {honesty ? (
            <span className="rounded-chip border-line text-micro text-text-2 inline-flex items-center border px-2.5 py-0.5">
              {honesty}
            </span>
          ) : null}
        </div>
        {gloss ? (
          screen?.meaning ? (
            <Tooltip>
              <TooltipTrigger
                render={
                  <p
                    id={glossId}
                    tabIndex={0}
                    className="text-small text-text-2 decoration-line-strong focus-visible:outline-tide inline-flex flex-wrap items-baseline gap-x-2 rounded-sm underline decoration-dotted underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2"
                  />
                }
              >
                {gloss}
              </TooltipTrigger>
              <TooltipContent side="bottom" align="start" className={GLOSS_TOOLTIP_CLASS}>
                {screen.meaning}
              </TooltipContent>
            </Tooltip>
          ) : (
            <p
              id={glossId}
              className="text-small text-text-2 flex flex-wrap items-baseline gap-x-2"
            >
              {gloss}
            </p>
          )
        ) : null}
        {description ? <p className="text-small text-text-2 max-w-[72ch]">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
