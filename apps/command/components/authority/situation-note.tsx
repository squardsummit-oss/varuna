"use client";

/**
 * The right column: a situation note against a ward (UI_SPEC 6, PRD 3.2, task D-15).
 *
 * **This column exists to be the opposite of the left one.** Everything on the left is consumed
 * by an engine; a note is read by a person. Dressing the two the same is how a desk starts
 * implying that writing "water at the market" moved the forecast, and PRD 3.2 names that as the
 * one rule this screen is built around.
 *
 * **And today VARUNA cannot store one, so the panel says that instead of pretending.** The ops
 * log accepts six kinds of entry - closure, reopen, pump status, alert acknowledgement,
 * escalation, dispatch (`varuna_route.ops_overlay.KINDS`) - and none of them is a note, while
 * TECH_SPEC 3.6 lists five write endpoints and none of them takes one either. A "Record note"
 * button here would post to a route that does not exist and fail every time, which is worse than
 * a control that names what is missing (SPEC.md 17: never a dead control). What it does offer
 * is real: the note is composed with its ward and its time and copied to the clipboard, so the
 * officer can put it in the record they do have. The result line says exactly that, including
 * the half VARUNA did not do.
 *
 * The fix is small and belongs to whoever owns the API: one entry kind and one route,
 * `POST /v1/ops/notes`, appending `{kind: "note", ward, text, user}` through the same overlay.
 * Then this panel posts, and its result line loses its second sentence.
 */

import { ClipboardCopy } from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/components/varuna/panel";
import { formatIst } from "@/lib/format";

import { ActionResult } from "./action-result";
import { istIso } from "./closure-panel";

/**
 * The line an officer copies: time, place, who, then the note.
 *
 * The ward is typed rather than picked from a list. VARUNA holds no ward register it could offer
 * one from - `city/mumbai` carries no boundary layer the API serves - and a list of ward names
 * written into this file would be five facts about Mumbai that nothing in the repository can
 * check (SPEC.md rule 7).
 */
export function composeNote(ward: string, text: string, officer: string, at: string): string {
  const place = ward.trim();
  return `${formatIst(at)}${place ? ` · ${place}` : ""} · ${officer}: ${text.trim()}`;
}

export interface SituationNoteProps {
  officer: string;
  className?: string;
}

export function SituationNote({ officer, className }: SituationNoteProps) {
  const [ward, setWard] = useState("");
  const [text, setText] = useState("");
  const [copied, setCopied] = useState<{ at: string; failed: boolean } | null>(null);

  const copy = useCallback(async () => {
    const at = istIso(new Date());
    const line = composeNote(ward, text, officer, at);
    try {
      await navigator.clipboard.writeText(line);
      setCopied({ at, failed: false });
    } catch {
      setCopied({ at, failed: true });
    }
    setText("");
  }, [officer, text, ward]);

  return (
    <Panel
      className={className}
      title="Situation note"
      description="For people only; reaches no forecast, route or alert."
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="situation-ward" className="type-small text-text">
            Ward or area
          </Label>
          <Input
            id="situation-ward"
            className="h-11"
            value={ward}
            onChange={(event) => setWard(event.target.value)}
            maxLength={80}
            placeholder="G/North"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="situation-note" className="type-small text-text">
            What is happening
          </Label>
          <Textarea
            id="situation-note"
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={500}
            rows={4}
            placeholder="Two feeder pumps idle at the market; crowd on the east footbridge."
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            className="h-11 px-4"
            disabled={!text.trim()}
            onClick={copy}
          >
            <ClipboardCopy className="size-4" aria-hidden="true" />
            Copy the note
          </Button>
          <Button type="button" className="h-11 px-4" disabled aria-describedby="note-not-stored">
            Record in the log
          </Button>
        </div>

        <p id="note-not-stored" className="type-micro text-text-3 max-w-[72ch]">
          Recording is not built: the ops log has no note entry yet. Copy the note instead.
        </p>

        {copied ? (
          <ActionResult
            outcome="recorded"
            at={formatIst(copied.at)}
            message={
              copied.failed
                ? "This browser refused the clipboard, so the note was not copied. Nothing changed, and nothing was recorded."
                : "Copied. This changed no forecast, no route and no alert - and VARUNA did not record it either."
            }
          />
        ) : null}
      </div>
    </Panel>
  );
}
