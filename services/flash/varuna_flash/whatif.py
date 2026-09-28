"""What-if on the emulator, attribution on the drain solver (SPEC.md 11.7, 7.7; P7.7, P7.8).

Two questions an operator asks that the Twin is too slow to answer, and they turned out to need
two different engines:

* **What if?** "Rain plus 30 % - what changes?" Three hours of city in milliseconds on
  Flash-lite, so the answer arrives while the question is still on screen. :func:`run_scenario`.
* **Why?** "Which pipes are making this junction flood?" One run per candidate pipe. This is
  **not** answerable on Flash-lite, for the reason ADR-0042 measured and this module still
  enforces, and it is answerable on `drain1d` with the street depth frozen - which is what the
  second half of this module does. :func:`attribute_pipes`.

The dividing line is worth stating once, because it is the whole lesson of ADR-0042: an emulator
fast enough to answer a *why* question is not thereby able to. The speed came from removing the
coupling the question is about.

**What these answers are worth, stated plainly.** Flash-lite reproduces the Twin's *pattern*
well - peak depth correlates at 0.98 across segments - and its *level* poorly: on held-out
storms its CSI at the 30 cm car threshold is 0.085. So:

* **Attribution cannot be answered on the cascade, and :func:`attribute` says so.** A ranking
  would only need the
  rank correlation of 0.98 - but this emulator is element-wise per segment (see
  :mod:`varuna_flash.model`: every term in :func:`~varuna_flash.model.simulate` is indexed by
  segment and nothing crosses between them). A pipe that is not under the target moves the
  target's peak by exactly zero, so there is no ranking to read. Measured at Hindmata,
  one of fourteen candidates scored above zero and it was the target's own segment; at the
  deepest street on the same run, none of twenty-nine did. :func:`attribute` therefore drops
  everything under :data:`ATTRIBUTION_FLOOR_CM` and refuses with a reason when nothing
  survives, rather than putting a rank-ordered list of zeros on screen. It is kept because it is
  the honest record of what this emulator can do, and because the what-if lab still cleans on it;
  the hotspot drawer reads :func:`attribute_pipes` instead.
* **A what-if depth is not a forecast, and nothing in this module checks it against the
  physics.** The delta is reported with the emulator's measured error attached, and the level it
  is added to comes from the Twin's own forecast for the run. The physics check of SPEC.md 7.7
  lives in the API (``POST /v1/whatif/physics-check``, two coupled Twin runs on a 990 m crop,
  P7.8), and a scenario the emulator cannot represent at all - a different sea level - runs on
  one full-city Twin (``POST /v1/whatif/twin``).

**The tide control is refused here, not approximated.** The emulator is a perturbation around a
base state measured from Twin runs that all shared one tide series (see
:mod:`varuna_flash.model`), so it has no representation of a different sea level. Returning a
plausible number for a tide offset would be inventing one. A tide scenario runs on the Twin itself
(``POST /v1/whatif/twin``): one full-city coupled run, whose answer is the physics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from varuna_flash.model import simulate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

    from varuna_flash.model import FlashModel

log = structlog.get_logger("varuna.flash.whatif")

__all__ = [
    "ATTRIBUTION_FLOOR_CM",
    "ATTRIBUTION_SYNC_S",
    "CANDIDATE_HOPS",
    "CELL_AREA_M2",
    "CLEANED_BETA",
    "NO_ATTRIBUTION_REASON",
    "SUBGRAPH_DOWN_HOPS",
    "AttributionResult",
    "DrainAdjacency",
    "PipeAttributionResult",
    "ScenarioResult",
    "attribute",
    "attribute_pipes",
    "build_adjacency",
    "candidate_pipes",
    "run_scenario",
]

CLEANED_BETA = 0.05
"""Blockage a desilted pipe is assumed to reach (SPEC.md 11.7).

Not zero. A jetted pipe is clear, not new: there is always some residual, and claiming a
perfectly clean pipe would overstate every cleaning benefit the board shows."""

ATTRIBUTION_FLOOR_CM = 0.1
"""Depth a candidate must move a junction by to be named as responsible at all.

A millimetre of street, and it applies to both operators. On Flash-lite, below this the emulator
has not measured an effect - it has measured floating-point dust or, far more often, an exact
zero, because it has no coupling between segments. On `drain1d` it is a real threshold on a real
sensitivity: on the 08:40 cycle it is what separates Sion Subway 1, whose worst pipe moves it
1.62 cm, from Hindmata, whose worst moves it 0.003 cm.

Either way a row at 0.00 cm on the hotspot drawer reads as "this pipe was evaluated and ranks
fourteenth", which is a claim the number does not make. :func:`attribute_pipes` applies it to the
**magnitude**, so a pipe that makes the junction measurably deeper is named too."""

NO_ATTRIBUTION_REASON = (
    "Flash-lite is element-wise per segment: a pipe that is not under the target has exactly "
    "zero effect. Attribution needs drain1d in the loop or the GNN (P7.12)."
)
"""What the drawer says when no Flash-lite candidate clears :data:`ATTRIBUTION_FLOOR_CM`.

Only :func:`attribute` uses it. :func:`attribute_pipes` composes its own refusal, because after
P7.7 the reason is a *measurement* - how many pipes were re-run and how far the best of them
moved the junction - rather than a structural fact about the operator.

SPEC.md section 17: a control that cannot do its job names what is missing and what would fix
it. Here the missing piece is a hydraulic operator - either `drain1d` inside the attribution loop
or the GNN surrogate of task P7.12, both of which carry pipe-to-street connectivity that this
cascade folded away into a per-segment capacity term."""


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One what-if: the depths it produces and how they differ from the baseline."""

    depth_cm: NDArray[np.floating]
    """``(steps, segments)`` under the scenario."""

    baseline_cm: NDArray[np.floating]
    delta_cm: NDArray[np.floating]
    """Peak change per segment: negative is improvement."""

    n_improved: int
    n_worse: int
    ms: float
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """Which pipes explain one junction's peak - or a refusal naming why none can be.

    ``rows`` is empty exactly when ``reason`` is set. A caller renders one or the other; there is
    no third state where both an empty ranking and no explanation reach the screen."""

    target_segment: str
    depth_before_cm: float
    rows: tuple[dict[str, Any], ...]
    """Surviving candidates, deepest first, each with ``rank``, ``beta`` and
    ``depth_explained_cm``."""

    combined: dict[str, Any] | None
    """Cleaning every surviving row at once, or ``None`` when none survived."""

    reason: str | None
    """Why the list is empty, when it is. :data:`NO_ATTRIBUTION_REASON` in the usual case."""


