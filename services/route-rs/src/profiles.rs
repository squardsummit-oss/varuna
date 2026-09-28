//! Vehicle profiles (`varuna_route.profiles`).
//!
//! The table the service routes with is the one in the graph export, written from the Python
//! `PROFILES` dict, so a threshold changed in Python reaches Rust on the next export rather than
//! drifting. [`defaults`] is a copy for unit tests only, and a test pins it to the export.

use serde::Deserialize;

#[derive(Deserialize, Debug, Clone, PartialEq)]
pub struct Profile {
    pub key: String,
    pub label: String,
    /// Depth at which this vehicle can no longer pass.
    pub depth_cm: f64,
    /// Probability of exceeding `depth_cm` the route will accept on an edge.
    pub risk_tolerance: f64,
    /// Multiplier on free-flow speed.
    pub speed_scale: f64,
    #[serde(default)]
    pub hazard_rule: bool,
}

/// The Python table as of 2026-09-19, for tests that run without an export.
pub fn defaults() -> Vec<Profile> {
    let p = |key: &str, label: &str, depth: f64, tol: f64, speed: f64, hazard: bool| Profile {
        key: key.into(),
        label: label.into(),
        depth_cm: depth,
        risk_tolerance: tol,
        speed_scale: speed,
        hazard_rule: hazard,
    };
    vec![
        p("two_wheeler", "Two-wheeler", 15.0, 0.5, 1.0, false),
        p("car", "Car", 30.0, 0.5, 1.0, false),
        p("bus", "Bus", 45.0, 0.4, 0.8, false),
        p("truck", "Truck", 45.0, 0.4, 0.8, false),
        p("ambulance", "Ambulance", 60.0, 0.2, 1.15, false),
        p("fire_tender", "Fire tender", 60.0, 0.2, 1.0, false),
        p("pedestrian", "Pedestrian", 30.0, 0.3, 0.06, true),
    ]
}

/// The valid keys, sorted, as the Python error message lists them.
pub fn valid_keys(profiles: &[Profile]) -> String {
    let mut keys: Vec<&str> = profiles.iter().map(|p| p.key.as_str()).collect();
    keys.sort_unstable();
    keys.join(", ")
}
