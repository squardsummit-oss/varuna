"""Can Pulse find a blocked pipe it was not told about? (SPEC.md 11.6, task P7.3)

The spec's acceptance test: on a synthetic truth with two blocked pipes and twenty observations,
the posterior mean must rank those two in the top five and cut their spread by at least 40 %.
That is the whole claim of the engine - the city reveals its own drains - so it is tested against
a truth the filter never sees.

The two halves are asserted separately, and both are scored across the filter's own ensemble
seeds rather than at one of them. Under the single-step stochastic EnKF the ranking held at every
seed and the spread cut was a coin flip - median 33.2 % at the committed observation draw, 49 of
100 seed pairs over the floor - so the spread test carried ``xfail(strict=True)``. The update is
ES-MDA with perturbed observations now (``varuna_pulse.enkf``), and the spread half is also
checked against an exact grid-Bayes posterior, so a pass means the filter found the right answer
rather than a lucky one. ``docs/QA.md`` records the sweep.
"""

from __future__ import annotations

import numpy as np
import pytest
from varuna_pulse.enkf import (
    INFLATION,
    assimilate,
    capacity_operator,
    hop_distances,
    logit,
    sigmoid,
)

N_EDGES = 200
BLOCKED = (37, 123)
"""The two pipes the synthetic city has actually blocked. The filter is never told."""

ENSEMBLE_SEEDS = tuple(range(2019, 2029))
"""The filter's own draw, which the engine defaults to 2019 and 11.6 says nothing about.

The acceptance test used to run at that one seed. Sweeping it is how the two halves of 11.6
turned out to be different claims: the ranking survives every seed, the spread cut does not.
"""


def _network() -> tuple[np.ndarray, np.ndarray]:
    """A chain of 200 pipes: edge k joins node k to node k+1.

    Long enough that three hops is a genuine neighbourhood rather than the whole network - on a
    short chain every edge is within reach of some observation, localisation cannot isolate
    anything, and a 50-member ensemble's spurious correlations move the lot.
    """
    return np.arange(N_EDGES, dtype=np.int64), np.arange(1, N_EDGES + 1, dtype=np.int64)


def _truth() -> np.ndarray:
    beta = np.full(N_EDGES, 0.20)
    for edge in BLOCKED:
        beta[edge] = 0.85
    return beta


def _setup(n_obs: int = 20, seed: int = 7):
    """Twenty observations, most on the blocked pipes and the rest spread over clean ones."""
    rng = np.random.default_rng(seed)
    from_node, to_node = _network()

    # Observations sit on edges: ten on each blocked pipe's immediate neighbourhood, the rest
    # scattered, which is what a traffic feed actually gives you - lots of normal streets and a
    # few that stopped.
    # Eight anomalies on each blocked junction and four scattered over clean pipes. A flooded
    # junction does not produce one observation, it produces every vehicle that stopped there.
    watched = list(BLOCKED) * 8 + list(rng.choice(N_EDGES, size=n_obs - 16, replace=False))
    edge_of_observation = np.array(watched[:n_obs], dtype=np.int64)

    # A junction whose pipe is overwhelmed: 30,000 m2 draining through one 0.35 m3/s pipe under
    # 80 mm/h, ponding over about 1,500 m2 - a junction some 40 m across. Blockage then moves the
    # depth by about 42 cm across its range, against an 8 cm observation error: one anomaly still
    # proves nothing, and eight of them are worth listening to. That ratio is the point of the
    # test - a prior tighter than the observations cannot be sharpened by them, however many
    # arrive, and the filter should not pretend otherwise.
    contributing = np.full(n_obs, 30_000.0)
    rain = np.full(n_obs, 80.0)
    ponding = np.full(n_obs, 1_500.0)
    q_full = np.full(n_obs, 0.35)

    operator = capacity_operator(
        edge_of_observation,
        contributing,
        rain,
        ponding_area_m2=ponding,
        q_full_m3s=q_full,
    )

    # The measurements the synthetic city produces, plus observation noise.
    truth_ensemble = _truth()[None, :]
    y = np.asarray(operator(truth_ensemble))[0]
    sd = np.full(n_obs, 8.0)
    y = y + rng.normal(size=n_obs) * sd

    hops = hop_distances(from_node, to_node, edge_of_observation)
    return operator, y, sd, hops, edge_of_observation


