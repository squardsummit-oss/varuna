# UI specification — citizen dashboard, authority desk, rural advisory

**Inherits** `SPEC.md` section 6 (design system), section 8 (motion catalogue) and section 6.8
(copy rules) without exception: tokens only, sentence case, no emoji, no spinners, every motion a
catalogue row with a reduced-motion branch.

---

## 1. Entry from the landing page

The hero gains a third control, below the two existing buttons and above the credit line:

```
[ Open the console ]  [ Watch the 2 July 2019 replay ]
Are you not an operator?  Open the citizen dashboard →   (text link, --tide, no arrow glyph)
```

Copy: **"Open the citizen dashboard"**. The footer's "Public map" link stays; `/map` is unchanged.

---

## 2. The globe entry (motion M27, new row in section 8)

| Act | Duration | What the reader sees | Reduced motion |
|---|---|---|---|
| 1 Turn | 1.4 s | A vector Earth on `--ink`, land in `--well`, a graticule at 10°, a soft limb shadow; it turns so India faces the reader | Skipped |
| 2 Approach | 1.6 s | The sphere flattens as the frame scales toward India, whose outline strengthens to `--text-2`; coastline detail rises from 110 m to 50 m | Skipped |
| 3 Arrive | 1.0 s | The frame narrows to the Mumbai AOI; a 900 ms cross-fade hands over to the Google map already framed on the same bounds | Static Mumbai frame, then a cut |

Total 4.0 s, skippable by any key, click or scroll ("Skip" is a text button, bottom right, from
0.6 s). It plays **once per session** (`sessionStorage`), because a judge who returns to the
dashboard should not watch it again.

The handover rule: the map is mounted and framed **behind** the globe before the cross-fade starts,
so no frame shows an unframed world map. This is the same sequence the landing hero already uses
for M1/M26.

---

## 3. `/dashboard` — layout

**Desktop (≥ 1024 px)** — map left, one rail right, nothing floating over the map except the legend:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ VARUNA  Mumbai ▾   Reconstructed replay · 2 Jul 2019 · 06:40 IST   ☁ 28° │ 56 px
├───────────────────────────────────────────────┬──────────────────────────┤
│                                               │  Can I get there?        │
│                GOOGLE MAP                     │  From [ my location ]    │
│        (VARUNA water + routes on top)         │  To   [ pick on map  ]   │
│                                               │  Vehicle ▾  Find the safe│
│                                               │  ─────────────────────── │
│  ┌ Legend ┐                                   │  Why this way            │
│  └────────┘                                   │  Corridor A of 3         │
│                                               │  Pumps near your route   │
├───────────────────────────────────────────────┴──────────────────────────┤
│  Streets near you · passable until …                        Report water │ 88 px
└──────────────────────────────────────────────────────────────────────────┘
```

**Phone (< 1024 px)**: the map fills the viewport; the rail becomes the existing `BottomSheet`
(M24) with three snaps; the weather chip stays in the header; "Report water" is the floating
button, and the sheet carries its own copy of it (as `/map` already does).

Map sizing: the map element is `absolute inset-0` inside a `relative` pane that owns the height, so
it fills its box at every viewport, and `fitBounds` frames the AOI on first paint. No fixed `vh`
band, no fixed pixel height.

---

## 4. The route answer, in words

When a route returns, the rail shows, in this order:

1. **Two ETAs.** "Shortest way: 21 min · Safe way: 27 min (+6 min)". Never a single number without
   its comparison.
2. **Why this way** — one line per reason, at most four, each from `lib/explain.ts`:
   - *avoided*: "Avoids Dr Ambedkar Road near Hindmata — 47 cm at 08:20, deeper than a car can
     cross (30 cm)."
   - *design*: "The drain under that junction was sized for 25 mm of rain an hour; this cycle
     peaks at 61 mm an hour."
   - *timing*: "Your road stays under 5 cm until 07:55, then rises to 47 cm by 08:20."
   - *closure*: "Closed by the ward officer at 08:12 — water main work."
   A reason whose number is missing is **not rendered**; it never degrades to a vague sentence.
3. **Corridor picker.** Three chips, "A · 6 in 10", "B · 3 in 10", "C · 1 in 10", the assigned one
   selected, with the disclosure underneath: *"We spread drivers across three safe roads so the
   safe road does not become the next jam. The split is our policy, not a measured traffic count."*
4. **Pumps near your route.** "P-05 at V B Worlikar Marg · expected to remove 1 h 10 min above
   45 cm" with the chip "Emulator estimate".
5. **Safe until.** "Leave before 08:05. After that this route needs a bus or a truck."

Every block that quotes a run also carries the run stamp in the header, so no number floats free.

---

## 5. Weather chip and dialog

- **Chip** (header): weather icon, temperature, "Live". Keyboard reachable, 44 px target.
- **Dialog** (one, never over another): 
  - Left: **now** — temperature, rain in the last hour, wind, humidity, the source line
    "Open-Meteo · CC BY 4.0 · fetched 2 min ago".
  - Right: **next three hours** — hourly rain in mm with probability, as small bars.
  - Below, boxed and separated: **"What is on the map is not this."** One sentence: the map shows
    the reconstructed 2 July 2019 replay; the weather above is today's sky over Mumbai; the two are
    never combined. Then the differentiator in one line: *"A 12 km forecast tells you it will rain.
    VARUNA tells you which 30 m of street it will sit on."*
  - Offline: the same layout with "Cached 41 min ago · network unavailable" in `--status-degraded`.

---

## 6. `/authority` — the ward officer's desk

Gate first: a single passphrase field, with the honest line *"Prototype access. This is a shared
passphrase, not a login."* Wrong passphrase gets the same message every time and the attempt is
rate-limited.

Two columns, and the split between them is the point:

| Left — "Changes the forecast" | Right — "Recorded only" |
|---|---|
| Close or reopen a street (street search, reason, until) | Situation note against a ward |
| Pump status: available, unavailable, moved | Free text, shown in the log |
| Optimise and dispatch the pump plan | |
| Acknowledge or escalate an alert | |

Each left-column action shows what it changed: *"Dr Ambedkar Road is closed. The next route avoids
it."* Each right-column action shows the opposite: *"Recorded at 08:14. This does not change the
forecast."*

Below both: **the citizen inbox** (`GET /v1/reports`, newest first) with depth chip, time and place,
and **the ops log** (append-only, newest first, user and time on every row).

---

## 7. `/rural` — the low-bandwidth advisory

One column, system fonts, no map, no client JavaScript. Under 30 KB.

```
VARUNA · road advisory
Mumbai central · reconstructed replay of 2 July 2019 · 06:40 IST

