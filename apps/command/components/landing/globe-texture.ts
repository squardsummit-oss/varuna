/**
 * The photographic Earth the two globe sequences (M26, M27) are painted on.
 *
 * **Why it exists.** `globe-paint.ts` draws a *diagram* of the planet - country polygons filled
 * with `--well` on a `--deep` disc. It is elegant and it reads as a chart, which is exactly wrong
 * for the one moment on the site that is supposed to say "this is the real world, and we are about
 * to land on one street of it". This module draws the same geometry as a photograph: NASA's Blue
 * Marble imagery, sampled per pixel through the *inverse* of the same morphing projection that
 * `globe-intro.tsx` uses for its vectors, so the picture and the outlines register exactly.
 *
 * **Why a shader and not an image.** The sequence is not a spinning sphere with a texture on it -
 * it is a continuous family of map projections, interpolated between the orthographic and the
 * equirectangular raw projections (see `morphProjection`). There is no mesh to texture and no
 * library that knows that projection, so the only way to paint imagery into it is to run the
 * inverse per pixel. That is what {@link EARTH_FRAGMENT_SOURCE} does, and what
 * {@link invertMorphedProjection} does on the CPU so the maths can be tested against d3's own
 * forward projection (`globe-texture.test.ts`). The GLSL is a line-by-line transcription of that
 * function; **it is not itself executed by any test**, because jsdom has no WebGL.
 *
 * **The inversion.** The forward map, for morph parameter `t`, is
 *
 *     rawX = (1 - t)·cos φ·sin λ + t·λ        rawY = (1 - t)·sin φ + t·φ
 *
 * in the frame rotated so the camera centre is at (0, 0). `rawY` is monotone in φ over the whole
 * pole-to-pole range for every `t`, so φ is recovered by bisection and polished by Newton. Given
 * φ, `rawX` is monotone in λ only out to
 *
 *     Λ(t, φ) = π                    when t ≥ (1 - t)·cos φ
 *             = acos(−t / ((1 − t)·cos φ))   otherwise
 *
 * which at `t = 0` is exactly π/2 - the near hemisphere, the visible half of a globe - and at
 * `t = 1` is π, the whole flat map. Outside |rawX| > rawX(Λ) the pixel is off the planet. That one
 * expression is what gives the limb its shape at every point of the morph without a special case.
 *
 * **Treatment, not a photograph.** A full-brightness Blue Marble would light up a page whose whole
 * argument is a monsoon night (SPEC.md 6.1, and 6.9 bans a light theme). The console already
 * settled this question for Esri's aerial imagery - it is "drawn dim under an `--ink` scrim, so the
 * city is a ground the water sits on rather than a photograph the water is drawn over" (ADR-0034) -
 * and the same treatment is applied here: a day/night gain, then a scrim toward `--ink`. No colour
 * is written in this file; the three it needs are read from the stylesheet by the component and
 * handed over as uniforms, exactly as `globe-paint.ts` does for its canvas.
 *
 * **The terminator is real.** The sun direction is the subsolar point of the replay's own instant,
 * 2 July 2019 08:40 IST - the cycle the alerts, pumps and route screens are pinned to - computed by
 * {@link subsolarPoint} from Spencer's (1971) declination and equation-of-time series, not chosen
 * to look nice. At that instant the subsolar point is over the Philippine Sea, so India is in
 * morning light and the Atlantic the approach opens on is in night. That the sequence therefore
 * turns *out of the dark into the sunrise over India* is a gift of the date, not a decision.
 */

import {
  MUMBAI_LAT,
  MUMBAI_LON,
  VIEW_H,
  VIEW_W,
  type GlobeFrame,
} from "@/components/landing/globe-paint";

/**
 * The committed Blue Marble texture: 4096 x 2048 equirectangular, 704 KB.
 *
 * Committed rather than fetched from NASA at runtime for the same reason the TopoJSON is
 * (SPEC.md 17: the finale runs with the venue's network off). Provenance, licence and sha256 are
 * in `THIRD_PARTY_NOTICES.md` § 2.
 */
export const EARTH_TEXTURE_URL = "/earth-bluemarble-4096.jpg";

/** Degrees to radians, and back. */
const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;

