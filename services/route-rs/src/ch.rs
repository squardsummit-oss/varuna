//! Contraction hierarchies for the **naive** search only (SPEC.md 11.9).
//!
//! A contraction hierarchy precomputes shortcuts over a fixed edge weight. That makes it exact
//! for the naive router, whose cost is the dry-weather free-flow time `t_e / speed` and never
//! depends on when the vehicle arrives, and useless for the VARUNA search, whose cost reads the
//! forecast at the arrival step and whose refusals change with it. Time-dependent CH exists, but
//! it stores piecewise-linear travel-time functions per shortcut; this service does not build
//! one, and does not claim to. The VARUNA search and its alternates stay Dijkstra.
//!
//! One hierarchy is built per distinct profile speed, because the naive cost is `t_e / speed`
//! and dividing each edge before summing rounds differently from dividing a sum: building on the
//! same per-edge doubles the Dijkstra sums keeps the two searches' arithmetic comparable.
//!
//! "Exact" here means the same *shortest distance*. Python's Dijkstra also has a tie rule - of
//! two paths within 1e-9 s of each other, the one relaxed first wins - that a hierarchy cannot
//! see. [`agreement`] measures how often that matters on the real graph, and the service uses the
//! hierarchy only when told to (`--naive ch`).

use std::cmp::Ordering;
use std::collections::BinaryHeap;

use crate::graph::RoadGraph;
use crate::profiles::Profile;
use crate::router::{py_max, search, NaiveEngine};

#[derive(Clone, Copy, Debug)]
enum Kind {
    Original(u32),
    Shortcut(u32, u32),
}

#[derive(Clone, Copy, Debug)]
struct ChEdge {
    from: u32,
    to: u32,
    weight: f64,
    kind: Kind,
}

/// A min-heap entry on `(cost, node)`.
#[derive(Clone, Copy, PartialEq)]
struct Item(f64, u32);
impl Eq for Item {}
impl PartialOrd for Item {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Item {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .0
            .partial_cmp(&self.0)
            .unwrap_or(Ordering::Equal)
            .then_with(|| other.1.cmp(&self.1))
    }
}

/// One hierarchy over one edge weighting.
pub struct Hierarchy {
    pub speed: f64,
    edges: Vec<ChEdge>,
    /// Upward edges out of each node (rank of head > rank of tail), as ids into `edges`.
    up_out: Vec<Vec<u32>>,
    /// Upward edges into each node, walked backwards (rank of tail > rank of head).
    up_in: Vec<Vec<u32>>,
    pub shortcuts: usize,
    pub build_ms: f64,
}

struct Remaining {
    out: Vec<Vec<u32>>,
    inc: Vec<Vec<u32>>,
}

const WITNESS_SETTLE_LIMIT: usize = 500;

/// Is there a path `u -> x` avoiding `skip` of cost `<= limit` in the remaining graph?
#[allow(clippy::too_many_arguments)]
fn witness(
    edges: &[ChEdge],
    rem: &Remaining,
    contracted: &[bool],
    u: u32,
    x: u32,
    skip: u32,
    limit: f64,
    dist: &mut [f64],
    touched: &mut Vec<u32>,
) -> bool {
    let mut heap = BinaryHeap::new();
    dist[u as usize] = 0.0;
    touched.push(u);
    heap.push(Item(0.0, u));
    let mut settled = 0;
    let mut found = false;
    while let Some(Item(d, v)) = heap.pop() {
        if d > dist[v as usize] {
            continue;
        }
        if d > limit {
            break;
        }
        if v == x {
            found = true;
            break;
        }
        settled += 1;
        if settled > WITNESS_SETTLE_LIMIT {
            break;
        }
        for &id in &rem.out[v as usize] {
            let e = edges[id as usize];
            if e.to == skip || contracted[e.to as usize] {
                continue;
            }
            let nd = d + e.weight;
            if nd < dist[e.to as usize] {
                if dist[e.to as usize] == f64::INFINITY {
                    touched.push(e.to);
                }
                dist[e.to as usize] = nd;
                heap.push(Item(nd, e.to));
            }
        }
    }
    for &t in touched.iter() {
        dist[t as usize] = f64::INFINITY;
    }
    touched.clear();
    found
}

