//! The HTTP contract of `POST /v1/route` (SPEC.md 12), as `varuna_api.routers.route` serves it.
//!
//! Request parsing follows the FastAPI handler field by field - the same defaults, the same
//! Python coercions (`float("72.8")` is a longitude, `True` is 1.0), the same order of checks and
//! the same error codes and messages - and [`as_dict`] writes the response `router.as_dict`
//! writes, rounding every float with Python's `round`.

use serde_json::{json, Map, Value};

use crate::graph::RoadGraph;
use crate::profiles::{valid_keys, Profile};
use crate::pynum::{float_value, repr_float, repr_value, round_n, str_value, truthy};
use crate::pytime::{fromisoformat, DateTime, Parsed};
use crate::router::{PlanRequest, Route, RouteResult};

/// An error envelope: `{"error": {"code", "message", "run_id"}}` with an HTTP status.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub status: u16,
    pub code: String,
    pub message: String,
}

impl ApiError {
    pub fn new(status: u16, code: &str, message: impl Into<String>) -> ApiError {
        ApiError {
            status,
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn body(&self) -> Value {
        json!({"error": {"code": self.code, "message": self.message, "run_id": Value::Null}})
    }
}

/// The request once parsed: what `plan()` is called with, plus the run to route against.
pub struct RouteRequest {
    pub plan: PlanRequest,
    /// `None` means "the newest run for this city" (`run_id` absent or falsy).
    pub run_id: Option<String>,
}

fn point(value: Option<&Value>, field: &str) -> Result<(f64, f64), ApiError> {
    let pair = match value {
        Some(Value::Object(map)) => (
            map.get("lon").cloned().unwrap_or(Value::Null),
            map.get("lat").cloned().unwrap_or(Value::Null),
        ),
        Some(Value::Array(items)) if items.len() >= 2 => (items[0].clone(), items[1].clone()),
        _ => {
            return Err(ApiError::new(
                422,
                "bad_point",
                format!("{field} must be [lon, lat] or {{lon, lat}}."),
            ))
        }
    };
    let (Some(lon), Some(lat)) = (float_value(&pair.0), float_value(&pair.1)) else {
        return Err(ApiError::new(
            422,
            "bad_point",
            format!("{field} must be two numbers, [lon, lat]."),
        ));
    };
    if !((-180.0..=180.0).contains(&lon) && (-90.0..=90.0).contains(&lat)) {
        return Err(ApiError::new(
            422,
            "bad_point",
            format!(
                "{field} is not a coordinate: [{}, {}].",
                repr_float(lon),
                repr_float(lat)
            ),
        ));
    }
    Ok((lon, lat))
}

fn when(value: Option<&Value>, field: &str) -> Result<Option<DateTime>, ApiError> {
    let v = match value {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::String(s)) if s.is_empty() => return Ok(None),
        Some(v) => v,
    };
    match fromisoformat(&str_value(v)) {
        Some(Parsed::Aware(dt)) => Ok(Some(dt)),
        // Python accepts a naive time here and then fails subtracting it from the run's aware
        // step times, answering 500. This refuses it up front with the message that fixes it.
        Some(Parsed::Naive(_)) | None => Err(ApiError::new(
            422,
            "bad_time",
            format!("{field} must be ISO 8601 with an offset, e.g. 2019-07-02T08:40:00+05:30."),
        )),
    }
}

fn flag(value: Option<&Value>, field: &str, default: bool) -> Result<bool, ApiError> {
    match value {
        None | Some(Value::Null) => Ok(default),
        Some(Value::Bool(b)) => Ok(*b),
        Some(other) => Err(ApiError::new(
            422,
            "bad_flag",
            format!("{field} must be true or false, got {}.", repr_value(other)),
        )),
    }
}

/// Parse the body bytes as FastAPI would, then the fields as the route handler does.
pub fn parse_request(bytes: &[u8], profiles: &[Profile]) -> Result<RouteRequest, ApiError> {
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Err(ApiError::new(
            422,
            "validation_error",
            "Request is invalid: body: Field required. Fix and retry.",
        ));
    }
    let body: Value = serde_json::from_slice(bytes).map_err(|e| {
        ApiError::new(
            422,
            "validation_error",
            format!(
                "Request is invalid: body.{}: JSON decode error. Fix and retry.",
                e.column()
            ),
        )
    })?;
    let Value::Object(body) = body else {
        return Err(ApiError::new(
            422,
            "validation_error",
            "Request is invalid: body: Input should be a valid dictionary. Fix and retry.",
        ));
    };
    parse_fields(&body, profiles)
}