/**
 * The instant whose sunlight the globe is lit by: 2 July 2019 08:40 IST, which is 03:10 UTC.
 *
 * 08:40 rather than the 06:40 the console opens on because at 06:40 Mumbai sits within a couple of
 * degrees of the terminator and the subject of the whole sequence would be in shadow. 08:40 is the
 * cycle the demo script's ambulance, alert and pump beats all use, so it is the replay's own
 * instant either way.
 */
export const SUN_INSTANT = new Date(Date.UTC(2019, 6, 2, 3, 10, 0));

/**
 * Where the sun is directly overhead at `when`, in degrees.
 *
 * Spencer (1971) "Fourier series representation of the position of the sun", as reproduced in the
 * NOAA solar-position notes: a truncated Fourier series in the day angle for the declination and
 * for the equation of time. Good to roughly 0.2° in declination and half a minute in the equation
 * of time, which is two orders of magnitude finer than anything this shading can show. It is here
 * rather than a pair of hand-typed constants so that the number on screen is computed from the
 * replay's date (SPEC.md rule 6) and can be checked by a test.
 */
export function subsolarPoint(when: Date): { lon: number; lat: number } {
  const startOfYear = Date.UTC(when.getUTCFullYear(), 0, 1);
  const hours = when.getUTCHours() + when.getUTCMinutes() / 60 + when.getUTCSeconds() / 3600;
  const dayOfYear = Math.floor((when.getTime() - startOfYear) / 86_400_000);
  // Spencer's day angle: the fraction of the year, with the time of day folded in.
  const g = ((2 * Math.PI) / 365) * (dayOfYear + (hours - 12) / 24);

  const declination =
    0.006918 -
    0.399912 * Math.cos(g) +
    0.070257 * Math.sin(g) -
    0.006758 * Math.cos(2 * g) +
    0.000907 * Math.sin(2 * g) -
    0.002697 * Math.cos(3 * g) +
    0.00148 * Math.sin(3 * g);

  // Minutes by which apparent solar time runs ahead of mean solar time.
  const equationOfTime =
    229.18 *
    (0.000075 +
      0.001868 * Math.cos(g) -
      0.032077 * Math.sin(g) -
      0.014615 * Math.cos(2 * g) -
      0.040849 * Math.sin(2 * g));

  // The sun is overhead where apparent solar time is noon; the Earth turns 15° an hour.
  let lon = -15 * (hours + equationOfTime / 60 - 12);
  lon -= 360 * Math.floor((lon + 180) / 360);
  return { lon, lat: declination * RAD };
}

/** A unit vector through the point (lon, lat) in degrees, in the Earth-fixed frame. */
export function unitVector(lon: number, lat: number): [number, number, number] {
  const phi = lat * DEG;
  const lambda = lon * DEG;
  const c = Math.cos(phi);
  return [c * Math.cos(lambda), c * Math.sin(lambda), Math.sin(phi)];
}

/**
 * The inverse of the morphed projection, on the CPU: a screen point in the projection's own raw
 * units back to (lon, lat) in degrees, or null where the point is off the planet.
 *
 * This is the reference the fragment shader transcribes. `centre` is the frame centre in degrees,
 * the same pair `GlobeFrame.centre` carries and the same one `globe-intro.tsx` negates into
 * `projection.rotate([-lon, -lat, 0])`.
 *
 * `rawX` and `rawY` are the projected coordinates *before* scale and translate, which is to say
 * `(x - translateX) / scale` and `(translateY - y) / scale`.
 */
