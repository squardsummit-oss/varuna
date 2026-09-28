"""EPA SWMM dynamic wave behind the same interface as ``drain1d`` (SPEC.md 11.4, task P4.9).

SPEC.md 11.4 makes the prototype's 1D drain model "diffusive-wave-lite" and names PySWMM
dynamic wave as the P1 upgrade "behind the same ``Drain1D`` interface"; SPEC.md 16's judge
answer promises that "when a SWMM model arrives we import it". This module is both halves of
that promise: :func:`write_inp` turns the inferred graph (SPEC.md 10.1 step 7) into a SWMM 5
``.inp`` that any SWMM user can open, and :func:`prepare` / :func:`simulate` drive the real
engine through the same call shape :mod:`varuna_twin.drain1d` presents, so a caller chooses an
engine rather than a code path.

**The point of having both is the gap between them.** ``drain1d`` solves a head per node and a
Manning flow per edge with no inertia; SWMM's DYNWAVE solves the full Saint-Venant momentum
equation on the same pipes. Running one small network through both and reporting where they
agree is the only honest answer to "how reduced is your reduced model?", so
``services/twin/tests/test_swmm.py`` measures it rather than asserting it. What that test found
on the 4-node linear network the drain tests already use - a 600 mm pipe at 0.5 %, 40 m between
manholes, a steady 10 L/s at the head of the line:

* **The flows agree to 2.7e-16 m3/s and the depths are out by a factor of 4.5.** Both engines
  settle at 0.010000 m3/s on every conduit, because both conserve what they are given. But
  ``drain1d`` needs 1.38 cm of water in the pipe to carry it and SWMM needs 6.28 cm - 4.90 cm
  of head at every manhole. That is not the momentum term: it is the full-bore hydraulic radius
  ``drain1d`` uses at every fill, which its own docstring names as an over-conveyance without
  quantifying it. Manning's normal depth for a part-full circle, worked by hand at SWMM's
  6.28 cm, gives 0.009975 m3/s against the 0.010000 asked for - **0.25 % off**. So SWMM is
  right and the reduced model is the one that is wrong, by 4.5x in depth at this fill. On a
  street this is the difference between an ankle and nothing at all, which is why the number is
  in the docstring rather than in a footnote.
* **The transient is a storage difference, not a wave-equation difference.** ``drain1d`` keeps
  every cubic metre in the manholes (``DrainNetwork.storage_area``, 1 m2 each here) and none in
  the pipes; SWMM keeps it in the conduits and treats a junction as a point with no plan area
  at all - measured, ``Node.volume`` reads exactly 0.0 m3 at every junction in every
  configuration tried, including with ``MIN_SURFAREA`` set to 1.0 m2, while the three 40 m
  conduits hold 1.8834 m3 against ``drain1d``'s 0.0410 m3, a factor of **45.9**. So the two
  models are filling different vessels and their filling times cannot be compared as if they
  were the same one. The test reports the steady state and the storage separately for that
  reason: one combined transient RMSE would blame Saint-Venant for a storage assumption.
* **Both lock at a tide, and only one of them tells you the street is wet.** Raising a FIXED
  outfall to 5.0 m, above the cover of every manhole on the line but the first, drowns the
  network in both engines - SPEC.md P4.7's acceptance test and the 1:40 beat of the demo
  (SPEC.md 15). What they do with the water that will not fit is the difference.
  ``drain1d`` has no flooding: its Preissmann slot holds the head past the cover
  (5.025/5.014/5.005 m against covers of 5.0/4.8/4.6) and leaves it to ``coupling`` to put that
  water on the street. SWMM spills at the cover, so the lowest manhole pins the whole line at
  its own ground level - 4.600 m - and 4,470 m3 leaves over the covers in two hours. A flap
  gate stops the sea in both: the adapter's boundary volume is exactly 0.0 m3 with the gate on.
* **SWMM is the one that loses water here.** Over a 30-minute run SWMM's own report gives a
  continuity error of -1.901 % where ``drain1d``'s :attr:`DrainRunReport.mass_balance` is
  0.0000 %; this adapter's independent audit of SWMM reads 1.901 %, which is how we know both
  numbers. It is a one-off **0.342 m3** taken during the initial filling transient, not a leak:
  the same run at two hours reports -0.475 %, the same 0.342 m3 against four times the inflow,
  and it does not move when the routing step goes from 1.0 to 0.05 s, when ``MAX_TRIALS`` goes
  from 8 to 20, or when ``MIN_SURFAREA`` or ``SurDepth`` change. Against SPEC.md 11.3's 0.1 %
  budget, the "upgrade" misses by 19x on a cold half-hour run. A cycle is five minutes, so the
  P1 upgrade would need a wet start before it could be trusted with the budget the P0 solver
  already meets.

**At city scale the export is lossless and the graph is not.** The whole Mumbai graph writes in
0.62 s to a 12.4 MB ``.inp`` (49,770 junctions, 127 outfalls of which 3 are tidal and 1 gated,
49,770 conduits - 47,492 circular and 2,278 box), byte-identical on a second call, with nothing
clamped and the hydraulic-radius mapping costing at most 48 micrometres. What it does count is
**18,994 conduits running uphill** - precisely the number ADR-0048's independent audit found.
``drain1d`` tolerates them because it drives flow from the head difference and caps at the city
pipeline's design ``q_full``; SWMM reads the slope off the inverts and would object to every
one. That count is the strongest argument in the repository for ADR-0048's regrade, and the
exporter is where it can be produced on demand.

**Three mappings the graph does not carry, stated once because they are where an importer
would otherwise guess.**

1. *Blockage.* SWMM has no ``beta``. ``drain1d`` writes ``Q_full = (1/n)(1-b) A R_h^(2/3)
   S^(1/2)`` (Appendix A), so the blockage divides the roughness rather than shrinking the
   pipe: ``n_eff = n / (1 - beta)`` reproduces that capacity exactly while leaving the
   geometry - and therefore the storage and the crown depth - untouched. Shrinking the
   diameter instead would have changed all three. ``beta >= 1`` has no finite roughness, so
   :data:`MAX_BLOCKAGE` clamps it and :attr:`InpReport.clamped_blockage` counts how often.
2. *Cross-section.* ``DrainNetwork`` carries an ``area`` and a crown depth but not a shape, so
   the shape is recovered: CIRCULAR when ``area`` matches ``pi D^2 / 4`` to
   :data:`SHAPE_TOLERANCE`, RECT_CLOSED of height ``D`` and width ``area / D`` otherwise. That
   preserves both the area and the crown. SWMM then computes its own hydraulic radius from
   that geometry, which is *not* ``DrainNetwork.hydraulic_radius``: ``drain1d`` uses the
   full-bore ``R_h`` at every fill on purpose (see its docstring), and SWMM varies it with
   depth. :attr:`InpReport.max_r_h_mismatch_m` measures how far apart the two are at full bore
   so the difference is a number rather than a footnote.
3. *Slope.* ``drain1d`` caps every flow at the ``q_full`` the city pipeline sized the pipe for,
   which was computed against an enforced 0.3 % minimum slope (SPEC.md 10.1 step 7); SWMM
   reads the slope off the inverts it is given. ADR-0048 measured 18,994 of Mumbai's 49,770
   edges running uphill, so on the real graph these are not the same number and SWMM will say
   so loudly. :attr:`InpReport.adverse_edges` counts them at export time rather than leaving
   the engine to find them.

**What this is not, and what stands in the way.** It is not wired into
:mod:`varuna_twin.runner`, and nothing in the cycle calls it. P4.9 is P1 in SPEC.md 3.2, so
that is expected rather than a shortfall, but two specific things would have to be settled
first and neither is a matter of effort:

1. *Who owns the street exchange.* SPEC.md 11.5 makes ``coupling`` the only thing that moves
   water between pipe and street, and SWMM computes its own spill at the manhole cover from
   better physics than the orifice formula in Appendix A. Run together as this adapter runs
   them, the same water leaves twice - :attr:`SwmmSolver.flooded_m3` measures how much and
   :func:`simulate` warns. Giving the junctions headroom so SWMM cannot spill was tried and is
   worse; the measurements are under :data:`DEFAULT_SURCHARGE_DEPTH_M`.
2. *Conservation.* SPEC.md 11.3 budgets 0.1 % and the engine misses it cold, as above.

Speed is a third question and this module makes no claim about it: a cycle makes 2,160
five-second ``simulate`` calls over 49,770 edges (task P4.6), the adapter is stepped through a
Python loop over every node per step, and nothing here has been profiled at that shape. No
timing in this file is a budget claim.

Determinism (rule 8): the ``.inp`` is written in node and edge index order with fixed field
widths and a caller-supplied title, so two exports of the same network are byte-identical. The
engine itself is deterministic given the same file and the same sequence of stepped forcings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from varuna_twin.drain1d import (
    BOUNDARY_TIDAL,
    ControlledSink,
    DrainRunReport,
    SinkState,
)
from varuna_twin.types import DrainNetwork, DrainState

if TYPE_CHECKING:  # pragma: no cover - keeps numpy off the runtime type surface
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.twin.swmm")

__all__ = [
    "DEFAULT_SURCHARGE_DEPTH_M",
    "MAX_BLOCKAGE",
    "MIN_CONDUIT_LENGTH_M",
    "MIN_MAX_DEPTH_M",
    "SHAPE_TOLERANCE",
    "InpReport",
    "SwmmNotAvailable",
    "SwmmSolver",
    "available",
    "prepare",
    "simulate",
    "write_inp",
]


# ------------------------------------------------------------------------ export floors
MAX_BLOCKAGE = 0.99
"""Largest ``beta`` the roughness mapping can express.

