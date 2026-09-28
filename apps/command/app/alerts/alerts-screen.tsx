"use client";

import { BellOff, ChevronDown, Send } from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { AlertDetails } from "@/components/varuna/alert-details";
import {
  ALERT_LEVELS,
  ALERT_LEVEL_LABELS,
  AlertLevelChip,
  type AlertLevel,
} from "@/components/varuna/alert-level-chip";
import { AlertRow } from "@/components/varuna/alert-row";
import { AppShell } from "@/components/varuna/app-shell";
import { CyclePicker, newestPerCycle, type BakedCycle } from "@/components/varuna/cycle-picker";
import { EmptyState } from "@/components/varuna/empty-state";
import { EscalationMatrix } from "@/components/varuna/escalation-matrix";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PhoneMock, type PhoneMessage } from "@/components/varuna/phone-mock";
import { SkeletonRows } from "@/components/varuna/skeleton";
import { alertIdentities, alertIdentity, freshAlertIds } from "@/lib/alert-identity";
import {
  alertMessages,
  alertPlace,
  alertStatus,
  isUnacknowledged,
  loadAlertCount,
  loadAlerts,
  loadDelivery,
  loadEscalation,
  loadSender,
  nextEscalation,
  sortByOnset,
  splitPending,
  summariseQueue,
  MAX_LISTED,
  type AlertSet,
  type DeliveryLog as DeliveryLogData,
  type EscalationStep,
  type PendingAlert,
  type RunAlert,
  type SenderStatus,
} from "@/lib/api/alerts";
import { apiUrl } from "@/lib/api/client";
import {
  describeRefusal,
  opsRefusal,
  postAlertAction,
  readPassphrase,
  sendAlertToPhone,
} from "@/lib/api/ops";
import { DEFAULT_CITY, cityFromSearch } from "@/lib/city";
import { formatCm, formatIst, formatTimeWithLead, minutesBetween } from "@/lib/format";
import { navItem } from "@/lib/nav";
import { useAlertChime } from "@/lib/sound";
import { useOpeningRun } from "@/lib/use-opening-run";
import { cn } from "@/lib/utils";

/** The tab holds no passphrase: the act is not sent, and the screen says where it is entered. */
const NO_PASSPHRASE =
  "This tab holds no desk passphrase. Enter it on the authority desk, then come back.";

const NO_FRESH: ReadonlySet<string> = new Set();

/** How many alerts each level shows before "Show all". */
const LEVEL_PREVIEW = 5;

/** How many messages the phone holds: the open alert, then what this cycle brought. */
const PHONE_MESSAGES = 4;

/**
 * The rule the product actually computes (`varuna_products.alerts`): a level is a candidate when
 * the Twin's own street depth is above its threshold for two 5-minute steps in a row, and it is
 * raised on the second consecutive cycle that says so. The raise P is 0 or 1 on a deterministic
 * run, so "P >= 0.6" described a probability the queue never used.
 */
const RULE =
  "Raised when the Twin's forecast depth on a street or hotspot stays above the level's threshold for two 5-minute steps in a row, on two consecutive cycles; cleared on the first cycle it no longer does. Each level is ordered by when the water arrives.";

const ALERTS_ROUTE = "/alerts";

/** A run from the registry, with the replay mode that decides Exercise or Actual. */
interface CycleRun extends BakedCycle {
  replayMode: string | null;
}

/** The phone's fallback text, for an API that does not serve the rendered WhatsApp card. */
function phoneText(alert: RunAlert): string {
  return [alert.headline, alert.instruction, alert.dispatchNote]
    .filter((part): part is string => Boolean(part))
    .join(". ")
    .replace(/\.\./g, ".");
}

function subscribeStorage(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function hasPassphraseNow(): boolean {
  return readPassphrase() !== null;
}

function noPassphrase(): boolean {
  return false;
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** The city's cycles, one run per cycle time, oldest first. */
function useCycles(city: string | undefined): CycleRun[] | null {
  const [cycles, setCycles] = useState<CycleRun[] | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const query = city ? `?city=${encodeURIComponent(city)}` : "";
    fetch(apiUrl(`/v1/runs${query}`), { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { runs: [] }))
      .then(
        (body: {
          runs?: {
            run_id: string;
            cycle_ts: string;
            created_at?: string;
            replay_mode?: string | null;
          }[];
        }) => {
          const runs: CycleRun[] = (body.runs ?? []).map((r) => ({
            runId: r.run_id,
            cycleTs: r.cycle_ts,
            massBalanceErr: null,
            createdAt: r.created_at ?? "",
            replayMode: r.replay_mode ?? null,
          }));
          setCycles(newestPerCycle(runs) as CycleRun[]);
        },
      )
      .catch(() => {
        if (!controller.signal.aborted) setCycles([]);
      });
    return () => controller.abort();
  }, [city]);
  return cycles;
}