export function invertMorphedProjection(
  t: number,
  centre: readonly [number, number],
  rawX: number,
  rawY: number,
): { lon: number; lat: number; lambdaRotated: number; phiRotated: number } | null {
  const halfPi = Math.PI / 2;
  const yMax = 1 - t + t * halfPi;
  if (!(Math.abs(rawY) <= yMax)) return null;

  // rawY is strictly increasing in phi for every t in [0, 1], so bisection cannot miss; Newton
  // then takes the last few digits. Fourteen halvings of [-pi/2, pi/2] leave 1.9e-4 rad.
  const yOf = (phi: number) => (1 - t) * Math.sin(phi) + t * phi;
  let lo = -halfPi;
  let hi = halfPi;
  for (let i = 0; i < 14; i += 1) {
    const mid = 0.5 * (lo + hi);
    if (yOf(mid) < rawY) lo = mid;
    else hi = mid;
  }
  let phi = 0.5 * (lo + hi);
  for (let i = 0; i < 3; i += 1) {
    const slope = (1 - t) * Math.cos(phi) + t;
    phi -= (yOf(phi) - rawY) / Math.max(slope, 1e-6);
  }
  phi = Math.min(Math.max(phi, -halfPi), halfPi);

  // How far east and west of the frame centre the projection stays single-valued: the near
  // hemisphere on a globe, the whole world on a flat map, and everything between.
  const a = (1 - t) * Math.cos(phi);
  const lambdaMax =
    t >= a ? Math.PI : Math.acos(Math.min(Math.max(-t / Math.max(a, 1e-12), -1), 1));
  const xOf = (lambda: number) => a * Math.sin(lambda) + t * lambda;
  const xMax = xOf(lambdaMax);
  if (!(Math.abs(rawX) <= xMax)) return null;

  lo = -lambdaMax;
  hi = lambdaMax;
  for (let i = 0; i < 14; i += 1) {
    const mid = 0.5 * (lo + hi);
    if (xOf(mid) < rawX) lo = mid;
    else hi = mid;
  }
  let lambda = 0.5 * (lo + hi);
  for (let i = 0; i < 3; i += 1) {
    const slope = a * Math.cos(lambda) + t;
    lambda -= (xOf(lambda) - rawX) / Math.max(slope, 1e-6);
  }
  lambda = Math.min(Math.max(lambda, -lambdaMax), lambdaMax);

  // Undo d3's rotation. `projection.rotate([-centreLon, -centreLat, 0])` composes a longitude
  // shift with a tilt about the y axis; this is `rotationPhiGamma(deltaPhi, 0).invert` followed by
  // `rotationLambda(deltaLambda).invert`, with gamma zero so the gamma terms drop out.
  const deltaLambda = -centre[0] * DEG;
  const deltaPhi = -centre[1] * DEG;
  const cosDelta = Math.cos(deltaPhi);
  const sinDelta = Math.sin(deltaPhi);
  const cosPhi = Math.cos(phi);
  const x = Math.cos(lambda) * cosPhi;
  const y = Math.sin(lambda) * cosPhi;
  const z = Math.sin(phi);
  const unrotatedLambda = Math.atan2(y, x * cosDelta + z * sinDelta);
  const unrotatedPhi = Math.asin(Math.min(Math.max(z * cosDelta - x * sinDelta, -1), 1));

  let lon = (unrotatedLambda - deltaLambda) * RAD;
  lon -= 360 * Math.floor((lon + 180) / 360);
  return {
    lon,
    lat: unrotatedPhi * RAD,
    lambdaRotated: lambda,
    phiRotated: phi,
  };
}

/**
 * The three colours the shader needs. Read from the stylesheet by the component that owns the
 * canvas and handed over, so that no colour literal is written here (SPEC.md rule 9 / 6.2).
 */
export interface EarthPalette {
  /** `--ink`: the scrim that seats the imagery in the app's background. */
  ink: [number, number, number];
  /** `--deep`: the unlit floor the night side falls toward. */
  deep: [number, number, number];
  /** `--tide`: the atmospheric rim. Brand teal rather than a literal sky blue - see below. */
  tide: [number, number, number];
}

/**
 * Turns a CSS colour into three 0-1 channels, in sRGB with no linearisation.
 *
 * The tokens are plain hex, which the regular expression handles without a DOM. Anything else -
 * `rgb()`, `oklch()`, a named colour - is handed to a 1 x 1 canvas, which is the only parser in a
 * browser that is guaranteed to agree with the browser. If both fail the caller gets null and
 * drops the imagery rather than painting a wrong colour.
 */
export function cssColorToRgb(value: string): [number, number, number] | null {
  const text = value.trim();
  // An empty string is what `getPropertyValue` returns for a custom property with no stylesheet
  // behind it, which is every test run. Answering it here keeps jsdom's unimplemented 2D context
  // out of the path entirely rather than making it log for nothing.
  if (text === "") return null;
  const hex = /^#([0-9a-fA-F]{3,8})$/.exec(text);
  if (hex) {
    const digits = hex[1];
    const expand = (s: string) => parseInt(s.length === 1 ? s + s : s, 16) / 255;
    if (digits.length === 3 || digits.length === 4) {
      return [expand(digits[0]), expand(digits[1]), expand(digits[2])];
    }
    if (digits.length === 6 || digits.length === 8) {
      return [expand(digits.slice(0, 2)), expand(digits.slice(2, 4)), expand(digits.slice(4, 6))];
    }
    return null;
  }
  if (typeof document === "undefined") return null;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.fillStyle = text;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
    return [r / 255, g / 255, b / 255];
  } catch {
    return null;
  }
}

