"""Design sizing for the inferred drain graph (SPEC.md 10.1 step 7, blueprint 7.1, task P1.8).

Every pipe in VARUNA's Mumbai graph is *inferred*: no municipal GIS exists for the AOI, so the
network is generated from roads and terrain and then sized the way a municipal engineer would
size it, using the rational method and Manning at full flow. This module is the sizing half of
that argument, kept separate from the graph building so the jury can read the physics on its own:

* :func:`runoff_coefficient` - rational-method ``C`` from imperviousness.
* :func:`rational_method_q` - ``Q = C i A`` in SI units.
* :func:`manning_diameter` - the circular full-flow diameter that carries ``Q`` on a given slope.
* :func:`snap_diameter` - snap up to the Indian standard sizes 450/600/900/1200/1500 mm.
* :func:`box_drain_dims` - trunks are box drains, not pipes.
* :func:`size_conduit` - the whole decision for one edge, returned as a :class:`ConduitSize`.
* :func:`beta_prior` / :func:`kappa_prior` - the honestly wide Beta priors Pulse later updates.

Nothing here is calibrated to observations. Every value it produces carries
``confidence = "inferred"`` when :mod:`varuna_city.drains` writes it out, and the UI labels the
whole layer "Drain graph inferred from roads and terrain".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

#: Standard Indian RCC pipe diameters, metres (blueprint 7.1).
STANDARD_DIAMETERS_M: Final[tuple[float, ...]] = (0.45, 0.60, 0.90, 1.20, 1.50)

#: Manning n for the inferred network: cast-in-place / RCC concrete conduit.
MANNING_N_CONCRETE: Final[float] = 0.013

#: Runoff coefficient endpoints. Fully pervious ground vs fully sealed surface.
C_PERVIOUS: Final[float] = 0.15
C_IMPERVIOUS: Final[float] = 0.90

#: Box drains are wider than they are deep; height snaps to this step.
BOX_ASPECT: Final[float] = 1.5
BOX_STEP_M: Final[float] = 0.25
BOX_MIN_HEIGHT_M: Final[float] = 0.60

#: Beta-prior means for the blockage fraction beta by land use (SPEC.md 10.1 step 7).
BETA_PRIOR_MEAN: Final[dict[str, float]] = {
    "market_informal": 0.35,
    "residential": 0.20,
    "arterial": 0.15,
}

#: Beta-prior concentration a+b. Small on purpose: the prior must be honestly wide, because
#: nobody has ever surveyed these pipes. a+b = 6 gives sd ~0.15 at mean 0.2, i.e. the prior
#: alone cannot tell a clean pipe from a half-blocked one - only Pulse's observations can.
BETA_PRIOR_CONCENTRATION: Final[float] = 6.0

#: Inlet clogging kappa: prior mean and concentration (SPEC.md 10.1 step 7).
KAPPA_PRIOR_MEAN: Final[float] = 0.25
KAPPA_PRIOR_CONCENTRATION: Final[float] = 6.0

#: Road classes treated as arterial corridors: wider drains, better maintained, BRIMSTOWAD-era.
ARTERIAL_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
    }
)

#: Narrow lanes: bazaars, gaothans and informal settlements, where blockage is worst.
MARKET_INFORMAL_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "service",
        "living_street",
        "pedestrian",
        "footway",
        "track",
        "unclassified",
        "path",
    }
)

Shape = Literal["circular", "box"]
LandUse = Literal["market_informal", "residential", "arterial"]


@dataclass(frozen=True, slots=True)
class ConduitSize:
    """One sized conduit. ``width_m``/``height_m`` are set for boxes, ``diameter_m`` for pipes."""

    shape: Shape
    diameter_m: float | None
    width_m: float | None
    height_m: float | None
    area_m2: float
    hydraulic_radius_m: float
    q_design_m3s: float
    q_full_m3s: float
    manning_n: float

    def as_dict(self) -> dict[str, float | str | None]:
        """Flat mapping for the edges table."""
        return {
            "shape": self.shape,
            "diameter_m": self.diameter_m,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "area_m2": round(self.area_m2, 4),
            "hydraulic_radius_m": round(self.hydraulic_radius_m, 4),
            "q_design_m3s": round(self.q_design_m3s, 5),
            "q_full_m3s": round(self.q_full_m3s, 5),
            "manning_n": self.manning_n,
        }


def runoff_coefficient(imperviousness: float) -> float:
    """Rational-method ``C``, linear in imperviousness between 0.15 and 0.90.

    Args:
        imperviousness: sealed fraction of the contributing area, 0-1.
    """
    imperv = min(1.0, max(0.0, float(imperviousness)))
    return C_PERVIOUS + (C_IMPERVIOUS - C_PERVIOUS) * imperv


def rational_method_q(area_m2: float, c: float, intensity_mm_h: float) -> float:
    """``Q = C i A`` in m3/s (blueprint 7.1, Appendix A).

    Args:
        area_m2: contributing area accumulated down the drain tree, m2.
        c: runoff coefficient, 0-1.
        intensity_mm_h: design rainfall intensity, mm/h (25 legacy, 50 upgraded corridors).
    """
    intensity_ms = float(intensity_mm_h) / 1000.0 / 3600.0
    return max(0.0, float(c)) * intensity_ms * max(0.0, float(area_m2))


def manning_diameter(q_m3s: float, slope: float, n: float = MANNING_N_CONCRETE) -> float:
    """Circular diameter that carries ``q_m3s`` flowing full under Manning, metres.

    Full-flow Manning for a circle gives ``Q = (pi / (4 * 4**(2/3) * n)) * D**(8/3) * S**0.5``,
    so the diameter is the 3/8 power of the flow. Inverted here rather than iterated so the
    result is exact and deterministic.
    """
    if q_m3s <= 0.0:
        return 0.0
    slope = max(float(slope), 1e-6)
    coefficient = math.pi / (4.0 * 4.0 ** (2.0 / 3.0))  # 0.31169...
    return (q_m3s * n / (coefficient * math.sqrt(slope))) ** (3.0 / 8.0)


def circular_q_full(diameter_m: float, slope: float, n: float = MANNING_N_CONCRETE) -> float:
    """Manning full-flow capacity of a circular pipe, m3/s (Appendix A ``Q_full``)."""
    if diameter_m <= 0.0:
        return 0.0
    area = math.pi * diameter_m**2 / 4.0
    r_h = diameter_m / 4.0
    return (1.0 / n) * area * r_h ** (2.0 / 3.0) * math.sqrt(max(float(slope), 1e-6))


def snap_diameter(diameter_m: float) -> float | None:
    """Snap up to the next standard size; ``None`` when the flow needs more than 1500 mm.

    A ``None`` is the signal to :func:`size_conduit` that this conduit must be a box drain.
    """
    for standard in STANDARD_DIAMETERS_M:
        if diameter_m <= standard + 1e-9:
            return standard
    return None


def box_q_full(
    width_m: float, height_m: float, slope: float, n: float = MANNING_N_CONCRETE
) -> float:
    """Manning full-flow capacity of a rectangular box drain, m3/s."""
    area = width_m * height_m
    perimeter = 2.0 * (width_m + height_m)
    if area <= 0.0 or perimeter <= 0.0:
        return 0.0
    r_h = area / perimeter
    return (1.0 / n) * area * r_h ** (2.0 / 3.0) * math.sqrt(max(float(slope), 1e-6))


def box_drain_dims(
    q_m3s: float,
    slope: float,
    n: float = MANNING_N_CONCRETE,
    *,
    aspect: float = BOX_ASPECT,
) -> tuple[float, float]:
    """Box-drain ``(width_m, height_m)`` for ``q_m3s``, snapped up to 0.25 m steps.

    Width is ``aspect`` times height, the usual shape of a Mumbai nullah box section.
    """
    if q_m3s <= 0.0:
        return (BOX_MIN_HEIGHT_M * aspect, BOX_MIN_HEIGHT_M)
    slope = max(float(slope), 1e-6)
    # Q = K h**(8/3) with K = (1/n) * aspect * (aspect / (2(aspect+1)))**(2/3) * sqrt(S)
    k = (1.0 / n) * aspect * (aspect / (2.0 * (aspect + 1.0))) ** (2.0 / 3.0) * math.sqrt(slope)
    height = (q_m3s / k) ** (3.0 / 8.0)
    height = max(BOX_MIN_HEIGHT_M, math.ceil(height / BOX_STEP_M - 1e-9) * BOX_STEP_M)
    width = math.ceil(aspect * height / BOX_STEP_M - 1e-9) * BOX_STEP_M
    return (round(width, 2), round(height, 2))


def size_conduit(
    *,
    area_m2: float,
    imperviousness: float,
    intensity_mm_h: float,
    slope: float,
    is_trunk: bool,
    n: float = MANNING_N_CONCRETE,
) -> ConduitSize:
    """Size one edge: rational method, then Manning at full flow, then snap.

    Trunks are box drains by construction (they run along nullahs and creeks); everything else
    is a circular pipe unless the design flow exceeds a 1500 mm pipe, in which case it becomes a
    box drain too.
    """
    c = runoff_coefficient(imperviousness)
    q_design = rational_method_q(area_m2, c, intensity_mm_h)
    slope = max(float(slope), 1e-6)

    diameter = None if is_trunk else snap_diameter(manning_diameter(q_design, slope, n))
    if diameter is None:
        width, height = box_drain_dims(q_design, slope, n)
        area = width * height
        r_h = area / (2.0 * (width + height))
        return ConduitSize(
            shape="box",
            diameter_m=None,
            width_m=width,
            height_m=height,
            area_m2=area,
            hydraulic_radius_m=r_h,
            q_design_m3s=q_design,
            q_full_m3s=box_q_full(width, height, slope, n),
            manning_n=n,
        )

    area = math.pi * diameter**2 / 4.0
    return ConduitSize(
        shape="circular",
        diameter_m=diameter,
        width_m=None,
        height_m=None,
        area_m2=area,
        hydraulic_radius_m=diameter / 4.0,
        q_design_m3s=q_design,
        q_full_m3s=circular_q_full(diameter, slope, n),
        manning_n=n,
    )


def land_use_class(road_class: str | None, *, imperviousness: float = 1.0) -> LandUse:
    """Land use of one edge, used only to pick its blockage prior.

    The rule, stated so it can be argued with: arterial corridors are swept and desilted before
    the monsoon, so their prior blockage is lowest; narrow service lanes, bazaars and informal
    settlements silt up worst; everything else is residential. Edges outside built-up land
    (imperviousness below 0.3) are treated as residential rather than informal - open ground is
    not a bazaar.
    """
    cls = (road_class or "").strip().lower()
    if cls in ARTERIAL_CLASSES:
        return "arterial"
    if cls in MARKET_INFORMAL_CLASSES and imperviousness >= 0.3:
        return "market_informal"
    return "residential"


def beta_prior(
    land_use: str, *, concentration: float = BETA_PRIOR_CONCENTRATION
) -> tuple[float, float]:
    """``(a, b)`` of the Beta prior on the blockage fraction beta for a land-use class.

    The mean is ``a / (a + b)`` and comes from :data:`BETA_PRIOR_MEAN`; the concentration is
    deliberately small so the prior is wide (sd ~0.15) and Pulse's evidence dominates quickly.
    """
    mean = BETA_PRIOR_MEAN.get(land_use, BETA_PRIOR_MEAN["residential"])
    a = mean * concentration
    b = (1.0 - mean) * concentration
    return (round(a, 6), round(b, 6))


def kappa_prior(
    *, mean: float = KAPPA_PRIOR_MEAN, concentration: float = KAPPA_PRIOR_CONCENTRATION
) -> tuple[float, float]:
    """``(a, b)`` of the Beta prior on inlet clogging kappa (mean 0.25 by default)."""
    return (round(mean * concentration, 6), round((1.0 - mean) * concentration, 6))


def beta_sd(a: float, b: float) -> float:
    """Standard deviation of ``Beta(a, b)`` - what the UI shows as ``beta +- sd``."""
    total = a + b
    if total <= 0.0:
        return 0.0
    return math.sqrt(a * b / (total * total * (total + 1.0)))


__all__ = [
    "ARTERIAL_CLASSES",
    "BETA_PRIOR_CONCENTRATION",
    "BETA_PRIOR_MEAN",
    "BOX_ASPECT",
    "C_IMPERVIOUS",
    "C_PERVIOUS",
    "KAPPA_PRIOR_MEAN",
    "MANNING_N_CONCRETE",
    "MARKET_INFORMAL_CLASSES",
    "STANDARD_DIAMETERS_M",
    "ConduitSize",
    "LandUse",
    "Shape",
    "beta_prior",
    "beta_sd",
    "box_drain_dims",
    "box_q_full",
    "circular_q_full",
    "kappa_prior",
    "land_use_class",
    "manning_diameter",
    "rational_method_q",
    "runoff_coefficient",
    "size_conduit",
    "snap_diameter",
]