def run_scenario(
    model: FlashModel,
    rain_mm_h: NDArray[np.floating],
    *,
    beta: NDArray[np.floating],
    rain_scale: float = 1.0,
    cleaned_segments: set[str] | None = None,
    pump_cm_per_step: NDArray[np.floating] | None = None,
    pump_from_step: NDArray[np.integer] | None = None,
    scenario_beta: NDArray[np.floating] | None = None,
    tide_offset_m: float = 0.0,
) -> ScenarioResult:
    """Run one scenario against the unmodified baseline and report the difference.

    Args:
        model: the fitted emulator.
        rain_mm_h: the cycle's rain, ``(steps,)`` or ``(steps, segments)``.
        beta: current blockage per segment - Pulse's posterior, joined onto segments.
        scenario_beta: the blockage the *scenario* runs at, before ``cleaned_segments`` is
            applied. Absent, it is ``beta``. Given, it is how a lever that cleans *pipes* rather
            than streets reaches the emulator: the caller re-joins the posterior with those pipes
            cleaned, so a street whose worst pipe was desilted drops to its next-worst pipe
            rather than to :data:`CLEANED_BETA` (the "clean top 14" lever, SPEC.md 7.7).
        rain_scale: multiplier on the storm (SPEC.md 7.7's 0.5x to 2.0x).
        cleaned_segments: segments whose pipe is desilted to :data:`CLEANED_BETA`. An id this
            fit has no segment for is dropped, and the returned note counts only what was
            actually cleaned - the caller is expected to have told the user about the rest.
        pump_cm_per_step: extra drawdown per segment from a pump plan.
        pump_from_step: the step each segment's pump arrives at, ``(segments,)``. Absent, a pump
            runs from step 0, which is what this function always did. Given, the drawdown a
            pump running from step 0 would have produced is shifted to start at the arrival -
            the same construction :func:`varuna_products.pumps._after_delta` prices a plan with,
            so the lab and the pump board cannot disagree about what a lorry does.
        tide_offset_m: refused; see the module docstring.

    Raises:
        ValueError: a tide offset was asked for. The emulator cannot represent it and returning
            a number anyway would be an invention (rule 6).
    """
    from time import perf_counter

    if abs(tide_offset_m) > 1e-9:
        msg = (
            "Flash-lite cannot move the tide: it is a perturbation around a base state measured "
            "at one tide series, so a sea level it never saw is outside what it represents. A "
            "tide scenario runs on the Twin (POST /v1/whatif/twin)."
        )
        raise ValueError(msg)

    started = perf_counter()
    rain = np.asarray(rain_mm_h, dtype=np.float64)
    beta_now = np.asarray(beta, dtype=np.float64)

    baseline = simulate(model, rain, beta=beta_now, pump_cm_per_step=None)

    beta_scenario = (
        beta_now.copy()
        if scenario_beta is None
        else np.asarray(scenario_beta, dtype=np.float64).copy()
    )
    if beta_scenario.shape != beta_now.shape:
        msg = (
            f"scenario_beta has shape {beta_scenario.shape}, the baseline blockage "
            f"{beta_now.shape}: they must describe the same segments."
        )
        raise ValueError(msg)
    repinned = int(np.count_nonzero(beta_scenario != beta_now))
    picked: list[int] = []
    if cleaned_segments:
        index = {sid: i for i, sid in enumerate(model.segment_ids)}
        picked = [index[s] for s in cleaned_segments if s in index]
        beta_scenario[picked] = CLEANED_BETA

    if pump_cm_per_step is None or pump_from_step is None:
        scenario = simulate(
            model,
            rain * rain_scale,
            beta=beta_scenario,
            pump_cm_per_step=pump_cm_per_step,
        )
    else:
        scenario = _pumped_from_arrival(
            model,
            rain * rain_scale,
            beta_scenario,
            np.asarray(pump_cm_per_step, dtype=np.float64),
            np.asarray(pump_from_step, dtype=np.int64),
        )

    delta = scenario.max(axis=0) - baseline.max(axis=0)
    notes = [
        f"Reduced-order emulator calibrated to VARUNA-Twin: RMSE {model.rmse_cm:.1f} cm and "
        f"CSI {model.csi_30cm:.2f} at 30 cm on held-out storms. The ranking is what this "
        f"supports; run the physics check for a depth."
    ]
    if picked:
        # Counted from what was cleaned, not from what was asked for: an id this fit has no
        # segment for is dropped above, and a note saying otherwise would name pipes the run
        # never touched (rule 6).
        notes.append(
            f"{len(picked)} pipe{'' if len(picked) == 1 else 's'} cleaned to beta = {CLEANED_BETA}."
        )
    if repinned:
        notes.append(
            f"{repinned} segment{'' if repinned == 1 else 's'} run at a changed blockage "
            "supplied by the caller."
        )
    if abs(rain_scale - 1.0) > 1e-9:
        notes.append(f"Rain scaled to {rain_scale:.2f}x.")
    if pump_cm_per_step is not None:
        pumped = int(np.count_nonzero(np.asarray(pump_cm_per_step) > 0.0))
        notes.append(
            f"Pumps drain {pumped} segment{'' if pumped == 1 else 's'}"
            + (" from each pump's arrival." if pump_from_step is not None else " from step 0.")
        )

    result = ScenarioResult(
        depth_cm=scenario,
        baseline_cm=baseline,
        delta_cm=delta,
        n_improved=int(np.sum(delta < -0.5)),
        n_worse=int(np.sum(delta > 0.5)),
        ms=(perf_counter() - started) * 1000.0,
        notes=tuple(notes),
    )
    log.info(
        "flash.whatif",
        ms=round(result.ms, 1),
        rain_scale=rain_scale,
        cleaned=len(picked),
        improved=result.n_improved,
        worse=result.n_worse,
    )
    return result


