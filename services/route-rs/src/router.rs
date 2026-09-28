//! Time-dependent routing around water: `varuna_route.router`, ported line for line.
//!
//! The cost of an edge is read at the time the vehicle reaches it (SPEC.md 11.9):
//!
//! ```text
//! c_e(tau, v) = t_e * phi(h_e(tau))   if P(h_e(tau) > theta_v) < p_max
//!             = infinity              otherwise
//! ```
//!
//! FIFO holds because `phi >= 1` and an edge is either passable or infinite, so Dijkstra keyed
//! on arrival time is exact - which is why the VARUNA search stays a Dijkstra here: its edge
//! costs change with the arrival step, and a plain contraction hierarchy precomputes costs that
//! do not. The naive search's costs never depend on time, and it is the one search a contraction
//! hierarchy can serve (see [`crate::ch`]); which engine answered is stated in `/healthz`.
//!
//! Every loop below mirrors the Python loop it replaces, including the order the exceedance is
//! looked up in, the `1e-9` slack on both the stale-entry check and the relaxation, and the
//! `(cost, node)` tuple order of Python's heap, so ties break the same way.

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashSet};

use crate::forecast::{at, SegmentDepths, DRY_CM};
use crate::graph::{Constants, RoadGraph};
use crate::ops::OpsOverlay;
use crate::profiles::Profile;
use crate::pytime::DateTime;

/// Traversal time at the profile's threshold depth, as a multiple of free flow.
pub const MAX_SLOWDOWN: f64 = 3.0;
/// Cost multiplier on edges a previous route used, when searching for an alternate.
pub const ALTERNATE_PENALTY: f64 = 3.0;
pub const MAX_ALTERNATES: usize = 2;

/// `profiles.HAZARD_M2_S`: the pedestrian hazard product, m^2/s (SPEC.md Appendix A).
pub const HAZARD_M2_S: f64 = 0.5;
/// Avoided streets worth naming in `reasons` (`reasons.MAX_AVOIDED_REASONS`).
pub const MAX_AVOIDED_REASONS: usize = 3;
pub const LABELS: [&str; 3] = ["A", "B", "C"];

/// The constants this binary was written against, as the export states them.
pub fn expected_constants() -> Constants {
    Constants {
        dry_cm: DRY_CM,
        step_min: crate::forecast::STEP_MIN,
        max_slowdown: MAX_SLOWDOWN,
        alternate_penalty: ALTERNATE_PENALTY,
        max_alternates: MAX_ALTERNATES,
        max_avoided_reasons: MAX_AVOIDED_REASONS,
        labels: LABELS.iter().map(|s| s.to_string()).collect(),
    }
}

/// Refuse an export whose Python constants differ from the ones compiled in here: the two
/// routers would then answer differently and nothing would say so.
pub fn check_constants(c: &Constants) -> Result<(), String> {
    let e = expected_constants();
    let same = c.dry_cm == e.dry_cm
        && c.step_min == e.step_min
        && c.max_slowdown == e.max_slowdown
        && c.alternate_penalty == e.alternate_penalty
        && c.max_alternates == e.max_alternates
        && c.max_avoided_reasons == e.max_avoided_reasons
        && c.labels == e.labels;
    if same {
        Ok(())
    } else {
        Err(format!(
            "The Python router's constants changed ({c:?}); this binary was written for {e:?}. \
             Port the change to services/route-rs before serving routes."
        ))
    }
}

/// Python's `max(a, b)`: `a` unless `b` is strictly greater.
#[inline]
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's `min(a, b)`: `a` unless `b` is strictly smaller.
#[inline]
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `profiles.hazard_unsafe`: the velocity half of the pedestrian rule, `h * v >= 0.5 m^2/s`
/// (Appendix A). No speed means "not unsafe on this half", never an assumed speed.
pub fn hazard_unsafe(depth_cm: f64, velocity_ms: Option<f64>) -> bool {
    match velocity_ms {
        Some(v) if depth_cm > 0.0 => (depth_cm / 100.0) * v.abs() >= HAZARD_M2_S,
        _ => false,
    }
}