/// The shortcuts contracting `v` would add, as `(from, to, weight, in_edge, out_edge)`.
fn needed_shortcuts(
    v: u32,
    edges: &[ChEdge],
    rem: &Remaining,
    contracted: &[bool],
    dist: &mut [f64],
    touched: &mut Vec<u32>,
) -> Vec<(u32, u32, f64, u32, u32)> {
    let mut out = Vec::new();
    for &in_id in &rem.inc[v as usize] {
        let ie = edges[in_id as usize];
        if contracted[ie.from as usize] || ie.from == v {
            continue;
        }
        for &out_id in &rem.out[v as usize] {
            let oe = edges[out_id as usize];
            if contracted[oe.to as usize] || oe.to == v || oe.to == ie.from {
                continue;
            }
            let w = ie.weight + oe.weight;
            if !witness(edges, rem, contracted, ie.from, oe.to, v, w, dist, touched) {
                out.push((ie.from, oe.to, w, in_id, out_id));
            }
        }
    }
    out
}

fn degree(v: u32, edges: &[ChEdge], rem: &Remaining, contracted: &[bool]) -> i64 {
    let live = |ids: &Vec<u32>, pick: fn(&ChEdge) -> u32| {
        ids.iter()
            .filter(|&&id| !contracted[pick(&edges[id as usize]) as usize])
            .count() as i64
    };
    live(&rem.inc[v as usize], |e| e.from) + live(&rem.out[v as usize], |e| e.to)
}

impl Hierarchy {
    /// Contract the graph under weights `time_s / speed`.
    pub fn build(graph: &RoadGraph, speed: f64) -> Hierarchy {
        let started = std::time::Instant::now();
        let n = graph.n_nodes();
        let mut edges: Vec<ChEdge> = Vec::with_capacity(graph.n_edges() * 2);
        let mut rem = Remaining {
            out: vec![Vec::new(); n],
            inc: vec![Vec::new(); n],
        };
        for e in 0..graph.n_edges() {
            let (from, to) = (graph.tail[e], graph.head[e]);
            if from == to {
                continue;
            }
            let id = edges.len() as u32;
            edges.push(ChEdge {
                from,
                to,
                weight: graph.edge_time_s[e] / speed,
                kind: Kind::Original(e as u32),
            });
            rem.out[from as usize].push(id);
            rem.inc[to as usize].push(id);
        }

        let mut contracted = vec![false; n];
        let mut deleted_neighbours = vec![0i64; n];
        let mut rank = vec![0u32; n];
        let mut dist = vec![f64::INFINITY; n];
        let mut touched = Vec::new();

        let priority = |v: u32,
                        edges: &[ChEdge],
                        rem: &Remaining,
                        contracted: &[bool],
                        deleted: &[i64],
                        dist: &mut [f64],
                        touched: &mut Vec<u32>| {
            let added = needed_shortcuts(v, edges, rem, contracted, dist, touched).len() as i64;
            added - degree(v, edges, rem, contracted) + deleted[v as usize]
        };

        let mut heap: BinaryHeap<std::cmp::Reverse<(i64, u32)>> = BinaryHeap::new();
        for v in 0..n as u32 {
            let p = priority(
                v,
                &edges,
                &rem,
                &contracted,
                &deleted_neighbours,
                &mut dist,
                &mut touched,
            );
            heap.push(std::cmp::Reverse((p, v)));
        }

        let mut next_rank = 0u32;
        let mut shortcuts = 0usize;
        while let Some(std::cmp::Reverse((p, v))) = heap.pop() {
            if contracted[v as usize] {
                continue;
            }
            // Lazy update: recompute, and put it back if it no longer beats the next node.
            let fresh = priority(
                v,
                &edges,
                &rem,
                &contracted,
                &deleted_neighbours,
                &mut dist,
                &mut touched,
            );
            if fresh > p {
                if let Some(std::cmp::Reverse((q, _))) = heap.peek() {
                    if fresh > *q {
                        heap.push(std::cmp::Reverse((fresh, v)));
                        continue;
                    }
                }
            }
            let added = needed_shortcuts(v, &edges, &rem, &contracted, &mut dist, &mut touched);
            for (from, to, weight, a, b) in added {
                let id = edges.len() as u32;
                edges.push(ChEdge {
                    from,
                    to,
                    weight,
                    kind: Kind::Shortcut(a, b),
                });
                rem.out[from as usize].push(id);
                rem.inc[to as usize].push(id);
                shortcuts += 1;
            }
            contracted[v as usize] = true;
            rank[v as usize] = next_rank;
            next_rank += 1;
            for &id in rem.out[v as usize].iter().chain(rem.inc[v as usize].iter()) {
                let e = edges[id as usize];
                let other = if e.from == v { e.to } else { e.from };
                if !contracted[other as usize] {
                    deleted_neighbours[other as usize] += 1;
                }
            }
        }

        let mut up_out = vec![Vec::new(); n];
        let mut up_in = vec![Vec::new(); n];
        for (id, e) in edges.iter().enumerate() {
            if rank[e.to as usize] > rank[e.from as usize] {
                up_out[e.from as usize].push(id as u32);
            } else {
                up_in[e.to as usize].push(id as u32);
            }
        }
        Hierarchy {
            speed,
            edges,
            up_out,
            up_in,
            shortcuts,
            build_ms: started.elapsed().as_secs_f64() * 1000.0,
        }
    }