/**
 * Alert centre (SPEC.md section 7.5). Two columns: the queue, and the ward officer's phone
 * beside it. A summary strip under the cycle picker says how many alerts each level holds (and
 * filters by them), the deepest place, the first onset and what raises next cycle.
 *
 * **Each alert is one row until it is asked about.** A row gives the level, the place, the peak,
 * where the desk has got with it and the window with its lead time. "See more" opens what to do,
 * who has been told and who is next, the timeline, the messages as rendered, the delivery rows
 * and the CAP 1.2 document, one alert at a time, with `?alert=` in the URL (M29). The card this
 * replaced was 210 px with the channels and both actions on it: 2.3 alerts per 900 px fold.
 *
 * **The hysteresis is across cycles** (SPEC.md 11.10), as `RULE` states it. A place that
 * crossed a level once and raises it next cycle if it holds is shown on the row it already has,
 * and otherwise in `PendingGroups`, which never calls a place raised beyond the 60 listed "not
 * raised yet".
 *
 * **The queue is capped at 60 and says so.** Level chips count every alert the cycle raised
 * (`n_raised_by_level`: Severe 13, Moderate 35, Watch 165 at 08:40) with the listed count
 * beside them, and the first onset is taken over all of them when the API carries it.
 *
 * **Every state here is the API's.** Acknowledge and escalate go through the desk's gated client
 * and the queue is read again, so a reload shows the same thing (7.5 AC4). "Send to my phone"
 * exists only when the API reports a configured sender (7.5 AC3).
 *
 * Motion M16: when a cycle brings alerts the queue did not show a moment ago, those rows slide
 * in, the phone pops them and shakes once, and the chime plays if sound is on. "New" is decided
 * by `freshAlertIds` on the cycle-independent identity (scope, place, level).
 */