``n_eff = n / (1 - beta)`` is the mapping (see the module docstring), so ``beta = 1`` is an
infinite roughness and SWMM would reject the conduit. A fully blocked pipe in SWMM is a closed
conduit - a control rule or a zero ``MaxFlow`` - which is a different model object, not a
parameter, so this clamps at 1 % of capacity and reports the count instead of silently
inventing a structure the graph never described. Mumbai's prior ``beta`` runs 0.15-0.35
(SPEC.md 10.1 step 7) and Pulse's posterior has never exceeded 0.683 on a baked cycle, so
the clamp is a guard rather than a routine conversion."""

MIN_MAX_DEPTH_M = 0.1
"""Smallest junction ``MaxDepth`` written, in metres.

``MaxDepth`` is ``z_ground - z_invert``, and ADR-0048 measured that the inferred graph is not
gravity-consistent: 27,974 of 49,897 Mumbai nodes reach an outfall only over an invert above
their own street, and a node whose invert sits above its ground gives a negative depth here.
SWMM reads ``MaxDepth = 0`` as "compute it from the deepest conduit", which would quietly
replace the value rather than refuse it, so a non-positive depth is clamped to 10 cm and
counted in :attr:`InpReport.clamped_depth`. The regrade ADR-0048 decided is the real fix."""

MIN_CONDUIT_LENGTH_M = 0.1
"""Shortest conduit length written, in metres. SWMM divides by the length for the friction
slope; the inferred graph places inlets every 40 m (SPEC.md 10.1 step 7) so this should never
fire, and :attr:`InpReport.clamped_length` says when it did."""

SHAPE_TOLERANCE = 0.01
"""Relative area mismatch above which an edge is written as a box rather than a circle.

The city pipeline snaps circular pipes to 450/600/900/1200/1500 mm and uses box drains for
trunks (SPEC.md 10.1 step 7), so the two families are far apart and 1 % separates them
cleanly; it is not a fitted number."""

DEFAULT_SURCHARGE_DEPTH_M = 0.0
"""Default ``SurDepth`` written on every junction, in metres above the manhole cover.

