# VARUNA — UI/UX Design System for Light Mode

**Version:** 1.0 · **Date:** 2026-09-29 · **Team:** VIT · **Spec above this:** `SPEC.md` section 6, `tokens.json`

---

## 1. Design Philosophy

VARUNA's light mode is **the same control room in daylight**: cool paper surfaces, white panels,
deeper accents so every data colour clears WCAG AA on every surface. The map ramps — depth
bands 1–5, rain bands 1–6 — keep their hex in both themes. A Python raster pixel, a CSS chip
and a deck.gl segment must never disagree on what 45 cm looks like.

### Guiding Principles

1. **Contrast first.** Every text colour meets 4.5:1 on its surface in both themes.
2. **Map colours are theme-invariant.** The flood is always the same blue-to-red ramp.
3. **Accent deepens, never brightens.** `--tide` in dark is `#2DD4BF`; in light it is `#0A655F`.
4. **No shadows.** Depth is still `--deep` on `--ink` and a `1 px --line` border. Light mode
   elevates panels with pure white (`#FFFFFF`) against cool paper (`#F5F7FB`).
5. **Instant switch.** `data-theme` on `<html>` is the only moving part. Every CSS custom
   property resolves through `@varuna/tokens`, so one attribute restyles the whole page.

---

## 2. Colour Palette — Light Theme

### Surface Tokens

| Token | Dark | Light | Use |
|-------|------|-------|-----|
| `--ink` | `#0A1020` | `#F5F7FB` | App background: cool paper in light |
| `--deep` | `#111A2E` | `#FFFFFF` | Panels, rails, cards: pure white |
| `--well` | `#17233B` | `#EEF2F8` | Inputs, hover rows, selected tabs |
| `--line` | `#24314F` | `#D8DEE9` | Borders, dividers |
| `--line-strong` | `#33436A` | `#A3AEC2` | Focused borders |

### Text Tokens

| Token | Dark | Light | Contrast on `--well` |
|-------|------|-------|---------------------|
| `--text` | `#E3EAF6` | `#0B1426` | 16.4:1 |
| `--text-2` | `#A7B4CC` | `#3A4A66` | 7.9:1 |
| `--text-3` | `#7E8DA9` | `#56657F` | 5.2:1 |

### Accent Tokens

| Token | Dark | Light | Note |
|-------|------|-------|------|
| `--tide` | `#2DD4BF` | `#0A655F` | Links, buttons, focus rings; 6.2:1 on `--well` |
| `--tide-soft` | `#0F3A3A` | `#E0F5F1` | Accent backgrounds |

### Status Tokens

| Token | Dark | Light |
|-------|------|-------|
| `--status-live` | `#22C55E` | `#166534` |
| `--status-replay` | `#38BDF8` | `#0369A1` |
| `--status-baked` | `#A78BFA` | `#6D28D9` |
| `--status-degraded` | `#FBBF24` | `#92400E` |

### Chart Tokens

| Token | Dark | Light |
|-------|------|-------|
| `--chart-1` | `#2DD4BF` | `#0A655F` |
| `--chart-2` | `#60A5FA` | `#2563EB` |
| `--chart-3` | `#F472B6` | `#DB2777` |
| `--chart-4` | `#FBBF24` | `#B45309` |
| `--chart-5` | `#A3E635` | `#4D7C0F` |

---

## 3. Component Behaviour in Light Mode

### Glass / Frosted Panels (Console Time Bar only)

| Property | Dark | Light |
|----------|------|-------|
| `background` | `#0A1020` | `#F5F7FB` |
| `opacity` | `0.72` | `0.82` |
| `blur` | `12px` | `12px` |

### Focus Ring

| Property | Dark | Light |
|----------|------|-------|
| `ring_color` | `#2DD4BF` | `#0A655F` |
| `ring_px` | `2` | `2` |

### Buttons

- **Primary (tide fill):** text is `#0A1020` in dark, `#FFFFFF` in light.
- **Outline:** border is `--line`, text is `--text`, hover fill is `--well`.
- **Ghost:** no border, text is `--text-2`, hover fill is `--well`.
- **Destructive:** `--danger` is `#F87171` in dark, `#B42318` in light.

### Panels and Cards

- Background: `--deep` (white `#FFFFFF` in light).
- Border: `1px solid --line`.
- No shadows in either theme.
- Hover row: `--well` (`#EEF2F8` in light).

### Scrollbars

- Track: transparent.
- Thumb: `--line` then `--line-strong` on hover.
- `color-scheme: light` makes native scrollbars match the theme.

---

## 4. Map Layer Behaviour

### Depth Ramp (Theme-Invariant)

The flood ramp is **never changed by theme**:

