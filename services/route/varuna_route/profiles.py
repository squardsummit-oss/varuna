"""Vehicle profiles: the depth at which each one stops (SPEC.md 11.8, Appendix A).

The thresholds are the depth ramp of SPEC.md 6.2, which is the same set of numbers the map
colours by and the alert levels raise at. That is deliberate: an operator who has learnt that
orange means "cars stop" should not have to learn a second scale to read a route.

Risk tolerance is the probability a driver will accept of meeting more than their threshold.
An ambulance's 0.2 is not caution, it is the opposite - it means the ambulance refuses a street
that has even a one-in-five chance of stopping it, because an ambulance stopped in water is a
second emergency. A car's 0.5 is an ordinary driver's coin flip.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["HAZARD_M2_S", "PROFILES", "Profile", "hazard_unsafe", "profile"]

HAZARD_M2_S = 0.5
"""Depth times flow speed, in m^2/s, at which moving water knocks a person over (Appendix A).

The other half of the pedestrian rule, ``h >= 0.3 m``, is the pedestrian profile's own
``depth_cm`` and is applied by the router the way every other profile's threshold is."""


@dataclass(frozen=True, slots=True)
class Profile:
    """One vehicle's rules for water."""

    key: str
    label: str
    depth_cm: float
    """Depth at which this vehicle can no longer pass."""

    risk_tolerance: float
    """Probability of exceeding ``depth_cm`` that the route will accept on an edge."""

    speed_scale: float
    """Multiplier on the road's free-flow speed: an ambulance is quicker than a bus."""

    hazard_rule: bool = False
    """Pedestrians also fail on the ``h * v >= 0.5 m^2/s`` hazard product (Appendix A)."""


PROFILES: dict[str, Profile] = {
    # 15 cm: `--depth-2` begins, the ramp's "two-wheelers impassable".
    "two_wheeler": Profile("two_wheeler", "Two-wheeler", 15.0, 0.5, 1.0),
    # 30 cm: `--depth-3`, "cars impassable".
    "car": Profile("car", "Car", 30.0, 0.5, 1.0),
    # 45 cm: `--depth-4`, "buses and trucks impassable".
    "bus": Profile("bus", "Bus", 45.0, 0.4, 0.8),
    "truck": Profile("truck", "Truck", 45.0, 0.4, 0.8),
    # 60 cm: `--depth-5`, "rescue vehicles only" - and this is the rescue vehicle.
    "ambulance": Profile("ambulance", "Ambulance", 60.0, 0.2, 1.15),
    "fire_tender": Profile("fire_tender", "Fire tender", 60.0, 0.2, 1.0),
    # A person is stopped by 30 cm, and by less when it moves (the hazard product below).
    "pedestrian": Profile("pedestrian", "Pedestrian", 30.0, 0.3, 0.06, hazard_rule=True),
}
"""Every profile SPEC.md 7.4 puts in the picker, keyed by the value the API takes."""


def hazard_unsafe(depth_cm: float, velocity_ms: float | None) -> bool:
    """The velocity half of the pedestrian rule: ``h * v >= 0.5 m^2/s`` (Appendix A).

    ``velocity_ms`` of None means the run did not say how fast the water moves. That is answered
    as *not unsafe on this half*, never as an assumed speed: the depth half still stops the
    walker at 30 cm, and the response says which half was applied.
    """
    if velocity_ms is None or depth_cm <= 0.0:
        return False
    return (depth_cm / 100.0) * abs(velocity_ms) >= HAZARD_M2_S


def profile(key: str) -> Profile:
    """Look one up, raising :class:`KeyError` with the valid set when it is not one."""
    try:
        return PROFILES[key]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        msg = f"Unknown vehicle profile {key!r}. Valid profiles: {valid}."
        raise KeyError(msg) from None