/**
 * Loads the committed texture once per page and keeps the promise, so both sequences and a
 * remount share one decode. Resolves to null on any failure, which leaves the vector globe alone.
 *
 * **The decode must not happen on the main thread.** `earth-bluemarble-4096.jpg` is 704 KB on the
 * wire and 4096 x 2048 = 8.4 million pixels once decoded. An `HTMLImageElement` fires `onload`
 * when the bytes have arrived, *not* when they have been decoded, and `decoding = "async"` is a
 * hint a browser is free to ignore - so the decode was landing inside the `texImage2D` upload
 * below, synchronously, in the middle of the sequence. Measured on a dev build at 1440 x 900,
 * fronted tab, 12 node processes: one frame of **916.7 ms** and a mean of 43.2 fps over the first
 * six seconds against section 14's 55.
 *
 * `createImageBitmap` decodes off the main thread and hands back something `texImage2D` can take
 * without decoding anything, which is the whole fix. The element path stays for browsers without
 * it - and it is what jsdom exercises, since jsdom implements neither `createImageBitmap` nor
 * `HTMLImageElement.decode` - and it now awaits `decode()` where that exists, so even the
 * fallback pays the decode before the upload rather than during it.
 */
let earthImage: Promise<TexImageSource | null> | null = null;

export function ensureEarthImage(url: string = EARTH_TEXTURE_URL): Promise<TexImageSource | null> {
  if (earthImage) return earthImage;
  if (typeof window === "undefined" || typeof Image === "undefined") {
    return Promise.resolve(null);
  }
  earthImage = loadEarthTexture(url);
  return earthImage;
}

async function loadEarthTexture(url: string): Promise<TexImageSource | null> {
  if (typeof createImageBitmap === "function" && typeof fetch === "function") {
    try {
      const response = await fetch(url);
      if (response.ok) {
        // These bytes become a texture, not a picture on the page: no premultiplication and no
        // colour conversion, so what the shader samples is what the file holds.
        return await createImageBitmap(await response.blob(), {
          premultiplyAlpha: "none",
          colorSpaceConversion: "none",
        });
      }
    } catch {
      // A refused fetch, a browser whose `createImageBitmap` cannot take a blob, an OOM on an
      // 8-megapixel bitmap: all of them fall through to the element, which always works.
    }
  }
  return loadEarthElement(url);
}

function loadEarthElement(url: string): Promise<HTMLImageElement | null> {
  return new Promise<HTMLImageElement | null>((resolve) => {
    let image: HTMLImageElement;
    try {
      image = new Image();
    } catch {
      resolve(null);
      return;
    }
    image.decoding = "async";
    image.onload = () => {
      // `decode()` resolves once the pixels exist, so the upload has nothing left to do. Its
      // rejection is not a failure worth reporting: the element loaded, and `texImage2D` will
      // decode it the old way.
      const done = () => resolve(image);
      if (typeof image.decode === "function") image.decode().then(done, done);
      else done();
    };
    image.onerror = () => resolve(null);
    try {
      image.src = url;
    } catch {
      resolve(null);
    }
  });
}

/** Only for tests: forgets the cached decode so each case starts from nothing. */
export function resetEarthImageForTests(): void {
  earthImage = null;
}

/**
 * How much of the picture the photograph carries, 0 to 1, for a frame of either sequence.
 *
 * It is 1 wherever the imagery has something to say and 0 where it does not. The one place it does
 * not is the approach's final act: `SCALE_AOI` puts about 1.6° of longitude across the frame, and
 * 1.6° of a 4096-pixel-wide texture is **eighteen pixels**. Rather than magnify eighteen pixels
 * across a screen and call it photorealistic, the imagery leaves during the first part of that act
 * and hands the frame back to the vector treatment, which is crisp at any scale and is what the
 * AOI box and the city map behind it are drawn in. The handover is over by the time the frame is
 * tighter than the texture can honestly fill.
 *
 * `ready` is whether the texture has decoded; when it has not, this is 0 and nothing changes.
 */
