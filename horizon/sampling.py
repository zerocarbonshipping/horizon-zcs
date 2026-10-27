# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Latin hypercube and Monte Carlo sampling of the parameters of one scenario combination."""

import numpy as np

from horizon.parameters import ContinuousParameter, DiscreteParameter, ensure_mid_val, round_value

_OPPOSITE_DIRECTIVE = {"MAXIMUM": "MINIMUM", "MINIMUM": "MAXIMUM"}


def _discrete_order(values):
    """Sort key of discrete values: by number when every value is one (so 5 < 10), else by text."""
    try:
        [float(value) for value in values]
    except (TypeError, ValueError):
        return str

    return float


class ParameterSampler:
    """
    Samples parameter sets by Monte Carlo or Latin hypercube; an inactive parameter stays at its default.

    A sampler instance caches its seeded draw matrices, so per-scenario
    sampling can reuse one instance across all scenario combinations and pay
    for the (identical, seed-determined) LHS/MC matrix only once instead of
    recomputing it per combination.
    """

    def __init__(self):
        self._draw_cache = {}

    def _cached_draw(self, kind, dim, rows, seed, compute):
        """Return a seeded draw matrix from the cache, computing it once.

        The matrices are read-only downstream (indexed, never mutated), so
        sharing one array across scenario combinations is safe.
        """
        key = (kind, dim, rows, seed)
        if key not in self._draw_cache:
            self._draw_cache[key] = compute()

        return self._draw_cache[key]

    # ------------------------------------------------------------------
    # Public sampling APIs
    # ------------------------------------------------------------------

    def sample_monte_carlo(self, parameters, num_samples, seed):
        """
        Monte Carlo sampling.

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = random MC samples (uniform draws mapped through each parameter's PPF)
        """
        np.random.seed(seed)

        def _compute_draws(remaining):
            return np.random.uniform(0.0, 1.0, size=(remaining, len(parameters)))

        return self._sample(parameters, num_samples, seed, "mc", _compute_draws)

    def sample_latin_hypercube(self, parameters, num_samples, seed):
        """
        Latin Hypercube Sampling (LHS).

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = LHS samples (inverse CDF per parameter)
        """
        np.random.seed(seed)

        def _compute_draws(remaining):
            if remaining == 1:
                return np.full((1, len(parameters)), 0.5)
            # The seed must be passed to lhs() explicitly: pyDOE3 >= 1.5 draws from its own numpy
            # Generator and ignores the legacy global numpy.random.seed() state.
            from pyDOE3 import lhs
            return np.clip(lhs(len(parameters), samples=remaining, criterion="maximin", seed=seed), 0.0, 1.0)

        return self._sample(parameters, num_samples, seed, "lhs", _compute_draws)

    def _sample(self, parameters, num_samples, seed, kind, compute_draws):
        """Defaults and emissions samples followed by random samples from a (cached) draw matrix.

        Seeded matrices are cached so per-scenario sampling pays for the identical, seed-determined
        draw (LHS maximin is O(n^2)) only once. The matrices are never mutated downstream.
        """
        self._normalize_probabilities(parameters)

        simulation_sets = self._build_base_simulation_sets(parameters, num_samples)
        remaining = num_samples - len(simulation_sets)

        if remaining <= 0:
            return simulation_sets

        draws = self._cached_draw(kind, len(parameters), remaining, seed, lambda: compute_draws(remaining))

        start = len(simulation_sets)
        for i in range(remaining):
            sample = {"sample": f"sample_{start + i + 1}"}

            for j, p in enumerate(parameters):
                if not p.active:
                    sample[p.token] = p.default
                elif isinstance(p, ContinuousParameter):
                    sample[p.token] = self._continuous_ppf(p, draws[i, j])
                else:
                    sample[p.token] = self._discrete_ppf(p, draws[i, j])

            simulation_sets.append(sample)

        return simulation_sets

    # ------------------------------------------------------------------
    # Base samples (defaults + emissions)
    # ------------------------------------------------------------------

    def _build_base_simulation_sets(self, parameters, num_samples):
        sets = []

        if num_samples >= 1:
            sets.append(self._defaults_sample(parameters))

        if num_samples >= 2:
            sets.append(self._emissions_sample(parameters, low=True))

        if num_samples >= 3:
            sets.append(self._emissions_sample(parameters, low=False))

        return sets

    def _emissions_sample(self, parameters, low=True):
        """
        Build the emissions sample (sample_2 for low, sample_3 for high).

        A missing directive becomes the opposite of the other one when that is MAXIMUM or MINIMUM;
        otherwise _resolve_emission falls back to min for low and max for high.
        """
        sample = {"sample": "sample_2" if low else "sample_3"}

        for p in parameters:
            if not p.active:
                sample[p.token] = p.default
                continue

            own, other = (p.emissions_low, p.emissions_high) if low else (p.emissions_high, p.emissions_low)
            if own is None:
                own = _OPPOSITE_DIRECTIVE.get(other)

            sample[p.token] = self._resolve_emission(p, own, low)

        return sample

    @staticmethod
    def _defaults_sample(parameters):
        return {
            "sample": "sample_1",
            **{p.token: p.default for p in parameters},
        }

    # ------------------------------------------------------------------
    # Continuous sampling
    # ------------------------------------------------------------------

    def _continuous_ppf(self, p, u):
        dist = p.distribution
        u = float(np.clip(u, 0.0, 1.0))

        if dist == "uniform":
            raw = p.low_val + (p.high_val - p.low_val) * u

        elif dist == "triangular":
            ensure_mid_val(p)
            raw = self._triangular_ppf(u, p.low_val, p.mid_val, p.high_val)

        elif dist == "log-uniform":
            lo, hi = np.log(p.low_val), np.log(p.high_val)
            raw = float(np.exp(lo + (hi - lo) * u))

        else:
            ensure_mid_val(p)
            lo, mid, hi = np.log(p.low_val), np.log(p.mid_val), np.log(p.high_val)
            log_raw = self._triangular_ppf(u, lo, mid, hi)
            raw = float(np.exp(log_raw))

        return round_value(raw, p.decimals)

    @staticmethod
    def _triangular_ppf(u, a, c, b):
        if a == b:
            return a

        Fc = (c - a) / (b - a)
        if u < Fc:
            return a + np.sqrt(u * (b - a) * (c - a))
        return b - np.sqrt((1 - u) * (b - a) * (b - c))

    # ------------------------------------------------------------------
    # Discrete sampling
    # ------------------------------------------------------------------

    def _discrete_ppf(self, p, u):
        cdf = np.cumsum(p.probabilities)
        idx = np.searchsorted(cdf, u, side="right")
        return p.values[min(idx, len(p.values) - 1)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_probabilities(parameters):
        """Scale the probabilities of each discrete parameter to sum to 1."""
        for p in parameters:
            if isinstance(p, DiscreteParameter):
                probs = np.asarray(p.probabilities, dtype=float)
                p.probabilities = (probs / probs.sum()).tolist()

    @staticmethod
    def _resolve_emission(p, directive, low):
        if directive is None:
            return ParameterSampler._min_value(p) if low else ParameterSampler._max_value(p)

        if directive == "MINIMUM":
            return ParameterSampler._min_value(p)

        if directive == "MAXIMUM":
            return ParameterSampler._max_value(p)

        return directive

    @staticmethod
    def _min_value(p):
        if isinstance(p, ContinuousParameter):
            return p.low_val
        return min(p.values, key=_discrete_order(p.values))

    @staticmethod
    def _max_value(p):
        if isinstance(p, ContinuousParameter):
            return p.high_val
        return max(p.values, key=_discrete_order(p.values))