    fn unpack(&self, id: u32, out: &mut Vec<u32>) {
        let mut stack = vec![id];
        while let Some(id) = stack.pop() {
            match self.edges[id as usize].kind {
                Kind::Original(e) => out.push(e),
                Kind::Shortcut(a, b) => {
                    stack.push(b);
                    stack.push(a);
                }
            }
        }
    }

    /// Shortest dry-weather path as original edge indices; `None` when unreachable.
    pub fn query(&self, source: u32, target: u32) -> Option<Vec<u32>> {
        let n = self.up_out.len();
        let mut df = vec![f64::INFINITY; n];
        let mut db = vec![f64::INFINITY; n];
        let mut pf = vec![u32::MAX; n];
        let mut pb = vec![u32::MAX; n];
        let mut hf = BinaryHeap::new();
        let mut hb = BinaryHeap::new();
        df[source as usize] = 0.0;
        db[target as usize] = 0.0;
        hf.push(Item(0.0, source));
        hb.push(Item(0.0, target));
        let mut best = f64::INFINITY;
        let mut meet = u32::MAX;
        if source == target {
            return Some(Vec::new());
        }
        loop {
            let top_f = hf.peek().map_or(f64::INFINITY, |i: &Item| i.0);
            let top_b = hb.peek().map_or(f64::INFINITY, |i: &Item| i.0);
            if top_f >= best && top_b >= best {
                break;
            }
            let forward = top_f <= top_b;
            let (heap, dist, par, other, adj) = if forward {
                (&mut hf, &mut df, &mut pf, &db, &self.up_out)
            } else {
                (&mut hb, &mut db, &mut pb, &df, &self.up_in)
            };
            let Some(Item(d, v)) = heap.pop() else { break };
            if d > dist[v as usize] {
                continue;
            }
            let through = d + other[v as usize];
            if through < best {
                best = through;
                meet = v;
            }
            for &id in &adj[v as usize] {
                let e = self.edges[id as usize];
                let next = if forward { e.to } else { e.from };
                let nd = d + e.weight;
                if nd < dist[next as usize] {
                    dist[next as usize] = nd;
                    par[next as usize] = id;
                    heap.push(Item(nd, next));
                }
            }
        }
        if meet == u32::MAX {
            return None;
        }
        let mut forward_ids = Vec::new();
        let mut v = meet;
        while v != source {
            let id = pf[v as usize];
            forward_ids.push(id);
            v = self.edges[id as usize].from;
        }
        forward_ids.reverse();
        let mut out = Vec::new();
        for id in forward_ids {
            self.unpack(id, &mut out);
        }
        let mut v = meet;
        while v != target {
            let id = pb[v as usize];
            self.unpack(id, &mut out);
            v = self.edges[id as usize].to;
        }
        Some(out)
    }
}

/// A hierarchy per distinct profile speed, serving the naive search.
pub struct ChNaive {
    pub hierarchies: Vec<Hierarchy>,
}

