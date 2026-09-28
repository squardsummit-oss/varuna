# Judge Q&A, with the prototype's actual numbers

Every figure here is measured from the artifacts in this repository, not estimated. Answers follow
SPEC.md 16; the numbers come from `/verify`, `city/mumbai/REPORT.md`, `docs/verification/` and
the run directories. Where a number is bad, it is here anyway — a measured weakness is worth more
in front of this jury than a round number nobody can reproduce (rule 6).

Last measured: 15 September 2026.

---

## "Is the physics real?"

Yes. A local-inertial 2D shallow-water solver (Bates 2010) on a hydro-conditioned 30 m DEM, coupled
to a head-driven 1D drain model with Manning capacity, surcharge and backflow, exchanging through
inlet capture and surcharge at every sync interval.

**Mass balance on the 08:40 cycle: 7.0 × 10⁻⁴** — inside the 0.1 % budget SPEC.md 11.3 sets.
Say the whole sentence: the audit only started counting what the drains discharge at their outfalls
after we found the network's main sink missing from it, which is why the error used to grow with
the water (1.5 × 10⁻² before).

One cycle of the seven, at 06:40, sits at **0.215 %** — over budget, and worse than the 0.18 % the
same cycle measured before the demo set was re-baked with the 20-member ensemble on 13 September
2026. The other six measure 0.014–0.091 %. It is on screen in the replay screen's cycle log rather
than hidden, and it is the one we would investigate next.

The blueprint's full dynamic-wave 1D and 5 m GPU nests are the pilot upgrade; the interfaces for
both are in place (a PySWMM adapter, and the nest geometry in `configs/mumbai.yaml`).

## "How fast is it?"

The Twin is far over its 8 s budget for a three-hour city run: **34–60 s** on a quiet machine
(36.07 s in an independent re-run of 08:40). The shipped runs' own `stage_ms` read up to 192 s,
because they were baked on 26 September with test suites running beside them - the run stamp
prints what was measured, not the best case. We missed the budget, and we can say exactly where
the time goes. **Sky is inside its 5 s**: 2.4–5.6 s per cycle across two bakes that day, with the
twenty members split over four worker processes (ADR-0075).

The drain solver was NumPy at 106 s and the coupling at 25 s; both are Numba kernels now (29 s and
4.6 s, physics bit-for-bit the same, ADR-0035). On 15 September the surface solver's per-call
Python setup came out of the sync loop as well — 1.95–2.06 ms per call down to 0.20–0.22 ms, with
outputs bitwise identical (ADR-0049). A one-hour coupled run now measures 27.1 s under contention
(14 python processes): the drain kernel is 13.2 s of it and the surface kernels 9.2 s, so the
drain step is the wall, and it needs parallelising, which its scatter-adds currently forbid.

On 24 September the scatter-adds were coloured so the edge passes run in parallel without a race
(ADR-0074): 3.49x faster on the drain step, byte-identical at any thread count. It cannot close
the budget: the drain is now 42 % of the run, so even an infinitely fast drain caps the whole-run
gain at 1.72x. What remains is the surface solver and the 2,160 coupling calls.

A whole cycle, counting each stage once, took **47–127 s** across the seven cycles baked on 26
September; the storm cycles carry 24–55 s of pipe attribution inside their products stage
(`stage_ms.products_attribution`, ADR-0071), which is what separates 08:40's 127 s from 07:10's
47 s. The 13 September bake read 64.8–147.2 s.
Figures of 155–359 s that appeared earlier counted the Twin's internal sub-timings on top of its
own wall clock (ADR-0046).

**Products** have 2 s in SPEC.md 11.8, and that includes the writers. Since 15 September the
column-wise segment table is wired in, the 36 depth PNGs encode on eight threads and the
wet-segment layer rounds each distinct value once; every one of the 08:40 cycle's 75 depth
products is byte-identical to before and to the shipped bake (ADR-0051). Measured on the 08:40
inputs with 12 falling to 10 python processes running (i5-1155G7):

| Item | Before | After |
|---|---|---|
| Segment forecast, 20 members | 6.86–8.46 s | 0.75–1.04 s |
| 36 depth PNGs | 0.95–1.22 s | 0.27–0.36 s |
| `segments_wet.json` | 0.89–1.27 s | 0.45–0.59 s |
| Segment sampling points | 0.31–0.61 s on every call | 0.35 s first call, 0.001 s after |
| `segment_forecast.parquet` write | 0.39–0.45 s | 0.39–0.47 s (unchanged code) |
| Hotspot ranking · surcharge product · street series | not re-timed | 0.28–0.30 · 0.32–0.35 · 0.10–0.11 s |

Together that is about **2.7 s warm and 3.6 s cold, down from about 10.5–13 s: the 2 s budget is
still missed.** A real bake of 08:40 records `stage_ms.products` as 3,009 ms, but that timer closes
before the writers run, so it cannot be read against the budget. Under heavier load (21 falling to
18 processes) the segment forecast went from 12.5–21.5 s to 1.41–1.67 s. These figures predate the
reversed-edge geometry join, which adds a one-time 1.1 s parse to the first cycle in a process
(ADR-0052); the two have not been timed together.

**Pulse** has 3 s in 11.6. Traffic detection is vectorised and three static city joins are cached
in process, with every observation and posterior hash identical at all seven demo cycles
(ADR-0054). Old and new code interleaved in one process: at 09:10 with 19–21 python processes,
13.67–16.64 s before, **6.18 s cold** and 3.56–4.97 s warm after; at 08:40 with 15–17 processes,
11.52–14.75 s before, 6.51 s cold and 2.15–2.16 s warm. An independent review at 09:10 with 10
processes measured **1.47–1.65 s warm** against 5.75–5.96 s for the old code. So the budget is
**met warm at the lower process count and missed cold**, and no cold figure exists without other
other processes running. The warm remainder is the ES-MDA update (1.12–2.45 s) and the
drain-health product (0.85–1.37 s).

What this costs the demo: nothing. The replay is baked and publishes in under 200 ms. It costs a
*live* cycle, which is why "Compute live" is a moment in the demo and not the default.

## "Does the map keep up?"

