"use client";

import { useState } from "react";

import { AlertDetails } from "@/components/varuna/alert-details";
import { AlertRow } from "@/components/varuna/alert-row";
import type { DeliveryLogRow } from "@/components/varuna/delivery-log";
import { Panel } from "@/components/varuna/panel";
import { alertStatus, type EscalationStep, type RunAlert } from "@/lib/api/alerts";
import { navItem } from "@/lib/nav";

import { Demo } from "./section";

const RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const CYCLE_TS = "2019-07-02T08:40:00+05:30";

/**
 * The deepest street alert of the 08:40 IST cycle, field for field as
 * `GET /v1/alerts?run_id=MUM-20190702T0310Z-...` answered on 2026-09-28 after the re-bake on the
 * rebuilt coast. Its CAP document is on disk, so "Show the CAP document" reads a real one.
 */
const SEVERE: RunAlert = {
  id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0977-SEVERE",
  runId: RUN_ID,
  level: "severe",
  thresholdCm: 45,
  headline:
    "Sant Shitolebaba Maharaj Marg, near Sakinaka: depth above 45 cm from 09:30 until at least 11:40",
  instruction:
    "Avoid Sant Shitolebaba Maharaj Marg. Peak forecast 76 cm. Route emergency vehicles around it; see the reachability tab for the affected catchment.",
  areaDesc: "Sant Shitolebaba Maharaj Marg",
  name: "Sant Shitolebaba Maharaj Marg",
  locality: "near Sakinaka",
  scope: "segment",
  hotspotId: null,
  lon: 72.89574214999999,
  lat: 19.0957649,
  peakCm: 76.3,
  windowFrom: "2019-07-02T09:30:00+05:30",
  windowTo: "2019-07-02T11:40:00+05:30",
  windowOpenEnded: true,
  membersAbove: 50,
  membersTotal: 50,
  raisedTs: "2019-07-02T08:40:00+05:30",
  persistsCycles: 2,
  persistsUnit: "cycles",
  firstSeenTs: "2019-07-02T08:10:00+05:30",
  sentTs: "2019-07-02T08:40:00+05:30",
  notify: ["ward_officer", "control_room", "police_traffic"],
  pumps: [],
  dispatchNote: null,
  triggerP: 1,
  capStatus: "Exercise",
  sourceUrl: null,
  state: "raised",
  acknowledgedBy: null,
  acknowledgedTs: null,
  escalatedTo: null,
  history: [],
};

/** A Watch from the same cycle and answer. */
const WATCH: RunAlert = {
  ...SEVERE,
  id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0770-WATCH",
  level: "watch",
  thresholdCm: 15,
  headline:
    "Pipeline Road, near Kamani junction: depth above 15 cm from 09:35 until at least 11:40",
  instruction:
    "Avoid Pipeline Road. Peak forecast 64 cm. Route emergency vehicles around it; see the reachability tab for the affected catchment.",
  areaDesc: "Pipeline Road",
  name: "Pipeline Road",
  locality: "near Kamani junction",
  lon: 72.88422845,
  lat: 19.088119899999995,
  peakCm: 63.7,
  windowFrom: "2019-07-02T09:35:00+05:30",
  notify: ["ward_officer"],
};

