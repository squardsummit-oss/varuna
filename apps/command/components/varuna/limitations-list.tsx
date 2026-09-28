import { cn } from "@/lib/utils";

/**
 * Blueprint section 15.1, "Limitations you should state before anyone asks", quoted as written
 * (docs/VARUNA_SIH2026_Blueprint.txt). These are UI copy, not fine print.
 */
export const LIMITATIONS: readonly string[] = [
  "Street-level depth accuracy is bounded by terrain data; open DEMs give pattern and timing, LiDAR gives centimetres.",
  "Radar-based nowcasting cannot foresee convective initiation; beyond ~90 minutes the system is probabilistic by nature.",
  "Inferred drain networks are hypotheses with uncertainty; the drain-health map is a ranking, not a survey.",
  "Traffic-as-sensor confounds (accidents, processions) are handled statistically, not perfectly; sensors and CCTV reduce ambiguity.",
  "Coastal storm-surge coupling uses IMD advisories rather than a full ocean model in V1.",
];

export interface LimitationsListProps {
  /** Anchor id so the landing footnote can link to `/verify#limitations`. */
  id?: string;
  className?: string;
}

/** The five limitations from the blueprint, anchored for deep links. */
export function LimitationsList({ id = "limitations", className }: LimitationsListProps) {
  const headingId = `${id}-heading`;
  return (
    <section id={id} aria-labelledby={headingId} className={cn("scroll-mt-6 space-y-3", className)}>
      <h2 id={headingId} className="type-h3 text-text">
        Limitations we state before anyone asks
      </h2>
      <p className="max-w-[72ch] type-small text-text-2">
        From the VARUNA blueprint, section 15.1. Each one is measured on this page as the scores
        arrive, not hidden behind them.
      </p>
      <ul className="max-w-[72ch] space-y-2">
        {LIMITATIONS.map((text) => (
          <li key={text} className="flex gap-3 type-body text-text">
            <span aria-hidden="true" className="mt-2.5 size-1.5 shrink-0 rounded-chip bg-tide" />
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
