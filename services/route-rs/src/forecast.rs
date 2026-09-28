//! A run's depth per segment and step (`varuna_route.forecast`), read from the run's own files.
//!
//! Like the Python router this reads `segments_wet.json` (the wet segments' depth series and the
//! 20-member `p_gt` exceedance series) and `run.json` (`ensemble_n`, the AOI hyetograph), never
//! the 19 MB parquet. The series are re-keyed from segment id to the graph's segment index once,
//! at load, so the search indexes a vector where Python looks up a dict.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::SystemTime;

use serde::Deserialize;
use serde_json::Value;

use crate::graph::RoadGraph;
use crate::pynum::{floordiv, str_value, trunc_i64};
use crate::pytime::{fromisoformat, DateTime, Parsed};

/// Forecast step, minutes (`forecast.STEP_MIN`).
pub const STEP_MIN: f64 = 5.0;
/// The same step in seconds (`forecast._STEP_S`).
pub const STEP_S: f64 = STEP_MIN * 60.0;
/// Below this a segment is dry (`forecast.DRY_CM`).
pub const DRY_CM: f64 = 5.0;

/// Per-segment series, indexed by the graph's segment index. `None` is "absent from the file",
/// which the Python lookups distinguish from an empty list.
pub type Series = Vec<Option<Vec<f64>>>;

pub struct SegmentDepths {
    pub run_id: String,
    pub times: Vec<DateTime>,
    pub depth: Series,
    pub n_steps: usize,
    pub ensemble_n: i64,
    pub rain_aoi_mm_h: Vec<f64>,
    /// `(threshold_cm, series)` for each threshold the run carries a `p_gt` block for.
    pub p_gt: Vec<(f64, Series)>,
    /// Whether the file had a `p_gt` block with at least one threshold (`has_exceedance`).
    pub has_exceedance: bool,
    /// `velocity_ms` per segment, the pedestrian hazard rule's other half. Empty on every run
    /// VARUNA bakes today: the Twin holds face fluxes while it runs and no product keeps them.
    pub velocity: Series,
    /// Whether the file carried a non-empty `velocity_ms` block (`has_velocity`).
    pub has_velocity: bool,
}

#[derive(Deserialize)]
struct WetFile {
    #[serde(default)]
    run_id: Option<Value>,
    #[serde(default)]
    valid_ts: Option<Vec<Value>>,
    #[serde(default)]
    depth_cm: Option<HashMap<String, Vec<f64>>>,
    #[serde(default)]
    p_gt: Option<HashMap<String, HashMap<String, Vec<f64>>>>,
    #[serde(default)]
    velocity_ms: Option<HashMap<String, Vec<f64>>>,
}

#[derive(Deserialize)]
struct RunFile {
    #[serde(default)]
    ensemble_n: Option<Value>,
    #[serde(default)]
    rain_aoi_mm_h: Option<Vec<f64>>,
}

impl SegmentDepths {
    pub fn valid_ts(&self) -> DateTime {
        self.times[0]
    }

    /// `step_at(when)`: the step covering an instant, clamped to the window.
    pub fn step_at(&self, when: DateTime) -> usize {
        let delta = when.seconds_since(self.times[0]) / 60.0;
        let step = trunc_i64(floordiv(delta, STEP_MIN));
        let last = self.n_steps as i64 - 1;
        step.min(last).max(0) as usize
    }

    /// `depart_offset_s(when)`.
    pub fn depart_offset_s(&self, when: DateTime) -> f64 {
        when.seconds_since(self.times[0])
    }

    /// `step_after(depart_offset_s, seconds)`: the search's integer-division form of `step_at`.
    #[inline]
    pub fn step_after(&self, depart_offset_s: f64, seconds: f64) -> usize {
        let step = trunc_i64(floordiv(depart_offset_s + seconds, STEP_S));
        if step < 0 {
            return 0;
        }
        let last = self.n_steps as i64 - 1;
        (if step < last { step } else { last }) as usize
    }

    /// `depth_at(segment_id, step)`.
    #[inline]
    pub fn depth_at(&self, segment: u32, step: usize) -> f64 {
        match &self.depth[segment as usize] {
            Some(series) if !series.is_empty() => at(series, step),
            _ => 0.0,
        }
    }

    /// `velocity_at(segment_id, step)`: flow speed in m/s, or None where the run does not say.
    #[inline]
    pub fn velocity_at(&self, segment: u32, step: usize) -> Option<f64> {
        match &self.velocity[segment as usize] {
            Some(series) if !series.is_empty() => Some(at(series, step)),
            _ => None,
        }
    }

    /// The `p_gt` series for a threshold, as `self.p_gt.get(float(threshold_cm))`.
    #[inline]
    pub fn p_for(&self, threshold_cm: f64) -> Option<&Series> {
        self.p_gt
            .iter()
            .find(|(t, _)| *t == threshold_cm)
            .map(|(_, s)| s)
    }

