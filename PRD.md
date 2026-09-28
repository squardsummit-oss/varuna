# VARUNA citizen dashboard, authority desk and rural advisory — product requirements

**Status:** draft for build, 2026-09-19 · **Owner:** Team VIT · **Spec above this one:** `SPEC.md`
(the build spec). Where this file and `SPEC.md` disagree on scope, `SPEC.md` wins; where this
file adds a surface `SPEC.md` does not name, it is additive and carries its own acceptance.

---

## 1. Why this exists

VARUNA today is an operator's product. The console, the drain X-ray, the route planner and the
verification page are built for someone paid to watch a storm. A judge who clicks the landing
page's only citizen link — one underlined "Public map" in the footer — lands on a screen that:

- lists **the twelve deepest streets in the whole city**, sorted by depth, with no idea where the
  reader is (`app/map/map-screen.tsx:62-88`; there is no geolocation on the screen);
- offers a "Save this location" button that saves a **clock reading, not a location**
  (`map-screen.tsx:199-206` — the stored object has `id` and `name` only);
- opens on the **09:10 cycle**, the calm hour after the storm, because it asks for the newest run;
- and cannot route anywhere at all. Routing lives only on `/route`, behind the operator chrome.

Meanwhile the thing that would matter most to a person in Mumbai — *can I get home, and by which
road* — is already computed every cycle and thrown away at the operator's screen edge.

This document specifies three new surfaces that turn the existing engines toward the people the
Problem Statement is actually about, and the repairs the demo path needs on the way.

---

## 2. Who we are building for

| User | What they want in one sentence | What they get today |
|---|---|---|
| **Commuter (Mumbai)** | "Can I get from here to there in the next hour, and if not, when?" | A list of the city's worst streets, no route, no location |
| **Ward officer / control room** | "Something changed on the ground — a pump moved, a road is shut — put it into the system now." | One working write path in the whole API (`POST /v1/reports`); every other authority action answers 501 |
| **Rural / peri-urban resident** | "Is the crossing on my road passable, told in words on a 2G phone?" | Nothing. Every screen assumes a smartphone, a map canvas and a built city |
| **Judge (SIH)** | "Is this real, is it honest, and would anyone use it?" | Two of three: real and honest, but visibly an operator tool |

---

## 3. What we are building

### 3.1 `/dashboard` — the citizen panel (primary)

The screen a person opens during a storm. Google Maps underneath (their map, their labels, their
familiar gestures), VARUNA's water and routes drawn on top.

1. **Where am I, and is it wet here.** Geolocation with a manual fallback; the streets around the
   reader coloured by the depth their vehicle stops in; the honesty line naming the run and its
   time.
2. **Can I get there.** Origin and destination, vehicle profile, "Find the safe way". Two answers
   side by side: the shortest way and VARUNA's way, with **plain-language reasons** (§3.4).
3. **Why this way, and not the obvious one.** Named junctions, the depth in centimetres, the
   clock time the vehicle would have reached them, and what the drain under that street was
   designed for.
4. **You are not the only one being routed.** Up to three safe corridors, with the reader assigned
   to one and told plainly that the traffic is being spread on purpose (§3.5).
5. **Weather now, and the next three hours.** A chip in the header opening a dialog with live
   observed conditions over Mumbai (§3.6) — the only genuinely live number on the screen, and
   labelled as such beside the reconstructed replay.
6. **What is being done about it.** The pumps dispatched near the reader's route and the minutes
   above 45 cm they are expected to remove, labelled as an emulator estimate.
