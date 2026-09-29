import { afterEach, describe, expect, it } from "vitest";

import { colorsFor, hexToRgb } from "@varuna/tokens";

import { setTheme } from "@/lib/theme";

import * as offline from "@/lib/offline/basemap";

import * as palette from "../palette";
import {
  imageryOpacity,
  labelLayers,
  LABELS_URL,
  LABELS_URL_LIGHT,
  satelliteLayers,
} from "../../satellite";

afterEach(() => {
  setTheme("dark");
});

describe("map palette and the theme", () => {
  it("builds the dark constants exactly from the dark tokens", () => {
    const dark = palette.mapPalette("dark");
    expect(dark.DRY_STREET).toEqual([43, 58, 85, 235]);
    expect(dark.BUILDING_FILL).toEqual([17, 26, 46, 235]);
    expect(dark.BUILDING_LINE).toEqual([36, 49, 79, 170]);
    expect(dark.STREET_HIGHLIGHT).toEqual([227, 234, 246, 90]);
    expect(dark.NAIVE_ROUTE).toEqual([100, 116, 139, 235]);
    expect(dark.VARUNA_ROUTE).toEqual([45, 212, 191, 255]);
    expect(dark.ROUTE_CASING).toEqual([10, 16, 32, 235]);
    expect(dark.DIFF_UNCHANGED).toEqual([43, 58, 85, 190]);
    expect(dark.HOTSPOT_RING).toEqual([45, 212, 191, 130]);
    expect(dark.REACH_FILL).toEqual({
      5: [45, 212, 191, 115],
      10: [45, 212, 191, 71],
      15: [45, 212, 191, 36],
    });
    expect(dark.REACH_LINE).toEqual([45, 212, 191, 140]);
    expect(dark.ROUTE_COLOUR.alternate).toEqual([45, 212, 191, 150]);
    expect(dark.DRAIN_RAMP[0]).toEqual([62, 76, 110]);
    expect(dark.SHAFT_COLOUR).toEqual([51, 67, 106, 225]);
  });

  it("starts in dark, the constants every recorded fixture expects", () => {
    expect(palette.DRY_STREET).toEqual(palette.mapPalette("dark").DRY_STREET);
    expect(palette.ROUTE_COLOUR).toEqual(palette.mapPalette("dark").ROUTE_COLOUR);
  });

  it("switches the ground with the theme and leaves the water alone", () => {
    const passable = palette.PASSABLE;
    const impassable = palette.IMPASSABLE;
    setTheme("light");
    const light = colorsFor("light");
    expect(palette.DRY_STREET).toEqual([...hexToRgb(light["depth-dry"]), 235]);
    expect(palette.BUILDING_FILL).toEqual([...hexToRgb(light.deep), 235]);
    expect(palette.ROUTE_CASING).toEqual([...hexToRgb(light.ink), 235]);
    expect(palette.VARUNA_ROUTE).toEqual([...hexToRgb(light.tide), 255]);
    expect(palette.ROUTE_COLOUR.varuna).toBe(palette.VARUNA_ROUTE);
    expect(palette.PASSABLE).toBe(passable);
    expect(palette.IMPASSABLE).toBe(impassable);
    expect(palette.diffColour(0)).toEqual(palette.DIFF_UNCHANGED);

    setTheme("dark");
    expect(palette.DRY_STREET).toEqual([43, 58, 85, 235]);
  });

  it("moves the drain shafts to the light border token", () => {
    setTheme("light");
    expect(palette.SHAFT_COLOUR).toEqual([...hexToRgb(colorsFor("light")["line-strong"]), 225]);
  });
});

describe("imagery and reference labels in each theme", () => {
  it("keeps the dark treatment exactly and washes the photograph toward paper in light", () => {
    // Dark draws the aerial opaque under the --ink scrim, which is what it has always shown.
    expect(imageryOpacity("dark")).toBe(1);
    expect(imageryOpacity("dark", true)).toBe(1);
    expect(imageryOpacity("light")).toBe(0.3);
    expect(imageryOpacity("light", true)).toBeCloseTo(0.21, 10);
  });

  it("hands the tile layer's opacity to every tile it draws", () => {
    const [imagery] = satelliteLayers({ enabled: true, theme: "light" }) as {
      props: {
        opacity: number;
        renderSubLayers: (p: Record<string, unknown>) => { props: { opacity: number } };
      };
    }[];
    const tile = {
      boundingBox: [
        [72.8, 19.0],
        [72.9, 19.1],
      ],
    };
    const bitmap = imagery!.props.renderSubLayers({
      id: "t",
      data: null,
      tile,
      opacity: imagery!.props.opacity,
    });
    expect(bitmap.props.opacity).toBe(0.3);
    const [labels] = labelLayers({ enabled: true, theme: "light" }) as (typeof imagery)[];
    const label = labels!.props.renderSubLayers({ id: "l", data: null, tile, opacity: 0.5 });
    expect(label.props.opacity).toBe(0.5);
  });

  it("draws Esri's night labels in dark and its light grey reference in light", () => {
    const [dark] = labelLayers({ enabled: true, theme: "dark" }) as {
      id: string;
      props: { data: string };
    }[];
    const [light] = labelLayers({ enabled: true, theme: "light" }) as {
      id: string;
      props: { data: string };
    }[];
    expect(dark?.id).toBe("place-labels");
    expect(dark?.props.data).toBe(LABELS_URL);
    expect(light?.id).toBe("place-labels-light");
    expect(light?.props.data).toBe(LABELS_URL_LIGHT);
    expect(labelLayers({ enabled: false, theme: "light" })).toEqual([]);
  });
});

describe("offline basemap in each theme", () => {
  it("is the dark ground by default and a paper ground whose sea still shows in light", () => {
    expect(offline.offlineBasemapPalette("dark").WATER_FILL).toEqual(offline.WATER_FILL);
    setTheme("light");
    const light = colorsFor("light");
    expect(offline.WATER_FILL).toEqual([...hexToRgb(light.line), 255]);
    // The sea is not the land's colour: paper on paper would erase the coastline.
    expect(offline.WATER_FILL.slice(0, 3)).not.toEqual(hexToRgb(light.ink));
    expect(offline.basemapFillColor({ properties: { layerName: "water" } })).toBe(
      offline.WATER_FILL,
    );
    setTheme("dark");
    expect(offline.WATER_FILL).toEqual([23, 35, 59, 255]);
  });
});