/// `_phi`: 1 when dry, `MAX_SLOWDOWN` at the threshold.
#[inline]
pub fn phi(depth_cm: f64, threshold_cm: f64) -> f64 {
    if depth_cm <= DRY_CM {
        return 1.0;
    }
    let span = py_max(threshold_cm - DRY_CM, 1.0);
    1.0 + (MAX_SLOWDOWN - 1.0) * py_min((depth_cm - DRY_CM) / span, 1.0)
}

#[derive(Clone, Debug)]
pub struct Leg {
    pub edge: u32,
    pub segment: u32,
    pub length_m: f64,
    pub seconds: f64,
    pub depth_cm: f64,
    pub arrive: DateTime,
    pub probability: f64,
    pub lanes: f64,
}

#[derive(Clone, Debug)]
pub struct Route {
    pub legs: Vec<Leg>,
    pub seconds: f64,
    pub distance_m: f64,
    pub max_depth_cm: f64,
    pub depart: DateTime,
    pub arrive: DateTime,
    pub path: Vec<(f64, f64)>,
    pub safe_until: Option<DateTime>,
}

impl Route {
    pub fn minutes(&self) -> f64 {
        self.seconds / 60.0
    }
}

#[derive(Clone, Debug)]
pub struct Avoided {
    pub segment: u32,
    pub name: String,
    pub depth_cm: f64,
    pub probability: f64,
    pub at: DateTime,
    pub closed_reason: Option<String>,
    pub path: Vec<(f64, f64)>,
}

/// A `(cost, node)` heap entry ordered as Python's `heapq` orders the tuple: a min-heap on cost,
/// then on node index.
#[derive(Clone, Copy, PartialEq)]
struct Entry {
    cost: f64,
    node: u32,
}
impl Eq for Entry {}
impl PartialOrd for Entry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Entry {
    fn cmp(&self, other: &Self) -> Ordering {
        // Reversed, because BinaryHeap is a max-heap. Costs are never NaN.
        other
            .cost
            .partial_cmp(&self.cost)
            .unwrap_or(Ordering::Equal)
            .then_with(|| other.node.cmp(&self.node))
    }
}

/// What a search needs to know about water. `None` is the naive router.
pub struct Water<'a> {
    pub depths: &'a SegmentDepths,
    pub depart_offset_s: f64,
    /// Per graph segment: closed by an authority.
    pub closed: &'a [bool],
}