def _pumped_from_arrival(
    model: FlashModel,
    rain: NDArray[np.floating],
    beta: NDArray[np.floating],
    pump_cm_per_step: NDArray[np.float64],
    pump_from_step: NDArray[np.int64],
) -> NDArray[np.floating]:
    """The scenario with each segment's pump switched on at its own arrival step.

    Two emulator runs, without and with the pump from step 0; their difference is the water the
    pump finds to remove, non-negative by construction (extra outflow cannot raise a depth, and it
    is clipped rather than trusted). That removal profile is then shifted to start at each
    segment's arrival - a pump that arrives at step ``a`` has been running ``t - a`` steps at step
    ``t`` - and subtracted from the unpumped run, floored at zero. Segments without a pump are
    returned exactly as the unpumped run has them.
    """
    unpumped = simulate(model, rain, beta=beta)
    pumped = simulate(model, rain, beta=beta, pump_cm_per_step=pump_cm_per_step)
    removal = np.maximum(unpumped - pumped, 0.0)
    n_steps = unpumped.shape[0]
    shifted = np.zeros_like(removal)
    arrival = np.clip(pump_from_step, 0, n_steps)
    for step in np.unique(arrival[pump_cm_per_step > 0.0]).tolist():
        if step >= n_steps:
            continue  # arrives after the window closes: it drains nothing the forecast can see
        columns = np.flatnonzero((arrival == step) & (pump_cm_per_step > 0.0))
        shifted[step:, columns] = removal[: n_steps - step, columns]
    return np.maximum(unpumped - shifted, 0.0)


def attribute(
    model: FlashModel,
    rain_mm_h: NDArray[np.floating],
    *,
    beta: NDArray[np.floating],
    target_segment: str,
    candidate_segments: list[str],
    top_n: int = 14,
) -> AttributionResult:
    """Which pipes are making one junction flood, by cleaning each in turn (SPEC.md 11.7).

    A finite-difference sensitivity: clean one candidate, re-run the whole city, and read the
    peak at the target. The candidate that drops it most is the one most responsible. This is
    only affordable because a run is milliseconds - on the Twin it would be three minutes per
    candidate.

    **On this emulator the measurement almost always comes back empty, and it says so.**
    :func:`~varuna_flash.model.simulate` is element-wise per segment, so cleaning a pipe that is
    not under the target moves the target's peak by *exactly* zero - the two do not interact at
    all. The only candidate that can score is the target's own segment, and then the combined
    figure equals that one candidate rather than exceeding it. Candidates below
    :data:`ATTRIBUTION_FLOOR_CM` are dropped, and when that leaves nothing the result carries
    :data:`NO_ATTRIBUTION_REASON` instead of a ranking, because a rank-ordered column of 0.00 cm
    on the hotspot drawer would read as a computed ranking of responsible pipes. What would make
    the ranking real is a hydraulic operator - `drain1d` inside this loop, or the GNN of P7.12.
    """
    index = {sid: i for i, sid in enumerate(model.segment_ids)}
    if target_segment not in index:
        # Not a refusal about the physics: the caller named a segment this fit does not contain.
        return AttributionResult(
            target_segment=target_segment,
            depth_before_cm=0.0,
            rows=(),
            combined=None,
            reason=(
                f"Segment {target_segment} is not in the fitted emulator, so there is no peak "
                f"to attribute. Refit Flash-lite on the current city layers."
            ),
        )
    target = index[target_segment]

    beta_now = np.asarray(beta, dtype=np.float64)
    base_peak = float(simulate(model, rain_mm_h, beta=beta_now).max(axis=0)[target])

    scored: list[dict[str, Any]] = []
    for candidate in candidate_segments:
        position = index.get(candidate)
        if position is None:
            continue
        trial = beta_now.copy()
        trial[position] = CLEANED_BETA
        peak = float(simulate(model, rain_mm_h, beta=trial).max(axis=0)[target])
        explained = base_peak - peak
        if explained < ATTRIBUTION_FLOOR_CM:
            continue
        scored.append(
            {
                "segment_id": candidate,
                "beta": round(float(beta_now[position]), 3),
                "depth_explained_cm": round(explained, 2),
                "depth_before_cm": round(base_peak, 1),
            }
        )

    if not scored:
        log.info(
            "flash.attribution.refused",
            target=target_segment,
            candidates=len(candidate_segments),
            base_cm=round(base_peak, 1),
            floor_cm=ATTRIBUTION_FLOOR_CM,
        )
        return AttributionResult(
            target_segment=target_segment,
            depth_before_cm=round(base_peak, 1),
            rows=(),
            combined=None,
            reason=NO_ATTRIBUTION_REASON,
        )

    scored.sort(key=lambda row: -row["depth_explained_cm"])
    top = scored[:top_n]
    for rank, row in enumerate(top, start=1):
        row["rank"] = rank

    cleaned = beta_now.copy()
    for row in top:
        cleaned[index[row["segment_id"]]] = CLEANED_BETA
    combined_peak = float(simulate(model, rain_mm_h, beta=cleaned).max(axis=0)[target])

    log.info(
        "flash.attribution",
        target=target_segment,
        candidates=len(candidate_segments),
        above_floor=len(scored),
        top_n=len(top),
        base_cm=round(base_peak, 1),
        combined_cm=round(combined_peak, 1),
    )
    return AttributionResult(
        target_segment=target_segment,
        depth_before_cm=round(base_peak, 1),
        rows=tuple(top),
        combined={
            "segment_id": f"top-{len(top)}-combined",
            "n_cleaned": len(top),
            "depth_before_cm": round(base_peak, 1),
            "depth_after_cm": round(combined_peak, 1),
            "depth_explained_cm": round(base_peak - combined_peak, 2),
        },
        reason=None,
    )


