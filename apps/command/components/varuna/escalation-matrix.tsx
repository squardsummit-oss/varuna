import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface EscalationTier {
  id: string;
  /** Who is told. */
  recipient: string;
  /** What raises this tier (SPEC.md section 11.10 hysteresis and levels). */
  trigger: string;
  /** Channels used for this tier. */
  channel: string;
  /**
   * Alert levels that reach this tier when raised; empty when only an escalation does. Omitted,
   * the column is not drawn.
   */
  levels?: readonly ("severe" | "moderate" | "watch")[];
}

const LEVEL_WORD = { severe: "Severe", moderate: "Moderate", watch: "Watch" } as const;

/**
 * The escalation ladder as `config/escalation.yaml` holds it, ward officer to public, for a
 * screen that draws the matrix without asking the API (the /design page). The alert centre draws
 * the served file instead. Each trigger is the file's own words, which name the raise the
 * product computes (the Twin's street depth over the threshold on consecutive cycles), not a
 * probability.
 */
export const ESCALATION_MATRIX: readonly EscalationTier[] = [
  {
    id: "ward_officer",
    recipient: "Ward officer",
    trigger: "Watch raised in the ward: forecast street depth above 15 cm for two cycles running",
    channel: "Dashboard, WhatsApp",
    levels: ["watch", "moderate", "severe"],
  },
  {
    id: "control_room",
    recipient: "Control room",
    trigger: "Moderate raised, or a Watch escalated by the ward officer",
    channel: "Dashboard, WhatsApp, phone call",
    levels: ["moderate", "severe"],
  },
  {
    id: "police_traffic",
    recipient: "Police and traffic",
    trigger: "Severe raised, or escalated by the control room",
    channel: "WhatsApp, SMS, road-conditions feed",
    levels: ["severe"],
  },
  {
    id: "transit",
    recipient: "Transit (buses, suburban rail)",
    trigger: "Escalated by the control room for a bus corridor or a station approach",
    channel: "GTFS-RT service alert, WhatsApp",
    levels: [],
  },
  {
    id: "public",
    recipient: "Public",
    trigger: "Escalated by the control room once a Severe has persisted two cycles",
    channel: "Public map, SMS broadcast, CAP feed",
    levels: [],
  },
];

export interface EscalationMatrixProps {
  tiers?: readonly EscalationTier[];
  className?: string;
}

/**
 * Escalation matrix table (SPEC.md section 7.5): recipient, trigger and channel per tier, and
 * the levels that reach a tier on their own when the tiers carry them. The ids are the values
 * `POST /v1/alerts/{id}/escalate` takes, as `config/escalation.yaml` spells them.
 */
export function EscalationMatrix({ tiers = ESCALATION_MATRIX, className }: EscalationMatrixProps) {
  const showLevels = tiers.some((tier) => tier.levels !== undefined);
  return (
    <div className={cn("overflow-x-auto", className)}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[28%]">Recipient</TableHead>
            <TableHead>Trigger</TableHead>
            <TableHead className="w-[30%]">Channel</TableHead>
            {showLevels ? <TableHead className="w-[20%]">Reached when raised</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {tiers.map((tier, i) => (
            <TableRow key={tier.id} className="h-10">
              <TableCell className="type-small text-text font-medium whitespace-normal">
                <span className="num text-text-3 mr-2">{i + 1}</span>
                {tier.recipient}
              </TableCell>
              <TableCell className="type-small text-text-2 whitespace-normal">
                {tier.trigger}
              </TableCell>
              <TableCell className="type-small text-text-2 whitespace-normal">
                {tier.channel}
              </TableCell>
              {showLevels ? (
                <TableCell className="type-small text-text-2 whitespace-normal">
                  {tier.levels && tier.levels.length > 0
                    ? tier.levels.map((level) => LEVEL_WORD[level]).join(", ")
                    : "By escalation only"}
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