fn parse_fields(body: &Map<String, Value>, profiles: &[Profile]) -> Result<RouteRequest, ApiError> {
    let origin = point(body.get("origin"), "origin")?;
    let destination = point(body.get("destination"), "destination")?;

    // str(body.get("profile") or "ambulance")
    let vehicle = match body.get("profile") {
        Some(v) if truthy(v) => str_value(v),
        _ => "ambulance".to_string(),
    };
    let Some(base) = profiles.iter().find(|p| p.key == vehicle) else {
        return Err(ApiError::new(
            422,
            "unknown_profile",
            format!(
                "No vehicle profile {}. Valid profiles: {}.",
                crate::pynum::repr_str(&vehicle),
                valid_keys(profiles)
            ),
        ));
    };

    let tolerance = match body.get("risk_tolerance") {
        None | Some(Value::Null) => None,
        Some(v) => {
            let Some(t) = float_value(v) else {
                return Err(ApiError::new(
                    422,
                    "bad_tolerance",
                    "risk_tolerance must be a number between 0 and 1.",
                ));
            };
            if !(0.0..=1.0).contains(&t) {
                return Err(ApiError::new(
                    422,
                    "bad_tolerance",
                    format!(
                        "risk_tolerance must be between 0 and 1, got {}.",
                        repr_float(t)
                    ),
                ));
            }
            Some(t)
        }
    };

    let trip_id = match body.get("trip_id") {
        None | Some(Value::Null) => None,
        Some(v) => {
            let s = str_value(v);
            if s.trim().is_empty() {
                None
            } else {
                Some(s)
            }
        }
    };

    // Keyword arguments to plan() are evaluated in this order in the Python handler.
    let depart_at = when(body.get("depart_at"), "depart_at")?;
    let run_id = match body.get("run_id") {
        Some(v) if truthy(v) => Some(str_value(v)),
        _ => None,
    };
    let spread = flag(body.get("spread"), "spread", true)?;
    let explain = flag(body.get("explain"), "explain", true)?;

    let mut profile = base.clone();
    if let Some(t) = tolerance {
        profile.risk_tolerance = t;
    }
    Ok(RouteRequest {
        plan: PlanRequest {
            origin,
            destination,
            depart_at,
            profile,
            spread,
            trip_id,
            explain,
        },
        run_id,
    })
}

fn path_json(path: &[(f64, f64)]) -> Value {
    Value::Array(
        path.iter()
            .map(|&(x, y)| json!([round_n(x, 6), round_n(y, 6)]))
            .collect(),
    )
}

/// `_street_names`: the named streets a route uses, in order, without consecutive repeats.
fn street_names(route: &Route, graph: &RoadGraph, limit: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for leg in &route.legs {
        let name = &graph.edge_name[leg.edge as usize];
        if !name.is_empty() && out.last() != Some(name) {
            out.push(name.clone());
        }
        if out.len() >= limit {
            break;
        }
    }
    out
}

fn route_json(r: Option<&Route>, graph: &RoadGraph) -> Value {
    let Some(r) = r else {
        return Value::Null;
    };
    json!({
        "minutes": round_n(r.minutes(), 1),
        "distance_m": round_n(r.distance_m, 1),
        "max_depth_cm": round_n(r.max_depth_cm, 1),
        "depart": r.depart.isoformat(),
        "arrive": r.arrive.isoformat(),
        "safe_until": r.safe_until.map(|t| t.isoformat()),
        "path": path_json(&r.path),
        "streets": street_names(r, graph, 12),
    })
}