# ======================================================================================
# The hydraulic operator: attribution on `drain1d`, not on the cascade above.
# ======================================================================================
#
# Everything above this line is the emulator. ADR-0042 measured what that costs for the *why*
# question and its measurement stands: `simulate` is element-wise per segment, so on Flash-lite
# a pipe that is not under the target moves it by exactly zero. What ADR-0042 also named as the
# right fix - "put `drain1d` in the attribution loop" - it priced at "21,296 candidates at a
# three-minute Twin-coupled run each" and shelved. Both halves of that price are wrong:
#
# * SPEC.md 11.7 asks for the pipes "within 5 upstream hops" of the target, which at Mumbai's
#   40 m inlet spacing is **67 pipes at Hindmata**, not 21,296.
# * SPEC.md 11.6 already describes the cheap operator: run `drain1d` "with the surface inflows
#   frozen from the last Twin run". A frozen street means no 2D solver, and the local network
#   below one junction is ~136 nodes rather than 49,897.
#
# So the loop below is the real hydraulics on a small network, and it does cross segments: a
# pipe two hops above Hindmata changes how much water the junction's own inlets can take.
#
# **What it is not.** The street depth is held at what the Twin produced, so this is a
# first-order sensitivity: it measures how much more (or less) water the drain takes off the
# junction when a pipe is cleaned, and it cannot see the second-order feedback where a shallower
# street then delivers less to its inlets. That feedback reduces the effect, so the number here
# is an upper bound on the depth a cleaning would actually remove, and is reported as such.


CANDIDATE_HOPS = 5
"""How far upstream of a junction a pipe may be and still be named as responsible.

SPEC.md 11.7 fixes it: "clean each candidate pipe within 5 upstream hops one at a time". At
Mumbai's 40 m inlet spacing that is roughly 200 m of network above the junction - 67 pipes above
Hindmata's 40 drain nodes."""

SUBGRAPH_DOWN_HOPS = 60
"""How far downstream of the candidate set the local network runs before it is cut.

Attribution is a local question; the water is not. A pipe above the junction only matters if
what is below the junction can take what it delivers, so the local network follows every node
down toward its outfall. A node whose real downstream pipes were all cut is given a free outfall
at its invert, so water still leaves rather than piling up against an artificial wall.

60 because that is where the cut stops being visible. Measured at Hindmata on the 08:40 cycle
(demo run ``MUM-20190702T0310Z``): at 60 hops the catchment closes on its own outfalls with
**zero** synthetic free outfalls (136 nodes, 135 pipes), and the junction's captured volume and
the sensitivity to cleaning one pipe are identical to four decimals at 25 hops (1 synthetic
outfall) and at 10 hops (2). Checking that before believing a localised answer is the point."""

ATTRIBUTION_SYNC_S = 5.0
"""Interval the frozen street depth and the exchange fluxes are held over, in seconds.

The same 5 s SPEC.md 11.5 fixes for the coupled solver, and **not** a free parameter. Every
limiter in :func:`~varuna_twin.coupling.compute_exchange` divides an available *volume* by this
interval, so a longer one throttles the exchange. Measured at Hindmata on the 08:40 cycle: at
5 s the junction's nodes capture a net 1,193 m3 over the window and surcharge 1,742 m3; at 60 s
the same run reads 815 m3 and 970 m3, and the sensitivity to cleaning one pipe falls from
0.019 cm to 0.004 cm. Running this six times cheaper would have quietly changed the answer."""

CELL_AREA_M2 = 900.0
"""Plan area of one 30 m grid cell in m2 - the area a node's exchange is spread over."""


@dataclass(frozen=True, slots=True)
class DrainAdjacency:
    """Incoming and outgoing edges per node, in CSR form.

    Built once per network and passed in: rebuilding it from the 49,770-edge table measured
    0.10-0.12 s, which across the 28-point hotspot register is three seconds spent learning the
    same thing 28 times."""

    in_offsets: NDArray[np.int64]
    in_edges: NDArray[np.int64]
    out_offsets: NDArray[np.int64]
    out_edges: NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class PipeAttributionResult:
    """Which pipes explain one junction's peak - or a refusal naming why none do.

    ``rows`` is empty exactly when ``reason`` is set, the same contract
    :class:`AttributionResult` carries, so a caller renders one or the other and never both."""

    target: str
    depth_before_cm: float
    rows: tuple[dict[str, Any], ...]
    combined: dict[str, Any] | None
    reason: str | None
    n_candidates: int
    """How many pipes were evaluated, including the ones that scored nothing.

    On screen beside a short list this is the difference between "three pipes matter" and "three
    of sixty-seven pipes matter", which are different claims."""

    n_nodes: int
    """Size of the local network the runs were made on, for the record."""

    method: str
    ms: float


def build_adjacency(network: Any) -> DrainAdjacency:
    """CSR incoming/outgoing edge lists for a :class:`~varuna_twin.types.DrainNetwork`."""
    n_nodes = int(network.n_nodes)
    to_node = np.asarray(network.to_node, dtype=np.int64)
    from_node = np.asarray(network.from_node, dtype=np.int64)

    def csr(endpoint: NDArray[np.int64]) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        order = np.argsort(endpoint, kind="stable").astype(np.int64)
        offsets = np.zeros(n_nodes + 1, dtype=np.int64)
        np.cumsum(np.bincount(endpoint, minlength=n_nodes), out=offsets[1:])
        return offsets, order

    in_offsets, in_edges = csr(to_node)
    out_offsets, out_edges = csr(from_node)
    return DrainAdjacency(in_offsets, in_edges, out_offsets, out_edges)


def candidate_pipes(
    network: Any,
    target_nodes: Sequence[int],
    *,
    adjacency: DrainAdjacency,
    hops: int = CANDIDATE_HOPS,
) -> tuple[list[int], list[int]]:
    """Pipes within ``hops`` upstream of ``target_nodes``, and the nodes the walk reached.

    Upstream means *against* the direction the graph was oriented in: an edge joins the
    candidate set when its ``to_node`` is already in the walk. This is SPEC.md 11.7's "within
    5 upstream hops" read literally, and it is the reason the candidate set is tens of pipes
    rather than the tens of thousands ADR-0042 priced the idea at.

    **One pipe this deliberately misses.** A junction is many nodes, and every pipe *between* two
    of them is a candidate (its ``to_node`` is a seed), but the single pipe leaving the junction's
    most downstream node is not - it is downstream of everything. Including it was tried and
    measured: it renames Sion Subway 1's worst pipe and cuts its ranking from 14 rows to 3,
    because seeding the walk with downstream nodes also prunes the upstream one. So the rule here
    is SPEC.md 11.7's, exactly, and the junction's own outfall pipe is left for the drain X-ray,
    which ranks every pipe by blockage rather than by what it explains.

    Returns ``(candidate edges, reached nodes)``, both sorted, both integer indices into
    ``network``."""
    seen = {int(n) for n in target_nodes}
    frontier = set(seen)
    edges: set[int] = set()
    from_node = np.asarray(network.from_node, dtype=np.int64)
    for _ in range(max(hops, 0)):
        nxt: set[int] = set()
        for node in frontier:
            lo, hi = adjacency.in_offsets[node], adjacency.in_offsets[node + 1]
            for e in adjacency.in_edges[lo:hi]:
                edge = int(e)
                edges.add(edge)
                upstream = int(from_node[edge])
                if upstream not in seen:
                    seen.add(upstream)
                    nxt.add(upstream)
        frontier = nxt
        if not frontier:
            break
    return sorted(edges), sorted(seen)