On your road
  Sion Circle to Kurla, by two-wheeler
  PASSABLE UNTIL 07:55.  After that: 24 cm at Sion Circle — too deep for a two-wheeler.
  Safer: leave before 07:55, or take LBS Marg (28 min).

What we do not know here
  This advisory covers the built Mumbai area. Outside it we have no drain map and no
  forecast, and we will say so rather than guess.

Share this: varuna-dhrishta.vercel.app/rural?from=…&to=…&v=two-wheeler
```

Rendered server-side from the same route API. Print stylesheet included; the share link carries the
whole query so a WhatsApp forward reproduces the page.

---

## 8. Repairs to existing screens

| Screen | Symptom | Fix |
|---|---|---|
| `/console` layers panel | Clipped at 1366 × 768; wheel does nothing | The floating column gets `min-h-0` and `overflow-y-auto`; `LayerPanel`'s root drops `overflow-hidden`; a test asserts it scrolls |
| `/console` cycle picker | 414 px of chips in a 380 px column, spilling over the map | Wrap to two rows, or scroll horizontally inside its own box |
| `/console` probability legend | Draws over the layer panel's rows | Stack below the panel, never over it |
| `/console`, `/drains` | Empty bands beside a tall AOI in a wide box | Keep the fit (cropping the city is worse) and fill the bands with the basemap, which is already unclipped |
| `/route`, `/onboard` | Map capped to a band inside a scrolling page | Map fills its pane; the page scrolls around it |
| Satellite imagery | Blurs past zoom 17 | Raise the cap to 19 (Esri serves to 23) and check the tile budget |
| `/map` | Opens on the calm 09:10 cycle | Pin the 06:40 opening cycle, as `/console` now does |
| `/onboard` | No layers panel; map stacks nothing; finish card opens a Mumbai console | Add the scoped layers panel; fade each layer in as its step completes (M19); thread `?city=chennai` so the card lands on Chennai |

---

## 9. Copy rules specific to these screens

- Never the bare word "live" without its subject: "live weather", never "live data".
- Never "we predict"; the product says what the run says: "this cycle says".
- Vehicle names as a person says them: two-wheeler, car, bus, ambulance, on foot.
- Depths always in cm with the vehicle that stops in them.
- The three honesty chips, verbatim: **"Reconstructed replay"**, **"Emulator estimate"**,
  **"Live weather · Open-Meteo"**.
- Every refusal names the fix: "We cannot route from there — the point is outside the Mumbai area
  VARUNA has built. Pick a point inside the shaded area."

---

## 10. Accessibility and responsiveness

- 44 px targets on every citizen control, at 390 × 844 and up (the public map's own floor).
- The globe is `aria-hidden`; the skip control is a real button, first in the tab order.
- The corridor picker is a radio group; the assignment is announced, not just coloured.
- Colour never alone: corridor chips carry letters, depth chips carry numbers.
- `prefers-reduced-motion`: no globe, no cross-fade, no route draw-on; the map appears framed.
- axe: 0 violations on all three new screens, joining the thirteen already gated.
