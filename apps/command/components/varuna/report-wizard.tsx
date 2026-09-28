"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { Camera, Check, CloudOff, Image as ImageIcon, ImageOff, MapPin, Waves } from "lucide-react";

import { rememberMyReport } from "@/components/citizen/my-reports";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { DepthChips, DEPTH_HINT_OPTIONS, type DepthHint } from "@/components/varuna/depth-chips";
import { Panel } from "@/components/varuna/panel";
import { errorMessage, useSubmitReport } from "@/lib/api";
import { photoTooLarge } from "@/lib/api/reports";
import type { ReportResponse } from "@/lib/api/schemas";
import { usePublicLocale, usePublicT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Hindmata junction, Dadar East: the chronic spot the demo report is filed from. */
export const DEFAULT_LAT = 19.012;
export const DEFAULT_LON = 72.841;

const DASHBOARD_ROUTE = "/dashboard" as Route;
const MAP_ROUTE = "/map" as Route;

/** `ReportInput.text` and the API's `ReportRequest.text`: the reporter's own words. */
export const REPORT_NOTE_MAX = 280;

// ---------------------------------------------------------------------------------------------
// The photo, prepared on the phone
// ---------------------------------------------------------------------------------------------

/** A file larger than this is refused before it is decoded: decoding it could exhaust a phone. */
export const PHOTO_FILE_MAX_BYTES = 15_000_000;
/** The longest edge of the photo that is sent; the API's full size is the same 1280 px. */
export const PHOTO_MAX_EDGE_PX = 1280;
/**
 * JPEG qualities tried in order. 0.8 fits almost every 1280 px photo inside the API's
 * `photo_data_url` limit; the lower two exist for a busy scene that does not, so it is shrunk
 * rather than refused.
 */
export const PHOTO_JPEG_QUALITIES = [0.8, 0.65, 0.5] as const;

export type PreparedPhoto =
  | { kind: "ready"; dataUrl: string; width: number; height: number; quality: number }
  /** Over {@link PHOTO_FILE_MAX_BYTES}; never decoded. */
  | { kind: "too-big-file"; bytes: number }
  /** The browser could not decode it (HEIC on an older browser, a damaged file). */
  | { kind: "undecodable" }
  /** Decoded and resized, and still longer than the API accepts at the lowest quality. */
  | { kind: "too-big-encoded" };

/** The size a `width` x `height` image is drawn at so neither edge exceeds `maxEdge`. */
export function fitWithin(
  width: number,
  height: number,
  maxEdge: number = PHOTO_MAX_EDGE_PX,
): { width: number; height: number } {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/**
 * Turn the file a reporter picked into the `photo_data_url` a report carries.
 *
 * The file is decoded with its EXIF orientation applied, drawn to a canvas no larger than
 * {@link PHOTO_MAX_EDGE_PX} on its longest edge, and re-encoded as JPEG. A canvas writes pixels
 * and nothing else, so the result carries no EXIF block: no GPS position, no camera serial, no
 * time. The report's own coordinates are the only location that leaves the phone.
 *
 * The result is a data URL inside the JSON body, not a multipart upload, because the offline
 * service worker queues the request's text and replays it later; a report sent now and one sent
 * when the connection returns are the same request.
 */
export async function preparePhoto(file: Blob): Promise<PreparedPhoto> {
  if (file.size > PHOTO_FILE_MAX_BYTES) return { kind: "too-big-file", bytes: file.size };
  if (typeof createImageBitmap !== "function") return { kind: "undecodable" };
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return { kind: "undecodable" };
  }
  try {
    if (!(bitmap.width > 0 && bitmap.height > 0)) return { kind: "undecodable" };
    const { width, height } = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return { kind: "undecodable" };
    // JPEG has no transparency; without a fill a transparent PNG's clear pixels turn black.
    context.fillStyle = "white";
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    for (const quality of PHOTO_JPEG_QUALITIES) {
      const dataUrl = canvas.toDataURL("image/jpeg", quality);
      // A browser that cannot write JPEG answers with PNG, which is neither small nor what the
      // API expects; treat it as a photo this browser cannot prepare.
      if (!dataUrl.startsWith("data:image/jpeg")) return { kind: "undecodable" };
      if (!photoTooLarge(dataUrl)) return { kind: "ready", dataUrl, width, height, quality };
    }
    return { kind: "too-big-encoded" };
  } finally {
    bitmap.close();
  }
}

type PhotoState = { kind: "none" } | { kind: "preparing" } | PreparedPhoto;

/**
 * What the confirmation says about the photo, from the fields the API answered with - never
 * from what the phone sent alone. `unconfirmed` is an API that predates `photo_stored` and so
 * did not say either way.
 */
export type PhotoOutcome = "stored" | "not-stored" | "unconfirmed" | "saved-offline";

export function photoOutcome(data: ReportResponse, photoSent: boolean): PhotoOutcome | null {
  if (!photoSent) return null;
  if (data.queued === true) return "saved-offline";
  if (data.photo_stored === true) return "stored";
  if (data.photo_stored === false) return "not-stored";
  return "unconfirmed";
}

const STEPS = [
  { id: 1, key: "stepLocation" },
  { id: 2, key: "stepPhoto" },
  { id: 3, key: "stepDepth" },
] as const;

/**
 * What a confirmation can honestly say, from the answer `POST /v1/reports` gave.
 *
 * - `offline`: a service worker answered 202 `{"queued": true}` because the phone is offline. The
 *   report is on the phone, not at VARUNA, so nothing may claim it changed a forecast.
 * - `queued`: the API accepted it for the next cycle; no count exists yet (`feedback_streets` is
 *   null until the EnKF has assimilated it, SPEC.md 11.6).
 * - `improved` / `assimilated`: a count arrived, positive or zero.
 */
export type ReportOutcome = "offline" | "queued" | "improved" | "assimilated";

export function reportOutcome(data: ReportResponse): ReportOutcome {
  if (data.queued === true) return "offline";
  const streets = data.feedback_streets;
  if (typeof streets !== "number") return "queued";
  return streets > 0 ? "improved" : "assimilated";
}

export interface ReportWizardProps {
  className?: string;
}

/**
 * Three steps to a citizen observation (SPEC.md section 7.11): where, an optional photo, and
 * how deep. `POST /v1/reports` accepts the report and queues it for the next cycle, so the API's
 * own message is shown verbatim in English rather than replaced by a claim about what the report
 * changed. In Hindi and Marathi the API's English sentence would be the only English on the
 * screen, so the same state is said in the reader's language instead.
 *
 * The photo is prepared on the phone ({@link preparePhoto}) and the confirmation says what the
 * API did with it: stored, or not stored and why - the deployed API keeps no photos, and its
 * reason is printed as it wrote it (in English, marked `lang="en"`, under a translated line).
 * An id the API minted is remembered in `varuna.my-reports` so the dashboard can follow it.
 */
export function ReportWizard({ className }: ReportWizardProps) {
  const t = usePublicT("report");
  const depth = usePublicT("depth");
  const english = (usePublicLocale()?.locale ?? "en") === "en";
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [lat, setLat] = useState(String(DEFAULT_LAT));
  const [lon, setLon] = useState(String(DEFAULT_LON));
  const [locating, setLocating] = useState(false);
  const [locateError, setLocateError] = useState<
    { kind: "none" } | { kind: "failed"; reason: string } | null
  >(null);
  const [photo, setPhoto] = useState<PhotoState>({ kind: "none" });
  const [depthHint, setDepthHint] = useState<DepthHint | null>(null);
  const [note, setNote] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  /** The pick being prepared; a slower earlier pick must not overwrite a later one. */
  const pickRef = useRef(0);

  const submit = useSubmitReport();

  const useMyLocation = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setLocateError({ kind: "none" });
      return;
    }
    setLocating(true);
    setLocateError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLat(position.coords.latitude.toFixed(5));
        setLon(position.coords.longitude.toFixed(5));
        setLocating(false);
      },
      (error) => {
        setLocating(false);
        setLocateError({ kind: "failed", reason: error.message });
      },
      { enableHighAccuracy: true, timeout: 8_000 },
    );
  }, []);

  const onPhotoChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const pick = ++pickRef.current;
    const file = event.target.files?.[0];
    if (!file) {
      setPhoto({ kind: "none" });
      return;
    }
    setPhoto({ kind: "preparing" });
    void preparePhoto(file).then(
      (result) => {
        if (pickRef.current === pick) setPhoto(result);
      },
      () => {
        if (pickRef.current === pick) setPhoto({ kind: "undecodable" });
      },
    );
  }, []);

  const clearPhoto = useCallback(() => {
    pickRef.current += 1;
    setPhoto({ kind: "none" });
    if (fileRef.current) fileRef.current.value = "";
  }, []);

  const send = useCallback(() => {
    if (!depthHint) return;
    const text = note.trim().slice(0, REPORT_NOTE_MAX);
    submit.mutate(
      {
        ts: new Date().toISOString(),
        lat: Number(lat),
        lon: Number(lon),
        depth_hint: depthHint,
        text: text || undefined,
        photo_data_url: photo.kind === "ready" ? photo.dataUrl : undefined,
        source: "public-report",
      },
      {
        onSuccess: (data) => {
          // Only an id the API minted is remembered. A report the service worker queued offline
          // has none yet, and the dashboard must not follow a report VARUNA has not received.
          if (data.queued !== true && typeof data.id === "string") rememberMyReport(data.id);
        },
      },
    );
  }, [depthHint, lat, lon, note, photo, submit]);

  if (submit.isSuccess) {
    // SPEC.md 11.6 defines the feedback count as the segments whose p50 moved by more than 3 cm
    // once the EnKF has assimilated the report - which happens on the *next* cycle, not inside the
    // request a citizen just pressed Send on. Until that number exists the API sends
    // `feedback_streets: null` with a queued message; printing `?? 0` there headlined an improved
    // forecast for a count of zero streets - a claim of effect over a number nobody computed
    // (rule 6). So the count is shown only when a count arrives.
    const outcome = reportOutcome(submit.data);
    const streets = submit.data.feedback_streets ?? 0;
    // The API's own wording in English, so the screen never invents a state the service did not
    // report; the fallback covers a service that accepted the report without one.
    const queuedMessage =
      english && submit.data.message ? submit.data.message : t("queuedFallback");
    const heading = {
      offline: t("savedOffline"),
      queued: t("sent"),
      improved: t("improved", { count: streets }),
      assimilated: t("assimilated"),
    }[outcome];
    const body = {
      offline: t("savedOfflineBody"),
      queued: queuedMessage,
      improved: t("improvedBody"),
      assimilated: t("assimilatedBody"),
    }[outcome];
    const Icon = outcome === "offline" ? CloudOff : Check;

    // The photo, as the API reported it. When it was not stored the API says why (on the
    // deployed API: photos are kept only on the demo laptop), and that reason is printed as the
    // API wrote it unless the body above already carries it, as the English `message` does.
    const photoSent = typeof submit.variables?.photo_data_url === "string";
    const photoResult = photoOutcome(submit.data, photoSent);
    const apiPhotoNote =
      typeof submit.data.photo_note === "string" && submit.data.photo_note.trim()
        ? submit.data.photo_note.trim()
        : null;
    const photoReason =
      photoResult === "not-stored" && apiPhotoNote && !body.includes(apiPhotoNote)
        ? apiPhotoNote
        : null;
    const photoLine =
      photoResult === null
        ? null
        : {
            stored: t("photoStored"),
            "not-stored": t("photoNotStored"),
            unconfirmed: t("photoNotConfirmed"),
            "saved-offline": t("photoSavedOffline"),
          }[photoResult];
    const PhotoIcon =
      photoResult === "stored" || photoResult === "saved-offline" ? ImageIcon : ImageOff;

    return (
      <Panel className={cn("p-6", className)}>
        <div className="flex flex-col items-start gap-3" data-outcome={outcome}>
          <Icon size={20} strokeWidth={1.75} aria-hidden="true" className="text-tide" />
          <h2 className="font-display text-h2 tracking-display text-text num font-semibold">
            {heading}
          </h2>
          <p className="type-body text-text-2 max-w-[60ch]">{body}</p>
          {photoLine ? (
            <p
              className="type-small text-text max-w-[60ch]"
              data-photo-outcome={photoResult ?? undefined}
            >
              <PhotoIcon
                size={16}
                strokeWidth={1.75}
                aria-hidden="true"
                className="text-text-2 mr-2 inline align-[-3px]"
              />
              {photoLine}
              {photoReason ? (
                <>
                  {" "}
                  <span lang="en" className="text-text-2">
                    {photoReason}
                  </span>
                </>
              ) : null}
            </p>
          ) : null}
          {outcome === "queued" ? (
            <p className="type-small text-text-3 max-w-[60ch]">{t("queuedFollowUp")}</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              size="lg"
              className="h-11"
              onClick={() => {
                submit.reset();
                setStep(1);
                setDepthHint(null);
                setNote("");
                clearPhoto();
              }}
            >
              {t("another")}
            </Button>
            {/* Navigation, so real links: a screen reader announces where each one goes. */}
            <Link
              href={DASHBOARD_ROUTE}
              className={buttonVariants({ variant: "outline", size: "lg", className: "h-11" })}
            >
              {t("backToDashboard")}
            </Link>
            <Link
              href={MAP_ROUTE}
              className={buttonVariants({ variant: "ghost", size: "lg", className: "h-11" })}
            >
              {t("backToMap")}
            </Link>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <ol className="flex items-center gap-2" aria-label={t("progress")}>
        {STEPS.map((s) => {
          const state = s.id === step ? "current" : s.id < step ? "done" : "todo";
          return (
            <li key={s.id} className="flex flex-1 flex-col gap-1.5">
              <span
                aria-hidden="true"
                className={cn("rounded-chip h-1", state === "todo" ? "bg-line" : "bg-tide")}
              />
              <span
                className={cn("type-micro", state === "current" ? "text-text" : "text-text-3")}
                aria-current={state === "current" ? "step" : undefined}
              >
                <span className="num">{s.id}.</span> {t(s.key)}
              </span>
            </li>
          );
        })}
      </ol>

      {step === 1 ? (
        <Panel title={t("whereTitle")} description={t("whereDescription")}>
          <div className="flex flex-col gap-4 p-4">
            <div className="flex flex-wrap items-center gap-2">
              {/* 44 px: every control a citizen touches (SPEC.md 7.11). */}
              <Button
                variant="outline"
                size="lg"
                className="h-11"
                onClick={useMyLocation}
                disabled={locating}
              >
                <MapPin aria-hidden="true" />
                {locating ? t("findingLocation") : t("useMyLocation")}
              </Button>
              <span className="type-micro text-text-3">{t("orType")}</span>
            </div>
            {locateError ? (
              <p role="alert" className="type-small text-text-2">
                {locateError.kind === "none"
                  ? t("noGeolocation")
                  : t("locationUnavailable", { reason: locateError.reason })}
              </p>
            ) : null}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="report-lat">{t("latitude")}</Label>
                <Input
                  id="report-lat"
                  inputMode="decimal"
                  className="num h-11"
                  value={lat}
                  onChange={(event) => setLat(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="report-lon">{t("longitude")}</Label>
                <Input
                  id="report-lon"
                  inputMode="decimal"
                  className="num h-11"
                  value={lon}
                  onChange={(event) => setLon(event.target.value)}
                />
              </div>
            </div>
            <div className="flex justify-end">
              <Button size="lg" className="h-11" onClick={() => setStep(2)}>
                {t("continueToPhoto")}
              </Button>
            </div>
          </div>
        </Panel>
      ) : null}

      {step === 2 ? (
        <Panel title={t("photoTitle")} description={t("photoDescription")}>
          <div className="flex flex-col gap-4 p-4">
            <input
              ref={fileRef}
              id="report-photo"
              type="file"
              accept="image/*"
              capture="environment"
              aria-label={t("photoTitle")}
              onChange={onPhotoChange}
              className="type-small text-text-2 file:rounded-control file:border-line file:bg-well file:type-small file:text-text block w-full file:mr-3 file:h-11 file:border file:px-3"
            />
            <p className="type-small text-text-2 max-w-[60ch]">{t("photoUse")}</p>
            {photo.kind === "preparing" ? (
              <p role="status" className="type-small text-text-2 flex items-center gap-2">
                <Camera size={16} strokeWidth={1.75} aria-hidden="true" />
                {t("photoPreparing")}
              </p>
            ) : photo.kind === "ready" ? (
              <div className="flex flex-col items-start gap-2">
                {/* eslint-disable-next-line @next/next/no-img-element -- a local data URL, never optimised */}
                <img
                  src={photo.dataUrl}
                  alt={t("photoPicked")}
                  className="rounded-panel border-line max-h-56 w-full border object-cover"
                />
                <p className="type-micro text-text-3 num">
                  {t("photoReady", { width: String(photo.width), height: String(photo.height) })}
                </p>
                <Button variant="outline" size="lg" className="h-11" onClick={clearPhoto}>
                  <ImageOff aria-hidden="true" />
                  {t("removePhoto")}
                </Button>
              </div>
            ) : photo.kind === "none" ? (
              <p className="type-small text-text-3 flex items-center gap-2">
                <Camera size={16} strokeWidth={1.75} aria-hidden="true" />
                {t("noPhoto")}
              </p>
            ) : (
              // Refused: said plainly, and the report can still be sent without it.
              <p role="alert" className="type-small text-text max-w-[60ch]">
                {photo.kind === "too-big-file"
                  ? t("photoTooBig", { mb: (photo.bytes / 1_000_000).toFixed(1) })
                  : photo.kind === "undecodable"
                    ? t("photoUnreadable")
                    : t("photoStillTooBig")}
              </p>
            )}
            <div className="flex flex-wrap justify-between gap-2">
              <Button variant="ghost" size="lg" className="h-11" onClick={() => setStep(1)}>
                {t("backToLocation")}
              </Button>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="lg"
                  className="h-11"
                  onClick={() => {
                    clearPhoto();
                    setStep(3);
                  }}
                >
                  {t("skipPhoto")}
                </Button>
                <Button
                  size="lg"
                  className="h-11"
                  onClick={() => setStep(3)}
                  disabled={photo.kind === "preparing"}
                >
                  {t("continueToDepth")}
                </Button>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      {step === 3 ? (
        <Panel title={t("depthTitle")} description={t("depthDescription")}>
          <div className="flex flex-col gap-4 p-4">
            <DepthChips value={depthHint} onValueChange={setDepthHint} />
            <p className="type-micro text-text-3 flex items-center gap-2">
              <Waves size={16} strokeWidth={1.75} aria-hidden="true" />
              {depthHint
                ? t("filedAs", {
                    hint: depth("about", {
                      cm: DEPTH_HINT_OPTIONS.find((o) => o.hint === depthHint)?.cm ?? 0,
                    }),
                  })
                : t("pickDepth")}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="report-note">{t("noteLabel")}</Label>
              <Textarea
                id="report-note"
                value={note}
                maxLength={REPORT_NOTE_MAX}
                rows={3}
                aria-describedby="report-note-hint report-note-count"
                className="min-h-11"
                onChange={(event) => setNote(event.target.value.slice(0, REPORT_NOTE_MAX))}
              />
              <p id="report-note-hint" className="type-micro text-text-3 max-w-[60ch]">
                {t("noteHint")}
              </p>
              <p id="report-note-count" className="type-micro text-text-3 num">
                {t("noteCount", { used: String(note.length), max: String(REPORT_NOTE_MAX) })}
              </p>
            </div>
            <div className="flex flex-wrap justify-between gap-2">
              <Button variant="ghost" size="lg" className="h-11" onClick={() => setStep(2)}>
                {t("backToPhoto")}
              </Button>
              <Button
                size="lg"
                className="h-11"
                onClick={send}
                disabled={!depthHint || submit.isPending}
              >
                {submit.isPending ? t("sending") : t("send")}
              </Button>
            </div>
          </div>
        </Panel>
      ) : null}

      {submit.isError ? (
        <Panel title={t("notAccepted")}>
          <p role="alert" className="type-small text-text-2 p-4">
            {errorMessage(submit.error)}
          </p>
        </Panel>
      ) : null}
    </div>
  );
}