    /// `exceedance(segment_id, threshold_cm, step)`.
    pub fn exceedance(&self, segment: u32, threshold_cm: f64, step: usize) -> f64 {
        if let Some(by_segment) = self.p_for(threshold_cm) {
            if let Some(series) = &by_segment[segment as usize] {
                return at(series, step);
            }
            if self.depth[segment as usize].is_none() {
                return 0.0;
            }
        }
        if self.depth_at(segment, step) > threshold_cm {
            1.0
        } else {
            0.0
        }
    }

    /// `peak(segment_id)`.
    pub fn peak(&self, segment: u32) -> f64 {
        match &self.depth[segment as usize] {
            Some(series) if !series.is_empty() => {
                // Python's max(): the first of equal maxima, and NaN never wins a `>`.
                let mut best = series[0];
                for &v in &series[1..] {
                    if v > best {
                        best = v;
                    }
                }
                best
            }
            _ => 0.0,
        }
    }

    /// `time_of(step)`.
    pub fn time_of(&self, step: usize) -> DateTime {
        if step < self.times.len() {
            return self.times[step];
        }
        let last = *self.times.last().expect("a run has at least one step time");
        let minutes = STEP_MIN as i64 * (step as i64 - self.times.len() as i64 + 1);
        DateTime {
            utc_us: last.utc_us + minutes * 60 * crate::pytime::US_PER_S,
            offset_us: last.offset_us,
        }
    }
}

/// `series[step] if step < len(series) else series[-1]`.
///
/// An empty `p_gt` series makes Python raise `IndexError` (a 500); no run has one, and this
/// answers 0.0 rather than panicking a worker thread.
#[inline]
pub fn at(series: &[f64], step: usize) -> f64 {
    if step < series.len() {
        series[step]
    } else if let Some(&last) = series.last() {
        last
    } else {
        0.0
    }
}

/// Why a run could not be loaded, worded as the Python router words it.
#[derive(Debug)]
pub enum RunError {
    /// `FileNotFoundError`: the API answers 404 `no_run`.
    NotFound(String),
    /// A `run_id` that would escape `data/runs` (Python raises `ValueError`).
    BadRunId(String),
    /// A file that exists and cannot be read as a run.
    Invalid(String),
}

impl std::fmt::Display for RunError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunError::NotFound(m) | RunError::BadRunId(m) | RunError::Invalid(m) => f.write_str(m),
        }
    }
}

fn load(path: &Path, graph: &RoadGraph) -> Result<SegmentDepths, RunError> {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let raw = std::fs::read(path.join("segments_wet.json"))
        .map_err(|e| RunError::Invalid(format!("{name}/segments_wet.json: {e}")))?;
    let wet: WetFile = serde_json::from_slice(&raw)
        .map_err(|e| RunError::Invalid(format!("{name}/segments_wet.json: {e}")))?;

    let depth_map = wet.depth_cm.unwrap_or_default();
    let n_steps = depth_map.values().map(Vec::len).max().unwrap_or(1);
    let mut times = Vec::new();
    for stamp in wet.valid_ts.unwrap_or_default() {
        match fromisoformat(&str_value(&stamp)) {
            Some(Parsed::Aware(dt)) => times.push(dt),
            _ => {
                return Err(RunError::Invalid(format!(
                "{name}/segments_wet.json has a step time that is not ISO 8601 with an offset: {}",
                str_value(&stamp)
            )))
            }
        }
    }
    if times.is_empty() {
        return Err(RunError::Invalid(format!(
            "{name}/segments_wet.json carries no step times; it cannot be routed against."
        )));
    }

    let n_seg = graph.segment_ids.len();
    let mut depth: Series = vec![None; n_seg];
    for (sid, series) in depth_map {
        if let Some(&j) = graph.segment_index.get(&sid) {
            depth[j as usize] = Some(series);
        }
    }

    let raw_p = wet.p_gt.unwrap_or_default();
    let has_exceedance = !raw_p.is_empty();
    let mut p_gt: Vec<(f64, Series)> = Vec::new();
    for (threshold, by_segment) in raw_p {
        let Some(t) = crate::pynum::parse_py_float(&threshold) else {
            return Err(RunError::Invalid(format!(
                "{name}/segments_wet.json has a p_gt threshold that is not a number: {threshold}"
            )));
        };
        let mut series: Series = vec![None; n_seg];
        for (sid, values) in by_segment {
            if let Some(&j) = graph.segment_index.get(&sid) {
                series[j as usize] = Some(values);
            }
        }
        p_gt.push((t, series));
    }

    let raw_v = wet.velocity_ms.unwrap_or_default();
    let has_velocity = !raw_v.is_empty();
    let mut velocity: Series = vec![None; n_seg];
    for (sid, series) in raw_v {
        if let Some(&j) = graph.segment_index.get(&sid) {
            velocity[j as usize] = Some(series);
        }
    }

    let mut ensemble_n = 1;
    let mut rain = Vec::new();
    let meta_path = path.join("run.json");
    if meta_path.is_file() {
        let raw = std::fs::read(&meta_path)
            .map_err(|e| RunError::Invalid(format!("{name}/run.json: {e}")))?;
        let meta: RunFile = serde_json::from_slice(&raw)
            .map_err(|e| RunError::Invalid(format!("{name}/run.json: {e}")))?;
        // int(meta.get("ensemble_n", 1) or 1)
        ensemble_n = match meta.ensemble_n {
            Some(v) if crate::pynum::truthy(&v) => v
                .as_f64()
                .map(|f| f.trunc() as i64)
                .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
                .unwrap_or(1),
            _ => 1,
        };
        rain = meta.rain_aoi_mm_h.unwrap_or_default();
    }

    let run_id = match wet.run_id {
        Some(v) => str_value(&v),
        None => name,
    };
    Ok(SegmentDepths {
        run_id,
        times,
        depth,
        n_steps,
        ensemble_n,
        rain_aoi_mm_h: rain,
        p_gt,
        has_exceedance,
        velocity,
        has_velocity,
    })
}

