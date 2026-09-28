/**
 * The landing page's sections below the hero (SPEC.md 7.1, items 2, 3, 5, 7, 8 and 10 here; 4
 * and 9 in their own files, re-exported below).
 *
 * Server components where they can be: the landing page's LCP budget (2.5 s) is easier to keep
 * when most of it ships as HTML. The parts that need the client - the hero's scrub loop, the proof
 * counters, the cycle diagram's beams and timings, and the roadmap's tracing beam - live in their
 * own files.
 *
 * Every claim here is one the rest of the repo can back: the engine names match `services/`, the
 * data sources match what `services/city` actually downloads, and the landscape table's rows come
 * from the blueprint's references rather than from memory.
 */

import Link from "next/link";
import type { Route } from "next";

import { ENGINE_DIAGRAMS, type EngineName } from "@/components/landing/engine-diagrams";
import { BODY, H2, LEAD, SECTION } from "@/components/landing/styles";
import { navAccessibleName, navItem } from "@/lib/nav";

// The cycle diagram (item 4, motion M3) and the roadmap (item 9, motion M5) live in their own
// files: the cycle is a client component that fetches its timings, and the roadmap carries the
// tracing beam. Re-exported so the page imports every section from one place.
export { TheCycle } from "@/components/landing/the-cycle";
export { Roadmap } from "@/components/landing/roadmap";

/* ---- 2. The gap ------------------------------------------------------------------------- */

/** An NWP cell over Mumbai beside a 30 m street with a dip under a rail bridge. */
function GapDiagram() {
  return (
    <svg
      viewBox="0 0 520 220"
      className="w-full max-w-[520px]"
      role="img"
      aria-label="A twelve-kilometre forecast cell beside a thirty-metre street section with a forty-centimetre dip under a rail bridge"
    >
      <rect x="8" y="20" width="200" height="180" fill="var(--deep)" stroke="var(--line)" />
      {[1, 2, 3].map((i) => (
        <line key={`v${i}`} x1={8 + i * 50} y1="20" x2={8 + i * 50} y2="200" stroke="var(--line)" />
      ))}
      {[1, 2, 3].map((i) => (
        <line
          key={`h${i}`}
          x1="8"
          y1={20 + i * 45}
          x2="208"
          y2={20 + i * 45}
          stroke="var(--line)"
        />
      ))}
      <rect x="58" y="65" width="50" height="45" fill="var(--tide-soft)" />
      <text x="8" y="14" className="fill-[var(--text-3)] text-[11px]">
        12 km forecast cell
      </text>
      <text x="58" y="216" className="fill-[var(--text-3)] text-[11px]">
        one number for all of Dadar
      </text>

      {/* The street */}
      <text x="300" y="14" className="fill-[var(--text-3)] text-[11px]">
        30 m street section
      </text>
      <path
        d="M300 120 L340 120 Q380 120 400 158 Q420 196 460 196 L512 196"
        fill="none"
        stroke="var(--line-strong)"
        strokeWidth="2"
      />
      <path
        d="M340 120 Q380 120 400 158 Q420 196 460 196 L460 200 L340 200 Z"
        fill="var(--depth-4)"
        opacity="0.55"
      />
      <rect x="392" y="60" width="14" height="96" fill="var(--deep)" stroke="var(--line)" />
      <text x="300" y="216" className="fill-[var(--text-3)] text-[11px]">
        45 cm under the rail bridge
      </text>
    </svg>
  );
}

export function TheGap() {
  return (
    <section className={SECTION}>
      <div className="mx-auto grid max-w-[1200px] items-start gap-10 lg:grid-cols-2">
        <div>
          <h2 className={H2}>Forecasts stop at 12 km. Streets flood at 30 m.</h2>
          <p className={LEAD}>
            India&apos;s operational rainfall forecasts resolve a city as a handful of grid cells. A
            cell covering all of Dadar gets one number.
          </p>
          <p className={`${BODY} mt-4`}>
            Water does not arrive at that resolution. It arrives on the road under a rail bridge
            that sits half a metre below its neighbours, through a drain nobody has surveyed, at an
            hour the tide happens to be high. A ward-level warning cannot tell an ambulance which
            underpass to avoid, because it does not know underpasses exist.
          </p>
          <p className={`${BODY} mt-4`}>
            VARUNA models the city at 30 m, couples the surface to a drain network inferred from
            roads and terrain, and answers the question a dispatcher actually asks: which street,
            how deep, and when.
          </p>
        </div>
        <GapDiagram />
      </div>
    </section>
  );
}

/* ---- 3. Four ways a street floods ------------------------------------------------------- */

