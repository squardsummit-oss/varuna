import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** What is missing, e.g. "No runs yet". */
  title: string;
  /** What to do about it, e.g. "Press Play on the replay, or Compute live." */
  description?: string;
  /** A button or link that performs the fix. */
  action?: React.ReactNode;
  icon?: LucideIcon;
  className?: string;
  size?: "sm" | "md";
}

/**
 * Centred column for empty panels and maps (SPEC.md section 6.8): the title says what is
 * missing, the description says what to do, the action does it. Announced as a status region.
 */
export function EmptyState({
  title,
  description,
  action,
  icon: Icon,
  className,
  size = "md",
}: EmptyStateProps) {
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        size === "sm" ? "gap-1.5 px-3 py-6" : "gap-2 px-4 py-10",
        className,
      )}
    >
      {Icon ? <Icon size={20} strokeWidth={1.75} aria-hidden="true" className="text-text-3" /> : null}
      <p className="text-body font-medium text-text">{title}</p>
      {description ? (
        <p className="max-w-[44ch] text-small text-text-2">{description}</p>
      ) : null}
      {action ? <div className={cn("flex items-center gap-2", size === "sm" ? "mt-1" : "mt-2")}>{action}</div> : null}
    </div>
  );
}
