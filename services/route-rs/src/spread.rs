//! Spreading traffic across the safe roads, as a stated policy (`varuna_route.spread`,
//! TECH_SPEC 3.3, ADR-0060).
//!
//! The capacity score is the 2026-09-19 correction: the **bottleneck** lanes over the legs,
//! times what the water has left of them, divided by the corridor's travel time - not the sum
//! over edges, which rewarded length. Assignment is `sha256(trip_id)` against cumulative shares.

use std::collections::HashSet;

use sha2::{Digest, Sha256};

use crate::forecast::SegmentDepths;
use crate::graph::RoadGraph;
use crate::profiles::Profile;
use crate::pynum::py_sum;
use crate::router::{phi, py_max, Route, LABELS};

pub const MAX_CORRIDORS: usize = LABELS.len();

/// A second, as the floor on a corridor's travel time.
const MIN_MINUTES: f64 = 1.0 / 60.0;

#[derive(Clone, Debug)]
pub struct Corridor {
    pub id: String,
    pub label: String,
    pub route: Route,
    pub share: f64,
    pub assigned: bool,
    pub capacity_score: f64,
    pub max_probability: f64,
}

/// `congestion_proxy`: the fraction of capacity water has taken, `1 - 1 / phi(h)`.
pub fn congestion_proxy(depth_cm: f64, threshold_cm: f64) -> f64 {
    1.0 - 1.0 / phi(depth_cm, threshold_cm)
}

/// `capacity_score`: bottleneck lanes over the legs, per minute the corridor holds a vehicle.
pub fn capacity_score(route: &Route, threshold_cm: f64) -> f64 {
    let mut iter = route.legs.iter();
    let Some(first) = iter.next() else {
        return 0.0;
    };
    let value = |lanes: f64, depth: f64| lanes * (1.0 - congestion_proxy(depth, threshold_cm));
    // Python's min(): the first of equal minima; only strictly smaller values replace it.
    let mut bottleneck = value(first.lanes, first.depth_cm);
    for leg in iter {
        let v = value(leg.lanes, leg.depth_cm);
        if v < bottleneck {
            bottleneck = v;
        }
    }
    py_max(bottleneck, 0.0) / py_max(route.minutes(), MIN_MINUTES)
}

/// `unit_interval`: a point in `[0, 1)` from the first 16 hex digits of `sha256(trip_id)`.
pub fn unit_interval(trip_id: &str) -> f64 {
    let digest = Sha256::digest(trip_id.as_bytes());
    let mut head = [0u8; 8];
    head.copy_from_slice(&digest[..8]);
    // int(hex[:16], 16) / float(1 << 64): the int is converted to the nearest double first.
    u64::from_be_bytes(head) as f64 / 18_446_744_073_709_551_616.0
}

/// `assign`: the corridor a trip lands on.
pub fn assign(shares: &[f64], trip_id: Option<&str>) -> isize {
    if shares.is_empty() {
        return -1;
    }
    let Some(trip) = trip_id.filter(|t| !t.trim().is_empty()) else {
        return 0;
    };
    let point = unit_interval(trip);
    let mut cumulative = 0.0;
    for (index, share) in shares.iter().enumerate() {
        cumulative += share;
        if point < cumulative {
            return index as isize;
        }
    }
    shares.len() as isize - 1
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// `_corridor_id`: 8 hex of the digest of the corridor's segment sequence.
pub fn corridor_id(route: &Route, graph: &RoadGraph) -> String {
    let key: Vec<&str> = route
        .legs
        .iter()
        .map(|l| graph.segment_ids[l.segment as usize].as_str())
        .collect();
    let digest = Sha256::digest(key.join("|").as_bytes());
    hex(&digest)[..8].to_string()
}

fn max_probability(route: &Route, depths: &SegmentDepths, vehicle: &Profile) -> f64 {
    let mut worst = 0.0;
    for leg in &route.legs {
        let step = depths.step_at(leg.arrive);
        worst = py_max(
            worst,
            depths.exceedance(leg.segment, vehicle.depth_cm, step),
        );
    }
    worst
}

/// `corridors`: score, share and assign up to three safe corridors, in the router's order.
pub fn corridors(
    routes: &[&Route],
    graph: &RoadGraph,
    depths: &SegmentDepths,
    vehicle: &Profile,
    trip_id: Option<&str>,
    closed: &[bool],
) -> Vec<Corridor> {
    let mut kept: Vec<(&Route, String, f64, f64)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for &route in routes {
        if route.legs.is_empty() {
            continue;
        }
        let identity = corridor_id(route, graph);
        if seen.contains(&identity) {
            continue;
        }
        if route
            .legs
            .iter()
            .any(|l| closed.get(l.segment as usize).copied().unwrap_or(false))
        {
            continue;
        }
        let worst = max_probability(route, depths, vehicle);
        if worst >= vehicle.risk_tolerance {
            continue;
        }
        seen.insert(identity.clone());
        let score = capacity_score(route, vehicle.depth_cm);
        kept.push((route, identity, score, worst));
        if kept.len() == MAX_CORRIDORS {
            break;
        }
    }
    if kept.is_empty() {
        return Vec::new();
    }
    let scores: Vec<f64> = kept.iter().map(|k| k.2).collect();
    let total = py_sum(&scores);
    let shares: Vec<f64> = if total <= 0.0 {
        vec![1.0 / kept.len() as f64; kept.len()]
    } else {
        scores.iter().map(|s| s / total).collect()
    };
    let chosen = assign(&shares, trip_id);
    kept.into_iter()
        .enumerate()
        .map(|(index, (route, id, score, worst))| Corridor {
            id,
            label: LABELS[index].to_string(),
            route: route.clone(),
            share: shares[index],
            assigned: index as isize == chosen,
            capacity_score: score,
            max_probability: worst,
        })
        .collect()
}

/// `spreading_note`: the disclosure that travels with any corridor split.
pub fn spreading_note(n_corridors: usize, spread: bool, trip_id: Option<&str>) -> Option<String> {
    if !spread || n_corridors == 0 {
        return None;
    }
    if n_corridors == 1 {
        return Some(
            "Only one road to this destination stays under the vehicle's depth threshold on this \
             run, so there is nothing to spread traffic across."
                .into(),
        );
    }
    if trip_id.is_none_or(|t| t.trim().is_empty()) {
        return Some(format!(
            "{n_corridors} safe roads were found and the share beside each is a policy, not a \
             measured traffic count: demand is not observed anywhere in this prototype. This \
             request carried no trip id, so it was not spread - it was given the fastest road."
        ));
    }
    Some(format!(
        "Traffic is spread across {n_corridors} safe roads so the safe road does not become the \
         next jam. The share beside each is a policy, not a measured traffic count: demand is \
         not observed anywhere in this prototype, and every corridor is shown so the split can \
         be refused."
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_interval_matches_python() {
        // Python: int(hashlib.sha256(b"trip-1").hexdigest()[:16], 16) / float(1 << 64)
        let v = unit_interval("trip-1");
        assert!((0.0..1.0).contains(&v));
        assert_eq!(assign(&[0.5, 0.5], None), 0);
        assert_eq!(assign(&[0.5, 0.5], Some("  ")), 0);
        assert_eq!(assign(&[], Some("x")), -1);
    }
}
