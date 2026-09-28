/**
 * Micro-diagrams for the six-engines bento (SPEC.md 7.1 item 5).
 *
 * Each is a small inline SVG that says what its engine does in one picture, drawn only in tokens
 * and only with each token's fixed meaning (6.2): pipes are blocked in the drain ramp, observations
 * in their Pulse colours, water in the depth ramp, the rain ensemble in chart colours with its
 * band at 20 %, the naive route dashed in `--naive` and VARUNA's in `--tide`.
 *
 * They are decorative (`aria-hidden`): every tile already says the same thing in words.
 */

import type { ReactElement } from "react";

export type EngineName = "Pulse" | "Twin" | "Flash" | "Sky" | "Route" | "Command";

const SVG = {
  "aria-hidden": true,
  fill: "none",
  focusable: false,
} as const;

/** A pipe network with one blocked pipe and the two observations that gave it away. */
function PulseDiagram() {
  const nodes: [number, number][] = [
    [16, 20],
    [72, 20],
    [128, 44],
    [184, 44],
    [240, 20],
    [72, 76],
    [128, 96],
    [240, 88],
  ];
  const pipes: [number, number][] = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 4],
    [5, 2],
    [6, 2],
    [7, 3],
  ];
  return (
    <svg
      {...SVG}
      viewBox="0 0 256 112"
      // Pulse is the largest tile (7.1), so its picture takes the tile's width rather than a corner.
      className="h-auto w-full max-w-[520px]"
      data-diagram="Pulse"
    >
      {pipes.map(([a, b]) => (
        <line
          key={`${a}-${b}`}
          x1={nodes[a][0]}
          y1={nodes[a][1]}
          x2={nodes[b][0]}
          y2={nodes[b][1]}
          stroke={a === 2 && b === 3 ? "var(--drain-3)" : "var(--drain-0)"}
          strokeWidth={a === 2 && b === 3 ? 4 : 2}
          strokeLinecap="round"
        />
      ))}
      {nodes.map(([x, y]) => (
        <circle
          key={`${x}-${y}`}
          cx={x}
          cy={y}
          r="4"
          fill="var(--deep)"
          stroke="var(--line-strong)"
        />
      ))}
      <circle cx="156" cy="24" r="5" fill="var(--obs-traffic)" />
      <circle cx="168" cy="68" r="5" fill="var(--obs-report)" />
    </svg>
  );
}

/** A street with water in its dip, and the manhole below it surcharging. */
function TwinDiagram() {
  return (
    <svg {...SVG} viewBox="0 0 160 64" className="h-16 w-auto" data-diagram="Twin">
      <path
        d="M4 24 L52 24 Q70 24 80 38 Q90 24 108 24 L156 24"
        stroke="var(--line-strong)"
        strokeWidth="2"
      />
      <path d="M60 30 Q70 26 80 38 Q90 26 100 30 Z" fill="var(--depth-2)" opacity="0.8" />
      <rect
        x="20"
        y="50"
        width="120"
        height="8"
        rx="4"
        stroke="var(--line-strong)"
        strokeWidth="1.5"
      />
      <line x1="80" y1="50" x2="80" y2="40" stroke="var(--surcharge)" strokeWidth="2" />
      <path d="M76 44 L80 39 L84 44" stroke="var(--surcharge)" strokeWidth="2" />
    </svg>
  );
}

/** Two reservoirs in cascade, the emulator's structure. */
function FlashDiagram() {
  return (
    <svg {...SVG} viewBox="0 0 160 64" className="h-16 w-auto" data-diagram="Flash">
      <rect
        x="8"
        y="8"
        width="44"
        height="36"
        rx="4"
        stroke="var(--line-strong)"
        strokeWidth="1.5"
      />
      <rect x="9" y="26" width="42" height="17" fill="var(--depth-1)" opacity="0.8" />
      <path d="M56 32 L70 32 L70 44 L84 44" stroke="var(--text-3)" strokeWidth="1.5" />
      <rect
        x="88"
        y="20"
        width="44"
        height="36"
        rx="4"
        stroke="var(--line-strong)"
        strokeWidth="1.5"
      />
      <rect x="89" y="44" width="42" height="11" fill="var(--depth-1)" opacity="0.8" />
      <path d="M136 50 L152 50" stroke="var(--text-3)" strokeWidth="1.5" />
    </svg>
  );
}

/** A rain ensemble fanning out with lead time. */
function SkyDiagram() {
  return (
    <svg {...SVG} viewBox="0 0 120 48" className="h-12 w-auto" data-diagram="Sky">
      <path d="M8 36 L112 8 L112 44 Z" fill="var(--chart-1)" opacity="0.2" />
      <path d="M8 36 L112 26" stroke="var(--chart-1)" strokeWidth="2" />
      <line x1="8" y1="44" x2="112" y2="44" stroke="var(--line)" />
    </svg>
  );
}

/** A naive route through a flooded street and VARUNA's route around it. */
function RouteDiagram() {
  return (
    <svg {...SVG} viewBox="0 0 120 48" className="h-12 w-auto" data-diagram="Route">
      <line x1="8" y1="24" x2="112" y2="24" stroke="var(--line)" strokeWidth="1.5" />
      <line x1="48" y1="24" x2="72" y2="24" stroke="var(--depth-4)" strokeWidth="4" />
      <path d="M8 24 L112 24" stroke="var(--naive)" strokeWidth="1.5" strokeDasharray="4 3" />
      <path d="M8 24 L36 24 L36 8 L84 8 L84 24 L112 24" stroke="var(--tide)" strokeWidth="2.5" />
    </svg>
  );
}

/** A console list: streets with their depth chips. */
function CommandDiagram() {
  const rows: [string, number][] = [
    ["var(--depth-4)", 56],
    ["var(--depth-2)", 44],
    ["var(--depth-1)", 32],
  ];
  return (
    <svg {...SVG} viewBox="0 0 120 48" className="h-12 w-auto" data-diagram="Command">
      <rect x="1" y="1" width="118" height="46" rx="6" stroke="var(--line)" />
      {rows.map(([colour, width], i) => (
        <g key={colour}>
          <circle cx="14" cy={12 + i * 12} r="3.5" fill={colour} />
          <rect x="24" y={10 + i * 12} width={width} height="4" rx="2" fill="var(--line-strong)" />
        </g>
      ))}
    </svg>
  );
}

export const ENGINE_DIAGRAMS: Record<EngineName, () => ReactElement> = {
  Pulse: PulseDiagram,
  Twin: TwinDiagram,
  Flash: FlashDiagram,
  Sky: SkyDiagram,
  Route: RouteDiagram,
  Command: CommandDiagram,
};