/// `router.as_dict`: the API's shape for a route result.
pub fn as_dict(result: &RouteResult, graph: &RoadGraph, ms: f64) -> Value {
    json!({
        "run_id": result.run_id,
        "profile": result.profile,
        "depart_at": result.depart.isoformat(),
        "naive": route_json(result.naive.as_ref(), graph),
        "varuna": route_json(result.varuna.as_ref(), graph),
        "alternates": result.alternates.iter().map(|r| route_json(Some(r), graph)).collect::<Vec<_>>(),
        "avoided": result.avoided.iter().map(|a| json!({
            "segment_id": graph.segment_ids[a.segment as usize],
            "name": a.name,
            "depth_cm": round_n(a.depth_cm, 1),
            "probability": round_n(a.probability, 3),
            "at": a.at.isoformat(),
            "closed_reason": a.closed_reason,
            "path": path_json(&a.path),
        })).collect::<Vec<_>>(),
        "corridors": result.corridors.iter().map(|c| json!({
            "id": c.id,
            "label": c.label,
            "route": route_json(Some(&c.route), graph),
            "share": round_n(c.share, 4),
            "assigned": c.assigned,
            "capacity_score": round_n(c.capacity_score, 4),
            "max_probability": round_n(c.max_probability, 3),
        })).collect::<Vec<_>>(),
        "reasons": result.reasons,
        "trip_id": result.trip_id,
        "notes": result.notes,
        "ms": round_n(ms, 1),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profiles::defaults;

    fn parse(body: Value) -> Result<RouteRequest, ApiError> {
        parse_request(body.to_string().as_bytes(), &defaults())
    }

    #[test]
    fn fields_default_as_the_python_handler_defaults_them() {
        let p =
            parse(json!({"origin": [72.84, 19.0], "destination": {"lon": 72.86, "lat": 19.04}}))
                .unwrap();
        assert_eq!(p.plan.profile.key, "ambulance");
        assert!(p.plan.spread && p.plan.explain);
        assert!(p.plan.trip_id.is_none() && p.run_id.is_none() && p.plan.depart_at.is_none());
        let p = parse(
            json!({"origin": ["72.84", "19.0"], "destination": [72.86, 19.04],
            "profile": "", "trip_id": "  ", "run_id": "", "risk_tolerance": "0.3"}),
        )
        .unwrap();
        assert_eq!(p.plan.origin, (72.84, 19.0));
        assert_eq!(p.plan.profile.key, "ambulance");
        assert_eq!(p.plan.profile.risk_tolerance, 0.3);
        assert!(p.plan.trip_id.is_none() && p.run_id.is_none());
    }

    #[test]
    fn errors_carry_the_python_codes_and_messages() {
        let e = parse(json!({"origin": 5, "destination": [1, 2]}))
            .err()
            .unwrap();
        assert_eq!(
            (e.code.as_str(), e.message.as_str()),
            ("bad_point", "origin must be [lon, lat] or {lon, lat}.")
        );
        let e = parse(json!({"origin": [200, 0], "destination": [1, 2]}))
            .err()
            .unwrap();
        assert_eq!(e.message, "origin is not a coordinate: [200.0, 0.0].");
        let e = parse(json!({"origin": [1, 2], "destination": [1, 2], "profile": "boat"}))
            .err()
            .unwrap();
        assert_eq!(e.code, "unknown_profile");
        assert_eq!(e.message, "No vehicle profile 'boat'. Valid profiles: ambulance, bus, car, fire_tender, pedestrian, truck, two_wheeler.");
        let e = parse(json!({"origin": [1, 2], "destination": [1, 2], "risk_tolerance": 2}))
            .err()
            .unwrap();
        assert_eq!(
            e.message,
            "risk_tolerance must be between 0 and 1, got 2.0."
        );
        let e = parse(json!({"origin": [1, 2], "destination": [1, 2], "spread": "yes"}))
            .err()
            .unwrap();
        assert_eq!(e.message, "spread must be true or false, got 'yes'.");
        let e = parse(json!({"origin": [1, 2], "destination": [1, 2], "depart_at": "08:40"}))
            .err()
            .unwrap();
        assert_eq!(e.code, "bad_time");
        let e = parse_request(b"[1]", &defaults()).err().unwrap();
        assert_eq!(e.code, "validation_error");
    }
}