Not reliably, once the surcharge markers pulse. The pulse (M8) and the reversed-flow dash (M9) run
on deck.gl's animation clock as shader uniforms, so an animation frame costs **0** React renders
where the old pulse cost 12.5 a second, and under reduced motion the canvas is byte-still
(ADR-0053). The price is that deck redraws the whole map every frame while a marker is in view.

Measured 15 September 2026 in headed Chromium on the demo laptop's Iris Xe (D3D11), full `/console`
at 1440 × 900, on a `next dev` build, with 10–12 python processes from other work running:

| Console state | Mean fps, 10 s samples | p95 frame |
|---|---|---|
| Surcharge on, pulse animating | 43.7 · 58.3 · 56.2 | 33.9 · 17.2 · 32.9 ms |
| Surcharge layer off | 59.7 · 60.0 | 17.1 · 17.0 ms |
| Reduced motion, surcharge on, settled | 60.0 | 16.9 ms |

SPEC.md 14 asks for 55 fps. This is **marginal, not met**, and the budget stays unticked. It was
taken on a development build under contention, so the next step is the same measurement on a
production build with nothing else running; if the pulse still pulls it under 55, the two loops
move to a second, small canvas so a pulse frame stops redrawing the city.

The dash is not visible yet. Every stored reversed edge in the seven committed runs lacks its line
(0 of 500 in each), because the geometry join landed after they were baked (ADR-0052), and the
layer panel says so: "This run stores no pipe geometry, so they are counted here but not drawn."
The count it quotes is the run's own, 18,380–24,014 reversed pipes per cycle, not the 500 it
stores.

## "Do the other animations do what the catalogue says?"

Measured 15 September 2026 by the engineer who built each one, in Playwright against `next dev` at
1440 × 900 with other work running. The alerts motion was reviewed and passed; the other three
reviews are still to do, so treat these as the builders' own measurements.

- **M16, alerts.** Moving the cycle picker from 06:40 to 08:40 slid 57 of 60 cards in from 12 px
  above, popped four phone bubbles and shook the phone once (peak 3.98 px, 80–348 ms after the
  queue changed), with one chime per batch. Under reduced motion: no translate, no shake, no sound,
  and no audio context created. The 60-card commit produces 170–440 ms long tasks in a development
  build, which cost the 180 ms and 300 ms motions frames (ADR-0057).
- **M17, pumps.** Optimise at 08:40 flew pump P-05 to Jijamata Road (moving 223–439 ms after the
  click, settled by 1,206 ms) and fired 9 NumberFlow animations, ending on "Minutes above 45 cm:
  0 min with the plan, 1 h 40 min without". Under reduced motion the card was already in place on
  the first sampled frame, with no number animation. Dragging a pump changed no figure.
- **M18, ground-truth pins.** A drop changes only a 31 px box around the pin for 450 ms, then the
  canvas is still and deck makes no further clear in the next 3 s. It costs 3 renders of the pin
  hook and 0 of `CityMap`; the old hook re-rendered the console every frame for 600 ms. Under
  reduced motion: 0 changed pixels.
- **M3 and M5, landing.** The cycle diagram's beams travel only in view, and the stage times under
  each node come from `/v1/cycle/status`. Production-build LCP: 976 ms median over 7 warm runs
  after the change against 1,344 ms before, CLS 0 both times, with 24 and 12 python processes
  running.

## "Is the radar real?"

No, and the bundle says so on its face. The replay radar is a storm-designer reconstruction,
calibrated so the AOI's three-hour accumulation matches the documented public gauge totals for
2 July 2019 — **75 mm over 05:40–09:40 IST, inferred from the IMD Santacruz 24-hour total of
375.2 mm**, because no hyetograph for that window is public.

Why not the real thing: IMD's public Mumbai radar products are latest-image endpoints overwritten
in place, with no archive and no dated path, so no July 2019 frame is publicly retrievable.
Archived volumes go through IMD's data-supply route. The frames we generate are quantised to the
same 5 dBZ classes the public images carry, so `services/sky/decode_imd.py` will read the real ones
the way it reads these.

Everything downstream of the frames is real: the Z–R fit, the gauge merge, pySTEPS optical flow and
the 20-member STEPS ensemble.

The same analysis now exists as a series of its own (ADR-0047): Sky's QC, Z–R and gauge merge of
every elapsed frame, the forcing a hot-started Twin will catch up on, computed without ever reading
the truth field. Against the reconstruction's truth it carries 0.955 of the rain over 05:40–08:40
and 1.106 over 05:40–09:40, hourly 0.80–1.46, so the three-hour agreement is errors cancelling. It
trails truth by about one 15-minute gauge interval, because the merge anchors on each station's
newest reading.

## "What datum is the tide in?"

Chart datum in the file, the terrain's datum in the solver. `tide.csv` keeps the sourced heights,
0.045–3.936 m across the demo window, and the Twin subtracts **2.70 m**: mean sea level above chart
datum at Apollo Bandar, from PSMSL station 43 (range 2.66–2.73 m over 2015–2024; 2019 has no annual
value and interpolates to 2.711 m). The boundary therefore runs −2.655 to +1.236 m in the DEM's
frame (ADR-0055). Regenerating the bundle changed only `manifest.json` — 95 of 96 files
byte-identical, 13 rules, 0 warnings.

Three things to say with it. The civic 4.92 m statement names no datum, so reading it as chart
datum is an assumption. The offset between the DEM's EGM2008 geoid and local mean sea level is
**not quantified**, so it stays in the boundary as an unmeasured bias. And the baked runs predate
the conversion: whether the tide-locked outfall still reverses has not been measured, and the
drain-graph fix in progress no longer places an outfall at Mahim, where the only stored tidal
reversed edge was (nearest node 1,049 m in its rebuild), so that claim waits for a measurement on
the regraded graph.

## "Where is the drain GIS?"

There isn't one — Mumbai has no public street-level storm-water network, which is the problem we
are solving rather than a gap in our data.

Ours is inferred from roads, terrain and design norms: **49,770 edges, 1,716 km of pipe, 127
outfalls (3 tidal), 100 % of nodes reaching an outfall**. Every element carries
`confidence = "inferred"`, every pipe carries a blockage random variable β, and the drain X-ray
draws them dashed for that reason.

