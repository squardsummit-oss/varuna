//! Why a route went the way it did, as structured records (`varuna_route.reasons`).
//!
//! No sentences here either: a reason is a kind and its numbers, and the frontend words it. A
//! reason whose number is missing is not emitted.

use serde_json::{json, Value};

use crate::forecast::{SegmentDepths, DRY_CM};
use crate::graph::RoadGraph;
use crate::ops::OpsOverlay;
use crate::profiles::Profile;
use crate::pynum::round_n;
use crate::router::{Avoided, Route, MAX_AVOIDED_REASONS};

fn avoided_reason(entry: &Avoided, graph: &RoadGraph, threshold_cm: f64) -> Value {
    json!({
        "kind": "avoided",
        "segment_id": graph.segment_ids[entry.segment as usize],
        "name": entry.name,
        "depth_cm": round_n(entry.depth_cm, 1),
        "threshold_cm": threshold_cm,
        "at": entry.at.isoformat(),
        "probability": round_n(entry.probability, 3),
    })
}

fn design_reason(
    segment: u32,
    name: &str,
    graph: &RoadGraph,
    depths: &SegmentDepths,
) -> Option<Value> {
    let intensity = graph
        .design_intensity
        .get(segment as usize)
        .copied()
        .flatten()?;
    if depths.rain_aoi_mm_h.is_empty() {
        return None;
    }
    // Python's max(): the first of equal maxima; a NaN first element stays the answer.
    let mut peak = depths.rain_aoi_mm_h[0];
    for &v in &depths.rain_aoi_mm_h[1..] {
        if v > peak {
            peak = v;
        }
    }
    if !peak.is_finite() || peak <= 0.0 {
        return None;
    }
    Some(json!({
        "kind": "design",
        "segment_id": graph.segment_ids[segment as usize],
        "name": name,
        "design_intensity_mm_h": round_n(intensity, 1),
        "forecast_peak_mm_h": round_n(peak, 1),
    }))
}

fn timing_reason(
    route: &Route,
    graph: &RoadGraph,
    depths: &SegmentDepths,
    vehicle: &Profile,
) -> Option<Value> {
    let mut worst: Option<(u32, u32)> = None; // (segment, edge)
    let mut worst_peak = 0.0;
    for leg in &route.legs {
        let peak = depths.peak(leg.segment);
        if peak > worst_peak {
            worst_peak = peak;
            worst = Some((leg.segment, leg.edge));
        }
    }
    if worst_peak <= DRY_CM {
        return None;
    }
    let (segment, edge) = worst?;
    let empty = Vec::new();
    let series = depths.depth[segment as usize].as_ref().unwrap_or(&empty);
    let mut dry_until = None;
    let mut peak_step = 0;
    for (step, &value) in series.iter().enumerate() {
        if value <= DRY_CM {
            dry_until = Some(depths.time_of(step).isoformat());
        }
        if value >= worst_peak {
            peak_step = step;
            break;
        }
    }
    let dry_until = dry_until?;
    let name = &graph.edge_name[edge as usize];
    Some(json!({
        "kind": "timing",
        "segment_id": graph.segment_ids[segment as usize],
        "name": if name.is_empty() { "Unnamed road" } else { name.as_str() },
        "dry_until": dry_until,
        "dry_below_cm": DRY_CM,
        "depth_cm": round_n(worst_peak, 1),
        "threshold_cm": vehicle.depth_cm,
        "at": depths.time_of(peak_step).isoformat(),
    }))
}

fn closure_reason(
    segment: u32,
    name: &str,
    graph: &RoadGraph,
    overlay: &OpsOverlay,
) -> Option<Value> {
    let sid = &graph.segment_ids[segment as usize];
    let closure = overlay.closures.get(sid)?;
    if closure.reason.is_empty() {
        return None;
    }
    Some(json!({
        "kind": "closure",
        "segment_id": sid,
        "name": name,
        "reason": closure.reason,
        "user": closure.user,
        "at": closure.ts.isoformat(),
        "until": closure.until.map(|u| u.isoformat()),
    }))
}

/// `build_reasons`: every reason this route can support, in UI_SPEC 4's order.
pub fn build_reasons(
    avoided: &[Avoided],
    route: Option<&Route>,
    depths: &SegmentDepths,
    vehicle: &Profile,
    graph: &RoadGraph,
    overlay: &OpsOverlay,
    closed_on_naive: &[(u32, String)],
) -> Vec<Value> {
    let mut out = Vec::new();
    for entry in avoided.iter().take(MAX_AVOIDED_REASONS) {
        out.push(avoided_reason(entry, graph, vehicle.depth_cm));
    }
    if let Some(worst) = avoided.first() {
        if let Some(design) = design_reason(worst.segment, &worst.name, graph, depths) {
            out.push(design);
        }
    }
    if let Some(route) = route {
        if let Some(timing) = timing_reason(route, graph, depths, vehicle) {
            out.push(timing);
        }
    }
    for (segment, name) in closed_on_naive {
        if let Some(closure) = closure_reason(*segment, name, graph, overlay) {
            out.push(closure);
        }
    }
    out
}
