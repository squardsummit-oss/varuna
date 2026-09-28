//! The Axum service: `POST /v1/route` and `GET /healthz`, with section 12's error envelope.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use axum::body::Bytes;
use axum::extract::State;
use axum::http::{StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};

use crate::api::{as_dict, parse_request, ApiError};
use crate::forecast::{RunError, RunStore};
use crate::graph::RoadGraph;
use crate::ops;
use crate::router::{plan, NaiveEngine};

pub struct AppState {
    pub city: String,
    pub graph: Result<Arc<RoadGraph>, String>,
    pub graph_path: PathBuf,
    pub graph_load_ms: f64,
    pub runs: RunStore,
    pub naive: Box<dyn NaiveEngine + Send + Sync>,
    pub naive_build_ms: f64,
}

impl AppState {
    fn ops_path(&self) -> PathBuf {
        self.runs
            .data_dir
            .join("ops")
            .join(format!("{}.jsonl", self.city))
    }
}

fn envelope(err: &ApiError) -> Response {
    let status = StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(err.body())).into_response()
}

/// One route request, off the async runtime: the search is CPU work.
pub fn handle_route(state: &AppState, bytes: &[u8]) -> Result<Value, ApiError> {
    let profiles = match &state.graph {
        Ok(g) => g.profiles.clone(),
        Err(_) => crate::profiles::defaults(),
    };
    let request = parse_request(bytes, &profiles)?;
    let started = Instant::now();
    let graph = state
        .graph
        .as_ref()
        .map_err(|message| ApiError::new(404, "no_run", message.clone()))?;
    let depths = state
        .runs
        .load(request.run_id.as_deref(), &graph.city_code, graph)
        .map_err(|e| match e {
            RunError::NotFound(m) => ApiError::new(404, "no_run", m),
            RunError::BadRunId(m) => ApiError::new(422, "bad_run_id", m),
            RunError::Invalid(m) => ApiError::new(500, "internal_error", m),
        })?;
    let ops_path = state.ops_path();
    let overlay_for = |at| ops::active(&ops_path, at);
    let result = plan(
        graph,
        &depths,
        &overlay_for,
        state.naive.as_ref(),
        request.plan,
    );
    let ms = started.elapsed().as_secs_f64() * 1000.0;
    eprintln!(
        "{}",
        json!({
            "event": "route.planned",
            "run_id": result.run_id,
            "profile": result.profile,
            "ms": crate::pynum::round_n(ms, 2),
            "naive_engine": state.naive.name(),
            "naive_min": result.naive.as_ref().map(|r| crate::pynum::round_n(r.minutes(), 1)),
            "varuna_min": result.varuna.as_ref().map(|r| crate::pynum::round_n(r.minutes(), 1)),
            "avoided": result.avoided.len(),
            "corridors": result.corridors.len(),
        })
    );
    Ok(as_dict(&result, graph, ms))
}

async fn route(State(state): State<Arc<AppState>>, body: Bytes) -> Response {
    let outcome = tokio::task::spawn_blocking(move || handle_route(&state, &body)).await;
    match outcome {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(err)) => envelope(&err),
        Err(join) => envelope(&ApiError::new(
            500,
            "internal_error",
            format!(
                "{join} while handling /v1/route. Check the service log and retry; if it persists, \
                 restart the service."
            ),
        )),
    }
}

async fn healthz(State(state): State<Arc<AppState>>) -> Json<Value> {
    let graph = match &state.graph {
        Ok(g) => json!({
            "path": state.graph_path.display().to_string(),
            "nodes": g.n_nodes(),
            "edges": g.n_edges(),
            "segments": g.segment_ids.len(),
            "exported_at": g.exported_at,
            "sources": g.sources,
            "load_ms": crate::pynum::round_n(state.graph_load_ms, 1),
        }),
        Err(message) => json!({"path": state.graph_path.display().to_string(), "error": message}),
    };
    let latest = state
        .graph
        .as_ref()
        .ok()
        .and_then(|g| state.runs.latest_run_dir(&g.city_code).ok())
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()));
    Json(json!({
        "status": if state.graph.is_ok() { "ok" } else { "degraded" },
        "service": "varuna-route-rs",
        "version": env!("CARGO_PKG_VERSION"),
        "city": state.city,
        "data_dir": state.runs.data_dir.display().to_string(),
        "last_run_id": latest,
        "graph": graph,
        "engines": {
            "naive": state.naive.name(),
            "naive_build_ms": crate::pynum::round_n(state.naive_build_ms, 1),
            "varuna": "time-dependent-dijkstra",
            "alternates": "time-dependent-dijkstra",
        },
    }))
}

async fn not_found(uri: Uri) -> Response {
    envelope(&ApiError::new(
        404,
        "not_found",
        format!(
            "No endpoint at {}. This service answers POST /v1/route and GET /healthz.",
            uri.path()
        ),
    ))
}

async fn method_not_allowed(uri: Uri) -> Response {
    envelope(&ApiError::new(
        405,
        "method_not_allowed",
        format!("Method Not Allowed at {}.", uri.path()),
    ))
}

pub fn app(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/v1/route", post(route))
        .route("/healthz", get(healthz))
        .fallback(not_found)
        .method_not_allowed_fallback(method_not_allowed)
        .with_state(state)
}