Zero, meaning SWMM spills at the cover and reports the spill as flooding - the physical
behaviour, and the one a SWMM modeller opening the file would expect.

**The alternative was tried and is worse, which is why this is a documented default rather
than an obvious one.** SPEC.md 11.5 makes ``coupling`` the only thing that moves water
between the pipe and the street, so it is tempting to give SWMM a large headroom and stop it
spilling on its own, the way ``drain1d``'s Preissmann slot holds the water at the manhole. It
does not work. Measured on a 150 mm pipe forced with 0.3 m3/s it cannot carry:

===================  ==========  ================  ===============  =================
``SURCHARGE_METHOD``  SurDepth    head at node 0    flooding rate    continuity error
===================  ==========  ================  ===============  =================
EXTRAN (default)      0 m         5.00 m (= cover)  0.280 m3/s       -0.144 %
EXTRAN                100 m       **105.0 m**       0.160 m3/s       -0.187 %
SLOT                  0 m         5.00 m            0.279 m3/s       +0.963 %
SLOT                  100 m       **47.5 m**        0.0              **+44.3 %**
===================  ==========  ================  ===============  =================

A 100 m head over a Mumbai street is not a pressurised sewer, it is a number driving fictitious
conveyance into every downstream pipe - and under EXTRAN the node floods anyway, so the
headroom bought nothing. Under SLOT it stopped the flooding and destroyed the continuity.

