//! `varuna-route`: the Rust routing service (task P8.12).
//!
//! ```text
//! varuna-route serve [--port 8161] [--host 127.0.0.1] [--city mumbai] [--graph PATH]
//!                    [--data-dir PATH] [--naive dijkstra|ch]
//! varuna-route check-ch [--pairs 2000] [--city mumbai] [--graph PATH]
//! ```
//!
//! Paths default the way the Python services default them: `VARUNA_DATA_DIR`, else
//! `<repo>/data`, where the repository root is the nearest ancestor of the working directory
//! whose `pyproject.toml` declares the uv workspace. The graph defaults to
//! `<repo>/services/route-rs/cache/<city>/route_graph.json`, written by `tools/export_graph.py`.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use varuna_route::ch::{agreement, ChNaive};
use varuna_route::forecast::RunStore;
use varuna_route::graph::RoadGraph;
use varuna_route::router::{DijkstraNaive, NaiveEngine};
use varuna_route::server::{app, AppState};

struct Args {
    command: String,
    port: u16,
    host: String,
    city: String,
    graph: Option<PathBuf>,
    data_dir: Option<PathBuf>,
    naive: String,
    pairs: usize,
}

fn usage() -> ! {
    eprintln!(
        "usage: varuna-route serve [--port 8161] [--host 127.0.0.1] [--city mumbai] [--graph PATH] \
         [--data-dir PATH] [--naive dijkstra|ch]\n       varuna-route check-ch [--pairs 2000] \
         [--city mumbai] [--graph PATH]"
    );
    std::process::exit(2)
}

fn parse_args() -> Args {
    let mut args = Args {
        command: "serve".into(),
        port: 8161,
        host: "127.0.0.1".into(),
        city: "mumbai".into(),
        graph: None,
        data_dir: None,
        naive: "dijkstra".into(),
        pairs: 2000,
    };
    let mut it = std::env::args().skip(1).peekable();
    if let Some(first) = it.peek() {
        if !first.starts_with("--") {
            args.command = it.next().unwrap_or_default();
        }
    }
    while let Some(flag) = it.next() {
        let mut value = || it.next().unwrap_or_else(|| usage());
        match flag.as_str() {
            "--port" => args.port = value().parse().unwrap_or_else(|_| usage()),
            "--host" => args.host = value(),
            "--city" => args.city = value(),
            "--graph" => args.graph = Some(PathBuf::from(value())),
            "--data-dir" => args.data_dir = Some(PathBuf::from(value())),
            "--naive" => args.naive = value(),
            "--pairs" => args.pairs = value().parse().unwrap_or_else(|_| usage()),
            _ => usage(),
        }
    }
    args
}

/// The nearest ancestor whose `pyproject.toml` declares the uv workspace, as
/// `varuna_schemas.paths.repo_root` finds it.
fn repo_root() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("VARUNA_REPO_ROOT") {
        if !root.trim().is_empty() {
            return Some(PathBuf::from(root.trim()));
        }
    }
    let start = std::env::current_dir().ok()?;
    start.ancestors().find_map(|dir| {
        let text = std::fs::read_to_string(dir.join("pyproject.toml")).ok()?;
        text.contains("[tool.uv.workspace]")
            .then(|| dir.to_path_buf())
    })
}

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var(name)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

fn resolve(args: &Args) -> (PathBuf, PathBuf) {
    let root = repo_root().unwrap_or_else(|| PathBuf::from("."));
    let data_dir = args
        .data_dir
        .clone()
        .or_else(|| env_path("VARUNA_DATA_DIR"))
        .unwrap_or_else(|| root.join("data"));
    let graph = args
        .graph
        .clone()
        .or_else(|| env_path("VARUNA_ROUTE_GRAPH"))
        .unwrap_or_else(|| {
            root.join("services")
                .join("route-rs")
                .join("cache")
                .join(&args.city)
                .join("route_graph.json")
        });
    (data_dir, graph)
}

fn load_graph(path: &Path) -> (Result<Arc<RoadGraph>, String>, f64) {
    let started = Instant::now();
    let graph = RoadGraph::load(path).map(Arc::new);
    (graph, started.elapsed().as_secs_f64() * 1000.0)
}