Say the other half too: the graph is connected but not gravity-consistent. Inverts sit at a fixed
cover and are never deepened, so **18,994 of the 49,770 pipes (38.2 %) run uphill**, and 56.1 % of
nodes reach their outfall only by surcharging over an invert higher than their own street —
Hindmata's included, by 8.56 m (ADR-0048). The 100 % above is topological; hydraulically it is
43.9 %. A regrade within the blueprint's 1–3 m depth bound is decided and not yet built.

The point is what happens next: Pulse learns β from every flood the city has. Across the seven
baked cycles the worst pipe reads 0.451 at 06:10 and 0.622 at 09:10, but not as a steady climb:
it is already 0.620 at 06:40 and sits at 0.471–0.526 through the middle of the morning. Each cycle
re-assimilates every observation up to its own time from the city's prior rather than carrying the
last cycle's posterior forward (SPEC.md P7.3), so the series is seven analyses rather than one
learning curve. When a surveyed SWMM model arrives we import it and keep the learning.

## "Does Pulse actually recover a blocked pipe?"

On the spec's own test, yes. Both halves of it now pass across the seeds the result depends on.

11.6's acceptance test: on a synthetic truth with two blocked pipes and twenty observations, "the
posterior mean ranks those two pipes in the top 5 with sd reduced by ≥ 40 %". Until 14 September
2026 only the ranking was robust. The single-step stochastic EnKF cleared the spread floor at 49 of
100 seed pairs, and at 3 of 10 ensemble seeds for the committed observation draw (median 33.2 %),
so the spread test shipped `xfail(strict=True)`.

That was a filter defect, not a data limit. An exact grid-Bayes posterior on the same observations
cuts the spread by a median 0.518 and clears 40 % at every draw; the old update missed it by 0.115.

The update is now ES-MDA with perturbed observations: 4 passes, R ×4, moment-matched prior, still
50 members (ADR-0044). The 40 % floor was not widened. Two alternatives were rejected: 200 members
in one step (median only 0.377), and a square-root update, which drops the perturbed observations
11.6 specifies.

Measured 14 September 2026 on the demo laptop with 8-10 python processes running (light numpy, so
the timings are not load-bearing), varying the observation draw and the filter's own ensemble seed:

| Grid (observation seed × ensemble seed) | Both pipes in the top five | Spread cut ≥ 40 % on both | Median cut on the worse pipe | Median over ensemble seeds, per observation draw |
|---|---|---|---|---|
| 0–9 × 2019–2028 | **100 / 100** | **100 / 100** | 0.509 | 0.473–0.564, all ≥ 0.40 |
| 7–16 × 2019–2028 | 100 / 100 | 100 / 100 | 0.531 | 0.498–0.564, all ≥ 0.40 |
| 0–9 × 0–9 | 100 / 100 | **98 / 100** | 0.514 | 0.457–0.557, all ≥ 0.40 |
| 20–39 × 100–109 (independent review) | 200 / 200 | 195 / 200 | — | all ≥ 0.40 |
| Committed draw (7) × 2019–2028 | 10 / 10 | 10 / 10 | 0.498 (0.406–0.538) | — |

Individual seed pairs still fail; the lowest is 0.352. The tests therefore assert the median over
ensemble seeds rather than one seed's score, and that median clears 0.40 at every observation draw
measured.

A pass is also checked against the right answer, not merely a narrow one:
`test_the_posterior_agrees_with_exact_bayes` runs at three observation draws. At the committed draw
the filter reads mean 0.694 / 0.743 against exact 0.690 / 0.728, and cut 0.507 / 0.570 against
0.505 / 0.544. Across the three draws the median over seeds is within 0.017 in mean and 0.027 in
cut. A collapsed ensemble would pass the floor and fail this.

What this does not claim:
- **The sd is not coverage.** The prior (0.20 ± 0.12) puts the true 0.85 about 3 sd out, so exact
  Bayes and this filter hold the truth inside their 90 % interval only about 40–46 % of the time.
- **It says nothing about the Mumbai runs.** The test uses a synthetic operator. On the city the
  capacity-deficit operator's inputs are wrong by metres (see the EnKF observation operator row in
  `docs/SIMPLIFICATIONS.md`), and the drain graph itself is not gravity-consistent (P1.8).
- **The shipped posteriors are still the old update's.** The baked demo runs predate this change.

## "Sub-second forecast — how?"

A reduced-order emulator (a two-reservoir Nash cascade per surface unit) calibrated to our own Twin
runs. A what-if is **62 ms** and an attribution over fifteen candidates **126 ms**.

Its skill is poor and we publish it: **RMSE 5.7 cm, CSI 0.085 at 30 cm** on held-out storms, fitted
on 8 runs where SPEC.md 11.7 asks for 200. More runs would not have rescued it — the limit is
structural. A local-rain cascade cannot reproduce depth that arrives as tide and upstream routing,
and segment S215609077-002 takes 10 mm of rain and ponds 60 cm for exactly that reason (ADR-0025).

So what-if **levels on the Twin's own forecast** and uses the emulator only for the *difference* a
scenario makes, a tide offset is refused rather than approximated, and the measured skill is
printed beside every answer. The GNN surrogate is the pilot upgrade.

## "Does the physics check agree?"

It runs, and it does not always agree — which is what it is for. Since 24 September
`POST /v1/whatif/physics-check` crops the city to 33 × 33 cells (990 m) around the run's worst
junction, turns every pipe that leaves the window into a free outfall, runs the coupled Twin twice
at the run's posterior blockage - baseline and scenario - and reports the emulator's *change*
against the Twin's at each junction inside the window (ADR-0077). `/whatif` and the console's
what-if drawer print it as section 7.7's bar, with each junction's two changes, the window, the
crop's mass balance and the check's own time against its 10 s.

| Measured | Figure |
|---|---|
| Warm call | 2.4–4.7 s; p95 4.09–7.37 s over five calls |
| First call in a fresh API process | 19.4–22.6 s (city load and Numba compile) |
| Rain +30 %, 08:40 | **5.07 cm at Bandra Talao against a 5 cm tolerance — outside, and printed** |
| Rain −20 %, 08:40 | emulator −0.31 cm against Twin −3.63 cm, 3.32 cm apart |
| Unchanged scenario | 0.00 cm on both sides |
| Crop mass balance | 0.0 at the posterior blockage; 1.7e-02 at the city's prior |