def _local_network(
    network: Any,
    nodes_up: Sequence[int],
    *,
    adjacency: DrainAdjacency,
    down_hops: int = SUBGRAPH_DOWN_HOPS,
) -> tuple[Any, dict[int, int], list[int], list[int], int]:
    """Cut the junction's own catchment out of the city graph.

    ``nodes_up`` is the target plus everything :func:`candidate_pipes` walked up to. This adds
    the downstream spine below all of them, keeps every edge whose ends are both inside, and
    returns ``(sub, node map, kept node indices, kept edge indices, synthetic outfalls)``.

    The cell addresses are rewritten to ``(i, 0)`` so the sub-network's "2D grid" is a single
    column with one cell per node. That is not a trick to avoid the real grid - it is what lets
    :func:`~varuna_twin.coupling.compute_exchange` run **unmodified** here, so the inlet capture
    and the surcharge are SPEC.md 11.5's own code rather than a second copy of the formula
    that could drift away from it.
    """
    from varuna_twin import drain1d
    from varuna_twin.types import DrainNetwork

    from_node = np.asarray(network.from_node, dtype=np.int64)
    to_node = np.asarray(network.to_node, dtype=np.int64)

    kept_nodes = {int(n) for n in nodes_up}
    kept_edges: set[int] = set()
    frontier = set(kept_nodes)
    for _ in range(max(down_hops, 0)):
        nxt: set[int] = set()
        for node in frontier:
            lo, hi = adjacency.out_offsets[node], adjacency.out_offsets[node + 1]
            for e in adjacency.out_edges[lo:hi]:
                edge = int(e)
                kept_edges.add(edge)
                downstream = int(to_node[edge])
                if downstream not in kept_nodes:
                    kept_nodes.add(downstream)
                    nxt.add(downstream)
        frontier = nxt
        if not frontier:
            break

    node_mask = np.zeros(int(network.n_nodes), dtype=bool)
    node_mask[np.fromiter(kept_nodes, dtype=np.int64, count=len(kept_nodes))] = True
    # Every edge internal to the kept set, not only the ones the two walks happened to traverse:
    # a pipe joining two kept nodes carries water between them whichever way the walks arrived.
    internal = np.flatnonzero(node_mask[from_node] & node_mask[to_node])
    kept_edges.update(int(e) for e in internal)

    node_list = sorted(kept_nodes)
    edge_list = sorted(kept_edges)
    nmap = {n: i for i, n in enumerate(node_list)}
    ni = np.asarray(node_list, dtype=np.int64)
    ei = np.asarray(edge_list, dtype=np.int64)

    boundary = np.asarray(network.boundary)[ni].astype(np.int8).copy()
    # A node whose real downstream pipes were all cut away drains freely at its invert. Without
    # this the cut is a wall: the local network would fill against it and every pipe above would
    # look blocked. The count is returned so a caller can say how much of the answer rests on
    # this rather than on the graph's real outfalls.
    kept_from = np.zeros(int(network.n_nodes), dtype=np.int64)
    if ei.size:
        np.add.at(kept_from, from_node[ei], 1)
    out_degree = adjacency.out_offsets[1:] - adjacency.out_offsets[:-1]
    synthetic = 0
    for n in node_list:
        if boundary[nmap[n]] == 0 and out_degree[n] > 0 and kept_from[n] == 0:
            boundary[nmap[n]] = drain1d.BOUNDARY_FREE
            synthetic += 1

    sub = DrainNetwork(
        node_ids=tuple(network.node_ids[i] for i in node_list),
        z_ground=np.asarray(network.z_ground)[ni].copy(),
        z_invert=np.asarray(network.z_invert)[ni].copy(),
        storage_area=np.asarray(network.storage_area)[ni].copy(),
        inlet_length=np.asarray(network.inlet_length)[ni].copy(),
        inlet_area=np.asarray(network.inlet_area)[ni].copy(),
        kappa=np.asarray(network.kappa)[ni].copy(),
        boundary=boundary,
        flap_gate=np.asarray(network.flap_gate)[ni].copy(),
        cell_row=np.arange(len(node_list), dtype=np.int32),
        cell_col=np.zeros(len(node_list), dtype=np.int32),
        edge_ids=tuple(network.edge_ids[e] for e in edge_list),
        from_node=np.array([nmap[int(from_node[e])] for e in edge_list], dtype=np.int32),
        to_node=np.array([nmap[int(to_node[e])] for e in edge_list], dtype=np.int32),
        length=np.asarray(network.length)[ei].copy(),
        area=np.asarray(network.area)[ei].copy(),
        hydraulic_radius=np.asarray(network.hydraulic_radius)[ei].copy(),
        diameter=np.asarray(network.diameter)[ei].copy(),
        edge_manning_n=np.asarray(network.edge_manning_n)[ei].copy(),
        q_full=np.asarray(network.q_full)[ei].copy(),
        beta=np.asarray(network.beta)[ei].copy(),
    )
    return sub, nmap, node_list, edge_list, synthetic