| Band | Hex | Range | Meaning |
|------|-----|-------|---------|
| Dry | Dark: `#2B3A55` / Light: `#C5CEDC` | < 5 cm | Barely visible |
| 1 | `#3B82F6` | 5-15 cm | Two-wheelers slow |
| 2 | `#F59E0B` | 15-30 cm | Two-wheelers impassable |
| 3 | `#F97316` | 30-45 cm | Cars impassable |
| 4 | `#EF4444` | 45-60 cm | Buses impassable |
| 5 | `#B91C1C` | > 60 cm | Rescue vehicles only |

Only the **dry band** changes: a faint line in dark, a quiet grey in light.

### Basemap

The Esri aerial imagery under the depth raster is drawn under a `--ink` scrim, which
inverts naturally: a dark scrim hides the photograph at night; a light scrim washes it
out in daylight. The map stays the ground the water sits on.

---

## 5. Screen-by-Screen Light Mode Notes

### Drishti (Console)

- The icon rail is `--deep` (white), with `--well` on hover and active.
- Layer panel, scrub card, replay panel: `--deep` background.
- Time bar glass: `#F5F7FB` at 82% opacity.
- The surcharge pulse red stays `#EF4444` in both themes.

### Nadi (Drains)

- Pipe glow is `--drain-1` (`#7C3AED` in both themes).
- Cleared pipes: `--naive` becomes `#5B6780` in light.
- Rest of network: `--drain-0` becomes `#94A3B8` in light.

### Marga (Route)

- VARUNA route: `--tide` (`#0A655F` in light).
- Avoided streets: `--depth-5` (`#B91C1C` in both themes).
- Naive route: `--naive` dashed (`#5B6780` in light).

### Kalpana (What-If)

- Diff wipe: the red-green delta uses `--chart-3` / `--chart-1`.
- Physics check panel: `--deep` with `--line` border.

### Pramana (Verification)

- Contingency cylinders: use `--chart-1`, `--chart-2`, `--chart-3`.
- The tank glass: `--well` at 55% opacity.
- Readout panel: `--well` background, `--line` border.

### Pravesh (Onboarding)

- Progress steps: `--tide` for complete, `--text-3` for waiting.
- Log stream: `--deep` background, monospace text in `--text`.
- Map layers fade in with M19 over the light basemap.

### Dashboard (Citizen)

- Header: `--deep` background.
- Weather chip: `--well` background with `--text` label.
- Route answer panel: `--deep` on desktop, bottom sheet on mobile.
- Report button: `--tide` primary style.

---

## 6. Typography in Light Mode

No change from dark mode:

| Scale | Size | Line Height | Use |
|-------|------|-------------|-----|
| `micro` | 12px | 1.3 | Labels, captions |
| `small` | 13px | 1.4 | Secondary text |
| `body` | 15px | 1.5 | Primary reading |
| `h3` | 18px | 1.35 | Section headings |
| `h2` | 24px | 1.2 | Panel headings |
| `h1` | 32px | 1.15 | Page titles |
| `display` | 48px | 1.05 | Hero text |

Fonts: **Bricolage Grotesque** (display), **Geist Sans** (body), **Geist Mono** (code).

---

## 7. Motion in Light Mode

All motion catalogue entries (M1-M37) are identical in both themes. The reduced-motion
escape hatch applies the same way. Colour transitions use `--ease-ui` (`cubic-bezier(0.2, 0.8, 0.2, 1)`)
at the same durations.

---

## 8. Accessibility

- **Contrast:** every text token clears 4.5:1 on its declared surface in both themes.
- **Focus ring:** `--tide` deepens to `#0A655F` in light, 6.2:1 on `--well`.
- **Colour never alone:** chips carry numbers, corridors carry letters, bands carry words.
- **`color-scheme: light`** on `<html>` matches native controls (scrollbars, checkboxes, selects).
- **axe:** 0 violations target on all screens in both themes.

---

## 9. Theme Switching

The toggle is a `ThemeToggle` component in the app shell top bar and the landing hero corner.
Switching is instant:

1. `data-theme` on `<html>` is set.
2. `color-scheme` is set (native controls follow).
3. `<meta name="theme-color">` content updates (browser chrome follows).
4. `localStorage` persists the choice.
5. Another tab `storage` event follows.
6. deck.gl layers re-read their colours from the resolved CSS properties.

The head script (`theme-bootstrap.ts`) runs before first paint, so a light user never
sees a dark frame on reload.

---

## 10. Design Rationale

### Why cool paper, not warm?

The flood data is blue-to-red. A warm paper (`#FFF8F0`) fights the red bands; cool
paper (`#F5F7FB`) recedes behind them. The temperature of the surface is what lets the
data be the loudest colour on screen.

### Why deepen the accent?

`#2DD4BF` at 4.5:1 on white requires darkening: `#0A655F` clears 6.2:1 on `#EEF2F8`
and 4.6:1 as text on a 20% tide tint. It reads as the same brand, heavier.

### Why no shadows in light mode either?

Shadows invite a hierarchy VARUNA does not have. Every panel is at the same level:
the map, the rail, the drawer. A border says "this is a box"; a shadow says "this
box is above that box". The design says neither is above the other.