/// `_search`: time-dependent Dijkstra from `source`, returning the out-edge indices of the path.
pub fn search(
    graph: &RoadGraph,
    source: u32,
    target: u32,
    vehicle: &Profile,
    water: Option<&Water<'_>>,
    penalised: Option<&HashSet<u32>>,
) -> Option<Vec<u32>> {
    let n = graph.n_nodes();
    let mut best = vec![f64::INFINITY; n];
    let mut came = vec![u32::MAX; n];
    best[source as usize] = 0.0;
    let mut heap = BinaryHeap::with_capacity(1024);
    heap.push(Entry {
        cost: 0.0,
        node: source,
    });
    let speed = py_max(vehicle.speed_scale, 0.05);
    let threshold = vehicle.depth_cm;
    let tolerance = vehicle.risk_tolerance;
    let p_by_segment = water.and_then(|w| w.depths.p_for(threshold));
    // The pedestrian's velocity half of the hazard rule, only where there is a speed to apply it
    // to. None for every other profile and for every run baked so far, so the loop below is the
    // loop it was (`router._search`'s `velocity_by_segment`).
    let velocity_by_segment = water.and_then(|w| {
        if vehicle.hazard_rule && w.depths.has_velocity {
            Some(&w.depths.velocity)
        } else {
            None
        }
    });
    let penalised = penalised.filter(|p| !p.is_empty());

    while let Some(Entry {
        cost: elapsed,
        node,
    }) = heap.pop()
    {
        if elapsed > best[node as usize] + 1e-9 {
            continue;
        }
        if node == target {
            break;
        }
        let step = water.map_or(0, |w| w.depths.step_after(w.depart_offset_s, elapsed));
        let lo = graph.indptr[node as usize] as usize;
        let hi = graph.indptr[node as usize + 1] as usize;
        for e in lo..hi {
            let free = graph.edge_time_s[e] / speed;
            let mut cost = free;

            if let Some(w) = water {
                let seg = graph.edge_segment[e] as usize;
                if w.closed.get(seg).copied().unwrap_or(false) {
                    continue;
                }
                let series = w.depths.depth[seg].as_deref();
                let depth = match series {
                    Some(s) if !s.is_empty() => at(s, step),
                    _ => 0.0,
                };
                let p = match p_by_segment {
                    None => {
                        if depth > threshold {
                            1.0
                        } else {
                            0.0
                        }
                    }
                    Some(ps) => match ps[seg].as_deref() {
                        Some(v) => at(v, step),
                        None if series.is_none() => 0.0,
                        None => {
                            if depth > threshold {
                                1.0
                            } else {
                                0.0
                            }
                        }
                    },
                };
                let p = match velocity_by_segment {
                    Some(vs) if p < tolerance => match vs[seg].as_deref() {
                        Some(v) if !v.is_empty() && hazard_unsafe(depth, Some(at(v, step))) => 1.0,
                        _ => p,
                    },
                    _ => p,
                };
                if p >= tolerance {
                    continue;
                }
                cost = free * phi(depth, threshold);
            }

            if let Some(pen) = penalised {
                if pen.contains(&(e as u32)) {
                    cost *= ALTERNATE_PENALTY;
                }
            }

            let nxt = graph.head[e] as usize;
            let candidate = elapsed + cost;
            if candidate < best[nxt] - 1e-9 {
                best[nxt] = candidate;
                came[nxt] = e as u32;
                heap.push(Entry {
                    cost: candidate,
                    node: nxt as u32,
                });
            }
        }
    }

    if best[target as usize] == f64::INFINITY {
        return None;
    }
    let mut edges = Vec::new();
    let mut node = target;
    while node != source {
        let e = came[node as usize];
        if e == u32::MAX {
            return None;
        }
        edges.push(e);
        node = graph.tail[e as usize];
    }
    edges.reverse();
    Some(edges)
}

/// `_build_route`.
pub fn build_route(
    graph: &RoadGraph,
    depths: &SegmentDepths,
    edges: &[u32],
    depart: DateTime,
    vehicle: &Profile,
    source: u32,
) -> Route {
    let mut legs = Vec::with_capacity(edges.len());
    let mut coords = Vec::with_capacity(edges.len() + 1);
    coords.push((graph.lon[source as usize], graph.lat[source as usize]));
    let mut elapsed = 0.0;
    let mut distance = 0.0;
    let speed = py_max(vehicle.speed_scale, 0.05);
    let mut worst = 0.0;

    for &e in edges {
        let e_us = e as usize;
        let arrive_at_edge = depart.add_seconds(elapsed);
        let step = depths.step_at(arrive_at_edge);
        let segment = graph.edge_segment[e_us];
        let depth = depths.depth_at(segment, step);
        let seconds = (graph.edge_time_s[e_us] / speed) * phi(depth, vehicle.depth_cm);
        elapsed += seconds;
        distance += graph.edge_length_m[e_us];
        worst = py_max(worst, depth);
        let head = graph.head[e_us] as usize;
        coords.push((graph.lon[head], graph.lat[head]));
        legs.push(Leg {
            edge: e,
            segment,
            length_m: graph.edge_length_m[e_us],
            seconds,
            depth_cm: depth,
            arrive: depart.add_seconds(elapsed),
            probability: depths.exceedance(segment, vehicle.depth_cm, step),
            lanes: graph.edge_lanes[e_us],
        });
    }

    let safe_until = safe_until(depths, &legs, vehicle, depart);
    Route {
        legs,
        seconds: elapsed,
        distance_m: distance,
        max_depth_cm: worst,
        depart,
        arrive: depart.add_seconds(elapsed),
        path: coords,
        safe_until,
    }
}

