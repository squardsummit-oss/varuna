//! The road graph, read from the Python router's own export (task P8.12).
//!
//! `tools/export_graph.py` writes `varuna_route.graph.load_graph(city)` out verbatim: the same
//! CSR arrays, the same node order, the same free-flow times. Nothing here derives a speed, a
//! lane count or a coordinate, so the two routers search the same network by construction.

use std::collections::HashMap;
use std::path::Path;

use serde::Deserialize;

use crate::profiles::Profile;

/// The layout version this binary reads. The export writes the same constant.
pub const FORMAT_VERSION: u32 = 1;

#[derive(Deserialize)]
struct ExportNodes {
    lon: Vec<f64>,
    lat: Vec<f64>,
}

#[derive(Deserialize)]
struct ExportEdges {
    indptr: Vec<u32>,
    head: Vec<u32>,
    tail: Vec<u32>,
    segment: Vec<u32>,
    length_m: Vec<f64>,
    time_s: Vec<f64>,
    name: Vec<String>,
    lanes: Vec<f64>,
}

#[derive(Deserialize)]
struct ExportSegments {
    id: Vec<String>,
    design_intensity_mm_h: Vec<Option<f64>>,
}

/// The router's constants as the Python modules define them; checked against this binary's.
#[derive(Deserialize, Debug, Clone)]
pub struct Constants {
    pub dry_cm: f64,
    pub step_min: f64,
    pub max_slowdown: f64,
    pub alternate_penalty: f64,
    pub max_alternates: usize,
    pub max_avoided_reasons: usize,
    pub labels: Vec<String>,
}

#[derive(Deserialize)]
struct Export {
    format_version: u32,
    city: String,
    city_code: String,
    #[serde(default)]
    exported_at: String,
    #[serde(default)]
    sources: serde_json::Value,
    constants: Constants,
    profiles: Vec<Profile>,
    nodes: ExportNodes,
    edges: ExportEdges,
    segments: ExportSegments,
}

/// A directed road network in flat arrays: `varuna_route.graph.RoadGraph`, in Rust.
pub struct RoadGraph {
    pub city: String,
    pub city_code: String,
    pub exported_at: String,
    pub sources: serde_json::Value,
    pub constants: Constants,
    pub profiles: Vec<Profile>,

    pub lon: Vec<f64>,
    pub lat: Vec<f64>,
    pub indptr: Vec<u32>,
    pub head: Vec<u32>,
    pub tail: Vec<u32>,
    /// Index into [`RoadGraph::segment_ids`] for each out-edge.
    pub edge_segment: Vec<u32>,
    pub edge_length_m: Vec<f64>,
    pub edge_time_s: Vec<f64>,
    pub edge_name: Vec<String>,
    pub edge_lanes: Vec<f64>,

    pub segment_ids: Vec<String>,
    pub segment_index: HashMap<String, u32>,
    /// `design_intensity_by_segment(city)` from the Python reasons module, per segment.
    pub design_intensity: Vec<Option<f64>>,
    /// The first out-edge in CSR order carrying each segment (`router._segment_path`).
    pub first_edge: Vec<u32>,
}

impl RoadGraph {
    pub fn n_nodes(&self) -> usize {
        self.lon.len()
    }

    pub fn n_edges(&self) -> usize {
        self.head.len()
    }

    /// Load an export, refusing anything whose shape or constants do not match this binary.
    pub fn load(path: &Path) -> Result<RoadGraph, String> {
        let text = std::fs::read(path).map_err(|e| {
            format!(
                "No route graph at {}: {e}. Run `uv run python services/route-rs/tools/export_graph.py` \
                 (it needs `make city CITY=mumbai` first).",
                path.display()
            )
        })?;
        let doc: Export = serde_json::from_slice(&text)
            .map_err(|e| format!("{} is not a route graph export: {e}", path.display()))?;
        Self::from_export(doc)
    }