7. **Tell us what you see.** The existing three-step report flow, reachable in one tap, because it
   is the one loop that already feeds the engines (`POST /v1/reports` → `data/reports/inbox.jsonl`
   → Pulse's assimilation).

### 3.2 `/authority` — the ward officer's desk

Not a dashboard of buttons. One rule decides what goes on this screen: **does an engine consume
it, or is it a note?** Both are allowed; they are never dressed the same.

Consumed by an engine:

- **Close or reopen a street** — a manual closure the router honours at read time, on top of the
  forecast, so a baked run stays byte-identical (rule 8) while the next route avoids the street.
- **Mark a pump unavailable, or move it** — the optimiser reads pump status, which today it
  ignores (the field is never read in `pumps.py`).
- **Dispatch the plan** — `POST /v1/pumps/optimise` and `/dispatch`, today 501 stubs with request
  models already written.
- **Acknowledge or escalate an alert** — `POST /v1/alerts/{id}/ack` and `/escalate`, also 501
  today, with `user` and `note` already in the request model. Recorded in an append-only log.
- **Read the citizen inbox** — `GET /v1/reports` already serves it newest-first and nothing in the
  UI consumes it.

A note, and labelled as one:

- Free-text situation notes against a ward. They appear in the log and change no forecast.

**Access:** a shared passphrase held in an environment variable, checked server-side, rate-limited.
This is prototype-grade and says so on the screen. It is not authentication and must never be
described as such.

### 3.3 `/rural` — the low-bandwidth advisory

One server-rendered page, no map canvas, no client JavaScript required, under 30 KB: the crossings
and roads on a chosen route in words, with "passable until HH:MM", the vehicle it applies to, and a
share link carrying the same text. It is printable, and it is what a panchayat officer reads aloud.

The honest framing, which the screen states: VARUNA's city pipeline runs on any bounding box of
open data, so a rural AOI is a build away — but the pipeline infers drains from **road density**,
which is an urban assumption, and a village AOI has no chronic-spot register to calibrate against.
The rural advisory therefore ships against a **built AOI** (Mumbai today, a second AOI if time
allows), and says what it does not know.

### 3.4 Plain-language explanations

Every claim on the citizen screen must be traceable to an artifact. Three sentence shapes, and
nothing else:

1. **What is avoided, from the run.**
   "Avoided Dr Ambedkar Road near Hindmata: 47 cm at 08:20, deeper than a car can cross (30 cm)."
   Every number here is already in the route response.
2. **What the drain was built for, from the city.**
   "The drain under this junction was sized for 25 mm of rain an hour. This cycle's forecast peaks
   at 61 mm an hour." Every pipe carries `design_intensity_mm_h`; 35,850 of 49,770 are at 25 mm/h.
3. **What this cycle says, from the products.**
   "On this cycle it stays under 5 cm until 07:55, then rises to 47 cm by 08:20."

**Explicitly refused:** "this junction floods once 5 cm of rain falls" as a general rule. It was
measured and it is not a property the artifacts hold: depth exists per run, per cycle, per 5-minute
step, and inverting the emulator produces a drizzle-level threshold (median 3.4 mm/h) that would be
a fabricated number. Sentence 2 is the honest version of the question, and it is the stronger
answer because it comes from the design norm the city actually built to.

### 3.5 Route spreading — the jam we would otherwise cause

If every citizen is handed the same safe corridor, the safe corridor becomes the jam. VARUNA
therefore returns **up to three safe corridors** and assigns each request to one, deterministically,
weighted by each corridor's spare road capacity, so repeated requests spread rather than pile up.

What is honest here: the **mechanism** is real (three genuinely different routes, all under the
vehicle's depth threshold, each with its own ETA). The **demand** is not measured — there are no
live traffic counts — so the split is a stated policy, not a traffic model, and the screen says
exactly that. Spreading is offered, never forced: the reader sees all three and may pick.

### 3.6 What is live, and what is a replay

The single most dangerous word on this screen is "live". Three states, three labels, never mixed
on one axis:

| Thing | State | Label on screen |
|---|---|---|
| Weather over Mumbai now and +3 h | **Live**, Open-Meteo, no key, CC BY 4.0 | "Live weather · Open-Meteo · updated 2 min ago" |
| Citizen reports | **Live**, ours | "Your report reaches the next cycle" |
| Streets, depths, drains, routes, alerts, pumps, tide | **Reconstructed replay of 2 July 2019** | "Reconstructed replay · 2 Jul 2019 06:40 IST" |

The weather dialog shows the live outlook and the replay's own rain side by side, on separate axes,
with a sentence explaining why a 2019 storm is on the map while today's sky is in the chip.

---

## 4. What we are not building

- **No authentication system.** A passphrase is a gate, not auth, and is labelled as such.
- **No Google Directions, Places, Geocoding or Static Maps.** The key is referrer-restricted and
  those APIs refuse it (measured), and Places (New) is not enabled on the project. Routing is
  VARUNA's, which is the point of the product.
- **No live tide.** The only key-free marine source disclaims coastal accuracy and its range is far
  under Mumbai's; the tide stays the sourced illustrative series in the DEM frame (ADR-0055).
- **No decoded live radar.** IMD's Mumbai radar imagery is public but is a GIF product; decoding it
  is P1 (`P2.9`) and is out of scope. It may be shown as an attributed image if time allows.
- **No new physics.** Every number on the new screens comes from an existing engine or is refused.

---

## 5. Success criteria

A build is done when all of these are true and measured:

1. From `/`, one click reaches `/dashboard`; the entry animation runs globe → India → Mumbai and
   hands over to a Google map already framed on the city, with zero console errors.
2. On `/dashboard`, a route from the reader's position (or a picked point) to a destination returns
   in under 2 s and renders two routes, up to three corridors, and at least one plain-language
   reason drawn from the run.
3. Every number on `/dashboard` traces to an artifact or a labelled live source; a reviewer can
   name the file behind each one.
4. The weather dialog shows Mumbai's current conditions and the next three hours from a live
   source, attributed, and degrades to a labelled cached copy with its age when the network is gone.
5. `/authority` can close a street and the next route avoids it; can mark a pump unavailable and the
   optimiser stops assigning it; can acknowledge an alert and the state survives a reload.
6. `/rural` renders under 30 KB with JavaScript disabled and states what it does not know.
7. Chennai onboarding runs end to end on the laptop, its finish card opens a **Chennai** console,
   and the wizard's map stacks its layers as the steps complete.
8. The console's layers panel scrolls at 1366 × 768, every map fills its pane on first paint, and
   satellite imagery stays sharp past zoom 17.
9. `pnpm lint && pnpm typecheck && pnpm test && uv run pytest` pass; `pnpm lint:design` is clean;
   axe finds 0 violations on the three new screens; CI is green.

---

## 6. Risks we are carrying knowingly

| Risk | Mitigation |
|---|---|
| Google refuses the key on the deployed domain (referrer list) | The map falls back to the existing deck.gl + Esri renderer with a labelled notice; nothing on the screen depends on Google being reachable |
| "Live weather" invites "why do you need your own nowcast?" | The dialog answers it in one line: a 12 km grid versus 30 m streets, with the fan chart beside it |
| Route spreading could be read as a traffic model | The screen states that the split is a policy and the demand is not measured |
| An authority edit could break byte-identical bakes (rule 8) | Edits are an append-only overlay applied at read time; no product file is rewritten |
| A public authority page is an unauthenticated write surface | Passphrase, rate limit, append-only log, and off by default on the deployed API |
| The demo trip (KEM → Sion) shows no detour — measured: both routes identical, 0 avoided | Choose the citizen demo trip from segments that are genuinely deep on the cycle (78 above 60 cm, 230 above 45 cm on the 08:40 run) and keep the ambulance comparison as the honest coda |