def _replicate(sub: Any, betas: NDArray[np.floating]) -> Any:
    """``betas.shape[0]`` disjoint copies of ``sub``, copy ``c`` carrying ``betas[c]``.

    The point is arithmetic, not elegance. One candidate run is 2,160 calls into the solver over
    a 136-node network, and at that size nearly all of the time is Python and Numba dispatch
    rather than the arithmetic - measured 0.36 s per candidate, so Hindmata's 67 candidates cost
    24 s and the 28-point register would cost about eleven minutes. Tiling the network puts every
    candidate in **one** run: the copies share no node and no edge, so :mod:`~varuna_twin.drain1d`
    (which scatters edge flows into nodes with ``bincount``) and :mod:`~varuna_twin.coupling`
    treat them as independent networks, and the result is exact rather than approximate. A test
    pins it against the one-at-a-time loop.
    """
    from varuna_twin.types import DrainNetwork

    copies = int(betas.shape[0])
    n_nodes = int(sub.n_nodes)
    shift = (np.arange(copies, dtype=np.int64) * n_nodes)[:, None]

    def tile(values: object) -> NDArray[Any]:
        return np.tile(np.asarray(values), copies)

    return DrainNetwork(
        node_ids=tuple(f"{nid}#{c}" for c in range(copies) for nid in sub.node_ids),
        z_ground=tile(sub.z_ground),
        z_invert=tile(sub.z_invert),
        storage_area=tile(sub.storage_area),
        inlet_length=tile(sub.inlet_length),
        inlet_area=tile(sub.inlet_area),
        kappa=tile(sub.kappa),
        boundary=tile(sub.boundary),
        flap_gate=tile(sub.flap_gate),
        # One cell per node of the tiled network, so each copy reads its own frozen street depth
        # and no copy can see another's water.
        cell_row=np.arange(copies * n_nodes, dtype=np.int32),
        cell_col=np.zeros(copies * n_nodes, dtype=np.int32),
        edge_ids=tuple(f"{eid}#{c}" for c in range(copies) for eid in sub.edge_ids),
        from_node=(np.asarray(sub.from_node, dtype=np.int64)[None, :] + shift)
        .ravel()
        .astype(np.int32),
        to_node=(np.asarray(sub.to_node, dtype=np.int64)[None, :] + shift).ravel().astype(np.int32),
        length=tile(sub.length),
        area=tile(sub.area),
        hydraulic_radius=tile(sub.hydraulic_radius),
        diameter=tile(sub.diameter),
        edge_manning_n=tile(sub.edge_manning_n),
        q_full=tile(sub.q_full),
        beta=np.asarray(betas, dtype=np.float64).ravel(),
    )


def _removed_series(
    net: Any,
    surface_depth_m: NDArray[np.floating],
    *,
    step_s: float,
    sync_s: float,
    cell_area_m2: float,
    tide_stage_m: NDArray[np.floating] | None,
    on_sea: NDArray[np.bool_] | None = None,
) -> NDArray[np.floating]:
    """Cumulative water the drain has taken off the street at every node, per step, in m3.

    ``surface_depth_m`` is ``(steps, n_nodes)`` and **frozen**: the depth the Twin already
    produced, held constant through each step, which is SPEC.md 11.6's "surface inflows frozen
    from the last Twin run". The return is the running sum of ``(Q_inlet - Q_surch) * dt``, so a
    node that surcharges contributes negatively - it is putting water back onto the street.

    ``on_sea`` is ``(n_nodes,)``, True on a node whose cell is the city's sea. It is handed to
    :func:`~varuna_twin.coupling.compute_exchange` as the ``sea`` of this module's one-column
    grid, so such a node neither captures nor surcharges - the rule the coupled Twin runs by. A
    zero street depth alone is not that rule: it stops the inlet, but a sea node whose head
    passes its ground would still vent into a cell the Twin holds at the tide. ``None``, or a
    mask with no node on it, is the call this function always made.
    """
    from varuna_twin import coupling, drain1d
    from varuna_twin.types import DrainState

    solver = drain1d.prepare(net)
    state = DrainState(
        head=np.asarray(net.z_invert, dtype=np.float64).copy(),
        flow=np.zeros(int(net.n_edges), dtype=np.float64),
    )
    z_grid = np.asarray(net.z_ground, dtype=np.float64).reshape(-1, 1)
    sea_grid: NDArray[np.bool_] | None = None
    if on_sea is not None:
        mask = np.asarray(on_sea, dtype=np.bool_)
        if mask.shape != (int(net.n_nodes),):
            msg = f"on_sea has shape {mask.shape}; the network has {int(net.n_nodes)} nodes"
            raise ValueError(msg)
        if mask.any():
            sea_grid = np.ascontiguousarray(mask.reshape(-1, 1))
    n_steps = int(surface_depth_m.shape[0])
    per_step = max(round(step_s / sync_s), 1)

    removed = np.zeros(int(net.n_nodes), dtype=np.float64)
    series = np.zeros((n_steps, int(net.n_nodes)), dtype=np.float64)
    for step in range(n_steps):
        h_grid = np.ascontiguousarray(surface_depth_m[step], dtype=np.float64).reshape(-1, 1)
        stage = None if tide_stage_m is None else float(tide_stage_m[step])
        for _ in range(per_step):
            exchange = coupling.compute_exchange(
                h_grid, z_grid, state.head, net, solver, cell_area_m2, sync_s, sea=sea_grid
            )
            q_inlet, q_surch = coupling.coupling_to_drain_rates(exchange)
            drain1d.simulate(
                solver,
                state,
                duration_s=sync_s,
                dt_s=1.0,
                q_inlet=q_inlet,
                q_surcharge=q_surch,
                tide_stage_m=stage,
            )
            removed += (q_inlet - q_surch) * sync_s
        series[step] = removed
    return series


