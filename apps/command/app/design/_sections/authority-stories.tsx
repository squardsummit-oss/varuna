"use client";

import { useMemo, useState } from "react";

import { ActionResult } from "@/components/authority/action-result";
import { AlertPanel } from "@/components/authority/alert-panel";
import { CitizenInbox, type InboxWriteAccess } from "@/components/authority/citizen-inbox";
import { ClosurePanel } from "@/components/authority/closure-panel";
import { DeskReportsProvider, type DeskReports } from "@/components/authority/desk-reports";
import { OpsLog } from "@/components/authority/ops-log";
import { PassphraseGate, type GateStatus } from "@/components/authority/passphrase-gate";
import { PumpPanel } from "@/components/authority/pump-panel";
import { SituationNote } from "@/components/authority/situation-note";
import type { OpsEntry } from "@/lib/api/ops";
import type { PublicReport } from "@/lib/api/reports";

import { Demo } from "./section";

/*
 * The ward officer's desk. This page holds no passphrase and the local API is read-only, so every
 * write a story offers is refused by the API, never faked by the page. The ops-log rows are an
 * illustrative sequence: nobody worked this desk on 2 July 2019.
 */

const RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const OFFICER = "Ward officer, F/South";
const READ_ONLY = "This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs.";

const GATES: { status: GateStatus; reason: string | null; note: string }[] = [
  { status: "checking", reason: null, note: "Asking the API whether writes are enabled." },
  { status: "disabled", reason: READ_ONLY, note: "The API's own sentence." },
  {
    status: "unreachable",
    reason: "The API did not answer at http://localhost:8000.",
    note: "Nothing can be written, and the desk says why.",
  },
  {
    status: "locked",
    reason: null,
    note: "Writes are enabled; the officer names themself to open the desk.",
  },
];

const ENTRIES: OpsEntry[] = [
  {
    id: "ops-3",
    kind: "alert_ack",
    ts: "2019-07-02T08:52:00+05:30",
    user: OFFICER,
    detail: {
      alert_id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0977-SEVERE",
    },
  },
  {
    id: "ops-2",
    kind: "pump_status",
    ts: "2019-07-02T08:47:00+05:30",
    user: OFFICER,
    detail: { pump_id: "P-05", status: "pumping" },
  },
  {
    id: "ops-1",
    kind: "closure",
    ts: "2019-07-02T08:41:00+05:30",
    user: OFFICER,
    detail: {
      segment_id: "Dr Babasaheb Ambedkar Marg",
      reason: "knee-deep water under the rail bridge",
    },
  },
];

const REPORT: PublicReport = {
  id: "seed-RPT-MUM-HS-01-ankle",
  origin: "seed",
  synthetic: true,
  ts: "2019-07-02T08:31:00+05:30",
  received_at: null,
  lat: 19.012,
  lon: 72.841,
  coordinates: "rounded to 3 decimals",
  city: "mumbai",
  outside_aoi: false,
  place: "Hindmata junction",
  depth_hint: "ankle",
  depth_cm: 10,
  text: "Water over the kerb at Hindmata junction.",
  source: "seed",
  photo_attached: false,
  has_photo: false,
  photo_url: null,
  thumb_url: null,
  photo_note: null,
  credit: null,
  status: "received",
  status_ts: null,
  history: [{ status: "received", ts: "2019-07-02T08:31:00+05:30", role: "citizen" }],
};

const REPORT_KINGS: PublicReport = {
  ...REPORT,
  id: "seed-RPT-MUM-HS-03-knee",
  ts: "2019-07-02T08:44:00+05:30",
  lat: 19.027,
  lon: 72.857,
  place: "King's Circle",
  depth_hint: "knee",
  depth_cm: 45,
  text: "Knee deep outside Maheshwari Udyan.",
  status: "seen",
  status_ts: "2019-07-02T08:50:00+05:30",
  history: [
    { status: "received", ts: "2019-07-02T08:44:00+05:30", role: "citizen" },
    { status: "seen", ts: "2019-07-02T08:50:00+05:30", role: "ward_officer" },
  ],
};

const ACCESS: InboxWriteAccess = {
  open: false,
  gate: "disabled",
  reason: READ_ONLY,
  officer: null,
};

/** The desk's panels, each over the read-only API the page talks to. */
export default function AuthorityStories() {
  const [selectedId, setSelectedId] = useState<string | null>(REPORT.id);
  const [reports, setReports] = useState<PublicReport[]>([REPORT, REPORT_KINGS]);

  const desk: DeskReports = useMemo(
    () => ({
      list: { count: reports.length, reports, notes: [] },
      error: null,
      exact: true,
      selectedId,
      focus: null,
      select: (id) => setSelectedId(id),
      replace: (report) =>
        setReports((current) => current.map((r) => (r.id === report.id ? report : r))),
    }),
    [reports, selectedId],
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {GATES.map(({ status, reason, note }) => (
          <Demo key={status} label={`Passphrase gate, ${status}`} note={note} bare>
            <PassphraseGate status={status} reason={reason} onOpen={() => undefined} />
          </Demo>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Demo label="Action result, changed" bare>
          <ActionResult
            outcome="changed"
            message="Closed Dr Babasaheb Ambedkar Marg until 10:00. Routes read the closure from now."
            at="2019-07-02T08:41:00+05:30"
          />
        </Demo>
        <Demo label="Action result, recorded" bare>
          <ActionResult
            outcome="recorded"
            message="Acknowledged the Sant Shitolebaba Maharaj Marg alert."
            notes={["This changed no forecast: an acknowledgement is only ever a record."]}
          />
        </Demo>
        <Demo label="Action result, refused" bare>
          <ActionResult outcome="refused" message={READ_ONLY} />
        </Demo>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Demo label="Ops log" note="Illustrative rows: nobody worked the desk on 2 July 2019." bare>
          <OpsLog entries={ENTRIES} total={ENTRIES.length} />
        </Demo>
        <div className="flex flex-col gap-4">
          <Demo label="Ops log, loading" bare>
            <OpsLog entries={null} />
          </Demo>
          <Demo label="Ops log, empty" bare>
            <OpsLog entries={[]} />
          </Demo>
        </div>
      </div>

      <Demo
        label="Citizen inbox"
        note="Two seeded reports, synthetic. The status form is closed because the API is read-only."
        bare
      >
        <DeskReportsProvider value={desk}>
          <CitizenInbox reports={desk} access={ACCESS} />
        </DeskReportsProvider>
      </Demo>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Demo label="Alert actions" note="The 08:40 run's queue, read from the API." bare>
          <AlertPanel runId={RUN_ID} city="mumbai" officer={OFFICER} />
        </Demo>
        <Demo label="Street closures" note="Closures read from the ops overlay." bare>
          <ClosurePanel runId={RUN_ID} city="mumbai" officer={OFFICER} />
        </Demo>
        <Demo label="Pump status" note="The synthetic fleet, read from the API." bare>
          <PumpPanel runId={RUN_ID} city="mumbai" officer={OFFICER} />
        </Demo>
        <Demo label="Situation note" note="Composed on the page and copied; nothing is sent." bare>
          <SituationNote officer={OFFICER} />
        </Demo>
      </div>
    </div>
  );
}
