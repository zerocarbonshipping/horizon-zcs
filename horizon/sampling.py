"""Latin hypercube and Monte Carlo sampling of the parameters of one scenario combination."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from horizon.parameters import ContinuousParameter, DiscreteParameter, ensure_mid_val, round_value

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

    from horizon.parameters import Emissions, Number, Parameter, Value

_OPPOSITE_DIRECTIVE = {"MAXIMUM": "MINIMUM", "MINIMUM": "MAXIMUM"}

# a sample: the value of each parameter, by token
type Sample = dict[str, Value]
type Draws = NDArray[np.float64]


def _discrete_order(values: list[str]) -> Callable[[str], Any]:
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

    def __init__(self) -> None:
        self._draw_cache: dict[tuple[str, int, int, int], Draws] = {}

    def _cached_draw(self, kind: str, dim: int, rows: int, seed: int, compute: Callable[[], Draws]) -> Draws:
        """
        Return a seeded draw matrix from the cache, computing it once.

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

    def sample_monte_carlo(self, parameters: list[Parameter], num_samples: int, seed: int) -> list[Sample]:
        """
        Sample by Monte Carlo.

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = random MC samples (uniform draws mapped through each parameter's PPF)
        """
        np.random.seed(seed)

        def _compute_draws(remaining: int) -> Draws:
            return np.random.uniform(0.0, 1.0, size=(remaining, len(parameters)))

        return self._sample(parameters, num_samples, seed, "mc", _compute_draws)

    def sample_latin_hypercube(self, parameters: list[Parameter], num_samples: int, seed: int) -> list[Sample]:
        """
        Sample by Latin hypercube (LHS).

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = LHS samples (inverse CDF per parameter)
        """

        def _compute_draws(remaining: int) -> Draws:
            if remaining == 1:
                return np.full((1, len(parameters)), 0.5)
            # The seed must be passed to lhs() explicitly: pyDOE3 >= 1.5 draws from its own numpy
            # Generator and ignores the legacy global numpy.random.seed() state.
            from pyDOE3 import lhs

            draws: Draws = np.clip(lhs(len(parameters), samples=remaining, criterion="maximin", seed=seed), 0.0, 1.0)
            return draws

        return self._sample(parameters, num_samples, seed, "lhs", _compute_draws)

    def _sample(
        self, parameters: list[Parameter], num_samples: int, seed: int, kind: str, compute_draws: Callable[[int], Draws]
    ) -> list[Sample]:
        """
        Sample the defaults and emissions, followed by random samples from a (cached) draw matrix.

        Seeded matrices are cached so per-scenario sampling pays for the identical, seed-determined
        draw (LHS maximin is O(n^2)) only once. The matrices are never mutated downstream.
        """
        self._normalize_probabilities(parameters)

        simulation_sets = self._build_base_simulation_sets(parameters, num_samples)
        remaining = num_samples - len(simulation_sets)

        if remaining <= 0:
            return simulation_sets

        draws = self._cached_draw(kind, len(parameters), remaining, seed, lambda: compute_draws(remaining))

        for i in range(remaining):
            sample: Sample = {}
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

    def _build_base_simulation_sets(self, parameters: list[Parameter], num_samples: int) -> list[Sample]:
        sets = []

        if num_samples >= 1:
            sets.append(self._defaults_sample(parameters))

        if num_samples >= 2:
            sets.append(self._emissions_sample(parameters, low=True))

        if num_samples >= 3:
            sets.append(self._emissions_sample(parameters, low=False))

        return sets

    def _emissions_sample(self, parameters: list[Parameter], low: bool = True) -> Sample:
        """
        Build the emissions sample (sample_2 for low, sample_3 for high).

        A missing directive becomes the opposite of the other one when that is MAXIMUM or MINIMUM;
        otherwise _resolve_emission falls back to min for low and max for high.
        """
        sample: Sample = {}

        for p in parameters:
            if not p.active:
                sample[p.token] = p.default
                continue

            own, other = (p.emissions_low, p.emissions_high) if low else (p.emissions_high, p.emissions_low)
            if own is None and isinstance(other, str):
                own = _OPPOSITE_DIRECTIVE.get(other)

            sample[p.token] = self._resolve_emission(p, own, low)

        return sample

    @staticmethod
    def _defaults_sample(parameters: list[Parameter]) -> Sample:
        return {p.token: p.default for p in parameters}

    # ------------------------------------------------------------------
    # Continuous sampling
    # ------------------------------------------------------------------

    def _continuous_ppf(self, p: ContinuousParameter, u: float) -> Number:
        dist = p.distribution
        u = float(np.clip(u, 0.0, 1.0))

        if dist == "uniform":
            raw = p.low_val + (p.high_val - p.low_val) * u

        elif dist == "triangular":
            ensure_mid_val(p)
            raw = self._triangular_ppf(u, p.low_val, _mid(p), p.high_val)

        elif dist == "log-uniform":
            lo, hi = np.log(p.low_val), np.log(p.high_val)
            raw = float(np.exp(lo + (hi - lo) * u))

        else:
            ensure_mid_val(p)
            lo, mid, hi = np.log(p.low_val), np.log(_mid(p)), np.log(p.high_val)
            log_raw = self._triangular_ppf(u, float(lo), float(mid), float(hi))
            raw = float(np.exp(log_raw))

        return round_value(raw, p.decimals)

    @staticmethod
    def _triangular_ppf(u: float, a: Number, c: Number, b: Number) -> Number:
        if a == b:
            return a

        mode_cdf = (c - a) / (b - a)
        if u < mode_cdf:
            return a + float(np.sqrt(u * (b - a) * (c - a)))
        return b - float(np.sqrt((1 - u) * (b - a) * (b - c)))

    # ------------------------------------------------------------------
    # Discrete sampling
    # ------------------------------------------------------------------

    def _discrete_ppf(self, p: DiscreteParameter, u: float) -> str:
        cdf = np.cumsum(p.probabilities)
        idx = int(np.searchsorted(cdf, u, side="right"))
        return p.values[min(idx, len(p.values) - 1)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_probabilities(parameters: list[Parameter]) -> None:
        """Scale the probabilities of each discrete parameter to sum to 1."""
        for p in parameters:
            if isinstance(p, DiscreteParameter):
                probs = np.asarray(p.probabilities, dtype=float)
                p.probabilities = (probs / probs.sum()).tolist()

    @staticmethod
    def _resolve_emission(p: Parameter, directive: Emissions, low: bool) -> Value:
        if directive is None:
            return ParameterSampler._min_value(p) if low else ParameterSampler._max_value(p)

        if directive == "MINIMUM":
            return ParameterSampler._min_value(p)

        if directive == "MAXIMUM":
            return ParameterSampler._max_value(p)

        return directive

    @staticmethod
    def _min_value(p: Parameter) -> Value:
        if isinstance(p, ContinuousParameter):
            return p.low_val
        return min(p.values, key=_discrete_order(p.values))

    @staticmethod
    def _max_value(p: Parameter) -> Value:
        if isinstance(p, ContinuousParameter):
            return p.high_val
        return max(p.values, key=_discrete_order(p.values))


def _mid(p: ContinuousParameter) -> Number:
    """Return the mode of a triangular parameter, which ensure_mid_val has set."""
    if p.mid_val is None:
        raise ValueError(f"parameter '{p.token}' has no mid_val")

    return p.mid_val
