"use client";

import { useEffect, useState } from "react";

/**
 * Whether a scroll container has content out of sight above or below it.
 *
 * The console's layer column scrolls, and nothing said so: over a dark aerial basemap the thin
 * `--line` scrollbar thumb is invisible, so at 1366 x 768 with probability and drains on the
 * column simply looked as though it ended. This is the measurement behind the affordance - the
 * fade is drawn only on the side that has something hidden, so the last row is never dimmed when
 * it is the last row.
 *
 * It is a layout fact, not an animation: nothing here moves, so there is nothing for
 * `prefers-reduced-motion` to switch off.
 *
 * The container is held in state rather than a ref, so the observer is set up by an effect that
 * depends on the node and nothing is read during render.
 */
export interface ScrollEdges {
  /** Attach to the scroll container. */
  attach: (node: HTMLElement | null) => void;
  /** Content is hidden above the top edge. */
  above: boolean;
  /** Content is hidden below the bottom edge. */
  below: boolean;
}

/** A pixel of slack, so a container scrolled to its end is not called "one pixel short". */
const EPSILON = 2;

export function useScrollEdges(): ScrollEdges {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const [edges, setEdges] = useState({ above: false, below: false });

  useEffect(() => {
    // Before the column is attached the initial "nothing is hidden" is already right, and there
    // is no element to measure.
    if (!node) return;
    const measure = () => {
      const above = node.scrollTop > EPSILON;
      const below = node.scrollTop + node.clientHeight < node.scrollHeight - EPSILON;
      setEdges((current) =>
        current.above === above && current.below === below ? current : { above, below },
      );
    };
    // The children too: toggling a layer on changes the column's height without any scrolling,
    // and an affordance that only appears after a scroll is an affordance nobody sees.
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    for (const child of Array.from(node.children)) observer.observe(child);
    node.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    // The first measurement waits a frame rather than running in the effect body: a synchronous
    // setState there cascades a second render before the browser has painted the first.
    const first = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(first);
      observer.disconnect();
      node.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
    // Re-runs when the column's contents change identity, which is what adds and removes rows.
  }, [node]);

  return { attach: setNode, above: edges.above, below: edges.below };
}

/**
 * The mask that fades the clipped edge of a scroll container.
 *
 * A mask rather than an overlay: it makes the content at the cut translucent instead of laying
 * something on top of it, so no row is covered and nothing intercepts a click. Returns undefined
 * when nothing is hidden, which is what keeps a fully visible column crisp.
 */
export function edgeFadeStyle(edges: {
  above: boolean;
  below: boolean;
}): React.CSSProperties | undefined {
  if (!edges.above && !edges.below) return undefined;
  // `black` and `transparent` are a mask's alpha channel, not colour: nothing here is a design
  // decision the token set should own.
  const top = edges.above ? "transparent 0, black 20px" : "black 0";
  const bottom = edges.below ? "black calc(100% - 20px), transparent 100%" : "black 100%";
  const gradient = `linear-gradient(to bottom, ${top}, ${bottom})`;
  return { maskImage: gradient, WebkitMaskImage: gradient };
}