export function photoAmount(frame: GlobeFrame, ready: boolean): number {
  if (!ready) return 0;
  // `aoi` runs 0 to 1 across the arrival act. Half of it is enough of a handover to be unnoticed
  // and short enough that the blur never arrives.
  const leaving = Math.min(Math.max(frame.aoi / 0.5, 0), 1);
  return 1 - leaving;
}

/** What the component drives each animation frame. */
export interface EarthPainter {
  /** Paint one frame. `photo` is {@link photoAmount}; at 0 the canvas is cleared. */
  frame(frame: GlobeFrame, photo: number): void;
  /** CSS pixel size of the canvas. */
  resize(width: number, height: number): void;
  dispose(): void;
}

const VERTEX_SOURCE = `#version 300 es
// A single triangle covering the clip volume; no buffers, no attributes.
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
`;

/**
 * The fragment shader: {@link invertMorphedProjection} per pixel, then the treatment.
 *
 * Exported so a test can assert the things about it that can be asserted without a GPU - that it
 * declares the uniforms the painter sets, and that it contains no colour literal.
 */
export const EARTH_FRAGMENT_SOURCE = `#version 300 es
precision highp float;

uniform sampler2D uEarth;
// rawX = gl_FragCoord.x * uRawMap.x + uRawMap.y ; rawY = gl_FragCoord.y * uRawMap.x + uRawMap.z
uniform vec3 uRawMap;
uniform float uMorph;      // 0 a globe, 1 a flat equirectangular map
// The camera rotation, pre-trigonometry: cos and sin of -centreLat, then +centreLon in radians.
uniform vec3 uCamera;
uniform vec3 uSun;         // unit vector through the subsolar point, Earth-fixed
uniform vec3 uInk;
uniform vec3 uDeep;
uniform vec3 uTide;
uniform float uPhoto;      // how much of the picture the photograph carries

out vec4 fragColor;

const float PI = 3.141592653589793;

/**
 * The treatment, in one place. A monsoon-night page cannot carry a daylight photograph at full
 * brightness (SPEC.md 6.1 and 6.9), so the lit side is held a little under and the night side
 * falls most of the way to the panel colour, and the whole thing then sits under an --ink scrim.
 * These are the same three moves the console makes on Esri's imagery (ADR-0034).
 */
const float DAY_GAIN = 0.94;
const float NIGHT_GAIN = 0.15;
const float SCRIM = 0.20;
/** The limb glow: an inner rim on the sphere and a halo just outside it. */
const float RIM_INNER = 0.42;
const float RIM_OUTER = 0.34;
const float HALO_FALLOFF = 24.0;

float rawYOf(float phi, float t) { return (1.0 - t) * sin(phi) + t * phi; }
float rawXOf(float lambda, float a, float t) { return a * sin(lambda) + t * lambda; }

void main() {
  float t = uMorph;
  float rx = gl_FragCoord.x * uRawMap.x + uRawMap.y;
  float ry = gl_FragCoord.y * uRawMap.x + uRawMap.z;
  float pxPerRaw = 1.0 / max(uRawMap.x, 1e-12);

  // --- latitude in the rotated frame -----------------------------------------------------
  float yMax = (1.0 - t) + t * PI * 0.5;
  float lo = -PI * 0.5;
  float hi = PI * 0.5;
  for (int i = 0; i < 14; i++) {
    float mid = 0.5 * (lo + hi);
    if (rawYOf(mid, t) < ry) { lo = mid; } else { hi = mid; }
  }
  float phi = 0.5 * (lo + hi);
  for (int i = 0; i < 3; i++) {
    float slope = (1.0 - t) * cos(phi) + t;
    phi -= (rawYOf(phi, t) - ry) / max(slope, 1e-6);
  }
  phi = clamp(phi, -PI * 0.5, PI * 0.5);

  // --- longitude in the rotated frame, and the limb --------------------------------------
  float a = (1.0 - t) * cos(phi);
  float lambdaMax = (t >= a) ? PI : acos(clamp(-t / max(a, 1e-12), -1.0, 1.0));
  float xMax = rawXOf(lambdaMax, a, t);
  lo = -lambdaMax;
  hi = lambdaMax;
  for (int i = 0; i < 14; i++) {
    float mid = 0.5 * (lo + hi);
    if (rawXOf(mid, a, t) < rx) { lo = mid; } else { hi = mid; }
  }
  float lambda = 0.5 * (lo + hi);
  for (int i = 0; i < 3; i++) {
    float slope = a * cos(lambda) + t;
    lambda -= (rawXOf(lambda, a, t) - rx) / max(slope, 1e-6);
  }
  lambda = clamp(lambda, -lambdaMax, lambdaMax);

  // One pixel of softness on the limb instead of a stair-stepped edge. No discard: the halo
  // outside wants the same fragments, and a branchless shader is kinder to a laptop GPU.
  float coverage = min(
    clamp((xMax - abs(rx)) * pxPerRaw, 0.0, 1.0),
    clamp((yMax - abs(ry)) * pxPerRaw, 0.0, 1.0)
  );

  // --- undo the camera rotation -----------------------------------------------------------
  // d3 composes a longitude shift with a tilt about the y axis; this is the tilt's inverse (with
  // gamma zero, so its terms drop out) followed by the shift's, which is one subtraction.
  float cosDelta = uCamera.x;
  float sinDelta = uCamera.y;
  float centreLon = uCamera.z;
  float cosPhi = cos(phi);
  float x = cos(lambda) * cosPhi;
  float y = sin(lambda) * cosPhi;
  float z = sin(phi);
  float lat = asin(clamp(z * cosDelta - x * sinDelta, -1.0, 1.0));
  float lon = atan(y, x * cosDelta + z * sinDelta) + centreLon;
  lon -= 2.0 * PI * floor((lon + PI) / (2.0 * PI));

  // --- sample ------------------------------------------------------------------------------
  vec2 uv = vec2(lon / (2.0 * PI) + 0.5, 0.5 - lat / PI);
  // The antimeridian is a discontinuity in u; left alone it makes the mip selector pick the
  // coarsest level for one column of pixels and draws a grey seam down the globe. Unwrapping the
  // derivative removes it.
  vec2 ddx = dFdx(uv);
  vec2 ddy = dFdy(uv);
  ddx.x -= round(ddx.x);
  ddy.x -= round(ddy.x);
  vec3 earth = textureGrad(uEarth, uv, ddx, ddy).rgb;

  // --- light -------------------------------------------------------------------------------
  float cosLat = cos(lat);
  vec3 normal = vec3(cosLat * cos(lon), cosLat * sin(lon), sin(lat));
  float day = smoothstep(-0.16, 0.32, dot(normal, uSun));
  vec3 lit = earth * mix(NIGHT_GAIN, DAY_GAIN, day);
  lit = mix(lit, uDeep, (1.0 - day) * 0.45);
  lit = mix(lit, uInk, SCRIM);

  // --- atmosphere --------------------------------------------------------------------------
  // cos of the angular distance from the frame centre: 1 under the camera, 0 at the limb. There
  // is no limb once the map is flat, so both rims fade out with the morph.
  float facing = clamp(cosPhi * cos(lambda), 0.0, 1.0);
  float sphere = 1.0 - t;
  lit += uTide * pow(1.0 - facing, 5.0) * RIM_INNER * sphere;

  float radius = length(vec2(rx, ry));
  float halo = sphere * (1.0 - coverage) * exp(-max(radius - 1.0, 0.0) * HALO_FALLOFF) * RIM_OUTER;

  float bodyAlpha = coverage * uPhoto;
  float haloAlpha = halo * uPhoto;
  float alpha = clamp(bodyAlpha + haloAlpha, 0.0, 1.0);
  vec3 rgb = alpha > 0.0 ? (lit * bodyAlpha + uTide * haloAlpha) / alpha : vec3(0.0);
  fragColor = vec4(rgb, alpha);
}
`;

