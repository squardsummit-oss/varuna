/**
 * The clock of one pump dispatch on Jalayantra (motions M33, M34, M35), with no deck.gl in it.
 *
 * The map's layers (`components/map/layers/pump-routes.ts`) read it in their `draw`; the gauges
 * and the arrival timeline read when it started. Keeping it free of WebGL means the page can
 * time the gauges without loading the map's bundle.
 */

import { DUR_MS } from "@/lib/motion";

/** How long one lorry drives its road on screen, whatever its length (section 8, M33). */
export const TRAVEL_MS = DUR_MS.routeDrawOn;
/** How much later each lorry leaves than the one before it (section 8, M33). */
export const STAGGER_MS = DUR_MS.staggerPumps;
/** What the clock reads once the dispatch is over, and always under reduced motion. */
export const FINISHED_MS = 1e7;
/**
 * What an idle clock reads: before every departure, so the lorries wait at their depots, no road
 * is drawn and every place shows its no-pump depth. The plan is on screen but not yet sent.
 */
export const IDLE_MS = -1;
/** Window edges meaning "since before the dispatch" and "for good". */
export const ALWAYS_BEFORE = -1e9;
export const ALWAYS_AFTER = 1e9;
/** Points a lorry is drawn at along its road: one per frame at 60 fps over 1.2 s. */
export const LORRY_SAMPLES = 64;

/** When the lorry with this dispatch order leaves its depot, in ms after the dispatch starts. */
export function departMs(order: number): number {
  return Math.max(0, order) * STAGGER_MS;
}

/** When it reaches its place. The gauge (M34) drains from here. */
export function arriveMs(order: number): number {
  return departMs(order) + TRAVEL_MS;
}

/** From the first departure to the last arrival: how long the layers keep asking to be drawn. */
export function dispatchSpanMs(nLegs: number): number {
  return nLegs > 0 ? arriveMs(nLegs - 1) : 0;
}

/**
 * The shared clock of one dispatch. It starts the first time a layer draws with it - so a map
 * that takes a second to open does not lose the first second of the drive - or when the screen
 * starts it, whichever is first; `onStart` tells the screen, which times the gauges (M34) and the
 * arrival markers (M35) from the same instant. A new dispatch is a new clock.
 */
export class DispatchClock {
  private started: number | null = null;

  constructor(
    private readonly options: {
      reduced?: boolean;
      /**
       * The plan has not been sent: the clock never starts and reads {@link IDLE_MS}, under
       * reduced motion too, because waiting at the depot is a state, not a motion. Optimise
       * replaces it with a running clock (section 8, M33 trigger "Optimise / dispatch").
       */
      idle?: boolean;
      onStart?: (startMs: number) => void;
    } = {},
  ) {}

  /** `performance.now()` at the start; null until the first draw or `start()`. */
  get startMs(): number | null {
    return this.started;
  }

  get reduced(): boolean {
    return Boolean(this.options.reduced);
  }

  get idle(): boolean {
    return Boolean(this.options.idle);
  }

  /** Start now if not started; idempotent. Returns the start, or NaN on an idle clock. */
  start(now: number = performance.now()): number {
    if (this.idle) return Number.NaN;
    if (this.started === null) {
      this.started = now;
      const notify = this.options.onStart;
      // Deck calls this from its render loop; the screen's state is set outside it.
      if (notify) queueMicrotask(() => notify(now));
    }
    return this.started;
  }

  /** Milliseconds since the dispatch began, starting the clock on first read. */
  elapsed(now: number = performance.now()): number {
    if (this.idle) return IDLE_MS;
    if (this.reduced) return FINISHED_MS;
    return now - this.start(now);
  }
}
