"use client";

/**
 * The three proof numbers (SPEC.md 7.1 section 6, motion M4).
 *
 * Fetched from `/v1/verification`, with the committed `public/verification.json` as the fallback
 * so the landing page still shows real figures when the API is unreachable - which, on a venue's
 * network the morning of the finale, it may well be (SPEC.md 17).
 *
 * Each number rolls to its value the first time it scrolls into view (M4's "in view" trigger), and
 * is simply there under reduced motion (M4's "instant"). Rolling on arrival rather than on fetch
 * is the point: the section is far below the fold, and a count that finished while nobody was
 * looking is not a motion anyone saw.
 *
 * The numbers are not flattering, and they are shown as they are. A CSI of 0.22 next to an honest
 * account of what it means is worth more in front of MoES scientists than a rounder number nobody
 * can reproduce (rule 6).
 */

import { useEffect, useRef, useState } from "react";
import NumberFlow from "@number-flow/react";
import { useInView } from "motion/react";

import { apiUrl } from "@/lib/api/client";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";

interface Proof {
  csi: number | null;
  pod: number | null;
  leadMin: number | null;
  /** Hits flagged before the civic log, and hits flagged after it inside the pin's uncertainty. */
  hitsEarly: number | null;
  hitsAfter: number | null;
  pins: number;
  thresholdCm: number;
}

function read(body: Record<string, unknown>): Proof {
  const scores = (body.scores ?? {}) as Record<string, number | null>;
  const truth = (body.ground_truth ?? {}) as Record<string, number>;
  return {
    csi: scores.csi ?? null,
    pod: scores.pod ?? null,
    leadMin: scores.median_lead_min ?? null,
    hitsEarly: scores.n_hits_early ?? null,
    hitsAfter: scores.n_hits_after ?? null,
    pins: Number(truth.n_in_window ?? 0),
    thresholdCm: Number(body.headline_threshold_cm ?? 15),
  };
}

/** Where the proof footnote points: the limitations on /verify (7.10 AC). */
export const LIMITATIONS_HREF = "/verify#limitations";

export function Figure({
  value,
  suffix,
  label,
  note,
  decimals = 0,
}: {
  value: number | null;
  suffix?: string;
  label: string;
  note: string;
  decimals?: number;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const ref = useRef<HTMLParagraphElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const rounded = value === null ? null : Number(value.toFixed(decimals));
  return (
    <div className="flex flex-col gap-2">
      <p
        ref={ref}
        data-figure={label}
        className="num font-display text-display tracking-display text-text font-semibold"
      >
        {rounded === null ? (
          "—"
        ) : reducedMotion ? (
          rounded.toFixed(decimals)
        ) : (
          <NumberFlow
            // Held at zero until the figure is on screen, then rolled to the fetched value once.
            value={inView ? rounded : 0}
            format={{ minimumFractionDigits: decimals, maximumFractionDigits: decimals }}
          />
        )}
        {value !== null && suffix ? (
          <span className="text-h2 text-text-2 ml-1">{suffix}</span>
        ) : null}
      </p>
      <p className="text-h3 text-text">{label}</p>
      <p className="text-small text-text-2 max-w-[42ch]">{note}</p>
    </div>
  );
}

/**
 * The warning-time note, which has to say why the figure is a dash when it is one. The median is
 * taken over the pins VARUNA flagged *before* the civic log (`varuna_verify.event`); on an event
 * where every hit came after the log there is no such pin, and a bare dash under "Median warning
 * time" read as a number that failed to load.
 */
export function leadNote(proof: Proof | null): string {
  const base = "How long before the civic log VARUNA first called that street impassable.";
  if (!proof || proof.leadMin !== null || proof.hitsEarly !== 0 || !proof.hitsAfter) return base;
  const n = proof.hitsAfter;
  return (
    `None on this event: VARUNA flagged ${n === 1 ? "the one pin" : `all ${n} pins`} it caught ` +
    "after the city logged them, inside each pin's stated time uncertainty."
  );
}

export function Proof() {
  const [proof, setProof] = useState<Proof | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const live = await fetch(apiUrl("/v1/verification"), { signal: controller.signal });
        if (live.ok) {
          setProof(read((await live.json()) as Record<string, unknown>));
          return;
        }
      } catch {
        // Fall through to the committed copy; an unreachable API is a venue problem, not an error
        // this page should show a judge.
      }
      try {
        const cached = await fetch("/verification.json", { signal: controller.signal });
        if (cached.ok) setProof(read((await cached.json()) as Record<string, unknown>));
      } catch {
        // Nothing to show. The dashes below are the honest state.
      }
    })();
    return () => controller.abort();
  }, []);

  return (
    <section className="px-6 py-[72px] sm:px-12 lg:px-24 lg:py-[120px]">
      <div className="mx-auto max-w-[1200px]">
        <h2 className="font-display text-h1 tracking-display text-text max-w-[24ch] font-semibold">
          How we score ourselves
        </h2>
        <div className="mt-10 grid gap-10 md:grid-cols-3">
          <Figure
            value={proof?.pod ?? null}
            decimals={2}
            label={`Probability of detection at ${proof?.thresholdCm ?? 15} cm`}
            // A hit is a pin where the forecast crossed the threshold inside the pin's own time
            // uncertainty, before or after the log; the warning time below counts only "before".
            note={`Share of the sourced pins where VARUNA forecast more than ${proof?.thresholdCm ?? 15} cm, inside each pin's stated time uncertainty.`}
          />
          <Figure
            value={proof?.leadMin ?? null}
            suffix="min"
            label="Median warning time"
            note={leadNote(proof)}
          />
          <Figure
            value={proof?.pins ?? null}
            label="Ground-truth pins"
            note="Curated public records inside the forecast window, each with a source URL and a stated time uncertainty."
          />
        </div>
        <p className="text-small text-text-3 mt-10 max-w-[72ch]">
          Computed on a reconstructed replay of 2 July 2019 against sourced ground truth. The
          numbers are what the model achieved, not what we would like it to achieve.{" "}
          <a href={LIMITATIONS_HREF} className="text-tide underline">
            See how we score ourselves, and where we fall short
          </a>
          .
        </p>
      </div>
    </section>
  );
}
