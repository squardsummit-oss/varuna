"""Learning the city's hidden drains from every flood it sees (SPEC.md 11.6, task P7.3).

This is the idea the whole project is built around. Mumbai has no reliable GIS of its stormwater
network and no sensors in it, so VARUNA infers the graph from roads and terrain and then treats
the one thing it cannot infer - how blocked each pipe is - as a random variable it *learns*.
Every flood the city has is an experiment: the streets that stopped moving and the people who
reported water are measurements of where the drainage failed.

**The state** is ``theta = logit(beta)`` per edge, not ``beta`` itself. Blockage lives in [0, 1]
and an ensemble Kalman filter is a Gaussian method; updating a bounded variable with Gaussian
increments walks members straight out of the interval. In logit space the update is unbounded and
the inverse transform puts it back inside, which is the standard fix and the reason SPEC.md's
equation sheet writes the update over logit beta.

**The update** is Appendix A's perturbed-observation Kalman update::

    theta^a = theta^f + P_theta_y (P_yy + R)^-1 (y + eps - H(theta^f))

applied as an ensemble smoother with multiple data assimilation (ES-MDA, Emerick & Reynolds
2013): the same batch is assimilated :data:`MDA_STEPS` times, each time with the observation
error variance inflated by ``MDA_STEPS`` and ``H`` re-evaluated on the updated members. Because
the inflation factors' reciprocals sum to one, a linear-Gaussian problem gets exactly the
single-step answer; a non-linear one - and ``H`` here is non-linear twice over, through the
sigmoid and through the ``max(0, .)`` of the capacity deficit - gets the posterior a single step
linearised around the prior cannot reach. Perturbed observations (``eps``, drawn afresh every
step with the inflated variance) are kept, as 11.6 and Appendix A write them; they are what keep
the posterior spread honest rather than collapsing it.

Why not the single stochastic step, measured on 11.6's own acceptance fixture (two blocked pipes
in 200, twenty observations): against an exact grid-Bayes posterior it under-cut the blocked
pipes' spread by a median 0.115, clearing the 40 % floor at 49 of 100 (observation seed, ensemble
seed) pairs. ES-MDA x4 lands within 0.03 of the exact cut and 0.02 of the exact mean (median over
ten ensemble seeds, at three observation draws; one seed alone can be 0.035 off the mean). More
members does not substitute: 200
members in one step measured a median cut of 0.377. The prior ensemble is also moment-matched -
the standard-normal draw is standardised per edge to zero mean and unit sd before scaling - so the
filter starts from the prior it was given rather than from 50 samples of it.

**Localisation** is by hydraulic hop distance, not by metres. Two pipes a hundred metres apart
with no hydraulic connection have nothing to say about each other, while a trunk two kilometres
downstream absolutely does. An observation may move a pipe within 3 hops (SPEC.md 11.6) and
the influence tapers to zero at the edge of that neighbourhood, so nothing updates discontinuously
as a pipe crosses the boundary.

**The observation operator** is deliberately injectable. SPEC.md 11.6 runs `drain1d` per member
with the surface inflows frozen and reads depth through the Flash-lite emulator; until Flash is
fitted, :func:`capacity_operator` below is used, which maps blockage to the depth the segment is
expected to hold through the capacity deficit it implies. It is the same shape of function and
the filter does not know the difference; what it is, is recorded in the product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.pulse.enkf")

__all__ = [
    "ENSEMBLE_SIZE",
    "LOCALISATION_HOPS",
    "MDA_STEPS",
    "EnkfResult",
    "assimilate",
    "capacity_operator",
    "logit",
    "sigmoid",
]

ENSEMBLE_SIZE = 50
"""Members in the parameter ensemble (SPEC.md 11.6).

Fifty is small for a Kalman filter and large enough here because the state is a few hundred
localised pipes rather than a full field. The localisation below is what makes that work: it is
also the standard remedy for the spurious long-range correlations a small ensemble invents."""

LOCALISATION_HOPS = 3
"""How far an observation may reach through the graph (SPEC.md 11.6)."""

MDA_STEPS = 4
"""Passes of the ES-MDA update per batch, each with the observation variance times this number.