impl ChNaive {
    /// Build one hierarchy per distinct `max(speed_scale, 0.05)` across the profiles, in parallel.
    pub fn build(graph: &RoadGraph, profiles: &[Profile]) -> ChNaive {
        let mut speeds: Vec<f64> = Vec::new();
        for p in profiles {
            let s = py_max(p.speed_scale, 0.05);
            if !speeds.contains(&s) {
                speeds.push(s);
            }
        }
        let hierarchies = std::thread::scope(|scope| {
            let handles: Vec<_> = speeds
                .iter()
                .map(|&s| scope.spawn(move || Hierarchy::build(graph, s)))
                .collect();
            handles
                .into_iter()
                .map(|h| h.join().expect("hierarchy build thread"))
                .collect()
        });
        ChNaive { hierarchies }
    }
}

impl NaiveEngine for ChNaive {
    fn naive_path(
        &self,
        graph: &RoadGraph,
        source: u32,
        target: u32,
        vehicle: &Profile,
    ) -> Option<Vec<u32>> {
        let speed = py_max(vehicle.speed_scale, 0.05);
        match self.hierarchies.iter().find(|h| h.speed == speed) {
            Some(h) => h.query(source, target),
            // A speed no profile had at build time cannot come from the API (a risk tolerance
            // override keeps the speed); answer it the exact way rather than refuse.
            None => search(graph, source, target, vehicle, None, None),
        }
    }
    fn name(&self) -> &'static str {
        "contraction-hierarchy"
    }
}

/// How often the hierarchy's naive path equals the Dijkstra's, over `pairs` deterministic
/// origin-destination pairs: `(same_path, same_cost_different_path, different_cost, unreachable)`.
pub fn agreement(
    graph: &RoadGraph,
    ch: &ChNaive,
    vehicle: &Profile,
    pairs: usize,
    seed: u64,
) -> (usize, usize, usize, usize) {
    let n = graph.n_nodes() as u64;
    let mut state = seed | 1;
    let mut next = || {
        // xorshift64*: deterministic, dependency-free.
        state ^= state >> 12;
        state ^= state << 25;
        state ^= state >> 27;
        (state.wrapping_mul(0x2545_F491_4F6C_DD1D) % n) as u32
    };
    let cost = |edges: &[u32]| -> f64 {
        let speed = py_max(vehicle.speed_scale, 0.05);
        edges
            .iter()
            .fold(0.0, |acc, &e| acc + graph.edge_time_s[e as usize] / speed)
    };
    let (mut same, mut tie, mut differ, mut none) = (0, 0, 0, 0);
    for _ in 0..pairs {
        let (s, t) = (next(), next());
        let a = search(graph, s, t, vehicle, None, None);
        let b = ch.naive_path(graph, s, t, vehicle);
        match (a, b) {
            (None, None) => none += 1,
            (Some(a), Some(b)) if a == b => same += 1,
            (Some(a), Some(b)) if (cost(&a) - cost(&b)).abs() <= 1e-6 => tie += 1,
            _ => differ += 1,
        }
    }
    (same, tie, differ, none)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profiles::defaults;

    #[test]
    fn hierarchy_finds_the_dijkstra_path_on_a_grid() {
        // A 6 x 6 grid, two-way, with distinct irrational-ish weights so there are no ties.
        let side = 6u32;
        let mut coords = Vec::new();
        for y in 0..side {
            for x in 0..side {
                coords.push((f64::from(x), f64::from(y)));
            }
        }
        let mut edges: Vec<(u32, u32, f64, String)> = Vec::new();
        let mut k = 0.0;
        for y in 0..side {
            for x in 0..side {
                let v = y * side + x;
                for (dx, dy) in [(1, 0), (0, 1)] {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < side && ny < side {
                        let w = v * side + dx;
                        k += 1.0;
                        let t = 10.0 + (k * 1.618_033_988_75_f64).fract() * 7.0;
                        edges.push((v, ny * side + nx, t, format!("s{w}-{k}")));
                        edges.push((ny * side + nx, v, t + 0.5, format!("s{w}-{k}")));
                    }
                }
            }
        }
        let refs: Vec<(u32, u32, f64, &str)> = edges
            .iter()
            .map(|e| (e.0, e.1, e.2, e.3.as_str()))
            .collect();
        let g = RoadGraph::for_test(&coords, &refs);
        let profiles = defaults();
        let ch = ChNaive::build(&g, &profiles);
        for p in &profiles {
            let (same, tie, differ, none) = agreement(&g, &ch, p, 300, 7);
            assert_eq!(
                differ, 0,
                "{}: {same} same, {tie} ties, {none} unreachable",
                p.key
            );
        }
    }
}