    fn from_export(doc: Export) -> Result<RoadGraph, String> {
        if doc.format_version != FORMAT_VERSION {
            return Err(format!(
                "Route graph export is format {} and this binary reads format {FORMAT_VERSION}. \
                 Re-export with tools/export_graph.py.",
                doc.format_version
            ));
        }
        crate::router::check_constants(&doc.constants)?;
        let n = doc.nodes.lon.len();
        let m = doc.edges.head.len();
        let s = doc.segments.id.len();
        let e = &doc.edges;
        let ok = doc.nodes.lat.len() == n
            && e.indptr.len() == n + 1
            && e.tail.len() == m
            && e.segment.len() == m
            && e.length_m.len() == m
            && e.time_s.len() == m
            && e.name.len() == m
            && e.lanes.len() == m
            && doc.segments.design_intensity_mm_h.len() == s
            && e.indptr.last().copied() == Some(m as u32)
            && e.head
                .iter()
                .chain(e.tail.iter())
                .all(|&v| (v as usize) < n)
            && e.segment.iter().all(|&v| (v as usize) < s);
        if !ok {
            return Err("Route graph export is internally inconsistent; re-export it.".into());
        }
        let segment_index: HashMap<String, u32> = doc
            .segments
            .id
            .iter()
            .enumerate()
            .map(|(i, sid)| (sid.clone(), i as u32))
            .collect();
        let mut first_edge = vec![u32::MAX; s];
        for (edge, &seg) in e.segment.iter().enumerate() {
            let slot = &mut first_edge[seg as usize];
            if *slot == u32::MAX {
                *slot = edge as u32;
            }
        }
        Ok(RoadGraph {
            city: doc.city,
            city_code: doc.city_code,
            exported_at: doc.exported_at,
            sources: doc.sources,
            constants: doc.constants,
            profiles: doc.profiles,
            lon: doc.nodes.lon,
            lat: doc.nodes.lat,
            indptr: doc.edges.indptr,
            head: doc.edges.head,
            tail: doc.edges.tail,
            edge_segment: doc.edges.segment,
            edge_length_m: doc.edges.length_m,
            edge_time_s: doc.edges.time_s,
            edge_name: doc.edges.name,
            edge_lanes: doc.edges.lanes,
            segment_ids: doc.segments.id,
            segment_index,
            design_intensity: doc.segments.design_intensity_mm_h,
            first_edge,
        })
    }

    /// `RoadGraph.nearest_node`: equirectangular distance, first minimum wins (`np.argmin`).
    pub fn nearest_node(&self, lon: f64, lat: f64) -> u32 {
        let k = lat.to_radians().cos();
        let mut best = f64::INFINITY;
        let mut arg = 0usize;
        let mut seen_nan = false;
        for i in 0..self.lon.len() {
            let dx = (self.lon[i] - lon) * k;
            let dy = self.lat[i] - lat;
            let d = dx * dx + dy * dy;
            // np.argmin returns the first NaN if there is one; otherwise the first minimum.
            if d.is_nan() {
                if !seen_nan {
                    seen_nan = true;
                    arg = i;
                }
                continue;
            }
            if !seen_nan && d < best {
                best = d;
                arg = i;
            }
        }
        arg as u32
    }

    /// The segment's endpoints from its first out-edge (`router._segment_path`).
    pub fn segment_path(&self, segment: u32) -> Vec<(f64, f64)> {
        match self.first_edge.get(segment as usize) {
            Some(&e) if e != u32::MAX => {
                let t = self.tail[e as usize] as usize;
                let h = self.head[e as usize] as usize;
                vec![(self.lon[t], self.lat[t]), (self.lon[h], self.lat[h])]
            }
            _ => Vec::new(),
        }
    }

    /// A small hand-built graph for unit tests: `edges` are `(tail, head, seconds, segment)`.
    #[cfg(test)]
    pub fn for_test(coords: &[(f64, f64)], edges: &[(u32, u32, f64, &str)]) -> RoadGraph {
        let mut sorted: Vec<(u32, u32, f64, &str)> = edges.to_vec();
        sorted.sort_by_key(|e| e.0);
        let n = coords.len();
        let mut indptr = vec![0u32; n + 1];
        for e in &sorted {
            indptr[e.0 as usize + 1] += 1;
        }
        for i in 0..n {
            indptr[i + 1] += indptr[i];
        }
        let mut ids: Vec<String> = Vec::new();
        let mut seg = Vec::new();
        for e in &sorted {
            let j = match ids.iter().position(|s| s == e.3) {
                Some(j) => j,
                None => {
                    ids.push(e.3.to_string());
                    ids.len() - 1
                }
            };
            seg.push(j as u32);
        }
        let s = ids.len();
        let doc = Export {
            format_version: FORMAT_VERSION,
            city: "test".into(),
            city_code: "TST".into(),
            exported_at: String::new(),
            sources: serde_json::Value::Null,
            constants: crate::router::expected_constants(),
            profiles: crate::profiles::defaults(),
            nodes: ExportNodes {
                lon: coords.iter().map(|c| c.0).collect(),
                lat: coords.iter().map(|c| c.1).collect(),
            },
            edges: ExportEdges {
                indptr,
                head: sorted.iter().map(|e| e.1).collect(),
                tail: sorted.iter().map(|e| e.0).collect(),
                segment: seg,
                length_m: sorted.iter().map(|e| e.2 * 10.0).collect(),
                time_s: sorted.iter().map(|e| e.2).collect(),
                name: sorted.iter().map(|e| format!("{} road", e.3)).collect(),
                lanes: vec![1.0; sorted.len()],
            },
            segments: ExportSegments {
                id: ids,
                design_intensity_mm_h: vec![Some(25.0); s],
            },
        };
        RoadGraph::from_export(doc).expect("test graph is consistent")
    }
}