/// Loads runs on demand and keeps the last few, invalidated by the file's mtime
/// (`forecast._load`'s `lru_cache(maxsize=4)` keyed on path and `st_mtime_ns`).
pub struct RunStore {
    pub data_dir: PathBuf,
    cache: Mutex<Vec<(PathBuf, SystemTime, Arc<SegmentDepths>)>>,
}

const CACHE_SIZE: usize = 8;

impl RunStore {
    pub fn new(data_dir: PathBuf) -> RunStore {
        RunStore {
            data_dir,
            cache: Mutex::new(Vec::new()),
        }
    }

    pub fn runs_dir(&self) -> PathBuf {
        self.data_dir.join("runs")
    }

    /// `latest_run_dir(city)`: the newest run for this city that carries `segments_wet.json`.
    pub fn latest_run_dir(&self, city_code: &str) -> Result<PathBuf, RunError> {
        let root = self.runs_dir();
        let prefix = if city_code.is_empty() {
            String::new()
        } else {
            format!("{city_code}-")
        };
        let mut names: Vec<(String, PathBuf)> = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&root) {
            for entry in entries.flatten() {
                let p = entry.path();
                let name = entry.file_name().to_string_lossy().into_owned();
                if p.is_dir()
                    && (prefix.is_empty() || name.starts_with(&prefix))
                    && p.join("segments_wet.json").is_file()
                {
                    names.push((name, p));
                }
            }
        }
        // Python sorts Path objects, which on Windows compare case-insensitively.
        names.sort_by(|a, b| {
            let key = |s: &str| {
                if cfg!(windows) {
                    s.to_lowercase()
                } else {
                    s.to_string()
                }
            };
            key(&b.0).cmp(&key(&a.0))
        });
        names.into_iter().next().map(|(_, p)| p).ok_or_else(|| {
            RunError::NotFound(
                "No baked run to route against. Press Play on the replay, or run \
                 `make bake BUNDLE=MUM-2019-07-02`."
                    .into(),
            )
        })
    }

    /// `load_depths(run_id, city)`.
    pub fn load(
        &self,
        run_id: Option<&str>,
        city_code: &str,
        graph: &RoadGraph,
    ) -> Result<Arc<SegmentDepths>, RunError> {
        let path = match run_id {
            Some(id) => {
                if id.is_empty()
                    || id == "."
                    || id == ".."
                    || id.contains('/')
                    || id.contains('\\')
                    || id.contains('\0')
                {
                    return Err(RunError::BadRunId(format!(
                        "run_id must be a single path segment, got {}",
                        crate::pynum::repr_str(id)
                    )));
                }
                self.runs_dir().join(id)
            }
            None => self.latest_run_dir(city_code)?,
        };
        let wet = path.join("segments_wet.json");
        let meta = std::fs::metadata(&wet).ok().filter(|m| m.is_file());
        let Some(meta) = meta else {
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            return Err(RunError::NotFound(format!(
                "Run {name} has no segment forecast; it cannot be routed against."
            )));
        };
        let mtime = meta.modified().unwrap_or(SystemTime::UNIX_EPOCH);
        {
            let cache = self.cache.lock().expect("run cache lock");
            if let Some((_, _, hit)) = cache.iter().find(|(p, m, _)| *p == path && *m == mtime) {
                return Ok(Arc::clone(hit));
            }
        }
        let loaded = Arc::new(load(&path, graph)?);
        let mut cache = self.cache.lock().expect("run cache lock");
        cache.retain(|(p, _, _)| *p != path);
        cache.insert(0, (path, mtime, Arc::clone(&loaded)));
        cache.truncate(CACHE_SIZE);
        Ok(loaded)
    }
}
