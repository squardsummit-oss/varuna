/**
 * `CityMap`'s camera: the fit, the fly-to and who owns the view (SPEC.md 7.2, motion M10).
 *
 * Not a layer, and kept under `layers/` only because MO1 moved it out of `city-map.tsx` with the
 * layer builders so the host stays a composer. Nothing here changed in the move.
 */

import { FlyToInterpolator, WebMercatorViewport } from "@deck.gl/core";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { boundsCentre, cityBounds, type Bbox } from "../basemap";
import type { MapFocus } from "./types";
import { DUR_MS, FLY_TO_CURVE } from "@/lib/motion";

const MUMBAI_CENTRE = boundsCentre(cityBounds("mumbai"));

/** Used only until the container has been measured; the fit below replaces it on that frame. */
const INITIAL_VIEW = { ...MUMBAI_CENTRE, zoom: 11.4, bearing: 0, pitch: 0 };

export type ViewState = typeof INITIAL_VIEW & {
  transitionDuration?: number;
  transitionInterpolator?: FlyToInterpolator;
};

/** Framing margin in pixels, so the coast and the northern subways are not against the edge.
 *
 * Deliberately small. The layer panel, the legend and the hotspot rail all float *over* the map,
 * so the city already has furniture around it; a wide margin as well leaves it swimming in a
 * panel it is meant to fill. */
const FIT_PADDING = 12;

/** A fit margin in pixels: one number for every side, or one per side. */
export type FitPadding = number | { top: number; right: number; bottom: number; left: number };

/** The share of the map's width or height a fit always keeps for the frame itself. */
const MIN_FIT_ROOM = 0.4;

/**
 * `padding` per side, shrunk where it would leave the frame less than 40 % of the map's width or
 * height. A screen passes the furniture it floats over its map - the console's layer column is
 * 412 px - and on a narrow window that alone would leave nothing to fit into (deck's `fitBounds`
 * answers a negative room with a nonsense zoom), so both sides of an axis give back in proportion.
 */
export function usablePadding(
  width: number,
  height: number,
  padding: FitPadding = FIT_PADDING,
): { top: number; right: number; bottom: number; left: number } {
  const p =
    typeof padding === "number"
      ? { top: padding, right: padding, bottom: padding, left: padding }
      : padding;
  const squeeze = (a: number, b: number, span: number): [number, number] => {
    const lo = Math.max(0, a);
    const hi = Math.max(0, b);
    const most = span * (1 - MIN_FIT_ROOM);
    if (lo + hi <= most || lo + hi === 0) return [lo, hi];
    const k = most / (lo + hi);
    return [lo * k, hi * k];
  };
  const [left, right] = squeeze(p.left, p.right, width);
  const [top, bottom] = squeeze(p.top, p.bottom, height);
  return { top, right, bottom, left };
}

interface InteractionState {
  isDragging?: boolean;
  isPanning?: boolean;
  isZooming?: boolean;
  isRotating?: boolean;
}

export interface CityCameraInput {
  /** What to fit: the drawn extent, see `drawnExtent`. */
  frame: Bbox;
  focus: MapFocus | null;
  reducedMotion: boolean;
  /** The hero map is read-only and never reports camera changes. */
  interactive: boolean;
  /**
   * 3D mode (task P6.15): the camera takes SPEC.md 6.7's 55 degree pitch, and goes back to the
   * pitch it had when 3D is turned off.
   *
   * It **cuts** rather than tilts, with or without reduced motion: section 8 has no row for a
   * pitch change, and a motion that is not in the catalogue is not allowed on screen (rule 9).
   * The reduced-motion branch the task asks for is therefore the only branch there is.
   */
  threeD?: boolean;
  /** Pitch to use in 3D; the caller passes `PHOTOREAL_PITCH` (SPEC.md 6.7 fixes it at 55). */
  pitch3d?: number;
  /**
   * A change re-arms the fit: whatever the operator did to the camera is dropped and `frame` is
   * framed again, as a cut (section 8 has no row for a re-frame, so there is no flight). For an
   * explicit ask - the console's full view, a new route - and never for a scrub or a data load:
   * a camera somebody has moved is otherwise theirs, whatever the data does.
   */
  fitKey?: string | number | null;
  /**
   * The one `fitKey` whose camera is kept when the map leaves it and handed back when the map
   * returns to it, as a cut: the console's own layout, so leaving full view puts back the view the
   * operator had, panned or not. Arriving at any other key re-fits, as before. Absent, nothing is
   * kept, and every screen that does not pass it behaves exactly as it did.
   */
  keepViewOf?: string | number | null;
  /**
   * The fit's margin, per side when the screen floats panels over its map (the console's layer
   * column and scrub card). Defaults to 12 px all round, so a screen that passes nothing is
   * framed exactly as before.
   */
  fitPadding?: FitPadding;
}