Four, because it is the smallest count that closed the gap to the exact posterior on the 11.6
fixture (see the module docstring); each pass costs one more evaluation of ``H`` over the
ensemble, which for the analytic operator is milliseconds."""

INFLATION = 1.02
"""Multiplicative spread inflation applied once to the prior draw, before the update passes.

Ensemble filters lose variance every cycle - the update is a contraction - and a filter that has
convinced itself stops learning. Two percent per cycle is enough to hold the spread open across
a replay without the posterior wandering."""

MIN_SD = 0.02
"""Floor on the posterior standard deviation of beta, in blockage units.

VARUNA never observes a pipe directly. Claiming to know a blockage to better than two points
would be a claim the data cannot support, however many consistent reports arrive."""


def logit(p: NDArray[np.floating]) -> NDArray[np.floating]:
    """``log(p / (1 - p))``, clipped off the asymptotes so a certain pipe stays finite."""
    q = np.clip(np.asarray(p, dtype=np.float64), 1e-4, 1.0 - 1e-4)
    return np.log(q / (1.0 - q))


def sigmoid(x: NDArray[np.floating]) -> NDArray[np.floating]:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=np.float64)))


class ObservationOperator(Protocol):
    """``H(theta)``: the depths in cm an ensemble of blockages implies at the observed places."""

    def __call__(self, beta: NDArray[np.floating]) -> NDArray[np.floating]: ...


@dataclass(frozen=True, slots=True)
class EnkfResult:
    """The posterior, and enough of the working to explain it on screen."""

    beta_mean: NDArray[np.floating]
    beta_sd: NDArray[np.floating]
    prior_mean: NDArray[np.floating]
    prior_sd: NDArray[np.floating]
    innovation: NDArray[np.floating]
    """``y - H(theta^f)`` per observation, in cm: what the model got wrong before the update."""

    updated_edges: NDArray[np.integer]
    operator: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def delta(self) -> NDArray[np.floating]:
        """Posterior minus prior blockage, per edge."""
        return self.beta_mean - self.prior_mean


def capacity_operator(
    edge_of_observation: NDArray[np.integer],
    contributing_area_m2: NDArray[np.floating],
    rain_mm_h: NDArray[np.floating],
    *,
    ponding_area_m2: NDArray[np.floating],
    q_full_m3s: NDArray[np.floating],
    window_s: float = 1800.0,
) -> ObservationOperator:
    """Depth implied by the capacity a blocked pipe no longer has.

    The reduced stand-in for SPEC.md 11.6's "run drain1d per member" (see the module docstring).
    Its physics is a volume balance over a window: the catchment delivers
    ``area * rain``, the pipe removes ``(1 - beta) * Q_full``, and whatever is left ponds over the
    junction::

        depth = max(0, (Q_in - (1 - beta) * Q_full) * window) / ponding_area

    It is monotone in beta and roughly linear where it matters, which is what the Kalman gain
    needs; it is not a hydraulic model, and the product records ``operator="capacity_deficit"``
    so nothing downstream can mistake it for one.
    """
    inflow_m3s = contributing_area_m2 * (rain_mm_h / 1000.0 / 3600.0)

    def operator(beta: NDArray[np.floating]) -> NDArray[np.floating]:
        # beta is (members, edges); pick the edge each observation is about.
        blocked = np.asarray(beta)[:, edge_of_observation]
        excess = inflow_m3s[None, :] - (1.0 - blocked) * q_full_m3s[None, :]
        volume = np.maximum(excess, 0.0) * window_s
        return volume / np.maximum(ponding_area_m2[None, :], 1.0) * 100.0

    return operator


def _localisation(
    hops: NDArray[np.integer], max_hops: int = LOCALISATION_HOPS
) -> NDArray[np.floating]:
    """Taper from 1 at the observed edge to 0 beyond ``max_hops``.

    Linear rather than the usual Gaspari-Cohn: hop distance is an integer over a graph with three
    useful values, and a fifth-order polynomial over {0, 1, 2, 3} is precision the input does not
    have.
    """
    weight = 1.0 - np.asarray(hops, dtype=np.float64) / (max_hops + 1.0)
    return np.clip(np.where(np.asarray(hops) < 0, 0.0, weight), 0.0, 1.0)


def assimilate(
    beta_prior_mean: NDArray[np.floating],
    beta_prior_sd: NDArray[np.floating],
    observations_cm: NDArray[np.floating],
    observation_sd_cm: NDArray[np.floating],
    operator: ObservationOperator,
    hops: NDArray[np.integer],
    *,
    n_members: int = ENSEMBLE_SIZE,
    seed: int = 2019,
    operator_name: str = "capacity_deficit",
) -> EnkfResult:
    """One ES-MDA update of blockage from a batch of depth observations.

    ``MDA_STEPS`` perturbed-observation Kalman passes with the observation variance inflated by
    ``MDA_STEPS``; ``innovation`` in the result is measured against the prior ensemble, before
    the first pass.

    Args:
        beta_prior_mean, beta_prior_sd: the current posterior, per edge, in blockage units.
        observations_cm: measured depth at each observation.
        observation_sd_cm: its standard deviation - the traffic prior's 8 cm, a report's 12.
        operator: ``H``; takes ``(members, edges)`` blockage, returns ``(members, obs)`` depth.
        hops: ``(obs, edges)`` hydraulic hop distance, negative where unreachable.
        n_members: ensemble size.
        seed: the run is deterministic (rule 8), so the draws are seeded.
    """
    rng = np.random.default_rng(seed)
    n_edges = int(np.size(beta_prior_mean))
    n_obs = int(np.size(observations_cm))
    notes: list[str] = []

    if n_obs == 0:
        notes.append("No observations this cycle; the posterior is the prior, untouched.")
        return EnkfResult(
            beta_mean=np.array(beta_prior_mean, dtype=np.float64),
            beta_sd=np.array(beta_prior_sd, dtype=np.float64),
            prior_mean=np.array(beta_prior_mean, dtype=np.float64),
            prior_sd=np.array(beta_prior_sd, dtype=np.float64),
            innovation=np.zeros(0),
            updated_edges=np.zeros(0, dtype=np.int64),
            operator=operator_name,
            notes=tuple(notes),
        )

    # Draw the prior ensemble in logit space, so every member is a valid blockage.
    theta_mean = logit(beta_prior_mean)
    # d(logit)/d(beta) = 1 / (beta (1 - beta)): the prior sd is carried across the transform
    # rather than reinvented, so a pipe the city pipeline was unsure about stays unsure.
    slope = 1.0 / np.clip(beta_prior_mean * (1.0 - beta_prior_mean), 1e-3, None)
    theta_sd = np.asarray(beta_prior_sd, dtype=np.float64) * slope * INFLATION
    # Moment-matched: each edge's 50 draws are standardised to exactly zero mean and unit sd, so
    # the ensemble *is* the prior's first two moments instead of a noisy estimate of them. The
    # update then spends none of its accuracy correcting a prior that was drawn slightly wrong.
    draw = rng.normal(size=(n_members, n_edges))
    if n_members > 1:
        draw = (draw - draw.mean(axis=0, keepdims=True)) / np.clip(
            draw.std(axis=0, ddof=1, keepdims=True), 1e-12, None
        )
    theta = theta_mean[None, :] + draw * theta_sd[None, :]

    y = np.asarray(observations_cm, dtype=np.float64)
    r = np.asarray(observation_sd_cm, dtype=np.float64) ** 2
    denominator = max(n_members - 1, 1)

    # Localisation: an observation may only move pipes near it in the graph.
    taper = _localisation(hops)  # (obs, edges)

    innovation = np.zeros(n_obs)
    singular = False
    for step in range(MDA_STEPS):
        # ES-MDA: the batch is assimilated MDA_STEPS times with R inflated by MDA_STEPS, so the
        # passes together weigh the observations exactly once.
        r_step = r * MDA_STEPS
        predicted = np.asarray(operator(sigmoid(theta)), dtype=np.float64)  # (members, obs)
        if step == 0:
            innovation = y - predicted.mean(axis=0)

        theta_anomaly = theta - theta.mean(axis=0, keepdims=True)
        y_anomaly = predicted - predicted.mean(axis=0, keepdims=True)

        # P_yy + R, with the (inflated) observation error on the diagonal.
        pyy = y_anomaly.T @ y_anomaly / denominator + np.diag(r_step)
        # A ridge, because a batch of observations at one junction makes P_yy singular and the
        # ensemble's own rank is only n_members - 1 anyway.
        pyy += np.eye(n_obs) * 1e-6
        ptheta_y = theta_anomaly.T @ y_anomaly / denominator  # (edges, obs)

        try:
            gain = np.linalg.solve(pyy.T, ptheta_y.T).T
        except np.linalg.LinAlgError:  # pragma: no cover - the ridge makes this very unlikely
            gain = ptheta_y @ np.linalg.pinv(pyy)
            singular = True
        gain = gain * taper.T

        # Perturbed observations, y + eps, drawn afresh each pass with the inflated variance.
        perturbed = y[None, :] + rng.normal(size=(n_members, n_obs)) * np.sqrt(r_step)[None, :]
        theta = theta + (perturbed - predicted) @ gain.T

    if singular:  # pragma: no cover
        notes.append("Innovation covariance was singular; the gain used a pseudo-inverse.")

    beta_posterior = sigmoid(theta)

    # Localisation, enforced rather than merely weighted. Tapering the gain to zero leaves an
    # unreachable edge's *theta* untouched, but its posterior beta is still the mean of a fresh
    # 50-member draw, which differs from the prior by sampling noise - about two blockage points
    # here. That noise is precisely what localisation exists to keep out, so an edge no
    # observation reaches keeps the prior it came in with, exactly.
    reachable = (taper > 0.0).any(axis=0)
    beta_mean = np.where(reachable, beta_posterior.mean(axis=0), beta_prior_mean)
    beta_sd = np.where(reachable, np.maximum(beta_posterior.std(axis=0), MIN_SD), beta_prior_sd)

    touched = np.flatnonzero(np.abs(beta_mean - beta_prior_mean) > 1e-4)

    log.info(
        "pulse.enkf",
        observations=n_obs,
        members=n_members,
        mda_steps=MDA_STEPS,
        edges_updated=int(touched.size),
        edges_reachable=int(reachable.sum()),
        mean_innovation_cm=round(float(np.mean(np.abs(innovation))), 2),
        mean_delta_beta=round(float(np.mean(beta_mean - beta_prior_mean)), 4),
        operator=operator_name,
    )
    notes.append(
        f"{n_obs} observations moved {touched.size} pipes; the update is ES-MDA with perturbed "
        f"observations ({MDA_STEPS} passes, {n_members} members, moment-matched prior draw); "
        f"the observation operator is {operator_name}."
    )
    return EnkfResult(
        beta_mean=beta_mean,
        beta_sd=beta_sd,
        prior_mean=np.array(beta_prior_mean, dtype=np.float64),
        prior_sd=np.array(beta_prior_sd, dtype=np.float64),
        innovation=innovation,
        updated_edges=touched,
        operator=operator_name,
        notes=tuple(notes),
    )


def hop_distances(
    from_node: NDArray[np.integer],
    to_node: NDArray[np.integer],
    seed_edges: NDArray[np.integer],
    max_hops: int = LOCALISATION_HOPS,
) -> NDArray[np.integer]:
    """Hydraulic hop distance from each seed edge to every edge, ``-1`` beyond ``max_hops``.

    Undirected, because a blockage propagates its effect both ways: a blocked pipe backs water
    up into what drains through it and starves what it drains into.
    """
    n_edges = int(np.size(from_node))
    adjacency: dict[int, list[int]] = {}
    for edge in range(n_edges):
        for node in (int(from_node[edge]), int(to_node[edge])):
            adjacency.setdefault(node, []).append(edge)

    out = np.full((len(seed_edges), n_edges), -1, dtype=np.int32)
    for row, seed_edge in enumerate(seed_edges):
        seed_edge = int(seed_edge)
        if seed_edge < 0 or seed_edge >= n_edges:
            continue
        out[row, seed_edge] = 0
        frontier = [seed_edge]
        for hop in range(1, max_hops + 1):
            nxt: list[int] = []
            for edge in frontier:
                for node in (int(from_node[edge]), int(to_node[edge])):
                    for neighbour in adjacency.get(node, ()):
                        if out[row, neighbour] < 0:
                            out[row, neighbour] = hop
                            nxt.append(neighbour)
            frontier = nxt
            if not frontier:
                break
    return out
