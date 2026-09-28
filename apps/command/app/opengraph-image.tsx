import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

export const alt =
  "VARUNA - every street, three hours early: flood depth on Mumbai's streets at +120 min on 2 July 2019";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/* The OG image is rendered by Satori without the stylesheet, so the tokens are literals here.
 * They are the same values as `tokens.json`. */
const INK = "#0A1020"; // lint-design-allow: og image has no stylesheet; equals --ink
const TIDE = "#2DD4BF"; // lint-design-allow: og image has no stylesheet; equals --tide
const TEXT = "#E3EAF6"; // lint-design-allow: og image has no stylesheet; equals --text
const TEXT_2 = "#A7B4CC"; // lint-design-allow: og image has no stylesheet; equals --text-2
const LINE = "#24314F"; // lint-design-allow: og image has no stylesheet; equals --line
/* The copy's wash, as the hero draws it: `--ink` fading to clear by the middle. */
const WASH =
  "linear-gradient(90deg, rgba(10,16,32,1) 45%, rgba(10,16,32,0.6) 54%, rgba(10,16,32,0) 63%)"; // lint-design-allow: og image has no stylesheet; --ink at 100/60/0 %

/**
 * The hero frame the image is built on (SPEC.md 7.1 AC5: "shows the hero frame with the
 * headline"): step 24, +120 min, the frame reduced motion holds on the landing page, from the
 * pre-rendered sequence in `public/hero/` (see `tests/e2e/landing-hero-frames.spec.ts`), whose
 * manifest names the run. The footer says what the picture is - a reconstructed replay of 2 July
 * 2019 at +120 min - so a shared link never shows water without saying where it came from.
 */
const HERO_STEP = 24;

interface Manifest {
  run_id: string;
  cycle_ts: string;
  step_min: number;
  frames: string[];
}

/**
 * The team's logo, emblem over the lettered name, from `public/brand/` (see
 * `components/varuna/wordmark.tsx`). Its background is transparent, so it sits on `--ink` here as
 * it does on the landing page. Null when the file is missing, and the card falls back to the name
 * in type rather than failing to render.
 */
async function brandLockup(): Promise<string | null> {
  try {
    const bytes = await readFile(join(process.cwd(), "public", "brand", "varuna-lockup.png"));
    return `data:image/png;base64,${bytes.toString("base64")}`;
  } catch {
    return null;
  }
}

/** The lockup's intrinsic proportions (640 x 497), drawn 200 px wide on the card. */
const LOCKUP_WIDTH = 200;
const LOCKUP_HEIGHT = Math.round((LOCKUP_WIDTH * 497) / 640);

async function heroFrame(): Promise<{ src: string; manifest: Manifest } | null> {
  try {
    const dir = join(process.cwd(), "public", "hero");
    const manifest = JSON.parse(await readFile(join(dir, "manifest.json"), "utf8")) as Manifest;
    const name = manifest.frames[HERO_STEP]?.split("/").pop();
    if (!name) return null;
    const bytes = await readFile(join(dir, name));
    return { src: `data:image/jpeg;base64,${bytes.toString("base64")}`, manifest };
  } catch {
    return null;
  }
}

export default async function OpenGraphImage() {
  const [frame, lockup] = await Promise.all([heroFrame(), brandLockup()]);
  const lead = frame ? HERO_STEP * frame.manifest.step_min : null;

  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        position: "relative",
        background: INK,
      }}
    >
      {frame ? (
        // eslint-disable-next-line @next/next/no-img-element -- Satori renders plain <img>.
        <img
          src={frame.src}
          alt=""
          width={1008}
          height={630}
          style={{ position: "absolute", top: 0, right: -260, width: 1008, height: 630 }}
        />
      ) : null}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          display: "flex",
          backgroundImage: WASH,
        }}
      />
      <div
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          width: "100%",
          height: "100%",
          padding: 64,
        }}
      >
        {lockup ? (
          // eslint-disable-next-line @next/next/no-img-element -- Satori renders plain <img>.
          <img
            src={lockup}
            alt="VARUNA"
            width={LOCKUP_WIDTH}
            height={LOCKUP_HEIGHT}
            style={{ width: LOCKUP_WIDTH, height: LOCKUP_HEIGHT, marginLeft: -8, marginTop: -16 }}
          />
        ) : (
          <div style={{ display: "flex", color: TEXT, fontSize: 42, fontWeight: 700 }}>VARUNA</div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          <div
            style={{
              color: TEXT,
              fontSize: 72,
              fontWeight: 700,
              letterSpacing: -2,
              lineHeight: 1.04,
              maxWidth: 620,
            }}
          >
            Every street. Three hours early.
          </div>
          <div style={{ color: TEXT_2, fontSize: 26, maxWidth: 560, lineHeight: 1.35 }}>
            Street-by-street flood depth for the next three hours, from Doppler radar.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderTop: `2px solid ${LINE}`,
            paddingTop: 22,
            color: TEXT_2,
            fontSize: 21,
          }}
        >
          <div style={{ display: "flex" }}>SIH 2026 - PS SIH26085 - Ministry of Earth Sciences</div>
          <div style={{ display: "flex", color: TIDE }}>
            {lead !== null
              ? `Reconstructed replay, 2 July 2019, +${lead} min`
              : "Reconstructed replay, 2 July 2019"}
          </div>
        </div>
      </div>
    </div>,
    size,
  );
}