fn main() {
    let args = parse_args();
    let (data_dir, graph_path) = resolve(&args);
    match args.command.as_str() {
        "serve" => serve(&args, data_dir, graph_path),
        "check-ch" => check_ch(&args, &graph_path),
        _ => usage(),
    }
}

fn check_ch(args: &Args, graph_path: &Path) {
    let (graph, load_ms) = load_graph(graph_path);
    let graph = graph.unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1)
    });
    println!(
        "graph loaded in {load_ms:.1} ms: {} nodes, {} edges",
        graph.n_nodes(),
        graph.n_edges()
    );
    let started = Instant::now();
    let ch = ChNaive::build(&graph, &graph.profiles);
    println!(
        "hierarchies built in {:.1} ms (wall, parallel)",
        started.elapsed().as_secs_f64() * 1000.0
    );
    for h in &ch.hierarchies {
        println!(
            "  speed {:>5}: {} shortcuts, {:.1} ms",
            h.speed, h.shortcuts, h.build_ms
        );
    }
    for profile in &graph.profiles {
        let t = Instant::now();
        let (same, tie, differ, none) = agreement(&graph, &ch, profile, args.pairs, 2019);
        println!(
            "{:<12} {} pairs: {same} identical paths, {tie} equal-cost different paths, {differ} different cost, {none} unreachable ({:.0} ms)",
            profile.key,
            args.pairs,
            t.elapsed().as_secs_f64() * 1000.0
        );
    }
    // Query cost, both engines, same pairs.
    let dij = DijkstraNaive;
    let car = graph
        .profiles
        .iter()
        .find(|p| p.key == "car")
        .cloned()
        .unwrap_or_else(|| graph.profiles[0].clone());
    let n = graph.n_nodes() as u64;
    let mut state = 12_345u64;
    let pairs: Vec<(u32, u32)> = (0..args.pairs)
        .map(|_| {
            let mut next = || {
                state ^= state >> 12;
                state ^= state << 25;
                state ^= state >> 27;
                (state.wrapping_mul(0x2545_F491_4F6C_DD1D) % n) as u32
            };
            (next(), next())
        })
        .collect();
    for (name, engine) in [
        ("dijkstra", &dij as &dyn NaiveEngine),
        ("ch", &ch as &dyn NaiveEngine),
    ] {
        let t = Instant::now();
        for &(s, d) in &pairs {
            std::hint::black_box(engine.naive_path(&graph, s, d, &car));
        }
        println!(
            "naive query, {name}: {:.3} ms mean over {} pairs",
            t.elapsed().as_secs_f64() * 1000.0 / pairs.len() as f64,
            pairs.len()
        );
    }
}

fn serve(args: &Args, data_dir: PathBuf, graph_path: PathBuf) {
    let (graph, graph_load_ms) = load_graph(&graph_path);
    if let Err(e) = &graph {
        eprintln!("{e}");
    }
    let started = Instant::now();
    let naive: Box<dyn NaiveEngine + Send + Sync> = match (args.naive.as_str(), &graph) {
        ("ch", Ok(g)) => Box::new(ChNaive::build(g, &g.profiles)),
        ("dijkstra", _) | ("ch", Err(_)) => Box::new(DijkstraNaive),
        _ => usage(),
    };
    let naive_build_ms = started.elapsed().as_secs_f64() * 1000.0;
    let state = Arc::new(AppState {
        city: args.city.clone(),
        graph,
        graph_path,
        graph_load_ms,
        runs: RunStore::new(data_dir),
        naive,
        naive_build_ms,
    });
    eprintln!(
        "{}",
        serde_json::json!({
            "event": "route.service_started",
            "host": args.host,
            "port": args.port,
            "graph_load_ms": varuna_route::pynum::round_n(graph_load_ms, 1),
            "naive_engine": state.naive.name(),
            "naive_build_ms": varuna_route::pynum::round_n(naive_build_ms, 1),
            "data_dir": state.runs.data_dir.display().to_string(),
        })
    );
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    runtime.block_on(async move {
        let addr = format!("{}:{}", args.host, args.port);
        let listener = tokio::net::TcpListener::bind(&addr)
            .await
            .unwrap_or_else(|e| {
                eprintln!("cannot listen on {addr}: {e}");
                std::process::exit(1)
            });
        axum::serve(listener, app(state))
            .with_graceful_shutdown(async {
                let _ = tokio::signal::ctrl_c().await;
            })
            .await
            .expect("server");
    });
}
