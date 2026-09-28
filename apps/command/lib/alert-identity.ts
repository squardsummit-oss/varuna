/**
 * What makes an alert "new" to the alert centre (motion M16, SPEC.md sections 7.5 and 8).
 *
 * An alert id embeds the run that raised it, so no two cycles share one: diffing ids would call
 * every card on every cycle new and slide the whole queue in. What an officer means by a new alert
 * is a street or hotspot that was not being warned about at that level a moment ago. So the
 * identity is the scope, the place (the hotspot id when the register names one, else the street)
 * and the level. The same street at the same level across two runs is the same alert; the same
 * street stepping up from moderate to severe is a new one.
 */

import type { RunAlert } from "@/lib/api/alerts";

export type AlertIdentityInput = Pick<RunAlert, "scope" | "hotspotId" | "areaDesc" | "level">;

/** The cycle-independent identity of one alert: scope, place and level. */
export function alertIdentity(alert: AlertIdentityInput): string {
  return `${alert.scope}|${alert.hotspotId ?? alert.areaDesc}|${alert.level}`;
}

/** The identities of a queue, for remembering what the screen showed. */
export function alertIdentities(alerts: readonly AlertIdentityInput[]): Set<string> {
  return new Set(alerts.map(alertIdentity));
}

/**
 * Ids of the alerts in `next` whose identity was not in the queue shown before.
 *
 * `shown` is null until the screen has shown a queue at all: the first queue a screen renders is
 * not news, so nothing in it is new.
 */
export function freshAlertIds(
  shown: ReadonlySet<string> | null,
  next: readonly (AlertIdentityInput & Pick<RunAlert, "id">)[],
): Set<string> {
  if (shown === null) return new Set();
  return new Set(next.filter((alert) => !shown.has(alertIdentity(alert))).map((alert) => alert.id));
}
