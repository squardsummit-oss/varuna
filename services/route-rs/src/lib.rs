//! VARUNA-Route in Rust (SPEC.md 11.9, task P8.12).
//!
//! `POST /v1/route` with the contract and the answers of the Python router in
//! `services/route/varuna_route`. The graph comes from the Python router's own export
//! (`tools/export_graph.py`); the run's depths and the ops log are read from the same files the
//! Python router reads, at request time. `tests/test_route_rs_parity.py` holds the two to the
//! same paths, minutes, avoided sets, corridors and reasons.
//!
//! Which search uses which engine:
//!
//! * the **VARUNA** search and its **alternates** are time-dependent Dijkstra keyed on arrival
//!   time ([`router::search`]) - their edge costs change with the forecast step the vehicle has
//!   reached, which a plain contraction hierarchy cannot represent;
//! * the **naive** search is dry-weather and time-independent, and may be answered by a
//!   contraction hierarchy ([`ch`]) or by the same Dijkstra with water ignored. `/healthz` says
//!   which one the running service uses.

pub mod api;
pub mod ch;
pub mod forecast;
pub mod graph;
pub mod ops;
pub mod profiles;
pub mod pynum;
pub mod pytime;
pub mod reasons;
pub mod router;
pub mod server;
pub mod spread;