Changes and not levels, because the same storm peaks at 87 cm on a 990 m window and 123 cm on a
2,430 m one: a crop's absolute depth is not the city's. A tide offset is refused, because the
emulator has no answer to a different sea to check. And the API has to be started before the
judges arrive, or the first press pays twenty seconds.

The design came from the feasibility measurement below, taken on 13 September before it was built.

### Physics check feasibility

Measured 13 September 2026 on the demo laptop (Intel64 family 6 model 140, 8 logical cores,
Windows 11) on the 08:10 IST cycle of 2 July 2019 (`MUM-20190702T0240Z`), 36 steps of 5 minutes,
with the same Sky forcing the baked run used. The crop is **33 × 33 cells at 30 m — 990 m ×
990 m** centred on the Hindmata register point, carrying the 410 drain nodes and 392 edges whose
cells fall inside it, with its boundary ring held at the full run's water surface interpolated
in time. Three warm repeats each, `structlog` at WARNING.

| Run | Three-hour cost | Against the 10 s budget |
|---|---|---|
| Full AOI, coupled — 168,606 cells, 49,897 nodes, 49,770 edges | 69,100 ms (surface 23,692 · drain 34,658 · coupling 6,265) | 6.9× over |
| Crop, 2D surface alone, 36 calls of 300 s | **151 / 154 / 163 ms** | met |
| Crop, coupled 2D + 1D + exchange, 2,160 syncs of 5 s | **931 / 893 / 922 ms** | met, with 10× to spare |

The 137–174 s above is what the baked cycles recorded in their own `stage_ms`; the 69,100 ms row
is this one cycle re-run today under the same quiet conditions as the crop, so the two columns of
this table are comparable to each other rather than to the run registry.

**10 s is reachable: the coupled crop is 0.9 s, and the endpoint is worth building.** Four
things that number comes with.

**The crop must be the coupled one, not the surface.** Over the same nine cells at Hindmata the
full run peaks at 11.23 cm, the coupled crop at 11.60 cm — 0.4 cm apart, which is the crop
reproducing the physics it was cut out of — and the surface alone at 5.09 cm. Over half of that
junction's water arrives back out of the drains as surcharge, so a 2D-only check at 0.15 s would
be checking the wrong thing.

**The crop borrows its boundary from the run it is checking.** Holding the ring at the baseline
run's water surface is what makes a 990 m window well-posed, and it also suppresses whatever the
scenario would have changed about the water arriving at that ring. The check is therefore a check
at the crop's centre, and a wider crop is the way to buy more of it — cheaply, since the cost is
not in the cells.

**A tide offset cannot be checked on this crop at all.** It contains **0 of the city's 3 tidal
outfalls**, so the tide boundary the scenario would move is not inside it. The what-if already
refuses a tide offset rather than approximating it (ADR-0025); this is the same wall.

**Shrinking the crop further buys almost nothing.** It has 155× fewer cells and 127× fewer edges
than the AOI but runs only 75× faster, because the 2,160 sync intervals cost the same number of
Python-level solver calls whatever the domain is. That per-call floor, not the grid, is what a
future speed-up has to attack — and it is the same scatter-add problem ADR-0035 names for the
full run. (For the same reason the number is sensitive to logging: with `structlog` left at its
default, the 2,160 `surface.run` debug lines take the coupled crop to 1.3–2.6 s. The API
configures INFO, so the endpoint gets the quiet path.)

## "Show us cleaning a drain, then"

We can now, and the answer is not the one section 7.2 was written around.

Attribution runs on `drain1d` since 24 September (ADR-0071): for each of the ten worst wet
junctions it cuts the junction's own catchment out of the city graph, freezes the street at the
Twin's depth, re-runs every pipe within five upstream hops cleaned to β = 0.05, and reads how much
more water the drain takes off the junction by its peak. It cleans at this cycle's Pulse
posterior, keeps the sign, and ranks by magnitude. On the cycles baked on 26 September:

| Junction, cycle | Candidates | Named | What cleaning does |
|---|---|---|---|
| Hindmata, every storm cycle | 51 | 0 | refused: the best pipe moves it **0.015–0.027 cm**, under the 0.1 cm floor |
| Sion Subway 1, 08:40 | 55 | 14 | all fourteen together: **9.6 → 11.6 cm, 2 cm deeper** |
| Sion Circle, 08:40 | 75 | 14 | 9.2 → 9.4 cm |
| Sion pedestrian subway, 08:40 | 35 | 7 | 8.9 → 9.4 cm |
| Sion Circle, 08:10 | 75 | 14 | every pipe the right way; together 7.5 → 7.4 cm |

Hindmata refuses because at that depth its **inlets**, not its pipes, limit how fast the street
drains - which is also what ADR-0048's uphill inverts predict for a junction whose outfall path
climbs above its own street. And a junction that is already surcharging gets *deeper* when the
pipes above it are cleaned, because a clear pipe delivers more water to it than the cleaning
drains away. The old emulator could not produce either answer: it is element-wise per segment, so
cleaning any pipe not under a street moved it by exactly 0.0000 cm (ADR-0042, which stands for
Flash-lite).

So "cleaning these 14 pipes: 55 → 20 cm" is not said on stage: no cycle computes it. The drawer
shows what was measured, including the uncomfortable result. Cost: 24–55 s per storm cycle, paid
at bake.

## "What is your ground truth?"

**29 curated pins for 2 July 2019, 17 inside the scoring window**, each with the URL it was read
from and a stated time uncertainty. Civic logs, news reports with timestamps, and geotagged posts
referenced by news. Nothing synthetic is in that set — the gauges, traffic and citizen reports in
the same bundle all are, and are labelled so.

Not one of the 29 states a depth. They say "waterlogging", "traffic diverted", "water in the
subway". That is why:

## "What are your scores?"

At the 15 cm headline threshold, over the 17 pins in the window:

| | |
|---|---|
| CSI | **0.24** |
| POD | **0.47** |
| FAR | **0.67** (a lower bound — see below) |
| Median lead time | **45 minutes**, over 7 pins found before they were logged |

Scored on the seven cycles re-baked on 26 September; the 13 September set read CSI 0.22, POD 0.41,
FAR 0.68 and 31 minutes. At 30 cm CSI went 0.09 → 0.14.