/** `config/escalation.yaml` as `GET /v1/alerts/escalation` served it. */
const STEPS: EscalationStep[] = [
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

/** The severe alert's three delivery rows and the two messages, from `/v1/alerts/delivery`. */
const DELIVERY: DeliveryLogRow[] = [
  {
    id: `${SEVERE.id}-dashboard`,
    label: "Dashboard",
    kind: "mock",
    status: "Shown on the alert queue",
    ts: "2019-07-02T08:40:00+05:30",
  },
  {
    id: `${SEVERE.id}-whatsapp_mock`,
    label: "WhatsApp mock",
    kind: "mock",
    status: "Shown on the on-screen phone",
    ts: "2019-07-02T08:40:00+05:30",
  },
  {
    id: `${SEVERE.id}-sms_mock`,
    label: "SMS mock",
    kind: "mock",
    status: "Rendered, not sent",
    ts: "2019-07-02T08:40:00+05:30",
  },
];

const MESSAGES = {
  whatsapp:
    "VARUNA severe alert (exercise)\nSant Shitolebaba Maharaj Marg, near Sakinaka: depth above 45 cm from 09:30 until at least 11:40.\nAvoid Sant Shitolebaba Maharaj Marg. Peak forecast 76 cm. Route emergency vehicles around it; see the reachability tab for the affected catchment.\nArea: Sant Shitolebaba Maharaj Marg. Forecast from 08:40 IST, 2 Jul 2019.",
  sms: "VARUNA severe exercise: Sant Shitolebaba Maharaj Marg, near Sakinaka: depth above 45 cm from 09:30 until at least 11:40. Avoid the street.",
};

const IGNORE = () => undefined;

/**
 * Sanket's queue row and the details it opens (M29), on the 08:40 cycle. The desk's acts are
 * shown refused: this page holds no passphrase and never writes. "Show the CAP document" reads
 * the real document from the API when pressed.
 */
export default function SanketStories() {
  const alerts = navItem("alerts");
  const [openId, setOpenId] = useState<string | null>(SEVERE.id);
  const toggle = (id: string) => setOpenId((current) => (current === id ? null : id));

  return (
    <Panel
      title={`${alerts.label}: the alert queue`}
      description="Rows by onset, each opening its details under it: the window, who has been told, the WhatsApp card and SMS, the delivery log and the CAP document. Replay alerts are Exercise, never Actual."
    >
      <div className="flex flex-col gap-6">
        <Demo
          label="Queue, one row open"
          note="Press a row to open or close it; one is open at a time."
          bare
        >
          <ul className="rounded-panel border-line bg-deep flex flex-col border">
            {[SEVERE, WATCH].map((alert) => {
              const detailsId = `design-alert-${alert.level}`;
              return (
                <li key={alert.id} className="border-line border-b last:border-b-0">
                  <AlertRow
                    alert={alert}
                    cycleTs={CYCLE_TS}
                    status={alertStatus(alert, CYCLE_TS, STEPS)}
                    open={openId === alert.id}
                    onToggle={toggle}
                    detailsId={detailsId}
                  >
                    <AlertDetails
                      alert={alert}
                      cycleTs={CYCLE_TS}
                      runId={RUN_ID}
                      steps={STEPS}
                      messages={alert.id === SEVERE.id ? MESSAGES : { whatsapp: null, sms: null }}
                      delivery={{
                        rows: alert.id === SEVERE.id ? DELIVERY : [],
                        loading: false,
                        error: null,
                      }}
                      hasPassphrase={false}
                      onAcknowledge={IGNORE}
                      onEscalate={IGNORE}
                    />
                  </AlertRow>
                </li>
              );
            })}
          </ul>
        </Demo>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo
            label="Acknowledged"
            note="An illustrative state: nobody acknowledged this alert on 2 July 2019."
            bare
          >
            <AlertRow
              alert={SEVERE}
              cycleTs={CYCLE_TS}
              status={alertStatus(
                {
                  ...SEVERE,
                  state: "acknowledged",
                  acknowledgedBy: "Ward officer",
                  acknowledgedTs: "2019-07-02T08:52:00+05:30",
                },
                CYCLE_TS,
                STEPS,
              )}
              open={false}
              onToggle={IGNORE}
              detailsId="design-alert-acknowledged"
            />
          </Demo>
          <Demo
            label="Escalated"
            note="An illustrative state: the desk's act, read back from the ops log on screen."
            bare
          >
            <AlertRow
              alert={WATCH}
              cycleTs={CYCLE_TS}
              status={alertStatus(
                { ...WATCH, state: "escalated", escalatedTo: "control_room" },
                CYCLE_TS,
                STEPS,
              )}
              open={false}
              onToggle={IGNORE}
              detailsId="design-alert-escalated"
            />
          </Demo>
        </div>
        <Demo
          label="Details while the desk's matrix and delivery log load"
          note="Skeletons where the steps and rows will be; nothing is claimed as sent."
        >
          <AlertDetails
            alert={WATCH}
            cycleTs={CYCLE_TS}
            runId={RUN_ID}
            steps={null}
            messages={{ whatsapp: null, sms: null }}
            delivery={{ rows: null, loading: true, error: null }}
            hasPassphrase={false}
            onAcknowledge={IGNORE}
            onEscalate={IGNORE}
          />
        </Demo>
      </div>
    </Panel>
  );
}