@pytest.mark.parametrize("ensemble_seed", ENSEMBLE_SEEDS)
def test_the_posterior_finds_the_blocked_pipes(ensemble_seed: int) -> None:
    """11.6's first half: the two blocked pipes reach the top five of the posterior mean.

    Parametrised over ten ensemble seeds because the filter's own draw is a free parameter the
    engine picks (``assimilate(seed=2019)``), not a property of the city: a claim that holds at
    one seed is a claim about that seed. This half holds at every one of them, and at all 100
    (observation draw, ensemble seed) pairs of the sweep recorded in ``docs/QA.md``.
    """
    operator, y, sd, hops, _edges = _setup()

    prior_mean = np.full(N_EDGES, 0.20)
    prior_sd = np.full(N_EDGES, 0.12)

    result = assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=ensemble_seed)

    ranked = np.argsort(-result.beta_mean)[:5]
    for edge in BLOCKED:
        assert edge in ranked, (
            f"pipe {edge} is blocked at 0.85 and did not reach the top five at ensemble seed "
            f"{ensemble_seed}; posterior ranks {ranked.tolist()}"
        )


PRIOR_BETA = 0.20
PRIOR_SD = 0.12


def _worst_cuts(obs_seed: int) -> np.ndarray:
    """The worse blocked pipe's spread cut, ``1 - sd_post / sd_prior``, at every ensemble seed.

    ``np.min`` over the two pipes because 11.6 asks it of both; one value per ensemble seed.
    """
    operator, y, sd, hops, _edges = _setup(seed=obs_seed)
    prior_mean = np.full(N_EDGES, PRIOR_BETA)
    prior_sd = np.full(N_EDGES, PRIOR_SD)
    return np.array(
        [
            np.min(
                1.0
                - assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=seed).beta_sd[
                    list(BLOCKED)
                ]
                / PRIOR_SD
            )
            for seed in ENSEMBLE_SEEDS
        ]
    )


def test_the_observations_cut_the_blocked_pipes_spread() -> None:
    """11.6's second half: the observations cut the blocked pipes' spread by at least 40 %.

    Scored on the median across the ten ensemble seeds of the worse of the two pipes, since it
    is ``np.all`` in the spec's wording - both pipes must clear the floor - and a single seed's
    score is noise. Under the single-step EnKF this median was 33.2 % and the test shipped
    ``xfail(strict=True)``; the 40 % floor was never widened, the update was fixed.

    The assertion is 0.45, not 0.40, at the committed observation draw only because that is
    where this module's done-when put it - measured 0.498 (range 0.406-0.538) - and a regression
    to the old single-step behaviour would land near 0.33, well under either.
    """
    worst_pipe = _worst_cuts(obs_seed=7)
    median = float(np.median(worst_pipe))
    print(
        f"\nspread cut on the worse blocked pipe, {len(ENSEMBLE_SEEDS)} ensemble seeds: "
        f"median {median * 100:.1f} % (range {worst_pipe.min() * 100:.1f}-"
        f"{worst_pipe.max() * 100:.1f} %); 11.6 asks for 40 %"
    )
    assert median >= 0.40, (
        f"observations must cut the blocked pipes' spread by 40 %; the median across "
        f"{len(ENSEMBLE_SEEDS)} ensemble seeds is {median * 100:.1f} %, "
        f"per seed {np.round(worst_pipe, 4).tolist()}"
    )
    assert median >= 0.45, (
        f"the median cut at the committed observation draw was measured at 49.8 % under ES-MDA "
        f"and is {median * 100:.1f} % now; below 45 % the update has regressed toward the "
        f"single-step EnKF's 33 %"
    )