export interface CityCamera {
  containerRef: RefObject<HTMLDivElement | null>;
  size: { width: number; height: number } | null;
  viewState: ViewState;
  onViewStateChange:
    ((change: { viewState: ViewState; interactionState?: InteractionState }) => void) | undefined;
}

export function useCityCamera({
  frame,
  focus,
  reducedMotion,
  interactive,
  threeD = false,
  pitch3d = 55,
  fitKey = null,
  keepViewOf = null,
  fitPadding = FIT_PADDING,
}: CityCameraInput): CityCamera {
  // **The camera is controlled.** It used to be handed to deck.gl as `initialViewState` on the
  // theory that deck would notice a changed object and move itself. It does not: `initialViewState`
  // is read once, when the view is created, and the fit computed from the first `ResizeObserver`
  // callback arrives a frame *after* that. So the map stayed at the placeholder zoom for ever -
  // the city sat in a corner of the console with the panel half empty, and on `/drains` the pipes
  // rendered as a thumbnail in the middle of nothing.
  //
  // The fit is **derived, not stored**. Only two things are state: the measured container and the
  // camera once somebody moves it. Everything else is computed during render, which is what makes
  // a resize or a data load re-frame on its own - no effect, no stale copy of the view.
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [camera, setCamera] = useState<ViewState | null>(null);
  // Whether the operator has taken the camera. deck reports *every* view-state change through
  // `onViewStateChange`, including ones it makes itself when the canvas is resized, so "camera is
  // not null" is not the same question as "somebody moved it" - treating them as the same left
  // `/route` framed on the whole city after a resize instead of on the trip it had just drawn.
  // Only a drag, a zoom, a rotate or a fly-to sets this; until then the fit owns the view.
  const [owned, setOwned] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Keyed on the values, not the object: a screen that writes its padding inline hands a new
  // object every render, and a new fit every render would be a new view state every render.
  const paddingKey = JSON.stringify(fitPadding);
  const fitted = useMemo<ViewState>(() => {
    if (!size || size.width < 2 || size.height < 2) return INITIAL_VIEW;
    const [[west, south], [east, north]] = frame;
    const padding = usablePadding(size.width, size.height, JSON.parse(paddingKey) as FitPadding);
    const view = new WebMercatorViewport({ width: size.width, height: size.height }).fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding },
    );
    return {
      longitude: view.longitude,
      latitude: view.latitude,
      zoom: view.zoom,
      bearing: 0,
      pitch: threeD ? pitch3d : 0,
    };
  }, [size, frame, threeD, pitch3d, paddingKey]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      setSize((current) =>
        current &&
        Math.abs(current.width - box.width) < 1 &&
        Math.abs(current.height - box.height) < 1
          ? current
          : { width: box.width, height: box.height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Motion M10: a 900 ms flight to the selected hotspot, a jump cut under reduced motion.
  //
  // Keyed on `focus.key` rather than the coordinates, so selecting the same row twice flies
  // again: after panning away, "show me Hindmata" should still take you back. A flight counts as
  // moving the camera, so the fit stops claiming it afterwards.
  const focusKey = focus?.key ?? null;
  useEffect(() => {
    if (!focus) return;
    // Everything the flight does not name it inherits from wherever the camera already is - and
    // when it has never been moved, from `INITIAL_VIEW`, whose bearing and pitch are the zero the
    // fit produces anyway. So the fallback costs nothing and a rotated camera keeps its rotation.
    //
    // `set-state-in-effect` is disabled here, and only here, with a reason. The rule exists to
    // stop effects being used to recompute state that could have been derived, and the fit above
    // takes that advice - it is derived, not stored. This is the other thing entirely: `focus` is
    // an imperative command from the hotspot rail ("fly here now"), and deck.gl's camera is the
    // external system it commands. Deriving it instead would pin the camera to the focus and the
    // operator could never pan away from a selected hotspot. One render per click is the cost.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOwned(true);
    setCamera((current) => ({
      ...(current ?? INITIAL_VIEW),
      longitude: focus.lon,
      latitude: focus.lat,
      zoom: focus.zoom ?? 14,
      transitionDuration: reducedMotion ? 0 : DUR_MS.flight,
      transitionInterpolator: reducedMotion
        ? undefined
        : new FlyToInterpolator({ curve: FLY_TO_CURVE }),
    }));
    // `focus` is a fresh object each render; `focusKey` is the identity that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, reducedMotion]);

  // 3D on: a camera the operator has moved takes the 3D pitch, remembering the pitch it had. 3D
  // off: it goes back to that pitch, so the flat map returns as it was left. A camera nobody has
  // moved follows the fit, which carries the right pitch already.
  //
  // Adjusted during render on the prop change - React's documented alternative to an effect,
  // which would paint one frame at the old pitch before correcting it.
  const [pitchMemo, setPitchMemo] = useState({ threeD, before: 0 });
  if (pitchMemo.threeD !== threeD) {
    const before = threeD ? (camera?.pitch ?? 0) : pitchMemo.before;
    setPitchMemo({ threeD, before });
    if (camera) {
      setCamera({
        ...camera,
        pitch: threeD ? pitch3d : pitchMemo.before,
        transitionDuration: 0,
        transitionInterpolator: undefined,
      });
    }
  }

  // A new `fitKey` hands the camera back to the fit. Adjusted during render, like the pitch above,
  // so the frame that paints is already the re-fitted one.
  //
  // Leaving `keepViewOf` stashes the camera first - the operator's own view, or null when the fit
  // had it, since the fit is derived and will frame the same box again at the same size. Coming
  // back to it hands that view back instead of re-fitting; the stash is spent either way.
  const [seenFitKey, setSeenFitKey] = useState(fitKey);
  const [kept, setKept] = useState<{ view: ViewState | null } | null>(null);
  if (seenFitKey !== fitKey) {
    setSeenFitKey(fitKey);
    const leaving = keepViewOf !== null && seenFitKey === keepViewOf;
    const returning = keepViewOf !== null && fitKey === keepViewOf ? kept : null;
    setKept(leaving ? { view: owned ? camera : null } : null);
    if (returning?.view) {
      setOwned(true);
      setCamera({ ...returning.view, transitionDuration: 0, transitionInterpolator: undefined });
    } else {
      setOwned(false);
      setCamera(null);
    }
  }

  const viewState = owned ? (camera ?? fitted) : fitted;

  // deck reports every camera change here, and a controlled view only moves because this writes
  // it back. `interactionState` is what separates the operator's own drags and zooms from deck's
  // internal adjustments; only the former take the camera.
  const onViewStateChange = interactive
    ? ({
        viewState: next,
        interactionState: how,
      }: {
        viewState: ViewState;
        interactionState?: InteractionState;
      }) => {
        if (how?.isDragging || how?.isPanning || how?.isZooming || how?.isRotating) {
          setOwned(true);
        }
        setCamera(next);
      }
    : undefined;

  return { containerRef, size, viewState, onViewStateChange };
}