/** Compiles one shader, returning null and logging nothing on failure (the caller degrades). */
function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/**
 * Sets up WebGL2 on `canvas` and returns something that can paint a {@link GlobeFrame}, or null.
 *
 * Null means the browser has no WebGL2, the shader would not compile, the texture never decoded,
 * or a colour could not be read - in every one of those the caller simply keeps the vector globe,
 * which is the picture that shipped before this module existed. WebGL1 is not attempted: its
 * `textureGrad` needs an extension and it has no `gl_VertexID`, and a second shader to cover the
 * few per cent of browsers without WebGL2 would be more code than the fallback it replaces.
 */
export function createEarthPainter(
  canvas: HTMLCanvasElement,
  image: TexImageSource,
  palette: EarthPalette,
  width: number,
  height: number,
  dpr: number,
): EarthPainter | null {
  let gl: WebGL2RenderingContext | null = null;
  try {
    gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: false,
      powerPreference: "low-power",
    }) as WebGL2RenderingContext | null;
  } catch {
    gl = null;
  }
  if (!gl) return null;

  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX_SOURCE);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, EARTH_FRAGMENT_SOURCE);
  const program = vertex && fragment ? gl.createProgram() : null;
  if (!vertex || !fragment || !program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }

  const texture = gl.createTexture();
  if (!texture) {
    gl.deleteProgram(program);
    return null;
  }
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image);
  // Longitude wraps and latitude does not, which is exactly REPEAT and CLAMP_TO_EDGE. The globe
  // acts minify the texture heavily (4096 pixels of longitude across a 600-pixel disc), so it
  // needs mip levels; `textureGrad` above is what keeps the antimeridian out of them.
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.generateMipmap(gl.TEXTURE_2D);

  const uniform = (name: string) => gl.getUniformLocation(program, name);
  const uEarth = uniform("uEarth");
  const uRawMap = uniform("uRawMap");
  const uMorph = uniform("uMorph");
  const uCamera = uniform("uCamera");
  const uSun = uniform("uSun");
  const uInk = uniform("uInk");
  const uDeep = uniform("uDeep");
  const uTide = uniform("uTide");
  const uPhoto = uniform("uPhoto");

  const subsolar = subsolarPoint(SUN_INSTANT);
  const sun = unitVector(subsolar.lon, subsolar.lat);

  gl.useProgram(program);
  gl.uniform1i(uEarth, 0);
  gl.uniform3f(uSun, sun[0], sun[1], sun[2]);
  gl.uniform3f(uInk, palette.ink[0], palette.ink[1], palette.ink[2]);
  gl.uniform3f(uDeep, palette.deep[0], palette.deep[1], palette.deep[2]);
  gl.uniform3f(uTide, palette.tide[0], palette.tide[1], palette.tide[2]);
  gl.disable(gl.DEPTH_TEST);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  let cssWidth = width;
  let cssHeight = height;
  const size = () => {
    canvas.width = Math.max(1, Math.round(cssWidth * dpr));
    canvas.height = Math.max(1, Math.round(cssHeight * dpr));
  };
  size();

  const context = gl;
  return {
    frame(globeFrame: GlobeFrame, photo: number) {
      context.viewport(0, 0, canvas.width, canvas.height);
      context.clear(context.COLOR_BUFFER_BIT);
      if (photo <= 0 || cssWidth <= 0 || cssHeight <= 0) return;

      // `xMidYMid slice`, the same fit the SVG and the vector canvas use, collapsed into the
      // affine map from a device pixel straight to the projection's raw units. Cover, not fit:
      // the hero is full-bleed, so the view box overflows the frame and is cropped.
      const fit = Math.max(cssWidth / VIEW_W, cssHeight / VIEW_H);
      const offsetX = (cssWidth - VIEW_W * fit) / 2;
      const offsetY = (cssHeight - VIEW_H * fit) / 2;
      const perPixel = 1 / (dpr * fit * globeFrame.scale);
      const originX = -(offsetX / fit + VIEW_W / 2) / globeFrame.scale;
      const originY =
        VIEW_H / (2 * globeFrame.scale) -
        (canvas.height / dpr - offsetY) / (fit * globeFrame.scale);

      context.useProgram(program);
      context.activeTexture(context.TEXTURE0);
      context.bindTexture(context.TEXTURE_2D, texture);
      context.uniform3f(uRawMap, perPixel, originX, originY);
      context.uniform1f(uMorph, globeFrame.alpha);
      const deltaPhi = -globeFrame.centre[1] * DEG;
      context.uniform3f(
        uCamera,
        Math.cos(deltaPhi),
        Math.sin(deltaPhi),
        globeFrame.centre[0] * DEG,
      );
      context.uniform1f(uPhoto, photo);
      context.drawArrays(context.TRIANGLES, 0, 3);
    },
    resize(nextWidth: number, nextHeight: number) {
      cssWidth = nextWidth;
      cssHeight = nextHeight;
      size();
    },
    dispose() {
      context.deleteTexture(texture);
      context.deleteProgram(program);
    },
  };
}

/** Mumbai, re-exported so a test can assert the city is in daylight at the replay's instant. */
export const MUMBAI = { lon: MUMBAI_LON, lat: MUMBAI_LAT };