We sweep 5, 15 and 30 cm rather than picking one, because scoring a civic log against a single
30 cm line treats it as though it had said "over thirty centimetres", which it did not. **The
spread across the three is the finding**: every pin is found at 5 cm, only three of seventeen at
30 cm. The pattern is right and the level is low — a different problem from missing the streets,
and the one we would fix first.

FAR is a lower bound because nobody logged most of the city that morning; we count false alarms
only within 250 m of some pin, or we would be scoring the record-keeping.

Depth MAE, the Brier score and the reliability diagram are returned as **unavailable, with their
reasons**: no pin states a depth, and the scorer does not read the runs' 50-member
probabilities yet. ADR-0029.

## "Is the spread real?"

Fifty members since 26 September (P7.6, ADR-0076): the twenty Sky members, each used two or three
times rather than resampled, crossed with draws of pipe blockage from this cycle's Pulse
posterior and of the emulator's storage coefficient. Every run prints what its width is made of.
At 08:40 the band - the mean p90 − p10 of peak depth over 7,646 wet segments - is **16.3 cm: 9.9 cm
from the weather and 8.9 cm from the parameters**, and switching the blockage draw off moves it
**−0.14 cm**, inside the ensemble's own sampling noise. So the width on screen is the weather's and
the emulator's own storage, not what Pulse learned; Pulse sets the level of the drainage term, not
the spread. Two limits, printed with every run: the members differ only in the *amplitude* of the
AOI-mean rain, because that is the only per-member rain a cycle keeps, and 50 over 20 weights ten
Sky members at 0.06 and ten at 0.04, which reads 2.0 % wider than a balanced 60.

Where the spread is drawn: under the time bar, as the p10–p90 of mean street depth across the
members (`aoi_depth_band` in `run.json`), and in each junction's fan chart, as its own Twin level
with its registered streets' member spread either side. Both arrived on 26 September; before that
the bar promised a band "with the first run" and the drawer said the spread had not arrived.

## "How deep does Hindmata get?"

**11.6 cm at 08:10 and 10.8 cm at 08:40**, on the cycles re-baked on 26 September - the 90th
percentile over a 45 m window around the registered point. No junction on the register reaches the
15 cm band on any cycle: the deepest is Bandra Talao at 13.7 cm.

The water is on the streets around them. At 08:40 **2,362 of the 6,904 wet segments peak above 15 cm
and 675 above 30 cm**, the deepest at 146.4 cm, behind nine severe, 35 moderate and 15 watch alerts;
at 08:10, 1,150 of 4,803 above 15 cm and 219 above 30 cm. On stage we quote those segment depths
and the blockage the drain map learns, not a junction depth.

Why the junctions read shallow is now measured rather than guessed: attribution finds that at
Hindmata the inlets and not the pipes limit the drain (the best single pipe moves it 0.015–0.027
cm), and the drain graph is inferred with 18,994 of its 49,770 edges running uphill (ADR-0048).
ADR-0039's building-footprint fix, which unblocked Khar Subway and Parel, is in these runs.

## "How accurate is the DEM?"

Copernicus GLO-30, 30 m, and it is a *surface* model — it carries flyovers and rail embankments
where a bare-earth model would not, and it cannot see a 40 m underpass dip or a kerb-height sag.

So we claim pattern and timing, not absolute level, and the scores above are consistent with that.
Chronic sinks are registered in their own right rather than discovered by the DEM, and
**depressions explain 82.1 % of the chronic register** against a 60 % target. LiDAR and the 5 m
nests are budgeted in the pilot.

## "How is this different from IFLOWS-Mumbai?"

Different layer, and we consume rather than compete. IFLOWS is the strategic layer: ward-level,
6–72 hours, for deciding whether to declare a holiday. VARUNA is the tactical layer: street-level,
0–3 hours, for deciding which junction to send a pump to and which road an ambulance takes.

Four things it does that a ward-level forecast structurally cannot: a drain network with learned
blockage, probabilities per street, sub-second what-if, and routing. We take IMD, NCMRWF and
IFLOWS outputs as inputs.

## "What would you do next?"

In order, and each because of something above:

1. **The 50-member ensemble products** (P7.6) — every probability on screen is currently 0 or 1.
2. **Parallelise the drain step** by edge colouring — 74 s to something that can run live.
3. **The level, not the pattern** — CSI 0.22 at 15 cm and 0.09 at 30 cm says the water arrives in
   the right streets and not deep enough. First suspects: DEM resolution at the sinks, and the
   design intensity the inferred pipes were sized to.
4. **A real IMD feed**, which is a data-supply request rather than an engineering problem.

## "What is synthetic, and what is real?"

Real: the terrain (Copernicus GLO-30), the roads, buildings and land cover (OSM, ESA WorldCover),
the hotspot register, the asset register, the ground-truth pins, and every algorithm.

Synthetic and labelled as such on screen: the radar frames and the rain field, the gauge readings,
the tide series, the traffic speeds, the citizen report stream, and the mobile pump inventory.

Inferred and labelled as such: the entire drain network.

The honesty labels are UI copy, not fine print — "Reconstructed replay", "Inferred drain graph",
"Reduced-order emulator", "Synthetic pump inventory", and `status=Exercise` on every CAP document
a replay raises.

That last one is tested, not asserted: all 272 committed demo CAP documents validate against the
vendored OASIS CAP-v1.2.xsd and carry `Exercise`, and generated CAP validates in baked, replay and
live modes with `Actual` only in live (`services/products/tests/test_cap_schema.py`, 14 tests,
passing with outbound sockets denied; ADR-0045, measured 14 September 2026 at 78f6c0b).

## "If everyone follows your safe route, don't you just move the jam?"

Yes, and the prototype answers it rather than pretending the question does not arise. A route
request comes back with up to three corridors - the VARUNA route and its alternates, each one
genuinely under the vehicle's depth threshold on this run - and the request is assigned to one
deterministically by hashing the client's own `trip_id`, so a reader who reloads is not sent
somewhere else while a population spreads across the three.

Be exact about what is real: **the corridors are real and the split is a policy.** There are no
live traffic counts in this prototype, so demand is unmeasured, and the screen and the response's
`notes` both say so in the same words. The share comes from each corridor's own bottleneck - its
narrowest leg, with the fraction of capacity the water has taken - divided by its travel time.