/// `_safe_until`: the last departure at which every street on this path is still passable,
/// each checked at the time the vehicle would reach it.
fn safe_until(
    depths: &SegmentDepths,
    legs: &[Leg],
    vehicle: &Profile,
    depart: DateTime,
) -> Option<DateTime> {
    if legs.is_empty() {
        return None;
    }
    let offsets: Vec<(u32, f64)> = legs
        .iter()
        .map(|leg| (leg.segment, leg.arrive.seconds_since(depart)))
        .collect();
    let mut last_good = None;
    for step in 0..depths.n_steps {
        let candidate = depths.time_of(step);
        if candidate < depart {
            continue;
        }
        let blocked = offsets.iter().any(|&(segment, offset)| {
            let at = depths.step_at(candidate.add_seconds(offset));
            depths.depth_at(segment, at) > vehicle.depth_cm
        });
        if blocked {
            break;
        }
        last_good = Some(candidate);
    }
    last_good
}

/// One routing request, already validated (the API layer does the parsing).
pub struct PlanRequest {
    pub origin: (f64, f64),
    pub destination: (f64, f64),
    pub depart_at: Option<DateTime>,
    pub profile: Profile,
    pub spread: bool,
    pub trip_id: Option<String>,
    pub explain: bool,
}

pub struct RouteResult {
    pub run_id: String,
    pub profile: String,
    pub depart: DateTime,
    pub naive: Option<Route>,
    pub varuna: Option<Route>,
    pub alternates: Vec<Route>,
    pub avoided: Vec<Avoided>,
    pub notes: Vec<String>,
    pub corridors: Vec<crate::spread::Corridor>,
    pub reasons: Vec<serde_json::Value>,
    pub trip_id: Option<String>,
}

/// The engine that answers the naive (dry-weather, time-independent) search.
pub trait NaiveEngine: Sync {
    fn naive_path(
        &self,
        graph: &RoadGraph,
        source: u32,
        target: u32,
        vehicle: &Profile,
    ) -> Option<Vec<u32>>;
    fn name(&self) -> &'static str;
}

/// The naive search as the Python router runs it: the same Dijkstra with water ignored.
pub struct DijkstraNaive;

impl NaiveEngine for DijkstraNaive {
    fn naive_path(
        &self,
        graph: &RoadGraph,
        source: u32,
        target: u32,
        vehicle: &Profile,
    ) -> Option<Vec<u32>> {
        search(graph, source, target, vehicle, None, None)
    }
    fn name(&self) -> &'static str {
        "dijkstra"
    }
}