@pytest.mark.parametrize("obs_seed", range(10))
def test_the_spread_cut_holds_for_other_observation_draws(obs_seed: int) -> None:
    """The same 40 % floor, not widened, at ten other draws of the synthetic city's noise.

    The committed draw is one sample of the observation noise too. The median over ensemble
    seeds is asserted at each observation draw; measured 0.473-0.564 across these ten.
    """
    worst_pipe = _worst_cuts(obs_seed)
    median = float(np.median(worst_pipe))
    assert median >= 0.40, (
        f"observation draw {obs_seed}: median spread cut {median * 100:.1f} % across "
        f"{len(ENSEMBLE_SEEDS)} ensemble seeds, per seed {np.round(worst_pipe, 4).tolist()}; "
        f"11.6 asks for 40 %"
    )


def _exact_posterior(obs_seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Grid-Bayes posterior mean and spread cut for each blocked pipe, from its own observations.

    4,001 points on beta, the prior the filter draws from (normal in logit space with the same
    inflated sd, carried back through the Jacobian), and the Gaussian likelihood of the
    observations that sit on that pipe. Every other pipe is held at the prior mean, which is
    exact here: the operator reads only the observed edge, and each blocked pipe's observations
    touch no other blocked pipe.
    """
    operator, y, sd, _hops, edges = _setup(seed=obs_seed)
    grid = np.linspace(1e-4, 1.0 - 1e-4, 4001)
    theta_mean = float(logit(np.array([PRIOR_BETA]))[0])
    theta_sd = PRIOR_SD / (PRIOR_BETA * (1.0 - PRIOR_BETA)) * INFLATION
    prior = np.exp(-0.5 * ((logit(grid) - theta_mean) / theta_sd) ** 2) / (grid * (1.0 - grid))
    means, cuts = [], []
    for pipe in BLOCKED:
        beta = np.full((grid.size, N_EDGES), PRIOR_BETA)
        beta[:, pipe] = grid
        predicted = np.asarray(operator(beta))
        own = edges == pipe
        loglik = -0.5 * (((y[own][None, :] - predicted[:, own]) / sd[own][None, :]) ** 2).sum(1)
        weight = prior * np.exp(loglik - loglik.max())
        weight /= weight.sum()
        mean = float((weight * grid).sum())
        means.append(mean)
        cuts.append(1.0 - float(np.sqrt((weight * (grid - mean) ** 2).sum())) / PRIOR_SD)
    return np.array(means), np.array(cuts)


@pytest.mark.parametrize("obs_seed", (7, 0, 3))
def test_the_posterior_agrees_with_exact_bayes(obs_seed: int) -> None:
    """A pass must be the right answer, not merely a narrow one.

    A filter that collapsed its ensemble would clear the 40 % floor trivially. So the blocked
    pipes' posterior - the median over ensemble seeds, as above - is compared with the exact
    one-dimensional posterior: mean within 0.03 blockage, spread cut within 0.05. Measured at
    the committed draw: exact mean 0.690 / 0.728 and cut 0.505 / 0.544. The single-step EnKF
    missed the exact cut by a median 0.115 and would fail this.
    """
    operator, y, sd, hops, _edges = _setup(seed=obs_seed)
    prior_mean = np.full(N_EDGES, PRIOR_BETA)
    prior_sd = np.full(N_EDGES, PRIOR_SD)
    runs = [
        assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=seed)
        for seed in ENSEMBLE_SEEDS
    ]
    mean = np.median([r.beta_mean[list(BLOCKED)] for r in runs], axis=0)
    cut = np.median([1.0 - r.beta_sd[list(BLOCKED)] / PRIOR_SD for r in runs], axis=0)

    exact_mean, exact_cut = _exact_posterior(obs_seed)
    print(
        f"\nobservation draw {obs_seed}: ES-MDA mean {np.round(mean, 3).tolist()} cut "
        f"{np.round(cut, 3).tolist()}; exact mean {np.round(exact_mean, 3).tolist()} cut "
        f"{np.round(exact_cut, 3).tolist()}"
    )
    assert np.all(np.abs(mean - exact_mean) <= 0.03), (
        f"posterior mean {mean.tolist()} is more than 0.03 from exact {exact_mean.tolist()}"
    )
    assert np.all(np.abs(cut - exact_cut) <= 0.05), (
        f"spread cut {cut.tolist()} is more than 0.05 from exact {exact_cut.tolist()}"
    )


def test_no_observation_moves_a_pipe_beyond_the_localisation_radius() -> None:
    """SPEC.md 11.6: an observation may reach three hydraulic hops, and no further."""
    operator, y, sd, hops, edges = _setup()
    prior_mean = np.full(N_EDGES, 0.20)
    prior_sd = np.full(N_EDGES, 0.12)

    result = assimilate(prior_mean, prior_sd, y, sd, operator, hops)

    reachable = (hops >= 0).any(axis=0)
    moved = np.abs(result.beta_mean - prior_mean) > 1e-4
    assert not np.any(moved & ~reachable), (
        "a pipe more than three hops from every observation was updated"
    )
    assert np.any(moved & reachable), "the filter did not update anything at all"
    assert set(edges.tolist()).issubset(set(np.flatnonzero(reachable).tolist()))


def test_an_empty_batch_leaves_the_posterior_exactly_as_it_was() -> None:
    """A quiet cycle must not perturb what earlier cycles learned."""
    prior_mean = np.linspace(0.1, 0.6, N_EDGES)
    prior_sd = np.full(N_EDGES, 0.1)
    result = assimilate(
        prior_mean,
        prior_sd,
        np.zeros(0),
        np.zeros(0),
        capacity_operator(
            np.zeros(0, dtype=np.int64),
            np.zeros(0),
            np.zeros(0),
            ponding_area_m2=np.zeros(0),
            q_full_m3s=np.zeros(0),
        ),
        np.zeros((0, N_EDGES), dtype=np.int32),
    )
    assert np.array_equal(result.beta_mean, prior_mean)
    assert np.array_equal(result.beta_sd, prior_sd)


def test_the_posterior_stays_a_blockage() -> None:
    """logit/sigmoid round trip: no member may leave [0, 1], however hard the update pushes."""
    operator, y, sd, hops, _edges = _setup()
    # A prior that is nearly certain the pipes are clean, against observations screaming water:
    # the largest innovation the filter can be handed.
    prior_mean = np.full(N_EDGES, 0.02)
    prior_sd = np.full(N_EDGES, 0.30)

    result = assimilate(prior_mean, prior_sd, y * 4.0, sd, operator, hops)

    assert np.all(result.beta_mean >= 0.0) and np.all(result.beta_mean <= 1.0)
    assert np.all(np.isfinite(result.beta_sd))


def test_it_is_deterministic_for_a_seed() -> None:
    """Rule 8: two runs on the same inputs give the same posterior, byte for byte."""
    operator, y, sd, hops, _edges = _setup()
    prior_mean = np.full(N_EDGES, 0.20)
    prior_sd = np.full(N_EDGES, 0.12)

    first = assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=2019)
    second = assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=2019)
    assert np.array_equal(first.beta_mean, second.beta_mean)

    other = assimilate(prior_mean, prior_sd, y, sd, operator, hops, seed=7)
    assert not np.array_equal(first.beta_mean, other.beta_mean), (
        "a different seed must draw a different ensemble, or the seed is not being used"
    )


def test_logit_and_sigmoid_invert_each_other() -> None:
    values = np.array([0.001, 0.05, 0.2, 0.5, 0.8, 0.999])
    assert sigmoid(logit(values)) == pytest.approx(values, abs=1e-6)
