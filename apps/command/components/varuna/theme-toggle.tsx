"use client";

import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

export interface ThemeToggleProps {
  className?: string;
  /** "sm" is the 32 px top-bar button; "md" is the 44 px touch target public screens need. */
  size?: "sm" | "md";
}

/**
 * Switches dark and light. The icon shows the theme the button switches *to*, and the label says
 * so, because a sun in a dark room reads as "make it bright" at a glance. The switch is instant
 * (motion M37): the attribute changes and every token follows in the same frame, so there is no
 * transition to reduce under `prefers-reduced-motion`.
 */
export function ThemeToggle({ className, size = "sm" }: ThemeToggleProps) {
  const { theme, toggle } = useTheme();
  const light = theme === "light";
  const label = light ? "Switch to dark mode" : "Switch to light mode";
  const Icon = light ? Moon : Sun;

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      aria-pressed={light}
      title={label}
      data-theme-toggle=""
      className={cn(
        "rounded-control text-text-2 hover:bg-well hover:text-text inline-flex shrink-0 items-center justify-center",
        size === "md" ? "size-11" : "size-8",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={size === "md" ? "size-5" : "size-[18px]"}
        strokeWidth={1.75}
      />
    </button>
  );
}