def attribute_pipes(
    network: Any,
    surface_depth_m: NDArray[np.floating],
    *,
    target_nodes: Sequence[int],
    peak_step: int,
    adjacency: DrainAdjacency | None = None,
    target_label: str = "",
    depth_before_cm: float = 0.0,
    hops: int = CANDIDATE_HOPS,
    top_n: int = 14,
    step_s: float = 300.0,
    sync_s: float = ATTRIBUTION_SYNC_S,
    cell_area_m2: float = CELL_AREA_M2,
    tide_stage_m: NDArray[np.floating] | None = None,
    floor_cm: float = ATTRIBUTION_FLOOR_CM,
    node_segment_ids: Sequence[str | None] | None = None,
    node_on_sea: Sequence[bool] | NDArray[np.bool_] | None = None,
) -> PipeAttributionResult:
    """Which pipes explain one junction's peak, measured on `drain1d` (SPEC.md 11.7, P7.7).

    For every pipe within ``hops`` upstream of the junction: set its blockage to
    :data:`CLEANED_BETA`, re-run the junction's own catchment with the street depth frozen at
    what the Twin produced, and read how much more water the drain takes off the junction's
    cells by ``peak_step``. Divided by those cells' area that is a depth, and it is the depth the
    pipe explains.

    **The sign is kept and the ranking is by magnitude**, which is a deliberate departure from a
    literal reading of 11.7's "rank by depth reduction". Measured on the test network in
    ``services/flash/tests``: the pipe that feeds the target moves it 32 m3 in the *worse*
    direction, because the junction is already surcharging and a clear pipe above delivers more
    water than the cleaning takes away. Ranking reductions only would have discarded the pipe
    that explains the junction best.

    Args:
        network: the city's :class:`~varuna_twin.types.DrainNetwork`, carrying the blockage this
            cycle is running at - Pulse's posterior once a caller joins it on, the city prior
            otherwise.
        surface_depth_m: ``(steps, n_nodes)`` street depth at each node's own cell, frozen from
            the Twin run this cycle published.
        target_nodes: the drain nodes inside the junction, as integer indices.
        peak_step: the step the junction peaks at; the answer is read there rather than at the
            end of the window, because the question is about the peak.
        depth_before_cm: the junction's peak depth, carried onto every row so the drawer can
            print "explains 4 of 55 cm" without a second lookup. It is not used in the
            arithmetic; the effect is measured as a volume and converted with the junction's own
            cell area.
        node_segment_ids: the road segment each drain node was placed along, from
            ``city/<city>/graph/nodes.parquet``. Supplying it lets each row name the street the
            pipe runs under, which is the vocabulary the what-if lab cleans in; without it the
            rows carry pipe ids only, and say so by leaving ``segment_id`` null.
        node_on_sea: ``(network.n_nodes,)``, True on every node whose cell is the city's sea
            (``terrain.sea``, the ``sea_mask.tif`` of open sea and tidal creek; an intertidal
            cell behind the coast wall is land to the Twin and is False here). Such a node
            exchanges nothing with the street - no inlet capture and no surcharge - exactly as
            :func:`~varuna_twin.coupling.compute_exchange` treats it in the coupled run, while
            its pipes still carry water through it to the outfall. Freezing its street depth at
            zero, which the products stage also does, stops the inlet but not the vent: a sea
            node whose head passed its ground would still surcharge. ``None`` is a city without
            a sea raster, and the answer is the one this function always gave.

    Returns:
        A :class:`PipeAttributionResult` with a ranking, or with ``reason`` set and no rows.

    **Three things this is not, stated where they cannot be missed.**

    1. *The street does not respond.* The depth is frozen, so this measures the change in what
       the drain takes off the junction, not the depth a coupled re-run would end at. The
       feedback it misses is self-limiting in both directions - a shallower street delivers less
       to its inlets, a deeper one more - so every figure here is first-order and larger in
       magnitude than the coupled answer would be.
    2. *It is the local catchment, not the city.* See :data:`SUBGRAPH_DOWN_HOPS` for what was
       measured about the cut.
    3. *The graph is inferred.* Every pipe here was synthesised from roads and terrain
       (SPEC.md 10.1 step 7) and 38.2 % of its edges run uphill (ADR-0048), so a pipe named
       responsible is a claim about VARUNA's model of the drain, not about a surveyed asset.
    """
    from time import perf_counter

    started = perf_counter()
    if adjacency is None:
        adjacency = build_adjacency(network)
    sea_nodes: NDArray[np.bool_] | None = None
    if node_on_sea is not None:
        sea_nodes = np.asarray(node_on_sea, dtype=np.bool_)
        if sea_nodes.shape != (int(network.n_nodes),):
            # A mask built on another graph would keep the wrong nodes out of the exchange and
            # still produce a plausible ranking, so it is refused rather than truncated.
            msg = (
                f"node_on_sea has shape {sea_nodes.shape}; the drain graph has "
                f"{int(network.n_nodes)} nodes"
            )
            raise ValueError(msg)

    seeds = sorted({int(n) for n in target_nodes})
    if not seeds:
        return PipeAttributionResult(
            target=target_label,
            depth_before_cm=round(float(depth_before_cm), 1),
            rows=(),
            combined=None,
            reason=(
                "No drain node sits inside this junction, so there is no pipe to clean. The "
                "inferred graph places inlets along road centrelines; a junction with none is "
                "outside the network the city pipeline built."
            ),
            n_candidates=0,
            n_nodes=0,
            method="drain1d, frozen surface",
            ms=(perf_counter() - started) * 1000.0,
        )

    candidates, reached = candidate_pipes(network, seeds, adjacency=adjacency, hops=hops)
    sub, nmap, node_list, edge_list, synthetic = _local_network(
        network, reached, adjacency=adjacency
    )
    emap = {e: i for i, e in enumerate(edge_list)}
    pairs = [(e, emap[e]) for e in candidates if e in emap]
    local_candidates = [local for _, local in pairs]
    seed_local = np.array(sorted(nmap[s] for s in seeds), dtype=np.int64)
    if not local_candidates:
        return PipeAttributionResult(
            target=target_label,
            depth_before_cm=round(float(depth_before_cm), 1),
            rows=(),
            combined=None,
            reason=(
                f"No pipe runs into this junction within {hops} upstream hops, so SPEC.md "
                f"11.7's candidate set is empty. Its {len(seeds)} drain nodes are heads of the "
                f"inferred network."
            ),
            n_candidates=0,
            n_nodes=sub.n_nodes,
            method="drain1d, frozen surface",
            ms=(perf_counter() - started) * 1000.0,
        )

    full = np.asarray(surface_depth_m, dtype=np.float64)
    step = int(np.clip(peak_step, 0, full.shape[0] - 1))
    # Stopped at the peak, not run to the end of the window. `_removed_series` is a running sum,
    # so the steps after the peak cannot change what is read at it. It is the identical answer
    # for less work - verified line for line across the 28-point register on the 08:40 cycle,
    # where it took the register from 79.1 s to 27.0 s.
    local_nodes = np.asarray(node_list, dtype=np.int64)
    surface_sub = np.ascontiguousarray(full[: step + 1, local_nodes])
    # The sea, on the local network. A catchment that reaches none - 25 of Mumbai's 28 register
    # junctions - passes `None` and runs exactly as it did before there was a sea.
    sea_sub = None if sea_nodes is None else sea_nodes[local_nodes]
    if sea_sub is not None and not sea_sub.any():
        sea_sub = None

    # Copy 0 is the untouched baseline; copy c + 1 has candidate c cleaned. One run answers all
    # of them (see `_replicate`), and the baseline travels in the same run so it cannot drift
    # from the candidates through some difference in how it was set up.
    base_beta = np.asarray(sub.beta, dtype=np.float64)
    betas = np.tile(base_beta, (len(local_candidates) + 1, 1))
    for c, edge in enumerate(local_candidates, start=1):
        betas[c, edge] = CLEANED_BETA

    tiled = _replicate(sub, betas)
    series = _removed_series(
        tiled,
        np.tile(surface_sub, (1, len(local_candidates) + 1)),
        step_s=step_s,
        sync_s=sync_s,
        cell_area_m2=cell_area_m2,
        tide_stage_m=tide_stage_m,
        on_sea=None if sea_sub is None else np.tile(sea_sub, len(local_candidates) + 1),
    )
    n_nodes = int(sub.n_nodes)
    at_peak = series[step].reshape(len(local_candidates) + 1, n_nodes)[:, seed_local].sum(axis=1)
    area_m2 = float(seed_local.size) * cell_area_m2
    explained_cm = (at_peak[1:] - at_peak[0]) / area_m2 * 100.0

    # Ranked by how far the pipe moves the junction, **either way**, not by how much it helps.
    # That is a change from a literal reading of SPEC.md 11.7 ("rank by depth reduction") and
    # it is forced by the measurement: on the test network in `services/flash/tests`, cleaning
    # the pipe that feeds the target moves it by 32 m3 in the *worse* direction, because the
    # junction is already surcharging and a clear pipe above delivers more water to it. Ranking
    # only reductions would have thrown away the pipe that explains the junction best and kept
    # the ones a hop or two further off. The sign travels on every row and the drawer prints it.
    scored: list[dict[str, Any]] = []
    for (global_edge, local_edge), explained in zip(pairs, explained_cm, strict=True):
        if abs(explained) < floor_cm:
            continue
        scored.append(
            {
                "pipe_id": sub.edge_ids[local_edge],
                "segment_id": _pipe_segment(network, global_edge, node_segment_ids),
                "beta": round(float(base_beta[local_edge]), 3),
                "depth_explained_cm": round(float(explained), 2),
                "depth_before_cm": round(float(depth_before_cm), 1),
            }
        )

    if not scored:
        best = float(explained_cm[np.argmax(np.abs(explained_cm))]) if explained_cm.size else 0.0
        log.info(
            "flash.attribution.refused",
            target=target_label,
            candidates=len(local_candidates),
            best_cm=round(best, 4),
            floor_cm=floor_cm,
            nodes=n_nodes,
        )
        return PipeAttributionResult(
            target=target_label,
            depth_before_cm=round(float(depth_before_cm), 1),
            rows=(),
            combined=None,
            reason=(
                f"{len(local_candidates)} pipes within {hops} upstream hops were re-run on "
                f"drain1d with the street frozen; the one that moves it most moves it by "
                f"{abs(best):.3f} cm, under the {floor_cm:.2f} cm this junction needs to name a "
                f"pipe. At this depth the junction's inlets, not its pipes, limit the drain."
            ),
            n_candidates=len(local_candidates),
            n_nodes=n_nodes,
            method="drain1d, frozen surface",
            ms=(perf_counter() - started) * 1000.0,
        )

    scored.sort(key=lambda row: -abs(row["depth_explained_cm"]))
    top = scored[:top_n]
    for rank, row in enumerate(top, start=1):
        row["rank"] = rank

    # The combined effect of cleaning them together is a separate run, not the sum of the rows:
    # pipes in series share the water they carry, so the sum would double-count it.
    combined_beta = base_beta.copy()
    ids = {row["pipe_id"] for row in top}
    for local_edge in local_candidates:
        if sub.edge_ids[local_edge] in ids:
            combined_beta[local_edge] = CLEANED_BETA
    combined_series = _removed_series(
        _replicate(sub, combined_beta[None, :]),
        surface_sub,
        step_s=step_s,
        sync_s=sync_s,
        cell_area_m2=cell_area_m2,
        tide_stage_m=tide_stage_m,
        on_sea=sea_sub,
    )
    combined_cm = float((combined_series[step][seed_local].sum() - at_peak[0]) / area_m2 * 100.0)

    ms = (perf_counter() - started) * 1000.0
    log.info(
        "flash.attribution",
        target=target_label,
        candidates=len(local_candidates),
        above_floor=len(scored),
        top_n=len(top),
        best_cm=top[0]["depth_explained_cm"],
        worsening=sum(1 for row in top if row["depth_explained_cm"] < 0),
        combined_cm=round(combined_cm, 3),
        nodes=n_nodes,
        synthetic_outfalls=synthetic,
        ms=round(ms, 1),
    )
    return PipeAttributionResult(
        target=target_label,
        depth_before_cm=round(float(depth_before_cm), 1),
        rows=tuple(top),
        combined={
            "n_cleaned": len(top),
            "depth_before_cm": round(float(depth_before_cm), 1),
            "depth_after_cm": round(float(depth_before_cm) - combined_cm, 1),
            "depth_explained_cm": round(combined_cm, 2),
        },
        reason=None,
        n_candidates=len(local_candidates),
        n_nodes=n_nodes,
        method="drain1d, frozen surface",
        ms=ms,
    )


def _pipe_segment(
    network: Any, edge: int, node_segment_ids: Sequence[str | None] | None
) -> str | None:
    """The road segment a pipe runs under, when the caller supplied the join.

    The drain graph is keyed on pipes; every product downstream of the map is keyed on road
    segments, so the drawer's deep link into the what-if lab needs the join. It lives on the
    *node*: an inlet knows the segment it was placed along
    (``city/<city>/graph/nodes.parquet``), and a pipe is named by the inlet it leaves. Absent the
    column this returns ``None`` rather than guessing, and the row says so."""
    if node_segment_ids is None:
        return None
    upstream = int(np.asarray(network.from_node)[edge])
    value = node_segment_ids[upstream]
    return None if value is None or value != value else str(value)
