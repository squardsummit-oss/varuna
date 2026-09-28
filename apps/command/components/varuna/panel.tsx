import { cn } from "@/lib/utils";

export interface PanelProps {
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Buttons or toggles rendered at the right of the header row. */
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  /** 12 px padding and a tighter header instead of 16 px. */
  dense?: boolean;
  /** Element for the outer box; a section by default. */
  as?: "section" | "div" | "aside" | "article";
}

/**
 * The card of the control room (SPEC.md section 6.4): `--deep` on `--ink`, a 1 px `--line`
 * border, 12 px radius, no shadow. The header row carries a small title and optional actions.
 */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  dense = false,
  as: Tag = "section",
}: PanelProps) {
  const hasHeader = Boolean(title || description || actions);
  const pad = dense ? "p-3" : "p-4";
  return (
    <Tag
      className={cn(
        "flex flex-col rounded-panel border border-line bg-deep text-text",
        className,
      )}
    >
      {hasHeader ? (
        <header
          className={cn(
            "flex items-start justify-between gap-3 border-b border-line",
            dense ? "px-3 py-2" : "px-4 py-3",
          )}
        >
          <div className="min-w-0">
            {title ? <h2 className="truncate text-small font-medium text-text">{title}</h2> : null}
            {description ? <p className="text-micro text-text-2">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn("min-h-0 flex-1", pad)}>{children}</div>
    </Tag>
  );
}
