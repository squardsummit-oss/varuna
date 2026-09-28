"use client";

/**
 * The desk's door (UI_SPEC 6, TECH_SPEC 3.6, task D-15).
 *
 * **It says what it is.** "Prototype access. This is a shared passphrase, not a login" is printed
 * above the field, verbatim, because PRD 3.2 refuses to let this be described as authentication.
 * There is no account, no session cookie and no identity: the passphrase says *someone at the
 * desk*, and the name field beside it is the only attribution the log will ever have.
 *
 * **Four states, and each one is different.** A screen that shows one "access denied" for all of
 * them wastes the officer's time, because the fix differs:
 *
 * - `checking` - asking the API whether it accepts writes at all.
 * - `disabled` - it does not: `VARUNA_OPS_PASSPHRASE` is unset where the API runs, which is how
 *   the deployed API ships. The desk then offers nothing it cannot do and says why, rather than
 *   taking a passphrase it has nowhere to send.
 * - `unreachable` - the API did not answer. Nothing about the desk can be known.
 * - `locked` - it accepts writes and this browser is holding nothing, so the door is shown.
 *
 * **One sentence for every wrong passphrase.** The API distinguishes a missing passphrase from a
 * wrong one; this screen does not, because a door that answers differently for a blank than for a
 * near miss tells an attacker which half they got right. The rate limit is reported as itself,
 * with the API's own wait.
 */

import { KeyRound, Lock, ShieldOff, WifiOff } from "lucide-react";
import { useCallback, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  checkPassphrase,
  describeRefusal,
  GATE_NOTE,
  OPS_HEADER,
  OPS_PASSPHRASE_ENV,
  writePassphrase,
  type OpsRefusal,
} from "@/lib/api/ops";

/** What the desk knows about the API it is talking to. */
export type GateStatus = "checking" | "disabled" | "unreachable" | "locked";

export interface PassphraseGateProps {
  status: GateStatus;
  /** The API's own sentence for `disabled` and `unreachable`. */
  reason?: string | null;
  /** Called once a passphrase has been accepted by the API, with the officer's name. */
  onOpen: (officer: string) => void;
  className?: string;
}

/** The name the log carries when the officer does not give one; matches the API's default. */
export const DEFAULT_OFFICER = "ward officer";

/** A note that states why writes are off, in any wording the API has used for it. */
const READ_ONLY_NOTE = /read-only|writes are disabled|is not set|is unset/i;

/**
 * The note in `GET /v1/ops/log`'s `notes` that says why this API takes no writes, or null.
 *
 * **Chosen by what it says, not by where it sits.** The list also carries the overlay's note and a
 * note about officer names that ends "Send the desk passphrase in X-Varuna-Ops" - the opposite of
 * the reason when the API holds no passphrase to check. The desk once printed the last note, which
 * is that one, where the read-only reason belonged. When no note states the reason the gate prints
 * its own sentence naming the variable.
 */
export function writesDisabledReason(notes: readonly string[] | null | undefined): string | null {
  return notes?.find((note) => READ_ONLY_NOTE.test(note)) ?? null;
}

export function PassphraseGate({ status, reason, onOpen, className }: PassphraseGateProps) {
  const [passphrase, setPassphrase] = useState("");
  const [officer, setOfficer] = useState(DEFAULT_OFFICER);
  const [checking, setChecking] = useState(false);
  const [refusal, setRefusal] = useState<OpsRefusal | null>(null);

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (checking) return;
      setChecking(true);
      setRefusal(null);
      const failure = await checkPassphrase(passphrase);
      setChecking(false);
      if (failure) {
        setRefusal(failure);
        return;
      }
      // Held only once the API has accepted it, so a tab never carries a passphrase that would
      // be refused by the thing it is for.
      writePassphrase(passphrase);
      setPassphrase("");
      onOpen(officer.trim() || DEFAULT_OFFICER);
    },
    [checking, officer, onOpen, passphrase],
  );

  if (status === "checking") {
    return (
      <Panel className={className} title="Ward officer access">
        <div className="max-w-[48ch] space-y-3">
          <p className="type-small text-text-2">
            Asking the API whether it accepts authority edits.
          </p>
          <Skeleton className="h-11" />
        </div>
      </Panel>
    );
  }

  if (status === "unreachable") {
    return (
      <Panel className={className} title="The desk cannot reach VARUNA">
        <div className="flex items-start gap-3">
          <WifiOff className="text-status-degraded mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div className="space-y-2">
            <p className="type-small text-text">
              {reason ?? "The VARUNA API did not answer, so nothing about this desk can be known."}
            </p>
            <p className="type-micro text-text-3">
              Nothing was sent. The forecast screens read the same API, so they are unavailable too.
            </p>
          </div>
        </div>
      </Panel>
    );
  }

  if (status === "disabled") {
    return (
      <Panel className={className} title="This API is read-only">
        <div className="flex items-start gap-3">
          <ShieldOff className="text-status-degraded mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div className="max-w-[72ch] space-y-2">
            <p className="type-small text-text">
              {reason ??
                `${OPS_PASSPHRASE_ENV} is not set where this API runs, so there is nothing to check a passphrase against and every authority edit is refused.`}
            </p>
            <p className="type-micro text-text-3">
              The deployed API leaves it unset on purpose: a shared passphrase on a public address
              is not access control. Set {OPS_PASSPHRASE_ENV} where the API runs and restart it to
              open the desk on this machine.
            </p>
            <p className="type-micro text-text-3">
              The citizen inbox and the ops log below are public reads and still work.
            </p>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <Panel className={className} title="Ward officer access">
      <form className="max-w-[48ch] space-y-4" onSubmit={submit}>
        <div className="flex items-start gap-3">
          <Lock className="text-text-3 mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <p className="type-small text-text-2">{GATE_NOTE}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ops-passphrase" className="type-small text-text">
            Passphrase
          </Label>
          <Input
            id="ops-passphrase"
            type="password"
            autoComplete="off"
            className="h-11"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            placeholder="Shared desk passphrase"
            aria-describedby="ops-passphrase-note"
          />
          <p id="ops-passphrase-note" className="type-micro text-text-3">
            Sent in the {OPS_HEADER} header for this tab only. It is never saved to this browser
            past the tab, never put in a link and never written to the log.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ops-officer" className="type-small text-text">
            Your name, for the log
          </Label>
          <Input
            id="ops-officer"
            className="h-11"
            value={officer}
            onChange={(event) => setOfficer(event.target.value)}
            maxLength={80}
            aria-describedby="ops-officer-note"
          />
          <p id="ops-officer-note" className="type-micro text-text-3">
            The passphrase is shared, so this name is the only thing that says who acted. It is
            recorded on every row.
          </p>
        </div>

        {refusal ? (
          <p role="alert" className="type-small text-danger">
            {describeRefusal(refusal)}
          </p>
        ) : null}

        <Button type="submit" className="h-11 px-4" disabled={checking || !passphrase.trim()}>
          <KeyRound className="size-4" aria-hidden="true" />
          {checking ? "Checking" : "Open the desk"}
        </Button>
      </form>
    </Panel>
  );
}
