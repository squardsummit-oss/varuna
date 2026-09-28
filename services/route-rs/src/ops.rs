//! Authority closures, folded from the append-only ops log at request time
//! (`varuna_route.ops_overlay.active`).
//!
//! The log is `data/ops/<city>.jsonl`, the same file the Python API appends to. It is read on
//! every request, as the Python router reads it, because a closure's `until` is evaluated at the
//! departure time and a closure entered a second ago must turn the next route. This module only
//! ever reads the file; appending stays the Python API's job.

use std::collections::HashMap;
use std::path::Path;

use serde_json::{Map, Value};

use crate::pynum::str_value;
use crate::pytime::{fromisoformat, DateTime, IST_OFFSET_US};

#[derive(Clone, Debug)]
pub struct Closure {
    pub segment_id: String,
    pub reason: String,
    pub user: String,
    pub ts: DateTime,
    pub until: Option<DateTime>,
}

/// The closures active at one instant. Only closures matter to routing; pump status does not.
#[derive(Default)]
pub struct OpsOverlay {
    /// Active closures keyed by segment id.
    pub closures: HashMap<String, Closure>,
}

impl OpsOverlay {
    /// `reason_for(segment_id)`: the officer's text, `""` if they gave none, `None` if open.
    pub fn reason_for(&self, segment_id: &str) -> Option<&str> {
        self.closures.get(segment_id).map(|c| c.reason.as_str())
    }
}

/// `_parse_time`: `None` for null, `""` or anything `fromisoformat` refuses; naive is IST.
fn parse_time(value: Option<&Value>) -> Option<DateTime> {
    let v = value?;
    if v.is_null() || v.as_str() == Some("") {
        return None;
    }
    fromisoformat(&str_value(v)).map(|p| p.assume(IST_OFFSET_US))
}

/// `str(item.get(key, default))`.
fn get_str(item: &Map<String, Value>, key: &str, default: &str) -> String {
    match item.get(key) {
        Some(v) => str_value(v),
        None => default.to_string(),
    }
}

/// `entries(city)` then `active(city, at)`.
pub fn active(path: &Path, at: DateTime) -> OpsOverlay {
    let Ok(text) = std::fs::read_to_string(path) else {
        return OpsOverlay::default();
    };
    let mut closures: HashMap<String, Closure> = HashMap::new();
    // Python's str.splitlines() also splits on \r, \v, \f and a few Unicode separators; the log
    // is written with "\n" by `ops_overlay.append`, and a JSON line cannot contain a raw control
    // character, so splitting on "\n" and trimming finds the same lines.
    for raw in text.split('\n') {
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(Value::Object(item)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let kind = get_str(&item, "kind", "");
        let ts = parse_time(item.get("ts")).unwrap_or(at);
        if kind == "closure" {
            let segment_id = get_str(&item, "segment_id", "").trim().to_string();
            if segment_id.is_empty() {
                continue;
            }
            closures.insert(
                segment_id.clone(),
                Closure {
                    segment_id,
                    reason: get_str(&item, "reason", "").trim().to_string(),
                    user: get_str(&item, "user", "unknown"),
                    ts,
                    until: parse_time(item.get("until")),
                },
            );
        } else if kind == "reopen" {
            let segment_id = get_str(&item, "segment_id", "").trim().to_string();
            closures.remove(&segment_id);
        }
    }
    closures.retain(|_, c| c.until.is_none_or(|u| u > at));
    OpsOverlay { closures }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pytime::Parsed;

    fn t(text: &str) -> DateTime {
        match fromisoformat(text) {
            Some(Parsed::Aware(dt)) => dt,
            _ => panic!("{text}"),
        }
    }

    #[test]
    fn folds_closures_reopens_and_expiries() {
        let dir = std::env::temp_dir().join(format!("varuna-route-ops-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("mumbai.jsonl");
        std::fs::write(
            &path,
            concat!(
                "{\"kind\":\"closure\",\"segment_id\":\"A\",\"reason\":\" Manhole open \",\"user\":\"ward\",\"ts\":\"2019-07-02T08:12:00+05:30\"}\n",
                "not json\n",
                "{\"kind\":\"closure\",\"segment_id\":\"B\",\"reason\":\"x\",\"until\":\"2019-07-02T08:30:00\"}\n",
                "{\"kind\":\"closure\",\"segment_id\":\"C\",\"reason\":\"y\"}\n",
                "{\"kind\":\"reopen\",\"segment_id\":\"C\"}\n",
                "{\"kind\":\"pump_status\",\"pump_id\":\"P-1\",\"status\":\"available\"}\n",
            ),
        )
        .unwrap();
        let overlay = active(&path, t("2019-07-02T08:40:00+05:30"));
        assert_eq!(overlay.reason_for("A"), Some("Manhole open"));
        assert_eq!(overlay.closures["A"].user, "ward");
        assert_eq!(
            overlay.closures["A"].ts.isoformat(),
            "2019-07-02T08:12:00+05:30"
        );
        assert!(
            overlay.reason_for("B").is_none(),
            "expired at 08:30 IST (naive is IST)"
        );
        assert!(overlay.reason_for("C").is_none(), "reopened");
        let early = active(&path, t("2019-07-02T08:20:00+05:30"));
        assert_eq!(early.reason_for("B"), Some("x"));
        assert_eq!(early.closures["B"].user, "unknown");
        std::fs::remove_dir_all(&dir).ok();
    }
}