Measured on the 08:40 demo run, a car from Babasaheb Worlikar Fire Station to Chembur Fire
Station (2026-09-19, `b6271e6`):

| Corridor | ETA | Share |
|---|---|---|
| A | 13.5 min | 0.4343 |
| B | 18.4 min | 0.3197 |
| C | 23.9 min | 0.2460 |

**This is the second answer to that question, and the first one was wrong.** `TECH_SPEC.md` 3.3
originally defined the capacity as a *sum* over the corridor's edges, and a sum rewards length:
the same trip gave 0.4038 to the 23.9-minute corridor and 0.2458 to the 13.5-minute one - most
drivers sent the slowest way for no gain in safety, which is worse advice than not spreading at
all. It was found by running the request against a real run rather than by reading the code. A
road is as wide as its narrowest point, so the minimum replaced the sum, and the travel time
divides it because a corridor that holds a vehicle twice as long absorbs half the flow.

## "How fast is a route, really?"

Against section 14's 300 ms budget, on the 08:40 demo run, an Intel i5-1155G7 laptop:

| Trip | Before 2026-09-19 | After (`983513e`) |
|---|---|---|
| KEM to Sion, ambulance (the demo trip) | 531.5-561.9 ms | **67.6-102.5 ms** |
| Worli to Chembur, car, 3 corridors + reasons | 1,953-2,533 ms | **215.3-218.3 ms** |
| First request after a restart | - | 1.5-1.6 s (loading the run's 6,492 wet segments; cached after) |

The "before" column is the cost of making the probability real (task D-01). ADR-0028 had
measured 85 ms on a router whose `P(h > threshold)` was a comparison against the median depth;
reading the run's own 20-member `p_gt` series put a dict resolution and a `timedelta` in a loop
that runs twenty thousand times a route. cProfile named it, the fix hoists the tables above the
loop and answers the step question by integer division on seconds the search already holds, and
the KEM-to-Sion response is identical field for field apart from `ms`.

## "Is the weather on the dashboard live?"

The weather is, and nothing else on the screen is - which is why they are labelled separately and
never share an axis. `GET /v1/weather` proxies Open-Meteo (CC BY 4.0, no key) for the city's AOI
centre. Measured by the implementer on the laptop: **1,260.7-1,465.0 ms uncached**, about 1.1 s
of it Open-Meteo, and **5.2-21.8 ms cached**, so the wire is touched once per fifteen minutes per
city. Offline, or when Open-Meteo fails, the last good copy is served with its real age and a
note; only a first request with no copy at all refuses.

On the deployed API, 2026-09-19 13:30 IST: 29.0 °C, light drizzle, 78 % humidity, wind 7.8 km/h,
with the next four hours' rain and probability. `grid_offset_km` reads 2.45, so the answer is for
a model cell 2.45 km from the AOI centre and the dialog says so.

Everything else - streets, depths, drains, routes, alerts, pumps, tide - is the reconstructed
replay of 2 July 2019.

## "Why doesn't the citizen dashboard use Google's map?"

It does, and since 2026-09-23 it does so on a key of this team's own. The dashboard draws VARUNA's water and routes over Google
Maps through `@vis.gl/react-google-maps`, with deck.gl **overlaid** rather than interleaved
(Google's context has no multisampling, and interleaved aliases every route line) and a dark
style built at runtime from `tokens.json`, so the map matches the depth ramp exactly.

Measured 2026-09-19: the supplied browser key carries an HTTP-referrer restriction whose list
contains neither development origin. Maps JS v3.66.4d answers `RefererNotAllowedMapError` for
both `http://127.0.0.1:8899/probe.html` and `http://localhost:3000/probe.html`, with
`google.maps` loaded and zero tiles drawn. Until `https://varuna-dhrishta.vercel.app/*` and
`http://localhost:3000/*` are added to that list in the Google Cloud Console (`TASKS.md` D-25),
the dashboard runs on its labelled fallback - VARUNA's own Esri-and-deck renderer, the same one
the console uses - and says so on screen. Nothing on the dashboard depends on Google being
reachable (ADR-0059).

## "Does the citizen dashboard actually work on the deployed site?"

Verified in a browser at `https://varuna-dhrishta.vercel.app/dashboard` on 2026-09-19, against
the Railway API redeployed the same day:

- The map draws **VARUNA's own Esri-and-deck basemap** with the run's wet streets over it, under
  the notice "Google refused this key for this address; showing VARUNA's own map." Google's
  console error confirms why: `RefererNotAllowedMapError` for
  `https://varuna-dhrishta.vercel.app/dashboard`. **The deployed domain is not in the key's
  referrer list either** - measured, not assumed - so `TASKS.md` D-25 is what stands between this
  build and a Google basemap, and nothing else.
- The header carries the run stamp (`run MUM-20190702T0…flash0.1-baked`), the three honesty chips
  and a live **29 °C** from `/v1/weather`.
- "The worst streets in the city right now" lists real segments with their depths and times:
  84 cm impassable now, 73 cm passable until 10:55, 65 cm until 10:40, 43 cm until 11:30.
- The rail loads the city's 368 facilities into its From and To pickers.

Timing, measured from the deployed page: `/v1/runs` 1,643 ms warm, the run's wet segments
791 KB in 856 ms, `/v1/weather` 1,806 ms on a first call and about 1.3 s after. A **cold**
Railway container adds roughly twenty seconds before the first of those answers, which is the
free tier waking up rather than the app; the demo laptop serves the same data locally in
milliseconds. Say that out loud if the deployed site is used on stage, or warm it first.

## "You have two routers now. Do they agree?"

Yes, and it is checked rather than asserted. `services/route-rs` (task P8.12) is a line-for-line
port of the Python router, and a parity test starts the compiled binary and compares it with
`plan()` **leaf by leaf on 31 trips** - all seven profiles, six cycles, a dozen trip ids, the
KEM-to-Sion ambulance run, a cross-city car trip and three trips over closed streets - excluding
only `ms`, with no tolerance anywhere. **72 of 72 checks pass** (2026-09-22).

| Trip | Python p95 | Rust p95 |
|---|---|---|
| KEM to Sion, ambulance, 08:40 | 62-73 ms | **2-3 ms** |
| Worli to Chembur, car, 08:40 | 207-230 ms | **10.6-13.7 ms** |
| Graph load, cold | 1,743-2,699 ms | **19-21 ms** |

Contraction hierarchies serve **only the naive, dry-weather search**, where the cost does not
depend on time and CH is exact: 1,974 of 2,000 random pairs identical for every profile, the other
26 unreachable in both, 0 different-cost paths. The VARUNA search and its two alternates stay
time-dependent Dijkstra, and `/healthz` names which engine answers which. CH costs 0.062 ms a
query against Dijkstra's 1.618 ms and barely moves a whole request, because three of the four
searches per route are time-dependent.

**The parity test earned its keep on day one.** The port was written from `main` before the
pedestrian hazard rule landed in Python, so Python answered a pedestrian with a fourth note that
Rust did not have. The test failed rather than letting two implementations of one answer drift
apart, and it failed a second time on the same note over stray whitespace. The deployed API still
serves the Python router; Rust is the upgrade path, proved.

## "Is the radar real?" - the second answer

It can be, for one product. `varuna_sky.decode_imd` (task P2.9) decodes IMD's published Mumbai
radar image. Measured on `https://mausam.imd.gov.in/Radar/ppi_mum.gif`, fetched 2026-09-22
(cached locally, never committed - it is IMD's image):

- 386,459 pixels inside the 150 km footprint; **373,158 decoded, 13,301 masked (3.44 %)** - 8,090
  black pixels of rings, spokes and text, and 5,211 of IMD's own coastline blue. No other colour
  was masked, and nothing is interpolated across them.
- Georeference from a concentric fit of the three range rings: **0.413 px RMS, 0.177 km**, with the
  rings agreeing on scale within 0.06 %. The image header prints 0.4 km/px, which is **6.9 % off**
  what its own rings say; IMD's station markers sit 0-4 px from the ring-fitted positions and
  16-20 px from the header's.
- On the Sky grid: 13,347 of 14,400 cells covered (92.7 %), 607 with echo. The other 1,053 are
  unobserved because every image pixel landing in them was an overlay pixel, and they are reported
  as such rather than filled.
- Decode time 845 ms cold, 753-954 ms warm with 18 python processes.

What it does not do: no attenuation correction, no beam-height correction, no PPI-to-CAPPI
conversion, slant range taken as ground range, and one product's layout only (Colaba PPI(Z) close
range) - any other layout is refused rather than approximated. It is **not wired into Sky**: a
nowcast needs three frames ten minutes apart, this is one image, and `qc.coverage_mask` still
assumes the radar sits at the domain centre while Colaba is 19 km from it. Two corrections to the
task's own wording: IMD serves **GIF**, not PNG, and the Colaba legend carries **4** dBZ classes,
not the 5 the task names - the decoder outputs the legend's own.

## "Why does the Sky stage time keep changing?"

Because the laptop was busy, and the number tracks that rather than the code. Measured on
2026-09-22 across one afternoon, same commit, same fixture:

| Machine state | Sky total | pySTEPS nowcast |
|---|---|---|
| 8 build jobs plus a test suite | 14.22 s | 13.47 s |
| Winding down | 11.36 s | 7.80 s |
| 12 python processes | 10.06 s | 9.61 s |
| **Quiet, 10 python processes** | **7.92 s** | **5.41 s** |

The regression guard sits at 10 s (2x the budget) and **passes quiet, fails loaded**. The real
budget is 5 s and is still missed at 7.92 s, which is what the status board has said since
2026-09-12. Any timing in this document was taken with its process count beside it for this
reason.

## "What is still missed in phases 6 and 8, now that every task is ticked?"

A task being done is not an acceptance criterion being met, and these are the ones that are not
(2026-09-22, all on a production build):

| Criterion | Budget | Measured |
|---|---|---|
| "Compute live" runs a real cycle (7.2) | 15 s | **141 s** (274 s contended); Twin is 90-232 s of it |
| Map frame rate, streets + raster + markers (14) | 55 fps | **57.8 flat, 54.2 contended, 50.4 in 3D** |
| Scrub restyle (14) | 16 ms | React commit median 12.9 ms, **p95 26.1 ms** |
| Reachability per facility (11.9) | 2 s | **p95 3,432 ms** (p50 1,583 ms) |
| Pump optimise, first call after a restart (7.6) | 1 s | **1,669 ms**; 157-554 ms warm |
| Reversed-flow edges at the tide-locked outfall (7.2) | drawn | **none drawn**: every run predates the geometry (ADR-0052), and the panel says 20,270 pipes run backwards with none drawable |
| Hotspot attribution, 5 pipes (7.2) | 5 | **refused with its reason** (ADR-0042); P7.7 owns it |
| KEM to Sion avoids a street (7.4) | avoids | **avoids nothing** for an ambulance on any 2 July cycle |

What did pass, on the same build: 0 network requests during a 36-step scrub, 0 console errors
across a full replay, every keyboard shortcut, and the ground-truth pins dropping on the replay
clock with their sources.

## "Does the public map really work with no network?"

Driven in a browser with the API stopped and the browser offline (task P9.10): `/map` drew the
last forecast it had cached over a PMTiles vector basemap built from VARUNA's own OSM layers, and
said how old that forecast was. A report submitted while offline was answered 202
`{"queued": true}`, held in IndexedDB, and arrived when the connection returned.

Sizes: the basemap is **4.19 MB**, built in **62 s** by `make city-basemap`; the terrain heightmap
for 3D is **278 KB** in **6.0 s** by `make city-terrain`. Both are served from the deployed API
(200, 4,189,309 B and 277,557 B on 2026-09-23), which builds them at boot after the city.

Esri's aerial imagery is **never** cached for offline use: it is not redistributable, which is the
same reason `make pack` leaves it out. The offline map draws OpenStreetMap-derived vectors, with
the ODbL attribution on screen.

## "Is that really Mumbai in 3D, or a model of it?"

It is Google's photography, and the water on it is ours. The console's 3D ground is Google's
Photorealistic 3D Tiles drawn through deck.gl's `Tile3DLayer`, with VARUNA's streets, depth
raster, routes and markers draped onto it (ADR-0069). It replaced a Terrarium heightmap exported
from the city's own DEM (ADR-0065), which is still built and still served but no longer drawn.

Coverage, measured 2026-09-23 by traversing the tileset from the global root down to the demo's
own hotspot at Hindmata junction (19.012 N, 72.841 E), descending at each level into the child
whose oriented bounding box contains the point:

| | |
|---|---|
| nested sub-tilesets to reach it | 6 |
| geometric error at each level | 1e100 -> 131,489 -> 8,218 -> 513.63 -> 32.10 -> **2.006 m** |
| finest tile | 13,900 bytes, glTF 2.0, `asset.copyright: "Google;Airbus"` |

A 2 m geometric error is individual buildings, not satellite imagery draped on terrain.

**It misses the frame-rate budget, and by more than what it replaced.** Measured at 1440 x 900 on
2026-09-23:

| view | fps | budget (section 14) |
|---|---|---|
| 3D, street level | **25.2-26.1** | 55 |
| 3D, AOI fit | **11.4-11.9** | 55 |
| flat map, 3D off | 44.9 | 55 |

So 3D is **off by default** and the demo runs on the flat map - the same conclusion ADR-0065
reached about the heightmap, for a worse number. Two further costs are real: 3D needs the network,
where the heightmap did not, and it needs a Google Cloud project with billing linked and the Map
Tiles API enabled.

## "How did you get the Google key working?"

It took three Cloud projects, and the failure mode is worth knowing because it names nothing.

| state | what the Map Tiles API answered | what actually diagnosed it |
|---|---|---|
| API not enabled | 403 `SERVICE_DISABLED`, naming the project | the error itself |
| API enabled, **no billing linked** | **404 `NOT_FOUND`, "Requested entity was not found."** | Static Maps, which answers 403 "You must enable Billing on the Google Cloud Project" |
| API enabled, billing linked | 200, and the tiles draw | - |

An unbilled Google Maps Platform project answers a bare 404 on every Map Tiles method - not "no
billing", not "forbidden". Billing attaches to a *project*, not to a key, so a key created outside
the billed project can never be rescued by linking more billing accounts.

**And the key this build carried until that day was not ours.** It was MCGM's browser key, read
off their public page and left in `.env.local`; `docs/research/_raw/mcgm_main.js:17` holds the same
value byte for byte. That is the whole explanation for the `RefererNotAllowedMapError` of
2026-09-19 (ADR-0059) and for `TASKS.md` D-25 having been impossible as written: nobody here could
add an origin to somebody else's key, and every call it served spent somebody else's quota.

The key now in use carries **no** referrer restriction and answers Static Maps, Geocoding and
Directions as well as Map Tiles, all measured 2026-09-23. Since it is read as a `NEXT_PUBLIC_*`
value and therefore inlined into the JavaScript every visitor downloads, restricting it is a
prerequisite for deploying, tracked as `TASKS.md` D-26.

## "Does the water balance?"

Yes, to a few ten-thousandths of a percent, and we can show where the rest is. Section 11.3
budgets the coupled solver at **0.1 % of inflow**. The seven demo cycles re-baked on 26 September:

| cycle (IST) | mass balance | peak depth | wet segments |
|---|---|---|---|
| 06:10 | 0.00012 % | 20.0 cm | 17 |
| 06:40 | 0.00038 % | 141.4 cm | 3,275 |
| 07:10 | 0.00028 % | 19.9 cm | 21 |
| 07:40 | 0.00018 % | 127.1 cm | 2,399 |
| 08:10 | 0.00015 % | 151.1 cm | 4,803 |
| 08:40 | 0.00050 % | 193.7 cm | 6,904 |
| 09:10 | 0.00066 % | 111.2 cm | 1,852 |

The same seven figures came out of three separate bakes that day, to every digit (rule 8).

08:40's 5.020345566001818e-06 is bit-identical to an independent re-run by the verification pass.
Every `run.json` carries the ledger: at 08:40 the surface solver's own residual is 0.0, the drain's
0.0, the surcharge exchange 0.0, and the whole 15.96 m3 of 3,178,275 m3 sits in the inlet exchange,
where the surface offers min(wanted, available) per CFL sub-step and the network takes what it was
offered.

**How it got here.** The re-bake of 23 September measured 0.127, 0.535, 0.144, 0.260, 0.306, 0.204
and 0.310 % (the bake log still has them). A single-variable bisect on 08:40 reproduced both that
bake and the 13 September one bit for bit and showed ADR-0055's tide had stopped admitting 5.35
Mm3 of sea the city should never have received - the error had not grown, its denominator had
shrunk. The ledger then put the whole residual in two exchange leaks, both fixed (ADR-0072):
surcharge the surface received that the drain's supply check never emitted, and surcharge onto
building cells that the surface zeroed before tallying.

**And why the 23 September numbers were briefly "not reproducible".** Those runs were deleted from
`data/runs` within the hour and replaced with the 13 September copies, so the verification pass
measured the old runs and concluded the new figures were wrong. The API's start-up seeder did it:
it treated any fresh bake whose alert files differed from the shipped set as an outdated copy. It
happened again on 26 September and is fixed (ADR-0082).

## "Can you show the reversed flow at the tide-locked outfall?"

The data is there on every shipped cycle. The 26 September re-bake stores each cycle's reversed
edges **with their geometry** (ADR-0052):

| cycle (IST) | reversed edges | stored | without geometry | at the tidal outfall | surcharging nodes |
|---|---|---|---|---|---|
| 06:10 | 18,382 | 500 | 0 | 0 | 2,762 |
| 06:40 | 20,206 | 500 | 0 | 1 | 7,159 |
| 07:10 | 18,677 | 500 | 0 | 1 | 3,018 |
| 07:40 | 19,846 | 500 | 0 | 1 | 6,319 |
| 08:10 | 20,492 | 500 | 0 | 1 | 7,666 |
| 08:40 | 22,082 | 500 | 0 | 1 | 8,932 |
| 09:10 | 19,927 | 500 | 0 | 1 | 6,392 |

Six of seven put reversed flow at the tidal outfall, which is what section 7.2's criterion and the
1:40 beat ask for; motion M9 animates each stored edge's dash. The surcharge counts moved a little
from 23 September's because the surcharge that reaches the street is now what the drain actually
emitted (ADR-0072).

**R3 is still unticked**: "visible" means seen on screen, and it has to be looked at on these runs.