function AlertCentre() {
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const city = cityFromSearch(search);
  // Sent to the API only when it is not the default, so a Mumbai screen asks what it always did.
  const cityQuery = city === DEFAULT_CITY ? undefined : city;
  const pinnedRun = searchParams.get("run") ?? undefined;

  const [set, setSet] = useState<AlertSet | null>(null);
  const [loaded, setLoaded] = useState(false);
  const raised = useMemo(() => set?.alerts ?? [], [set]);
  const [openId, setOpenId] = useState<string | null>(() => searchParams.get("alert"));
  // Bumped after a write, to re-read the queue. The desk's state is the API's, never this
  // screen's: a local boolean was the old behaviour and it vanished on reload (B1).
  const [reload, setReload] = useState(0);
  // **Which cycle.** An alert is a statement about a forecast, so it only means anything beside
  // the run that raised it. The screen opens on `?run=`, else the demo's 06:40 cycle by the rule
  // `/console` and `/map` use, and a pick overrides either from then on.
  const opening = useOpeningRun(cityQuery, pinnedRun);
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const runId = picked ?? pinnedRun ?? opening.runId;
  const cycleReady = picked !== undefined || opening.resolved;
  const cycles = useCycles(cityQuery);

  const shownRef = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(NO_FRESH);
  const [batch, setBatch] = useState(0);

  const [steps, setSteps] = useState<EscalationStep[] | null>(null);
  const [stepsError, setStepsError] = useState<string | null>(null);
  const [sender, setSender] = useState<SenderStatus | null>(null);
  const [delivery, setDelivery] = useState<DeliveryLogData | null>(null);
  const [deliveryError, setDeliveryError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // The passphrase is in this tab's session storage, which the server render cannot see: false
  // there, read on every client render, and re-read when another tab writes storage.
  const hasPassphrase = useSyncExternalStore(subscribeStorage, hasPassphraseNow, noPassphrase);

  const [levelFilter, setLevelFilter] = useState<AlertLevel | null>(null);
  const [unackOnly, setUnackOnly] = useState(false);
  const [showAll, setShowAll] = useState<Partial<Record<AlertLevel, boolean>>>({});
  const [groupOpen, setGroupOpen] = useState<Partial<Record<AlertLevel, boolean>>>({});
  const [peak, setPeak] = useState<{
    runId: string;
    cycleTs: string;
    total: number;
    listedOnly: boolean;
  } | null>(null);

  useEffect(() => {
    if (!cycleReady) return;
    const controller = new AbortController();
    loadAlerts(runId, controller.signal, cityQuery)
      .then((next) => {
        const alerts = next?.alerts ?? [];
        const nextFresh = freshAlertIds(shownRef.current, alerts);
        shownRef.current = alertIdentities(alerts);
        setSet(next);
        setLoaded(true);
        setFresh(nextFresh);
        if (nextFresh.size > 0) setBatch((current) => current + 1);
      })
      .catch(() => {
        // A superseded request is not an empty queue: treating it as one would make every alert
        // of the next cycle look new.
        if (controller.signal.aborted) return;
        if (shownRef.current !== null) shownRef.current = new Set();
        setSet(null);
        setLoaded(true);
        setFresh(NO_FRESH);
      });
    return () => controller.abort();
  }, [runId, reload, cycleReady, cityQuery]);

  // The delivery log moves with the queue: a new cycle, an acknowledgement or a real send. The
  // whole queue (the product caps it at 60), because each alert's details list its own rows and
  // the phone shows the API's own WhatsApp text, and the endpoint has no per-alert filter.
  useEffect(() => {
    if (!cycleReady) return;
    const controller = new AbortController();
    loadDelivery(runId, controller.signal, 60, cityQuery)
      .then((log) => {
        setDelivery(log);
        setDeliveryError(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setDelivery(null);
        setDeliveryError(
          error instanceof Error
            ? `The delivery log could not be read: ${error.message}`
            : "The delivery log could not be read.",
        );
      });
    return () => controller.abort();
  }, [runId, reload, cycleReady, cityQuery]);

  // The matrix and the sender are configuration: read once.
  useEffect(() => {
    const controller = new AbortController();
    loadEscalation(controller.signal)
      .then(setSteps)
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setStepsError(
          error instanceof Error
            ? `config/escalation.yaml could not be read: ${error.message}`
            : "config/escalation.yaml could not be read.",
        );
      });
    loadSender(controller.signal)
      .then(setSender)
      .catch(() => {
        // No answer is no sender: the button stays away rather than promising a send.
        if (!controller.signal.aborted) setSender(null);
      });
    return () => controller.abort();
  }, []);

  useAlertChime(batch);

  // **When the cycle raises nothing**, the screen says so in one line and offers the storm's peak:
  // the cycle that raised the most alerts, the most severe ones breaking a tie. Ranked on
  // `n_raised`, every alert raised, where the API serves it: the product lists at most 60, so on
  // the listed count 08:10 and 08:40 tie at 60 while 08:40 raised 213. Found by asking each of
  // the city's cycles for its severe queue. Only then: on a cycle with alerts there is nothing to
  // point at.
  const emptyCycle = loaded && set !== null && raised.length === 0;
  useEffect(() => {
    if (!emptyCycle || !cycles || cycles.length === 0) return;
    const controller = new AbortController();
    Promise.all(
      cycles.map((cycle) =>
        loadAlertCount(cycle.runId, controller.signal, cityQuery)
          .then((count) => ({ cycle, count }))
          .catch(() => ({ cycle, count: null })),
      ),
    )
      .then((counts) => {
        let best: {
          runId: string;
          cycleTs: string;
          total: number;
          severe: number;
          listedOnly: boolean;
        } | null = null;
        for (const { cycle, count } of counts) {
          if (!count || count.total === 0) continue;
          if (
            !best ||
            count.total > best.total ||
            (count.total === best.total && count.severe > best.severe)
          ) {
            best = { runId: cycle.runId, cycleTs: cycle.cycleTs, ...count };
          }
        }
        setPeak(best);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [emptyCycle, cycles, cityQuery]);

  /**
   * Opens one alert's details, closes the rest, and keeps the choice in the URL, with the run the
   * queue on screen came from: an alert id belongs to one run, and a link without it would open
   * on the default cycle, where the id names nothing.
   */
  const shownRun = set?.runId || runId;
  const writeUrl = useCallback(
    (alertId: string | null) => {
      const params = new URLSearchParams(search);
      if (alertId) {
        params.set("alert", alertId);
        if (shownRun) params.set("run", shownRun);
      } else {
        params.delete("alert");
      }
      const query = params.toString();
      // `history.replaceState`, not `router.replace`: the App Router syncs it into
      // `useSearchParams` without a server round trip, where `router.replace` fetched the route
      // again and, measured on the dev server, had not moved the URL 1.8 s after the click.
      window.history.replaceState(null, "", query ? `${ALERTS_ROUTE}?${query}` : ALERTS_ROUTE);
    },
    [shownRun, search],
  );

  const toggle = useCallback(
    (id: string) => {
      const next = openId === id ? null : id;
      setOpenId(next);
      writeUrl(next);
    },
    [openId, writeUrl],
  );

  const closeOpen = useCallback(() => {
    setOpenId(null);
    writeUrl(null);
  }, [writeUrl]);

  const pickCycle = useCallback(
    (next: string) => {
      // The old queue stays until the new one arrives, so the rows a new cycle keeps move rather
      // than remount and only the new ones slide in (M16).
      setPicked(next);
      setPeak(null);
      setShowAll({});
      if (openId) setOpenId(null);
      // A `?run=` left naming the old cycle would reopen it on a reload.
      const params = new URLSearchParams(search);
      if (openId || params.has("run")) {
        params.delete("alert");
        if (params.has("run")) params.set("run", next);
        const query = params.toString();
        window.history.replaceState(null, "", query ? `${ALERTS_ROUTE}?${query}` : ALERTS_ROUTE);
      }
    },
    [openId, search],
  );

  /**
   * Acknowledge an alert, then re-read the queue so what is on screen is what the API holds.
   * The acknowledgement lives in the ops log and is folded into `GET /v1/alerts`, so it survives
   * a reload and the change of cycle that renames every alert.
   */
  const acknowledge = useCallback(
    async (id: string) => {
      if (!readPassphrase()) {
        toast("Acknowledged nothing", { description: NO_PASSPHRASE });
        return;
      }
      try {
        const done = await postAlertAction({
          action: "ack",
          alertId: id,
          user: "console",
          runId,
          city: cityQuery,
        });
        toast("Acknowledged", { description: done.notes[0] });
        setReload((current) => current + 1);
      } catch (error) {
        toast("Not acknowledged", { description: describeRefusal(opsRefusal(error)) });
      }
    },
    [runId, cityQuery],
  );

  /** Escalate one step up `config/escalation.yaml`, past every tier the level already reached. */
  const escalate = useCallback(
    async (id: string) => {
      const alert = raised.find((a) => a.id === id);
      const next = alert && steps ? nextEscalation(alert, steps) : null;
      if (!next) {
        toast("Escalated nothing", {
          description: steps
            ? "This alert has reached every step of the escalation matrix."
            : (stepsError ??
              "The escalation matrix is not loaded, so there is no next step to name."),
        });
        return;
      }
      if (!readPassphrase()) {
        toast("Escalated nothing", { description: NO_PASSPHRASE });
        return;
      }
      try {
        await postAlertAction({
          action: "escalate",
          alertId: id,
          user: "console",
          runId,
          city: cityQuery,
          escalateTo: next.id,
        });
        toast("Escalated", {
          description: `To ${next.recipient}. Recorded in the ops log with who and when.`,
        });
        setReload((current) => current + 1);
      } catch (error) {
        toast("Not escalated", { description: describeRefusal(opsRefusal(error)) });
      }
    },
    [raised, steps, stepsError, runId, cityQuery],
  );

  const openAlert = raised.find((a) => a.id === openId) ?? null;
  const active = openAlert?.id ?? raised[0]?.id ?? null;

  /** A real send, only ever offered when the API has a sender configured (7.5 AC3). */
  const sendToPhone = async () => {
    if (!active) return;
    if (!readPassphrase()) {
      toast("Sent nothing", { description: NO_PASSPHRASE });
      return;
    }
    setSending(true);
    try {
      // The city and the run on screen: a send from /alerts?city=chennai recorded under Mumbai
      // would never reach the delivery log this screen reads.
      const done = await sendAlertToPhone({
        alertId: active,
        user: "console",
        runId: set?.runId || runId,
        city: cityQuery,
      });
      toast("Sent to my phone", {
        description: done.toMasked ? `Delivered to the provider for ${done.toMasked}.` : undefined,
      });
    } catch (error) {
      toast("Not sent", { description: describeRefusal(opsRefusal(error)) });
    } finally {
      setSending(false);
      setReload((current) => current + 1);
    }
  };

  const cycleTs = set?.cycleTs ?? null;
  const cycleTime = cycleTs ? formatIst(cycleTs) : "This cycle";
  const summary = useMemo(() => summariseQueue(raised, cycleTs), [raised, cycleTs]);
  const pending = useMemo(() => set?.pending ?? [], [set]);
  const capped = set?.capped ?? false;
  const { upgrades, stepUps, pendingOnly, unplaced } = useMemo(
    () => splitPending(raised, pending, capped),
    [raised, pending, capped],
  );
  // Every alert raised by level, not the listed sixty: the chips say both when they differ.
  const levelCounts = set?.nRaisedByLevel ?? null;
  const nRaised = set?.nRaised ?? null;
  const nPendingNew = set?.nPendingNew ?? null;
  const nPendingStepUp = set?.nPendingStepUp ?? null;
  const nextCycle =
    cycles && cycleTs ? (cycles.find((c) => c.cycleTs.localeCompare(cycleTs) > 0) ?? null) : null;
  const nextAt = nextCycle ? `at ${formatIst(nextCycle.cycleTs)}` : "next cycle";
  const nPending = set?.nPending ?? 0;

  // Exercise or Actual: the queue's own CAP status when it has alerts, else the run's mode by
  // the product's rule (`cap_status` is Exercise unless the cycle ran live).
  const thisRun = cycles?.find((c) => c.runId === set?.runId);
  const capStatus =
    raised[0]?.capStatus ?? (thisRun?.replayMode === "live" ? "Actual" : "Exercise");

  const visible = raised.filter(
    (a) => (levelFilter === null || a.level === levelFilter) && (!unackOnly || isUnacknowledged(a)),
  );
  const hasUpperLevels = summary.counts.severe + summary.counts.moderate > 0;

  const deliveryRowsFor = (alertId: string) =>
    delivery
      ? delivery.rows
          .filter((row) => row.alertId === alertId)
          .map((row) => ({ ...row, alert: null }))
      : null;

  const phoneMessages: PhoneMessage[] = [
    ...(openAlert ? [openAlert] : []),
    ...raised.filter((a) => fresh.has(a.id) && a.id !== openAlert?.id),
    ...raised.filter((a) => !fresh.has(a.id) && a.id !== openAlert?.id),
  ]
    .slice(0, PHONE_MESSAGES)
    .map((a) => ({
      id: a.id,
      time: a.sentTs ?? a.raisedTs,
      text: alertMessages(delivery?.rows, a.id).whatsapp ?? phoneText(a),
    }));

  const renderRow = (alert: RunAlert) => {
    const detailsId = `alert-details-${alert.id}`;
    const open = alert.id === openId;
    const messages = alertMessages(delivery?.rows, alert.id);
    return (
      // Keyed by identity, not id: a street still warned about at the same level keeps its row
      // across cycles, so only new rows slide in.
      <li key={alertIdentity(alert)}>
        <AlertRow
          alert={alert}
          cycleTs={cycleTs}
          status={alertStatus(alert, cycleTs, steps)}
          upgrade={upgrades.get(alert.id)?.level ?? null}
          open={open}
          onToggle={toggle}
          detailsId={detailsId}
          entering={fresh.has(alert.id)}
        >
          {open ? (
            <AlertDetails
              alert={alert}
              cycleTs={cycleTs}
              runId={set?.runId ?? runId}
              city={city}
              steps={steps}
              stepsError={stepsError}
              messages={{ whatsapp: messages.whatsapp ?? phoneText(alert), sms: messages.sms }}
              delivery={{
                rows: deliveryRowsFor(alert.id),
                loading: delivery === null && deliveryError === null,
                error: deliveryError,
              }}
              hasPassphrase={hasPassphrase}
              onAcknowledge={(id) => void acknowledge(id)}
              onEscalate={(id) => void escalate(id)}
            />
          ) : null}
        </AlertRow>
      </li>
    );
  };

  return (
    <AppShell>
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="flex flex-col gap-4 p-6">
          <PageHeader
            title={navItem("alerts").label}
            screen={navItem("alerts")}
            description="Every alert VARUNA raises, what to do about it, who has been told, and the message the ward officer receives."
          />

          {cityQuery ? null : <CyclePicker currentRunId={set?.runId ?? runId} onPick={pickCycle} />}

          {set && !set.crossCycle ? (
            <p className="type-small text-text-2">
              This cycle was baked before alerts needed two consecutive cycles: its queue raised on
              this cycle alone, and persistence is counted in forecast steps.
            </p>
          ) : null}

          <section
            aria-label="This cycle's alerts"
            className="rounded-panel border-line bg-deep flex flex-col gap-3 border p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="Filter by level" className="flex flex-wrap gap-2">
                {ALERT_LEVELS.map((level) => (
                  <button
                    key={level}
                    type="button"
                    aria-pressed={levelFilter === level}
                    onClick={() => setLevelFilter((current) => (current === level ? null : level))}
                    className={cn(
                      "rounded-chip focus-visible:ring-tide inline-flex items-center gap-1.5 border px-1 py-0.5 focus-visible:ring-2 focus-visible:outline-none",
                      levelFilter === level ? "border-tide bg-tide/15" : "border-transparent",
                    )}
                  >
                    <AlertLevelChip level={level} size="sm" />
                    <span className="num type-small text-text pr-1">
                      {levelCounts ? levelCounts[level] : summary.counts[level]}
                      {levelCounts && levelCounts[level] !== summary.counts[level] ? (
                        <span className="text-text-3"> ({summary.counts[level]} listed)</span>
                      ) : null}
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  aria-pressed={unackOnly}
                  onClick={() => setUnackOnly((current) => !current)}
                  className={cn(
                    "rounded-chip type-micro focus-visible:ring-tide inline-flex h-7 items-center gap-1.5 border px-2.5 focus-visible:ring-2 focus-visible:outline-none",
                    unackOnly ? "border-tide bg-tide/15 text-tide" : "border-line text-text-2",
                  )}
                >
                  Unacknowledged
                  <span className="num text-text">{summary.unacknowledged}</span>
                </button>
              </div>
              <span className="rounded-chip border-line type-micro text-text-2 ml-auto inline-flex h-7 items-center border px-2.5">
                {capStatus === "Actual"
                  ? "Actual: live alerts, CAP status Actual"
                  : "Exercise: replay alerts are drills"}
              </span>
            </div>
            {capped ? (
              <p className="num type-small text-text-2 max-w-[72ch]">
                {nRaised !== null
                  ? `The queue lists the worst ${raised.length} of the ${nRaised} alerts this cycle raised, worst level first. The level counts are all ${nRaised}; the filters and Unacknowledged cover the ${raised.length} listed.`
                  : `The queue lists at most ${MAX_LISTED} alerts, worst level first, and this run does not say how many more it raised. Every count here is of the ${raised.length} listed.`}
              </p>
            ) : null}
            <div className="type-small text-text-2 flex flex-wrap gap-x-6 gap-y-1">
              {summary.worst ? (
                <p>
                  Deepest <span className="text-text font-medium">{alertPlace(summary.worst)}</span>
                  , <span className="num text-text">{formatCm(summary.worst.peakCm)}</span> at peak
                </p>
              ) : null}
              {set?.firstOnset ? (
                <p className="num">
                  First street over its threshold{" "}
                  <span className="text-text">{atLead(set.firstOnset.ts, cycleTs)}</span>
                  {set.firstOnset.place ? `, ${set.firstOnset.place}` : ""}
                </p>
              ) : summary.firstOnset ? (
                <p className="num">
                  {capped
                    ? `First onset among the ${raised.length} listed `
                    : "First street over its threshold "}
                  <span className="text-text">
                    {summary.firstOnset.leadMin === null
                      ? formatIst(summary.firstOnset.ts)
                      : formatTimeWithLead(summary.firstOnset.ts, summary.firstOnset.leadMin)}
                  </span>
                </p>
              ) : null}
              {set?.crossCycle && nPendingNew !== null && nPendingStepUp !== null ? (
                <>
                  {nPendingNew > 0 ? (
                    <p className="num">
                      <span className="text-text">{nPendingNew}</span> new{" "}
                      {plural(nPendingNew, "place raises", "places raise")} {nextAt} if{" "}
                      {plural(nPendingNew, "it holds", "they hold")}
                    </p>
                  ) : null}
                  {nPendingStepUp > 0 ? (
                    <p className="num">
                      <span className="text-text">{nPendingStepUp}</span> raised{" "}
                      {plural(nPendingStepUp, "place goes", "places go")} up a level {nextAt} if{" "}
                      {plural(nPendingStepUp, "it holds", "they hold")}
                    </p>
                  ) : null}
                </>
              ) : set?.crossCycle && nPending > 0 ? (
                <p className="num">
                  <span className="text-text">{nPending}</span>{" "}
                  {plural(nPending, "place raises", "places raise")} a level {nextAt} if{" "}
                  {plural(nPending, "it holds", "they hold")}
                </p>
              ) : null}
              {set && set.nCleared > 0 ? (
                <p className="num">
                  Cleared this cycle: <span className="text-text">{set.nCleared}</span>
                  {set.cleared[0]
                    ? `, first ${set.cleared[0].name ?? set.cleared[0].areaDesc} (${set.cleared[0].level})`
                    : ""}
                </p>
              ) : null}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)]">
            <Panel title="Queue" description={RULE} className="min-w-0 self-start">
              {!loaded ? (
                <SkeletonRows rows={6} />
              ) : set === null ? (
                <EmptyState
                  size="sm"
                  icon={BellOff}
                  title="No alert product for this run"
                  description="This run was baked before alerts were written. Bake the cycle again, or pick another cycle above."
                />
              ) : (
                <div className="flex flex-col gap-4">
                  {raised.length === 0 ? (
                    <div className="flex flex-col items-start gap-2">
                      <p className="type-body text-text max-w-[72ch]">
                        {nPending > 0
                          ? `${cycleTime} raises nothing yet: ${nPending} ${plural(nPending, "place", "places")} crossed a threshold for the first time and ${plural(nPending, "raises", "raise")} ${nextAt} if ${plural(nPending, "it holds", "they hold")}.`
                          : `${cycleTime} raises nothing: no street or hotspot stays over a threshold for two forecast steps in a row this cycle.`}
                      </p>
                      {peak && peak.runId !== set.runId ? (
                        <Button variant="outline" size="sm" onClick={() => pickCycle(peak.runId)}>
                          {`Open ${formatIst(peak.cycleTs)}, the storm's peak (${peak.total} ${plural(peak.total, "alert", "alerts")} ${peak.listedOnly ? "listed" : "raised"})`}
                        </Button>
                      ) : null}
                    </div>
                  ) : visible.length === 0 ? (
                    <p className="type-small text-text-2">
                      No alert matches the filter. Press a highlighted chip above to clear it.
                    </p>
                  ) : null}

                  {ALERT_LEVELS.map((level) => {
                    const group = sortByOnset(visible.filter((a) => a.level === level));
                    if (group.length === 0) return null;
                    const holdsOpen = group.some((a) => a.id === openId);
                    const isOpen =
                      holdsOpen ||
                      (groupOpen[level] ??
                        (level !== "watch" || !hasUpperLevels || levelFilter === "watch"));
                    const openBeyondPreview =
                      group.findIndex((a) => a.id === openId) >= LEVEL_PREVIEW;
                    const all = showAll[level] === true || openBeyondPreview;
                    const shown = all ? group : group.slice(0, LEVEL_PREVIEW);
                    const unacked = group.filter(isUnacknowledged).length;
                    const listId = `alert-level-${level}`;
                    return (
                      <section
                        key={level}
                        aria-label={`${ALERT_LEVEL_LABELS[level]} alerts`}
                        className="flex flex-col gap-2"
                      >
                        <button
                          type="button"
                          aria-expanded={isOpen}
                          aria-controls={listId}
                          onClick={() => {
                            // The open alert holds its level open, so shutting the level closes
                            // that alert too; otherwise the header would read expanded and do
                            // nothing.
                            if (isOpen && holdsOpen) closeOpen();
                            setGroupOpen((current) => ({ ...current, [level]: !isOpen }));
                          }}
                          className="rounded-control focus-visible:ring-tide flex items-center gap-3 text-left focus-visible:ring-2 focus-visible:outline-none"
                        >
                          <AlertLevelChip level={level} size="sm" showThreshold />
                          <span className="num type-micro text-text-2">
                            {group.length} {plural(group.length, "alert", "alerts")}
                            {unacked < group.length ? `, ${unacked} unacknowledged` : ""}
                          </span>
                          <ChevronDown
                            size={16}
                            strokeWidth={1.75}
                            aria-hidden="true"
                            className={cn("text-text-3 ml-auto", isOpen && "rotate-180")}
                          />
                        </button>
                        <div id={listId} hidden={!isOpen} className="flex flex-col gap-2">
                          <ul className="flex flex-col gap-1.5">{shown.map(renderRow)}</ul>
                          {group.length > LEVEL_PREVIEW ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="self-start"
                              aria-expanded={all}
                              onClick={() => {
                                // Past the preview the open alert keeps the level whole; showing
                                // fewer closes it rather than leaving the button dead.
                                if (all && openBeyondPreview) closeOpen();
                                setShowAll((current) => ({ ...current, [level]: !all }));
                              }}
                            >
                              {all
                                ? "Show fewer"
                                : `Show all ${group.length} ${ALERT_LEVEL_LABELS[level].toLowerCase()}`}
                            </Button>
                          ) : null}
                        </div>
                      </section>
                    );
                  })}

                  {set.crossCycle ? (
                    <PendingGroups
                      pendingOnly={pendingOnly}
                      stepUps={stepUps}
                      unplaced={unplaced}
                      nextAt={nextAt}
                      listedAlerts={raised.length}
                      listedPending={pending.length}
                      onRows={upgrades.size}
                      total={nPending}
                      // With nothing raised, every pending place is new, whatever the API says.
                      totalNew={nPendingNew ?? (raised.length === 0 && !capped ? nPending : null)}
                    />
                  ) : null}
                </div>
              )}
            </Panel>

            <div className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
              <Panel
                title="Ward officer's phone"
                description="The WhatsApp card as the ward officer receives it: the open alert first, then what this cycle brought. On-screen mock."
              >
                <PhoneMock
                  messages={phoneMessages}
                  freshIds={fresh}
                  popKey={batch}
                  simTime={cycleTs ?? undefined}
                />
                {sender?.configured && active ? (
                  <div className="mt-3 flex flex-col gap-1">
                    <Button
                      size="sm"
                      onClick={() => void sendToPhone()}
                      disabled={sending}
                      aria-disabled={sending}
                    >
                      <Send size={16} strokeWidth={1.75} aria-hidden="true" />
                      Send to my phone
                    </Button>
                    <p className="type-micro text-text-3">
                      A real {sender.channel === "sms" ? "SMS" : "WhatsApp message"} through{" "}
                      {sender.provider === "twilio" ? "Twilio" : "WhatsApp Cloud"} to{" "}
                      {sender.toMasked}, the number configured where the API runs.
                    </p>
                  </div>
                ) : null}
                {deliveryError ? (
                  <p className="type-micro text-text-3 mt-3">{deliveryError}</p>
                ) : (
                  delivery?.notes.map((note) => (
                    <p key={note} className="type-micro text-text-3 mt-3">
                      {note}
                    </p>
                  ))
                )}
              </Panel>
            </div>
          </div>

          <EscalationPanel steps={steps} error={stepsError} />
        </div>
      </div>
    </AppShell>
  );
}

/** "09:05 (+25 min)" from the cycle, or the bare time when the cycle is not known. */
function atLead(ts: string, cycleTs: string | null): string {
  const lead = cycleTs ? minutesBetween(cycleTs, ts) : null;
  return lead === null ? formatIst(ts) : formatTimeWithLead(ts, lead);
}

/**
 * The pending places the queue has no row for, in the groups that say what each one is.
 *
 * The product lists at most 60 alerts and 60 pending places while counting all of them, so "no
 * row" is not "not raised": at 08:40 on 2 July, 24 of the 32 pending places with no listed row
 * were already raised at a lower level, beyond the 60 listed. Each pending entry now says the
 * level its place is raised at (`raisedLevel`), and the screen puts it where it belongs:
 *
 * - Watching, not raised yet: places raised at nothing, counted against every new place.
 * - Raised beyond the listed: a place raised at a lower level the list does not show, going up.
 * - Not placed: an API that does not say, with a capped queue; the screen says it cannot tell.
 *
 * A pending level above a listed row is printed on that row instead (`onRows`).
 */
function PendingGroups({
  pendingOnly,
  stepUps,
  unplaced,
  nextAt,
  listedAlerts,
  listedPending,
  onRows,
  total,
  totalNew,
}: {
  pendingOnly: readonly PendingAlert[];
  stepUps: readonly PendingAlert[];
  unplaced: readonly PendingAlert[];
  nextAt: string;
  listedAlerts: number;
  listedPending: number;
  /** Listed pending entries that are a level up on a row the queue shows. */
  onRows: number;
  total: number;
  /** Every pending place raised at nothing yet, when the API says; null when it does not. */
  totalNew: number | null;
}) {
  if (pendingOnly.length + stepUps.length + unplaced.length === 0) return null;
  const pendingCapped = total > listedPending;
  return (
    <div className="flex flex-col gap-3">
      {pendingOnly.length > 0 ? (
        <PendingGroup
          id="alert-watching"
          title={`Watching, not raised yet (${pendingOnly.length}${
            totalNew !== null && totalNew > pendingOnly.length ? ` shown of ${totalNew}` : ""
          })`}
          entries={pendingOnly}
          line={(p) => `First crossed ${formatIst(p.sinceTs)}, raises ${nextAt} if it holds`}
        />
      ) : null}
      {stepUps.length > 0 ? (
        <PendingGroup
          id="alert-step-ups"
          title={`Raised, not among the ${listedAlerts} listed, going up a level (${stepUps.length})`}
          entries={stepUps}
          line={(p) =>
            `Raised at ${ALERT_LEVEL_LABELS[p.raisedLevel ?? "watch"]}; ${ALERT_LEVEL_LABELS[p.level]} ${nextAt} if it holds`
          }
        />
      ) : null}
      {unplaced.length > 0 ? (
        <PendingGroup
          id="alert-unplaced"
          title={`Crossed a level this cycle, may already be raised (${unplaced.length})`}
          entries={unplaced}
          line={(p) =>
            `First crossed ${formatIst(p.sinceTs)}; raises or goes up a level ${nextAt} if it holds`
          }
          note={`This run does not say which of these are already raised at a lower level: the queue lists ${listedAlerts} alerts and may hold more.`}
        />
      ) : null}
      {pendingCapped || onRows > 0 ? (
        <p className="num type-micro text-text-3">
          {pendingCapped
            ? `This run lists ${listedPending} of the ${total} places waiting to raise or go up a level.`
            : `This run lists all ${listedPending} places waiting to raise or go up a level.`}
          {onRows > 0
            ? ` ${onRows} of them already ${plural(onRows, "has", "have")} a row above at a lower level, which says so.`
            : ""}
        </p>
      ) : null}
    </div>
  );
}

function PendingGroup({
  id,
  title,
  entries,
  line,
  note,
}: {
  id: string;
  title: string;
  entries: readonly PendingAlert[];
  line: (entry: PendingAlert) => string;
  note?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((current) => !current)}
        className="rounded-control type-small text-text focus-visible:ring-tide flex items-center gap-2 text-left font-medium focus-visible:ring-2 focus-visible:outline-none"
      >
        <span className="num">{title}</span>
        <ChevronDown
          size={16}
          strokeWidth={1.75}
          aria-hidden="true"
          className={cn("text-text-3 ml-auto", open && "rotate-180")}
        />
      </button>
      <div id={id} hidden={!open} className="flex flex-col gap-2">
        <ul className="flex flex-col gap-1.5">
          {entries.map((p) => (
            <li
              key={p.id}
              className="rounded-control border-line flex min-h-10 items-center gap-3 border px-3 py-1.5"
            >
              <AlertLevelChip level={p.level} size="sm" className="shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="type-small text-text truncate">
                  {p.name ?? p.areaDesc}
                  {p.locality ? <span className="text-text-3"> {p.locality}</span> : null}
                </p>
                <p className="num type-micro text-text-3">{line(p)}</p>
              </div>
              <span className="num type-small text-text shrink-0">{formatCm(p.peakCm)}</span>
            </li>
          ))}
        </ul>
        {note ? <p className="type-micro text-text-3">{note}</p> : null}
      </div>
    </section>
  );
}

/** The escalation matrix from `config/escalation.yaml`, closed until asked for (SPEC.md 7.5). */
function EscalationPanel({
  steps,
  error,
}: {
  steps: EscalationStep[] | null;
  error: string | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Panel
      title="Who is told at each level"
      description="The escalation matrix from config/escalation.yaml, ward officer to public."
      actions={
        <Button
          variant="outline"
          size="sm"
          aria-expanded={open}
          aria-controls="escalation-matrix"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      <div id="escalation-matrix" hidden={!open}>
        {steps ? (
          <EscalationMatrix tiers={steps} />
        ) : (
          <p className="type-small text-text-2">{error ?? "Reading the escalation matrix."}</p>
        )}
      </div>
    </Panel>
  );
}

/**
 * The alert centre, behind the Suspense boundary `useSearchParams` needs: `next build` refuses a
 * prerendered route whose client component reads the query string without one.
 */
export function AlertsScreen() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="flex flex-col gap-4 p-6">
            <PageHeader
              title={navItem("alerts").label}
              screen={navItem("alerts")}
              description="Every alert VARUNA raises, what to do about it, who has been told, and the message the ward officer receives."
            />
            <SkeletonRows rows={6} />
          </div>
        </AppShell>
      }
    >
      <AlertCentre />
    </Suspense>
  );
}