So the adapter keeps the physical default and makes the *consequence* visible instead:
:attr:`SwmmSolver.flooded_m3` accumulates what SWMM spilled, and :func:`simulate` warns when a
run both imposed a ``q_surcharge`` and let SWMM flood, because that is the same water leaving
the network twice. Reconciling the two - letting SWMM own the street exchange, which is the
better physics, instead of imposing it - is the work that stands between this module and the
coupled runner."""

_ROUTING_STEP_S = 1.0
"""SWMM's internal routing step, in seconds. Matched to ``TwinInputs.inner_dt_s`` (SPEC.md
11.4) so a difference between the engines is the wave equation and not the time step."""


class SwmmNotAvailable(RuntimeError):
    """Raised when the engine is asked for and ``pyswmm`` is not importable.

    Section 17's habit for a dependency that will not install is a fallback and a note, not a
    fight: ``drain1d`` is the P0 solver and stays the default everywhere, so this is only ever
    raised by a caller that explicitly asked for SWMM."""


def available() -> bool:
    """True when ``pyswmm`` and its bundled SWMM library import.

    Measured on this machine: pyswmm 2.1.0 against SWMM 5.2, installed by ``uv add pyswmm``
    into the dev group. The import is attempted rather than assumed because the engine is a
    compiled library and a wheel that installs can still fail to load."""
    try:
        import pyswmm  # noqa: F401
    except Exception:  # pragma: no cover - exercised only where the wheel is missing
        return False
    return True


# ============================================================================ export
@dataclass(frozen=True, slots=True)
class InpReport:
    """What :func:`write_inp` had to change, and by how much, to fit SWMM's data model.

    Every field is a count or a measured distance rather than a flag, because each one is a
    place the inferred graph and SWMM disagree about what a drain is, and the size of the
    disagreement is the useful part. A report whose counts are all zero means the export was
    lossless apart from the three mappings named in the module docstring.
    """

    path: Path
    n_junctions: int
    n_outfalls: int
    n_conduits: int
    n_circular: int
    n_box: int
    n_tidal: int
    n_flap_gates: int

    clamped_blockage: int
    """Edges whose ``beta`` exceeded :data:`MAX_BLOCKAGE` and were written at that cap."""

    clamped_depth: int
    """Junctions whose ``z_ground - z_invert`` was non-positive (ADR-0048) and were written at
    :data:`MIN_MAX_DEPTH_M`."""

    clamped_length: int
    adverse_edges: int
    """Conduits whose downstream invert is above the upstream one. ``drain1d`` does not care -
    it drives every flow from the head difference - but SWMM will report them, so the count is
    taken here where it can be attributed to the graph rather than to the engine."""

    max_r_h_mismatch_m: float
    """Largest ``|R_h(SWMM full bore) - DrainNetwork.hydraulic_radius|`` over the edges, in
    metres. Mapping 2 in the module docstring: the two models disagree about the hydraulic
    radius by construction, and this says by how much on this graph."""

    bytes_written: int

    @property
    def lossless(self) -> bool:
        """True when nothing had to be clamped. The three documented mappings still apply."""
        return self.clamped_blockage == 0 and self.clamped_depth == 0 and self.clamped_length == 0


def _shape_of(area: float, depth: float) -> tuple[str, float, float]:
    """``(shape, geom1, geom2)`` for one edge, recovering what the graph did not store.

    See mapping 2 in the module docstring. ``geom1`` is the depth in both cases, which is what
    SWMM wants for CIRCULAR (diameter) and RECT_CLOSED (height).
    """
    circular_area = math.pi * depth * depth / 4.0
    if circular_area > 0.0 and abs(area - circular_area) / circular_area <= SHAPE_TOLERANCE:
        return "CIRCULAR", depth, 0.0
    width = area / depth if depth > 0.0 else 0.0
    return "RECT_CLOSED", depth, width


def _full_bore_r_h(shape: str, geom1: float, geom2: float) -> float:
    """SWMM's hydraulic radius at full bore for the shape :func:`_shape_of` chose.

    Circular: ``D/4``. Closed rectangle: ``wh / (2(w + h))``. Only used to measure the gap
    against ``DrainNetwork.hydraulic_radius`` - the engine computes its own and is never told
    this number.
    """
    if shape == "CIRCULAR":
        return geom1 / 4.0
    perimeter = 2.0 * (geom1 + geom2)
    return (geom1 * geom2 / perimeter) if perimeter > 0.0 else 0.0


def _check_names(ids: tuple[str, ...], kind: str) -> None:
    """Refuse an id SWMM's tokenizer would split or comment out.

    SWMM reads the ``.inp`` as whitespace-separated tokens with ``;`` starting a comment, so a
    name containing either would silently become two objects or half a line. VARUNA's ids are
    ``MUM-N000000`` and have never tripped this; it raises rather than sanitising because a
    renamed node would break the join back to ``city/<city>/graph/*.parquet``.
    """
    for name in ids:
        if not name or any(c.isspace() for c in name) or ";" in name:
            msg = f"{kind} id {name!r} cannot be written to a SWMM .inp (whitespace or ';')"
            raise ValueError(msg)


def write_inp(
    network: DrainNetwork,
    path: Path | str,
    *,
    title: str = "VARUNA inferred drain graph",
    start: datetime | None = None,
    duration_s: float = 10800.0,
    routing_step_s: float = _ROUTING_STEP_S,
    report_step_s: float = 300.0,
    surcharge_depth_m: float = DEFAULT_SURCHARGE_DEPTH_M,
    initial_head: NDArray[np.floating] | None = None,
    tide_stage_m: float = 0.0,
) -> InpReport:
    """Write ``network`` as a SWMM 5 input file and report what the mapping cost.

    This is the "when a SWMM model arrives we import it" claim run in reverse (SPEC.md 16):
    the file it writes opens in EPA SWMM's own GUI, so the inferred graph can be inspected,
    edited and handed back by someone who has never seen this repository. Elevations are metres
    in the DEM's vertical datum and flows are CMS, which is what makes SWMM read the whole file
    as metric - there is no separate unit switch.

    ``duration_s`` sizes the simulation window in the header; the stepped adapter needs the
    window to outlast every ``simulate`` call it will make, so :func:`prepare` passes its own
    horizon rather than this default. ``initial_head`` seeds each node's ``InitDepth`` from a
    :class:`~varuna_twin.types.DrainState`, which is how a run resumes; without it every pipe
    starts empty.

    ``tide_stage_m`` is only the stage written into the file for the FIXED outfalls. The stepped
    adapter overrides it every step through ``Outfall.outfall_stage`` (a method, not a property
    - assigning to it silently does nothing, which cost an hour to find), so this value matters
    only to someone opening the file directly.
    """
    path = Path(path)
    _check_names(network.node_ids, "node")
    _check_names(network.edge_ids, "edge")
    start = start or datetime(2019, 7, 2, 0, 0, 0)

    z_ground = np.asarray(network.z_ground, dtype=np.float64)
    z_invert = np.asarray(network.z_invert, dtype=np.float64)
    boundary = np.asarray(network.boundary, dtype=np.int8)
    flap = np.asarray(network.flap_gate, dtype=bool)
    beta = np.clip(np.asarray(network.beta, dtype=np.float64), 0.0, 1.0)
    length = np.asarray(network.length, dtype=np.float64)
    area = np.asarray(network.area, dtype=np.float64)
    depth = np.asarray(network.diameter, dtype=np.float64)
    manning_n = np.asarray(network.edge_manning_n, dtype=np.float64)
    r_h_declared = np.asarray(network.hydraulic_radius, dtype=np.float64)
    from_node = np.asarray(network.from_node, dtype=np.intp)
    to_node = np.asarray(network.to_node, dtype=np.intp)

    max_depth = z_ground - z_invert
    # Only a junction carries a maximum depth in the .inp; an outfall has none. A tidal outfall
    # standing on ground below the sea's datum has its invert capped at that ground (drains.py),
    # so its depth is 0 by design and is not a clamp.
    clamped_depth = int(np.count_nonzero((max_depth <= 0.0) & (boundary == 0)))
    max_depth = np.maximum(max_depth, MIN_MAX_DEPTH_M)

    clamped_length = int(np.count_nonzero(length < MIN_CONDUIT_LENGTH_M))
    length = np.maximum(length, MIN_CONDUIT_LENGTH_M)

    clamped_blockage = int(np.count_nonzero(beta > MAX_BLOCKAGE))
    n_eff = manning_n / (1.0 - np.minimum(beta, MAX_BLOCKAGE))

    init_depth = (
        np.maximum(np.asarray(initial_head, dtype=np.float64) - z_invert, 0.0)
        if initial_head is not None
        else np.zeros(network.n_nodes, dtype=np.float64)
    )

    is_outfall = boundary != 0
    lines: list[str] = [
        "[TITLE]",
        f";; {title}",
        ";; Written by varuna_twin.swmm (SPEC.md task P4.9). Every element of this graph is",
        ";; INFERRED from roads and terrain (SPEC.md 10.1 step 7), not surveyed. Blockage is",
        ";; carried in the Manning roughness as n/(1-beta); see the module docstring.",
        "",
        "[OPTIONS]",
        "FLOW_UNITS           CMS",
        "INFILTRATION         HORTON",
        "FLOW_ROUTING         DYNWAVE",
        "LINK_OFFSETS         DEPTH",
        f"START_DATE           {start:%m/%d/%Y}",
        f"START_TIME           {start:%H:%M:%S}",
        f"REPORT_START_DATE    {start:%m/%d/%Y}",
        f"REPORT_START_TIME    {start:%H:%M:%S}",
        *_end_time_lines(start, duration_s),
        "SWEEP_START          01/01",
        "SWEEP_END            12/31",
        "DRY_DAYS             0",
        f"REPORT_STEP          {_hms(report_step_s)}",
        f"WET_STEP             {_hms(report_step_s)}",
        f"DRY_STEP             {_hms(report_step_s)}",
        f"ROUTING_STEP         {routing_step_s:.3f}",
        "ALLOW_PONDING        NO",
        "INERTIAL_DAMPING     PARTIAL",
        "VARIABLE_STEP        0.00",
        "LENGTHENING_STEP     0",
        "MIN_SURFAREA         0",
        "NORMAL_FLOW_LIMITED  BOTH",
        "SKIP_STEADY_STATE    NO",
        "FORCE_MAIN_EQUATION  H-W",
        "MIN_SLOPE            0",
        "HEAD_TOLERANCE       0.0015",
        "",
        "[JUNCTIONS]",
        ";;Name           Elev       MaxDepth   InitDepth  SurDepth   Aponded",
    ]

    n_junctions = 0
    for i, name in enumerate(network.node_ids):
        if is_outfall[i]:
            continue
        n_junctions += 1
        lines.append(
            f"{name:<16} {z_invert[i]:<10.4f} {max_depth[i]:<10.4f} "
            f"{init_depth[i]:<10.4f} {surcharge_depth_m:<10.4f} 0"
        )

    lines += ["", "[OUTFALLS]", ";;Name           Elev       Type       Stage      Gated"]
    n_outfalls = n_tidal = n_flap = 0
    for i, name in enumerate(network.node_ids):
        if not is_outfall[i]:
            continue
        n_outfalls += 1
        gated = "YES" if flap[i] else "NO"
        n_flap += int(flap[i])
        if boundary[i] == BOUNDARY_TIDAL:
            n_tidal += 1
            lines.append(
                f"{name:<16} {z_invert[i]:<10.4f} {'FIXED':<10} {tide_stage_m:<10.4f} {gated}"
            )
        else:  # BOUNDARY_FREE
            lines.append(f"{name:<16} {z_invert[i]:<10.4f} {'FREE':<10} {'':<10} {gated}")

    lines += [
        "",
        "[CONDUITS]",
        ";;Name           FromNode         ToNode           Length     Roughness    "
        "InOffset   OutOffset  InitFlow   MaxFlow",
    ]
    for e, name in enumerate(network.edge_ids):
        lines.append(
            f"{name:<16} {network.node_ids[from_node[e]]:<16} "
            # Roughness gets eight decimals, not the four the other columns use: it carries the
            # blockage (`n_eff = n / (1 - beta)`), so rounding it rounds VARUNA's learned pipe
            # capacity. At six decimals a beta of 0.4 on n = 0.013 wrote 0.021667 for
            # 0.0216666..., a 1.5e-5 relative error in Q_full that the capacity test caught.
            f"{network.node_ids[to_node[e]]:<16} {length[e]:<10.4f} {n_eff[e]:<12.8f} "
            f"{0.0:<10.4f} {0.0:<10.4f} {0.0:<10.4f} 0"
        )

    lines += [
        "",
        "[XSECTIONS]",
        ";;Link           Shape        Geom1      Geom2      Geom3      Geom4      Barrels",
    ]
    n_circular = n_box = 0
    max_r_h_gap = 0.0
    for e, name in enumerate(network.edge_ids):
        shape, geom1, geom2 = _shape_of(float(area[e]), float(depth[e]))
        if shape == "CIRCULAR":
            n_circular += 1
        else:
            n_box += 1
        gap = abs(_full_bore_r_h(shape, geom1, geom2) - float(r_h_declared[e]))
        max_r_h_gap = max(max_r_h_gap, gap)
        lines.append(
            f"{name:<16} {shape:<12} {geom1:<10.4f} {geom2:<10.4f} {0.0:<10.4f} {0.0:<10.4f} 1"
        )

    lines += [
        "",
        "[REPORT]",
        "INPUT                NO",
        "CONTROLS             NO",
        "SUBCATCHMENTS        NONE",
        "NODES                ALL",
        "LINKS                ALL",
        "",
    ]

    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii", newline="\n")

    adverse = int(np.count_nonzero(z_invert[to_node] > z_invert[from_node]))
    report = InpReport(
        path=path,
        n_junctions=n_junctions,
        n_outfalls=n_outfalls,
        n_conduits=network.n_edges,
        n_circular=n_circular,
        n_box=n_box,
        n_tidal=n_tidal,
        n_flap_gates=n_flap,
        clamped_blockage=clamped_blockage,
        clamped_depth=clamped_depth,
        clamped_length=clamped_length,
        adverse_edges=adverse,
        max_r_h_mismatch_m=max_r_h_gap,
        bytes_written=len(text.encode("ascii")),
    )
    log.info(
        "swmm.inp_written",
        path=str(path),
        junctions=n_junctions,
        outfalls=n_outfalls,
        conduits=network.n_edges,
        adverse_edges=adverse,
        clamped_depth=clamped_depth,
        clamped_blockage=clamped_blockage,
        bytes=report.bytes_written,
    )
    return report


def _hms(seconds: float) -> str:
    """``HH:MM:SS`` for a SWMM time-step field."""
    total = round(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _end_time_lines(start: datetime, duration_s: float) -> list[str]:
    """``END_DATE``/``END_TIME`` for a window of ``duration_s`` from ``start``."""
    from datetime import timedelta

    end = start + timedelta(seconds=float(duration_s))
    return [f"END_DATE             {end:%m/%d/%Y}", f"END_TIME             {end:%H:%M:%S}"]


# ============================================================================ adapter
@dataclass(slots=True)
class SwmmSolver:
    """An open SWMM simulation over a :class:`DrainNetwork`, held between ``simulate`` calls.

    The shape mirrors :class:`varuna_twin.drain1d.DrainSolver`: built once from a network,
    passed to :func:`simulate` with a :class:`DrainState`, rebuilt when the network changes.
    What it cannot mirror is the *statelessness* - ``drain1d``'s solver is precomputed arrays
    and the state lives entirely in the ``DrainState``, whereas the SWMM engine owns its own
    state behind a C library and can only move forward. So this object is a live simulation
    that must be closed (:meth:`close`, or a ``with`` block), and seeking backwards means
    building a new one.
    """

    network: DrainNetwork
    inp_path: Path
    inp_report: InpReport
    _sim: Any = field(repr=False)
    _nodes: list[Any] = field(repr=False)
    """Node handles in ``network.node_ids`` order, so the array exchange is a zip and never a
    dict lookup per node per step."""

    _links: list[Any] = field(repr=False)
    _outfall_idx: NDArray[np.intp] = field(repr=False)
    _tidal_idx: NDArray[np.intp] = field(repr=False)
    _boundary_edges: NDArray[np.intp] = field(repr=False)
    """Edges with exactly one end at an outfall - the only way water leaves the network."""

    _boundary_sign: NDArray[np.floating] = field(repr=False)
    """+1 where the edge's positive direction leaves the network, -1 where it enters.

    Integrating ``sign * Link.flow`` is the only outfall measurement that carries a sign.
    ``Node.total_inflow`` and ``Node.cumulative_inflow`` at an outfall are both *unsigned*
    totals - measured under a tide lock, they read 45.53 m3 arriving where the signed link
    integral reads -9.35 m3 net, i.e. the sea pushing water in. Reading either of them would
    have reported an outfall discharging hard at the exact moment it was being flooded."""

    flooded_m3: float = 0.0
    """Volume SWMM has spilled at manhole covers over every :func:`simulate` call so far.

    Exposed on the solver rather than in the report because :class:`DrainRunReport` is
    ``drain1d``'s contract and has no field for it: ``drain1d`` cannot flood, since its
    Preissmann slot holds the water at the node however high the head goes. SWMM can, and with
    :data:`DEFAULT_SURCHARGE_DEPTH_M` it does - see that constant for why stopping it is worse.

    This is the number a coupled caller has to reconcile: SPEC.md 11.5 already computes a
    surcharge onto the street, so anything SWMM spills on top of it is the same water leaving
    twice. :func:`simulate` warns when both happen in one call."""

    _closed: bool = False

    def close(self) -> None:
        """Finalise the report and close the engine. Idempotent.

        ``sim.report()`` runs first because it is what writes the continuity table
        :meth:`continuity_error_pct` reads; closing without it leaves a ``.rpt`` with no audit
        in it, which is the one number a caller comparing engines came for.
        """
        if not self._closed:
            self._sim.report()
            self._sim.close()
            self._closed = True

    def __enter__(self) -> SwmmSolver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def continuity_error_pct(self) -> float | None:
        """SWMM's own flow-routing continuity error in percent, read from the written report.

        Read from the ``.rpt`` rather than from ``Simulation.flow_routing_error``, which
        **returns 0.0 for the whole of a stepped run** on pyswmm 2.1.0 - measured before
        ``next()``, after ``sim.report()`` and after ``sim.close()``, all 0.0, while the report
        file for the same run said -1.901 %. Trusting the live property would have reported a
        perfectly conservative engine on a run that lost 1.9 % of its water.

        Returns ``None`` until :meth:`close` has been called, because SWMM writes the
        continuity table when it finalises the report and there is nothing to read before then.

        This is the engine's audit of itself on its own storage model, and is not SPEC.md
        11.3's budget - that one is :attr:`DrainRunReport.mass_balance`, computed over VARUNA's
        volumes. On the 4-node test network the two agree to 0.03 percentage points."""
        rpt = self.inp_path.with_suffix(".rpt")
        if not rpt.is_file():
            return None
        import re

        match = re.search(
            r"Continuity Error \(%\)\s*\.+\s*(-?[\d.]+)",
            rpt.read_text(encoding="utf-8", errors="replace"),
        )
        return float(match.group(1)) if match else None


def prepare(
    network: DrainNetwork,
    state: DrainState | None = None,
    *,
    workdir: Path | str,
    horizon_s: float = 86400.0,
    routing_step_s: float = _ROUTING_STEP_S,
    surcharge_depth_m: float = DEFAULT_SURCHARGE_DEPTH_M,
    title: str = "VARUNA inferred drain graph",
) -> SwmmSolver:
    """Export ``network`` and open the engine on it, ready for :func:`simulate`.

    ``workdir`` is required rather than defaulted to a temporary directory: SWMM writes a
    ``.rpt`` and an ``.out`` beside the ``.inp`` and a caller comparing engines wants to read
    them, so the location is the caller's decision and nothing here deletes anything.

    ``state`` seeds the initial depths, which is the only moment SWMM will accept a head from
    outside; after ``sim.start()`` the engine owns its state. ``horizon_s`` must outlast the sum
    of every ``simulate`` call that will be made, because SWMM stops at the window in the file
    and :func:`simulate` reports a short run rather than pretending the water kept moving.

    ``routing_step_s`` is SWMM's own integration step, written into the file; it is not the
    stride, which :func:`simulate` sets from its ``dt_s``. On the 4-node test network 1.0, 0.25
    and 0.05 s reached the same heads to five decimal places, so the default matches
    ``TwinInputs.inner_dt_s`` and the difference between the engines stays the wave equation
    rather than the clock.
    """
    if not available():
        msg = (
            "pyswmm is not importable, so the SWMM engine is unavailable. `drain1d` is the "
            "P0 solver (SPEC.md 11.4) and needs nothing; install with `uv add pyswmm`."
        )
        raise SwmmNotAvailable(msg)
    from pyswmm import Links, Nodes, Simulation

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    inp_path = workdir / "network.inp"
    inp_report = write_inp(
        network,
        inp_path,
        title=title,
        duration_s=horizon_s,
        routing_step_s=routing_step_s,
        surcharge_depth_m=surcharge_depth_m,
        initial_head=None if state is None else state.head,
    )

    sim = Simulation(str(inp_path))
    sim.start()
    nodes, links = Nodes(sim), Links(sim)
    node_handles = [nodes[name] for name in network.node_ids]
    link_handles = [links[name] for name in network.edge_ids]

    boundary = np.asarray(network.boundary, dtype=np.int8)
    is_outfall = boundary != 0
    to_out = is_outfall[np.asarray(network.to_node, dtype=np.intp)]
    from_out = is_outfall[np.asarray(network.from_node, dtype=np.intp)]
    # One end only. An edge between two outfalls would be ambiguous in sign and does not exist
    # in the inferred graph (outfalls are leaves of the shortest-path tree, SPEC.md 10.1
    # step 7); it is excluded rather than assigned an arbitrary direction.
    boundary_edges = np.flatnonzero(to_out ^ from_out).astype(np.intp)
    return SwmmSolver(
        network=network,
        inp_path=inp_path,
        inp_report=inp_report,
        _sim=sim,
        _nodes=node_handles,
        _links=link_handles,
        _outfall_idx=np.flatnonzero(is_outfall).astype(np.intp),
        _tidal_idx=np.flatnonzero(boundary == BOUNDARY_TIDAL).astype(np.intp),
        _boundary_edges=boundary_edges,
        _boundary_sign=np.where(to_out[boundary_edges], 1.0, -1.0),
    )


def simulate(
    solver: SwmmSolver,
    state: DrainState,
    *,
    duration_s: float,
    dt_s: float = _ROUTING_STEP_S,
    q_inlet: NDArray[np.floating] | None = None,
    q_surcharge: NDArray[np.floating] | None = None,
    tide_stage_m: float | NDArray[np.floating] | None = None,
    sinks: ControlledSink | None = None,
    sink_state: SinkState | None = None,
) -> DrainRunReport:
    """Advance the SWMM engine over one sync interval and write the result back into ``state``.

    The signature is :func:`varuna_twin.drain1d.simulate`'s, minus the two arguments that only
    mean something to the NumPy/Numba implementation (``compiled``, ``applied_surcharge_out``),
    so a caller that holds either solver calls the same function with the same arguments. The
    forcings are frozen over the interval exactly as SPEC.md 11.5 freezes them.

    **How each forcing reaches SWMM, and where that is not the same thing.**

    * ``q_inlet`` and ``q_surcharge`` are summed into one signed lateral inflow per node and set
      through ``Node.generated_inflow``, which is the same node-continuity term ``drain1d``
      writes as ``(Q_inlet - Q_surch)/A_s``. SWMM would normally compute the surcharge itself as
      flooding; it is imposed here because SPEC.md 11.5 makes ``coupling`` the only thing that
      moves water between street and pipe, and two models spilling the same water is a leak, not
      a second opinion.
    * ``sinks`` are drawn on the *engine's* heads, read at the start of the interval, so a pump
      curve sees the same depth it would under ``drain1d`` - but the draw is held constant
      across the interval rather than re-evaluated every inner step, because SWMM's inner steps
      are not reachable from here. On a 5 s sync with a wet-well curve that is a sub-millimetre
      difference; on a long ``duration_s`` it is not, and a caller doing that should say so.
    * ``tide_stage_m`` is pushed to every tidal outfall each step through
      ``Outfall.outfall_stage``. A per-node array is accepted for signature parity and only its
      tidal entries are read. **A FREE outfall ignores the stage entirely** - measured: setting
      5.0 m on a FREE outfall left its head at 3.161 m and the trunk flowing forward - so a
      network whose outfalls are all FREE cannot tide-lock in either engine.

    The returned :class:`DrainRunReport` is filled from measured engine quantities:
    ``stored_*`` sums ``Node.volume`` and ``Link.volume`` (SWMM keeps water in the conduits,
    ``drain1d`` keeps it in the manholes - see the module docstring), and ``boundary_m3``
    integrates the signed flow on the conduits that touch an outfall, so a tide pushing water
    back in lands in ``volume_in_m3`` the way ``drain1d``'s does - see
    :attr:`SwmmSolver._boundary_sign` for why the node accessors could not be used.
    ``limited_edges`` is 0 rather than a guess: SWMM's flow limiter is not the per-node supply
    scaling that field counts.

    The resulting ``mass_balance`` is the *adapter's* audit of SWMM in VARUNA's terms, and it
    is not expected to be zero - it tracked SWMM's own reported continuity error to 0.03
    percentage points on the test network, including the 1.9 % that engine loses on a short
    run. Read it as a measurement of the engine, not as a check on this code.
    """
    if solver._closed:
        msg = "This SwmmSolver has been closed; build a new one with prepare()."
        raise RuntimeError(msg)

    # `step_advance` is the *stride* - how far the engine runs before it hands control back so
    # the forcings can be re-set - and it is a separate knob from the ROUTING_STEP inside the
    # .inp, which is SWMM's own integration step. The stride must be whole seconds: SWMM's
    # stride API takes an int and passing 1.0 raises a SWIG TypeError rather than rounding, so
    # a caller asking for a sub-second sync would otherwise get a silently longer one. A finer
    # *integration* step is still reachable through `prepare(routing_step_s=0.25)` - measured
    # on the 4-node network, 1.0 / 0.25 / 0.05 s all reach the same heads to five decimals.
    stride = round(dt_s)
    if stride < 1 or abs(stride - dt_s) > 1e-9:
        msg = (
            f"SWMM hands control back on whole seconds; dt_s={dt_s} cannot be expressed. "
            "drain1d's inner step is 1 s (SPEC.md 11.4). For a finer integration step, pass "
            "routing_step_s to prepare()."
        )
        raise ValueError(msg)
    solver._sim.step_advance(stride)

    n_steps = max(round(duration_s / dt_s), 0)
    stored_start = _stored_volume(solver)
    if n_steps == 0:
        return DrainRunReport(
            n_steps=0,
            inlet_m3=0.0,
            surcharge_m3=0.0,
            sink_m3=0.0,
            boundary_m3=0.0,
            stored_start_m3=stored_start,
            stored_end_m3=stored_start,
            limited_edges=0,
        )

    n_nodes = solver.network.n_nodes
    inlet = np.zeros(n_nodes) if q_inlet is None else np.asarray(q_inlet, dtype=np.float64)
    surch = np.zeros(n_nodes) if q_surcharge is None else np.asarray(q_surcharge, dtype=np.float64)
    draw = _sink_draw(solver, sinks, sink_state)
    lateral = inlet - surch - draw

    stage = _tide_stage(solver, tide_stage_m)
    tidal = solver._tidal_idx
    nodes = solver._nodes
    links = solver._links
    b_edges = solver._boundary_edges
    b_sign = solver._boundary_sign

    boundary_m3 = 0.0
    flooded_m3 = 0.0
    steps_run = 0
    for _ in range(n_steps):
        for i in range(n_nodes):
            nodes[i].generated_inflow(float(lateral[i]))
        if stage is not None:
            for i in tidal:
                nodes[i].outfall_stage(float(stage))
        try:
            next(solver._sim)
        except StopIteration:
            # The window in the .inp ran out. Report the steps that actually happened rather
            # than the ones that were asked for: a short run that claims its full duration is
            # how a mass balance quietly stops meaning anything.
            log.warning(
                "swmm.window_exhausted",
                asked_steps=n_steps,
                ran_steps=steps_run,
                hint="raise prepare(horizon_s=...)",
            )
            break
        steps_run += 1
        boundary_m3 += dt_s * float(
            sum(s * float(links[e].flow) for e, s in zip(b_edges, b_sign, strict=True))
        )
        flooded_m3 += dt_s * float(sum(float(nodes[i].flooding) for i in range(n_nodes)))

    _read_back(solver, state)
    solver.flooded_m3 += flooded_m3
    if flooded_m3 > 0.0 and float(np.sum(surch)) > 0.0:
        # The same water leaving the network twice: once as the coupling's imposed surcharge,
        # once as SWMM's own spill at the cover. Neither engine is wrong on its own; the
        # combination is, and it is the specific thing that has to be settled before this
        # adapter can stand in for `drain1d` inside `varuna_twin.runner`.
        log.warning(
            "swmm.double_counted_surcharge",
            swmm_flooded_m3=round(flooded_m3, 4),
            imposed_surcharge_m3=round(float(np.sum(surch)) * dt_s * steps_run, 4),
            hint="let SWMM own the street exchange, or give the junctions headroom (see "
            "DEFAULT_SURCHARGE_DEPTH_M for why headroom is worse)",
        )
    if sinks is not None and sink_state is not None and sinks.n_units:
        sink_state.filled_m3 += dt_s * steps_run * draw[sinks.node]

    elapsed = dt_s * steps_run
    return DrainRunReport(
        n_steps=steps_run,
        inlet_m3=float(np.sum(np.maximum(inlet, 0.0)) * elapsed),
        surcharge_m3=float(np.sum(np.maximum(surch, 0.0)) * elapsed),
        sink_m3=float(np.sum(draw) * elapsed),
        boundary_m3=boundary_m3,
        stored_start_m3=stored_start,
        stored_end_m3=_stored_volume(solver),
        limited_edges=0,
    )


def _tide_stage(
    solver: SwmmSolver, tide_stage_m: float | NDArray[np.floating] | None
) -> float | None:
    """The single stage to push to every tidal outfall, or ``None`` to leave the file's value.

    ``drain1d`` accepts a per-node array for the same argument; the sea is one number over an
    AOI 9.5 km across, so an array is read at its tidal entries and refused if they disagree
    rather than silently using the first.
    """
    if tide_stage_m is None:
        return None
    arr = np.asarray(tide_stage_m, dtype=np.float64)
    if arr.ndim == 0:
        return float(arr)
    tidal = solver._tidal_idx
    if tidal.size == 0:
        return None
    values = arr[tidal]
    if float(np.ptp(values)) > 1e-9:
        msg = (
            "SWMM's outfall stage is set per node but this adapter pushes one sea level; the "
            f"tidal outfalls were given {values.min():.4f}-{values.max():.4f} m, which is a "
            "spatially varying sea the adapter will not silently flatten."
        )
        raise ValueError(msg)
    return float(values[0])


def _sink_draw(
    solver: SwmmSolver, sinks: ControlledSink | None, sink_state: SinkState | None
) -> NDArray[np.floating]:
    """Per-node withdrawal in m3/s from the pumps and tanks, on the engine's current heads.

    Deliberately a copy of ``drain1d.sink_draw``'s arithmetic rather than a call into it: that
    function takes a ``DrainSolver``, which this engine does not have, and the twelve-unit loop
    (SPEC.md 3.3) is four lines. If a third engine appears the arithmetic should move to a
    shared helper.
    """
    draw = np.zeros(solver.network.n_nodes, dtype=np.float64)
    if sinks is None or sink_state is None or sinks.n_units == 0:
        return draw
    invert = np.asarray(solver.network.z_invert, dtype=np.float64)
    head = np.array([float(n.head) for n in solver._nodes], dtype=np.float64)
    remaining = sinks.capacity_m3 - sink_state.filled_m3
    for u in range(sinks.n_units):
        if remaining[u] <= 0.0:
            continue
        node = int(sinks.node[u])
        depth = head[node] - invert[node]
        rate = float(np.interp(depth, sinks.curve_depth_m[u], sinks.curve_rate_m3_s[u]))
        if rate > 0.0:
            draw[node] += rate
    return draw


def _stored_volume(solver: SwmmSolver) -> float:
    """Water in the model right now: node storage plus conduit storage, in m3.

    Both terms are read because SWMM splits the water differently from ``drain1d``: a junction
    has no plan area and reports ``volume`` 0.0, and everything is in the conduits, where
    ``drain1d`` has it the other way round. Summing both is the only comparison that is about
    water rather than about where a model decided to keep it.
    """
    nodes = sum(float(n.volume) for n in solver._nodes)
    links = sum(float(link.volume) for link in solver._links)
    return nodes + links


def _read_back(solver: SwmmSolver, state: DrainState) -> None:
    """Copy the engine's heads and flows into ``state``, in place.

    In place because every caller of ``drain1d.simulate`` holds the same ``DrainState`` across
    calls and reads it afterwards; an engine that returned a new object would be a different
    interface, which is the one thing task P4.9 asks this module not to be.
    """
    state.head[:] = [float(n.head) for n in solver._nodes]
    state.flow[:] = [float(link.flow) for link in solver._links]