const WAYS = [
  {
    title: "Pluvial",
    body: "Rain falls faster than the drains can take it. The water never reaches a river; it sits in the road.",
    example: "Hindmata junction, Dadar East, in almost every heavy spell.",
  },
  {
    title: "Fluvial",
    body: "A river or nullah overtops and spills into the streets beside it.",
    example: "The Mithi at Kurla and Kalina, 26 July 2005 and since.",
  },
  {
    title: "Tidal lock",
    body: "A high tide holds the outfall shut. The drains have capacity and nowhere to put it, so the water comes back up through the manholes.",
    example:
      "Mumbai's coastal outfalls at spring high tide, which is why the tide table is on the console.",
  },
  {
    title: "Invisible drainage",
    body: "The pipe is there and blocked. Nothing on any map says so, and the street floods where the model says it should not.",
    example: "What VARUNA-Pulse learns from traffic anomalies and citizen reports.",
  },
];

export function FourWays() {
  return (
    <section className={SECTION}>
      <div className="mx-auto max-w-[1200px]">
        <h2 className={H2}>Four ways a street floods</h2>
        <p className={LEAD}>
          They need different physics, and a model that only knows one of them will be confidently
          wrong about the other three.
        </p>
        <dl className="mt-10 flex flex-col divide-y divide-line border-t border-line">
          {WAYS.map((way) => (
            <div key={way.title} className="grid gap-3 py-6 lg:grid-cols-[16ch_1fr_1fr] lg:gap-8">
              <dt className="text-h3 text-text">{way.title}</dt>
              <dd className={BODY}>{way.body}</dd>
              <dd className="text-small text-text-3">{way.example}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

/* ---- 5. Six engines ---------------------------------------------------------------------- */

const ENGINES: { name: EngineName; job: string; why: string; span: string }[] = [
  {
    name: "Pulse",
    job: "The city reveals its own drains",
    why: "Every flood is an experiment somebody already ran. A traffic feed collapsing on one street and not its neighbour is a measurement of a pipe nobody has surveyed, and an ensemble Kalman filter turns a monsoon's worth of them into a blockage map.",
    span: "md:col-span-2 lg:col-span-3 lg:row-span-2",
  },
  {
    name: "Twin",
    job: "Surface and sewer, solved together",
    why: "Local-inertial shallow water at 30 m, coupled to a head-driven 1D drain model through inlet capture and surcharge. The manhole that fountains is the same manhole the drain solver pressurised.",
    span: "lg:col-span-3",
  },
  {
    name: "Flash",
    job: "Three hours of city in milliseconds",
    why: "A reservoir cascade calibrated to the Twin's own runs, so a what-if answers while the question is still on screen. Its measured error is printed beside every answer.",
    span: "lg:col-span-3",
  },
  {
    name: "Sky",
    job: "Radar into a rain ensemble",
    why: "pySTEPS on calibrated reflectivity: 20 members, three hours, five-minute steps.",
    span: "lg:col-span-2",
  },
  {
    name: "Route",
    job: "Prediction into an ambulance route",
    why: "Time-dependent Dijkstra, costed at the depth the vehicle will meet when it arrives.",
    span: "lg:col-span-2",
  },
  {
    name: "Command",
    job: "One screen at three in the morning",
    why: "Depth on streets, alerts, pumps, reachability - and a label on every simplification.",
    span: "lg:col-span-2",
  },
];

/**
 * The second sentence is revealed on hover or keyboard focus where the device can hover (7.1), and
 * always shown where it cannot, so a phone never hides it. The reveal is an instant switch, not a
 * transition: section 8 has no row for it, and 7.1 rules out a scale bounce. It stays in the
 * accessibility tree either way, because it is hidden with opacity and not removed.
 */
export function SixEngines() {
  return (
    <section className={SECTION}>
      <div className="mx-auto max-w-[1200px]">
        <h2 className={H2}>Six engines</h2>
        <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-6">
          {ENGINES.map((engine) => {
            const Diagram = ENGINE_DIAGRAMS[engine.name];
            return (
              <article
                key={engine.name}
                data-engine={engine.name}
                tabIndex={0}
                className={`group flex flex-col rounded-panel border border-line bg-deep p-5 outline-none focus-visible:ring-2 focus-visible:ring-tide ${engine.span}`}
              >
                <div className="mb-4">
                  <Diagram />
                </div>
                <p className="font-display text-h2 font-semibold tracking-display text-text">
                  {engine.name}
                </p>
                <p className="mt-1 text-h3 text-text-2">{engine.job}</p>
                <p
                  data-engine-why
                  className="mt-4 text-small text-text-2 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-focus-within:opacity-100 [@media(hover:hover)]:group-hover:opacity-100"
                >
                  {engine.why}
                </p>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ---- 7. Where VARUNA sits ---------------------------------------------------------------- */

const LANDSCAPE = [
  { system: "IFLOWS-Mumbai", scale: "Ward, 6–72 h", drains: "No", learns: "No" },
  { system: "C-FLOWS (Chennai)", scale: "Basin, hours to days", drains: "Partial", learns: "No" },
  { system: "IIT-B Mumbai Flood", scale: "Ward to sub-ward", drains: "Partial", learns: "No" },
  { system: "IMD nowcasts", scale: "Rainfall only, 0–3 h", drains: "No", learns: "No" },
  { system: "Google Flood Hub", scale: "Riverine, global", drains: "No", learns: "No" },
  { system: "VARUNA", scale: "Street, 30 m, 0–3 h", drains: "Inferred and learned", learns: "Yes" },
];

const LANDSCAPE_COLUMNS = [
  { key: "scale", label: "Resolution and horizon" },
  { key: "drains", label: "Drainage" },
  { key: "learns", label: "Learns from events" },
] as const;

/**
 * A table from `sm` up, and one short definition list per system below it, so a 390 px phone reads
 * the same comparison without scrolling sideways (7.1 AC). Both are in the HTML and CSS picks one,
 * so there is no layout shift and no script; the table keeps its own overflow container for any
 * width where it still does not fit.
 */
export function Landscape() {
  return (
    <section className={SECTION}>
      <div className="mx-auto max-w-[1200px]">
        <h2 className={H2}>Where VARUNA sits</h2>
        <blockquote className="mt-6 max-w-[64ch] border-l-2 border-tide pl-4 text-h3 text-text-2">
          The strategic layer tells a city that a ward will flood tomorrow. VARUNA is the tactical
          layer: which street, how deep, and when, for the next three hours.
        </blockquote>
        <ul
          data-landscape="list"
          className="mt-10 flex flex-col divide-y divide-line border-t border-line sm:hidden"
        >
          {LANDSCAPE.map((row) => (
            <li key={row.system} className="py-4">
              <p className="text-body text-text">{row.system}</p>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                {LANDSCAPE_COLUMNS.map((column) => (
                  <div key={column.key} className="contents">
                    <dt className="text-small text-text-3">{column.label}</dt>
                    <dd className="text-small text-text-2">{row[column.key]}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
        <div data-landscape="table" className="mt-10 hidden overflow-x-auto sm:block">
          <table className="w-full min-w-[560px] border-collapse">
            <thead>
              <tr className="border-b border-line text-left">
                {["System", ...LANDSCAPE_COLUMNS.map((column) => column.label)].map((h) => (
                  <th key={h} className="px-3 py-3 text-small font-medium text-text-2">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {LANDSCAPE.map((row) => (
                <tr key={row.system} className="border-b border-line last:border-b-0">
                  <td className="px-3 py-3 text-body text-text">{row.system}</td>
                  {LANDSCAPE_COLUMNS.map((column) => (
                    <td key={column.key} className="px-3 py-3 text-body text-text-2">
                      {row[column.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

/* ---- 8. Data we use ---------------------------------------------------------------------- */

const SOURCES = [
  { name: "Copernicus GLO-30 DEM", status: "Public", note: "30 m terrain, downloaded per city" },
  {
    name: "OpenStreetMap",
    status: "Public",
    note: "Roads, buildings, waterways, hospitals, stations",
  },
  {
    name: "ESA WorldCover",
    status: "Public",
    note: "10 m land cover, for imperviousness and runoff",
  },
  {
    name: "BMC and news archives",
    status: "Public",
    note: "29 sourced ground-truth pins, each with a URL",
  },
  {
    name: "IMD Doppler radar volumes",
    status: "Requested from MoES",
    note: "The prototype reconstructs the storm instead",
  },
  {
    name: "BMC drain GIS",
    status: "Requested from MoES",
    note: "The prototype infers the network and learns it",
  },
  {
    name: "Radar reflectivity frames",
    status: "Synthetic in the prototype",
    note: "Storm designer calibrated to published gauge totals",
  },
  {
    name: "Traffic speeds and citizen reports",
    status: "Synthetic in the prototype",
    note: "Labelled everywhere they appear on screen",
  },
  {
    name: "Mobile pump inventory",
    status: "Synthetic in the prototype",
    note: "Twelve pumps at plausible depots, labelled",
  },
];

export function DataSources() {
  return (
    <section className={SECTION}>
      <div className="mx-auto max-w-[1200px]">
        <h2 className={H2}>What the data actually is</h2>
        <p className={LEAD}>
          Three categories, and nothing moves between them quietly. Anything synthetic carries the
          word on screen, next to the number it produced.
        </p>
        <ul className="mt-10 flex flex-col divide-y divide-line border-t border-line">
          {SOURCES.map((source) => (
            <li
              key={source.name}
              className="flex flex-col gap-2 py-4 sm:flex-row sm:items-baseline sm:gap-6"
            >
              <span className="min-w-[26ch] text-body text-text">{source.name}</span>
              <span className="w-fit shrink-0 rounded-chip border border-line bg-well px-2 py-0.5 text-micro text-text-2">
                {source.status}
              </span>
              <span className="text-small text-text-3">{source.note}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ---- 10. Team and footer ---------------------------------------------------------------- */

/**
 * Every operator screen is listed by its Sanskrit name and gloss ("Nadi, drain health", "Drishti,
 * command console"), from the same rail entry the console's nav reads (ADR-0085), so the footer
 * can never name a screen the bar does not. The public map and the API explorer are not on the
 * rail and keep their English names.
 */
const screenLink = (id: string) => ({
  href: navItem(id).href,
  label: navAccessibleName(navItem(id)),
  // The Sanskrit word is a proper noun a translator must leave alone; the gloss is not.
  name: navItem(id).label,
});

const LINKS: { href: string; label: string; name?: string }[] = [
  screenLink("console"),
  screenLink("drains"),
  screenLink("route"),
  screenLink("alerts"),
  screenLink("pumps"),
  screenLink("whatif"),
  screenLink("replay"),
  screenLink("verify"),
  { href: "/map", label: "Public map" },
  screenLink("onboard"),
  { href: "/api", label: "API explorer" },
];

/** The public repository, as `git remote -v` names it. */
export const REPOSITORY_URL = "https://github.com/shahul-royalss/varuna";

/**
 * The six roles from the blueprint, section 12.1, each with what it owns in this prototype. The
 * names of the people in them are not in the repository, so none are shown: a role with an
 * invented name is exactly the placeholder section 6.9 bans (and rule 7 applies to people too).
 */
export const TEAM_ROLES = [
  {
    role: "Nowcast lead (Sky)",
    owns: "Radar QC, Z–R, gauge merging, the pySTEPS ensemble and rain verification",
  },
  {
    role: "Hydrodynamics lead (Twin 2D)",
    owns: "DEM conditioning, the shallow-water solver, boundaries and mass balance",
  },
  {
    role: "Drainage and assimilation lead (graph and Pulse)",
    owns: "Drain synthesis, the EnKF, the traffic-anomaly detector and the drain-health product",
  },
  {
    role: "ML lead (Flash)",
    owns: "Training runs, the reduced-order emulator, uncertainty and attribution",
  },
  {
    role: "Backend and infrastructure lead (Route and platform)",
    owns: "The bus, FastAPI, routing, tiles and CI",
  },
  {
    role: "Frontend and product lead (Command and pitch)",
    owns: "The console, the public map, the demo script and the verification report",
  },
] as const;

export function Footer() {
  return (
    <footer className="border-t border-line px-6 py-12 sm:px-12 lg:px-24">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-8">
        <div>
          <p className="text-h3 text-text">Team VIT, in six roles</p>
          <dl className="mt-4 grid gap-x-8 gap-y-3 md:grid-cols-2 lg:grid-cols-3">
            {TEAM_ROLES.map((entry) => (
              <div key={entry.role}>
                <dt className="text-small font-medium text-text">{entry.role}</dt>
                <dd className="text-small text-text-2">{entry.owns}</dd>
              </div>
            ))}
          </dl>
        </div>
        <nav aria-label="Screens" className="flex flex-wrap gap-x-6 gap-y-2">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              // Next's typed routes want a literal; these come from a list, and every one of them
              // is a route in this app.
              href={link.href as Route}
              className="text-small text-text-2 underline"
            >
              {link.name ? (
                <>
                  <span translate="no">{link.name}</span>
                  {link.label.slice(link.name.length)}
                </>
              ) : (
                link.label
              )}
            </Link>
          ))}
          <a
            href={REPOSITORY_URL}
            className="text-small text-text-2 underline"
            rel="noopener noreferrer"
          >
            Source on GitHub
          </a>
        </nav>
        <p className="max-w-[72ch] text-small text-text-3">
          VARUNA, for the Smart India Hackathon 2026: problem statement SIH26085, Ministry of Earth
          Sciences, Team VIT. Every number on these screens comes from a run the engines computed;
          every simplification against the blueprint is listed in the repository and labelled where
          it is visible.
        </p>
      </div>
    </footer>
  );
}