/// `plan()`: route from one point to another, naively and around the forecast water.
pub fn plan(
    graph: &RoadGraph,
    depths: &SegmentDepths,
    overlay_for: &dyn Fn(DateTime) -> OpsOverlay,
    naive_engine: &dyn NaiveEngine,
    req: PlanRequest,
) -> RouteResult {
    let base = req.profile;
    let depart = req.depart_at.unwrap_or_else(|| depths.valid_ts());
    let source = graph.nearest_node(req.origin.0, req.origin.1);
    let target = graph.nearest_node(req.destination.0, req.destination.1);

    let mut notes = Vec::new();
    if depths.has_exceedance {
        notes.push(format!(
            "Probabilities are this run's own, across {} members; the risk tolerance applied is {}.",
            depths.ensemble_n,
            crate::pynum::format_fixed(base.risk_tolerance, 2)
        ));
    } else {
        notes.push(format!(
            "This run carries no per-member exceedance ({} member(s), and no p_gt in its segment \
             forecast), so a street is either predicted impassable or it is not and the risk \
             tolerance has nothing to weigh.",
            depths.ensemble_n
        ));
    }

    if base.hazard_rule {
        // `router.hazard_note`: which half of the rule was applied, so a walker is never told a
        // street is safe on the strength of a rule that was only half checked. The text is
        // byte-for-byte Python's; `concat!` rather than a `\`-continuation, because the
        // continuation's whitespace stripping put extra spaces into the note and the parity test
        // caught it.
        notes.push(if depths.has_velocity {
            concat!(
                "Pedestrian: a street is refused at 30 cm, or where the median depth times this ",
                "run's flow speed reaches 0.5 m2/s (Appendix A), whichever comes first."
            )
            .to_string()
        } else {
            concat!(
                "Pedestrian: only the depth half of the hazard rule is applied here, refusing a ",
                "street at 30 cm. The other half, depth times flow speed at or above 0.5 m2/s, ",
                "needs a speed, and this run carries none - the Twin computes surface fluxes, ",
                "but no product keeps them - so it is not checked and no speed is assumed. ",
                "Fast, shallow water is not caught."
            )
            .to_string()
        });
    }

    let overlay = overlay_for(depart);
    let mut closed = vec![false; graph.segment_ids.len()];
    for sid in overlay.closures.keys() {
        if let Some(&j) = graph.segment_index.get(sid) {
            closed[j as usize] = true;
        }
    }
    if !overlay.closures.is_empty() {
        notes.push(format!(
            "{} street(s) closed by an authority are treated as impassable whatever the forecast \
             says. Closures are an append-only overlay read at request time; no forecast product \
             was changed.",
            overlay.closures.len()
        ));
    }

    if source == target {
        notes.push(
            "Origin and destination snap to the same junction; there is nothing to route.".into(),
        );
        return RouteResult {
            run_id: depths.run_id.clone(),
            profile: base.key.clone(),
            depart,
            naive: None,
            varuna: None,
            alternates: Vec::new(),
            avoided: Vec::new(),
            notes,
            corridors: Vec::new(),
            reasons: Vec::new(),
            trip_id: None,
        };
    }

    let water = Water {
        depths,
        depart_offset_s: depths.depart_offset_s(depart),
        closed: &closed,
    };
    let naive_edges = naive_engine.naive_path(graph, source, target, &base);
    let varuna_edges = search(graph, source, target, &base, Some(&water), None);

    let build = |edges: &Option<Vec<u32>>| {
        edges
            .as_ref()
            .filter(|e| !e.is_empty())
            .map(|e| build_route(graph, depths, e, depart, &base, source))
    };
    let naive = build(&naive_edges);
    let varuna = build(&varuna_edges);

    if varuna_edges.is_none() && naive_edges.is_some() {
        notes.push(format!(
            "Every route to this destination crosses water deeper than {} cm for a {}. The \
             shortest way is shown; it is not passable.",
            crate::pynum::format_fixed(base.depth_cm, 0),
            base.label.to_lowercase()
        ));
    }

    // What the naive route walks into and VARUNA does not, refused on exactly the criterion the
    // search used: the run's probability against the tolerance, or an authority's closure.
    let mut avoided: Vec<Avoided> = Vec::new();
    let mut closed_on_naive: Vec<(u32, String)> = Vec::new();
    if let Some(naive) = &naive {
        let chosen: HashSet<u32> = varuna
            .as_ref()
            .map(|r| r.legs.iter().map(|l| l.segment).collect())
            .unwrap_or_default();
        let mut seen: HashSet<u32> = HashSet::new();
        for leg in &naive.legs {
            if chosen.contains(&leg.segment) || seen.contains(&leg.segment) {
                continue;
            }
            let sid = &graph.segment_ids[leg.segment as usize];
            let closure_reason = overlay.reason_for(sid).map(str::to_string);
            let refused = closure_reason.is_some() || leg.probability >= base.risk_tolerance;
            if refused {
                seen.insert(leg.segment);
                let raw_name = &graph.edge_name[leg.edge as usize];
                let name = if raw_name.is_empty() {
                    "Unnamed road".to_string()
                } else {
                    raw_name.clone()
                };
                if closure_reason.is_some() {
                    closed_on_naive.push((leg.segment, name.clone()));
                }
                avoided.push(Avoided {
                    segment: leg.segment,
                    name,
                    depth_cm: leg.depth_cm,
                    probability: leg.probability,
                    at: leg.arrive,
                    closed_reason: closure_reason,
                    path: graph.segment_path(leg.segment),
                });
            }
        }
        // `avoided.sort(key=lambda a: -a.depth_cm)`: stable, deepest first.
        avoided.sort_by(|a, b| {
            (-a.depth_cm)
                .partial_cmp(&(-b.depth_cm))
                .unwrap_or(Ordering::Equal)
        });
    }

    let mut alternates = Vec::new();
    if let Some(first) = varuna_edges.as_ref().filter(|e| !e.is_empty()) {
        let mut used: HashSet<u32> = first.iter().copied().collect();
        for _ in 0..MAX_ALTERNATES {
            let more = search(graph, source, target, &base, Some(&water), Some(&used));
            let Some(more) = more.filter(|m| !m.is_empty()) else {
                break;
            };
            let more_set: HashSet<u32> = more.iter().copied().collect();
            if more_set == used {
                break;
            }
            alternates.push(build_route(graph, depths, &more, depart, &base, source));
            used.extend(more_set);
        }
    }

    let mut corridors = Vec::new();
    if req.spread {
        if let Some(v) = &varuna {
            let mut routes: Vec<&Route> = vec![v];
            routes.extend(alternates.iter());
            corridors = crate::spread::corridors(
                &routes,
                graph,
                depths,
                &base,
                req.trip_id.as_deref(),
                &closed,
            );
            if let Some(note) =
                crate::spread::spreading_note(corridors.len(), req.spread, req.trip_id.as_deref())
            {
                notes.push(note);
            }
        }
    }

    let mut reasons = Vec::new();
    if req.explain {
        let assigned = corridors
            .iter()
            .find(|c| c.assigned)
            .map(|c| &c.route)
            .or(varuna.as_ref());
        reasons = crate::reasons::build_reasons(
            &avoided,
            assigned,
            depths,
            &base,
            graph,
            &overlay,
            &closed_on_naive,
        );
        if reasons
            .iter()
            .any(|r| r.get("kind").and_then(|k| k.as_str()) == Some("design"))
        {
            notes.push(
                "The design intensity is the drain under that street, from the inferred drain \
                 graph; the peak rain beside it is this run's AOI mean, not the rain over that \
                 one junction."
                    .into(),
            );
        }
    }

    RouteResult {
        run_id: depths.run_id.clone(),
        profile: base.key.clone(),
        depart,
        naive,
        varuna,
        alternates,
        avoided,
        notes,
        corridors,
        reasons,
        trip_id: req.trip_id,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profiles::defaults;

    fn car() -> Profile {
        defaults().into_iter().find(|p| p.key == "car").unwrap()
    }

    #[test]
    fn phi_is_one_when_dry_and_three_at_the_threshold() {
        assert_eq!(phi(0.0, 30.0), 1.0);
        assert_eq!(phi(5.0, 30.0), 1.0);
        assert_eq!(phi(30.0, 30.0), 3.0);
        assert_eq!(phi(90.0, 30.0), 3.0);
        assert_eq!(phi(17.5, 30.0), 2.0);
    }

    #[test]
    fn naive_search_takes_the_shortest_path() {
        // 0 -> 1 -> 3 costs 20; 0 -> 2 -> 3 costs 30.
        let g = RoadGraph::for_test(
            &[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
            &[
                (0, 1, 10.0, "a"),
                (1, 3, 10.0, "b"),
                (0, 2, 15.0, "c"),
                (2, 3, 15.0, "d"),
            ],
        );
        let path = search(&g, 0, 3, &car(), None, None).unwrap();
        let segs: Vec<&str> = path
            .iter()
            .map(|&e| g.segment_ids[g.edge_segment[e as usize] as usize].as_str())
            .collect();
        assert_eq!(segs, ["a", "b"]);
        // Penalising the chosen edges threefold sends the alternate the other way.
        let used: HashSet<u32> = path.iter().copied().collect();
        let alt = search(&g, 0, 3, &car(), None, Some(&used)).unwrap();
        assert_ne!(alt, path);
    }

    #[test]
    fn equal_costs_break_ties_on_the_lower_node_as_heapq_does() {
        // Two equal paths via node 1 and node 2; the heap pops node 1 first, and node 3 is
        // relaxed from it and never improved by more than 1e-9 from node 2.
        let g = RoadGraph::for_test(
            &[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
            &[
                (0, 2, 10.0, "c"),
                (0, 1, 10.0, "a"),
                (1, 3, 10.0, "b"),
                (2, 3, 10.0, "d"),
            ],
        );
        let path = search(&g, 0, 3, &car(), None, None).unwrap();
        assert_eq!(g.tail[path[1] as usize], 1);
    }
}
