# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
ParameterSampler and sampling helpers.

Added:
 - resolve_parameters_for_scenario(parameters, scenario_map)
 - _override_matches(ov_conditions, scenario_map)

These helpers are used to apply 'if' override blocks parsed from .hor files to parameter
objects before sampling, producing per-scenario resolved parameter instances.
"""

import logging
import math

import numpy as np
from pyDOE3 import lhs

from horizon.exceptions import SamplingError
from horizon.parameters.parameter import ContinuousParameter, DiscreteParameter

logger = logging.getLogger(__name__)


def _override_matches(ov_conditions, scenario_map):
    """
    Determine whether a list of parsed override conditions matches a scenario mapping.

    Parameters
    ----------
    ov_conditions : list
        List of condition dicts as returned by parse_overrides()
    scenario_map : dict
        Mapping of scenario_token -> scenario_value

    Returns
    -------
    bool
        True if all conditions are satisfied by scenario_map.
    """
    for c in ov_conditions:
        token = c["token"]
        if c["op"] == "eq":
            if str(scenario_map.get(token)) != str(c["value"]):
                return False
        elif c["op"] == "in":
            if str(scenario_map.get(token)) not in [str(v) for v in c["values"]]:
                return False
        else:
            return False
    return True


def resolve_parameters_for_scenario(parameters, scenario_map):
    """
    Return a list of parameters with the best-matching override applied
    for each parameter (if any).

    Uses shallow copy + selective attribute override instead of deep copy
    for significantly better performance on large scenario matrices.

    Matching policy:
      - Overrides that match all their conditions are considered.
      - The 'most specific' (largest number of conditions) wins.
      - Ties broken by last-declared override (higher index).
    """
    resolved = []
    for p in parameters:
        overrides = getattr(p, "overrides", []) or []
        matches = []
        for idx, ov in enumerate(overrides):
            if _override_matches(ov["conditions"], scenario_map):
                matches.append((len(ov["conditions"]), idx, ov))
        if matches:
            # pick the most specific, tie-break by later declaration
            matches.sort(key=lambda t: (t[0], t[1]))
            chosen = matches[-1][2]
            resolved.append(p.shallow_copy_with_overrides(chosen["attrs"]))
        else:
            resolved.append(p.shallow_copy_with_overrides({}))
    return resolved


def ensure_mid_val(p):
    """
    Infer p.mid_val when missing, for triangular distributions only.

    Log-triangular uses the geometric mean (positive bounds required), triangular the arithmetic
    mean. Degenerate bounds (low == high) give mid_val = low. Other distributions are left alone.
    """
    if p.mid_val is not None:
        return

    lo, hi = p.low_val, p.high_val
    dist = (p.distribution or "uniform").lower()

    if lo == hi:
        p.mid_val = lo
    elif "triangular" in dist and "log" in dist:
        if lo <= 0 or hi <= 0:
            raise ValueError(f"Log distributions require positive bounds for '{p.token}'. Cannot infer mid_val.")
        p.mid_val = math.sqrt(lo * hi)
    elif "triangular" in dist:
        p.mid_val = (lo + hi) / 2.0
    else:
        return

    logger.debug("Parameter '%s' missing mid_val for distribution '%s': assuming %s", p.token, p.distribution, p.mid_val)


class ParameterSampler:
    """
    Samples parameter sets for Monte Carlo, Latin Hypercube, calibration,
    and one-at-a-time sensitivity analysis.

    Design principles:
    - active=False always means "fixed at default"
    - MC and LHS behave consistently
    - distributions are respected explicitly

    A sampler instance caches its seeded draw matrices, so per-scenario
    sampling can reuse one instance across all scenario combinations and pay
    for the (identical, seed-determined) LHS/MC matrix only once instead of
    recomputing it per combination. Unseeded draws are never cached.
    """

    def __init__(self):
        self._draw_cache = {}

    # ------------------------------------------------------------------
    # Public sampling APIs
    # ------------------------------------------------------------------

    def _cached_draw(self, kind, dim, rows, seed, compute):
        """Return a seeded draw matrix from the cache, computing it once.

        The matrices are read-only downstream (indexed, never mutated), so
        sharing one array across scenario combinations is safe. With
        seed=None the draw must stay random, so nothing is cached.
        """
        if seed is None:
            return compute()
        key = (kind, dim, rows, seed)
        matrix = self._draw_cache.get(key)
        if matrix is None:
            matrix = compute()
            self._draw_cache[key] = matrix
        return matrix

    def sample_group(self, parameters, num_samples, seed=None):
        """
        Monte Carlo sampling.

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = random MC samples (uniform draws mapped through each parameter's PPF)
        """
        self._set_random_seed(seed)

        def _compute_draws(remaining):
            return np.random.uniform(0.0, 1.0, size=(remaining, len(parameters)))

        return self._sample(parameters, num_samples, seed, "mc", _compute_draws)

    def sample_latin_hypercube(self, parameters, num_samples, seed=None):
        """
        Latin Hypercube Sampling (LHS).

        sample_1 = defaults
        sample_2 = emissions_low
        sample_3 = emissions_high
        sample_4..N = LHS samples (inverse CDF per parameter)
        """
        self._set_random_seed(seed)

        def _compute_draws(remaining):
            if remaining == 1:
                return np.full((1, len(parameters)), 0.5)
            # The seed must be passed to lhs() explicitly: pyDOE3 >= 1.5 draws from its own numpy
            # Generator and ignores the legacy global numpy.random.seed() state.
            return np.clip(lhs(len(parameters), samples=remaining, criterion="maximin", seed=seed), 0.0, 1.0)

        return self._sample(parameters, num_samples, seed, "lhs", _compute_draws)

    def _sample(self, parameters, num_samples, seed, kind, compute_draws):
        """Defaults and emissions samples followed by random samples from a (cached) draw matrix.

        Seeded matrices are cached so per-scenario sampling pays for the identical, seed-determined
        draw (LHS maximin is O(n^2)) only once. The matrices are never mutated downstream.
        """
        self._validate_parameters(parameters)

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

    @staticmethod
    def sample_calibration(parameters):
        """
        Calibration runs: each active discrete parameter is
        varied one value at a time.
        """
        active = [p for p in parameters if p.active]

        if not active:
            raise ValueError("No active parameters to calibrate.")

        simulations = []

        for p in active:
            if not isinstance(p, DiscreteParameter):
                raise ValueError("Calibration only supports DiscreteParameter.")

            for value in p.values:
                run = {"sample": f"{p.token}_{value}"}
                for q in parameters:
                    run[q.token] = value if q is p else q.default
                simulations.append(run)

        return simulations

    def sensitivity_analysis(self, parameters):
        """
        One-at-a-time sensitivity analysis.
        """
        simulations = []

        base = {"sample": "base_case"}
        for p in parameters:
            if not p.active:
                base[p.token] = p.default
            elif isinstance(p, ContinuousParameter):
                # triangular distributions use their (inferred) mid_val, all others the arithmetic midpoint
                if "triangular" in (p.distribution or "uniform").lower():
                    ensure_mid_val(p)
                    base[p.token] = p.mid_val
                else:
                    base[p.token] = (p.low_val + p.high_val) / 2.0
            else:
                base[p.token] = p.values[len(p.values) // 2]

        simulations.append(base)

        for p in parameters:
            if not p.active:
                continue

            for label, value in [("min", self._min_value(p)), ("max", self._max_value(p))]:
                run = dict(base)
                run["sample"] = f"{p.token}_{label}"
                run[p.token] = value
                simulations.append(run)

        return simulations

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
        Build the emissions sample (sample_2 or sample_3).

        Behaviour:
          - If the user specified only one of emissions_low/emissions_high and it was
            'MAXIMUM' or 'MINIMUM', the other directive is set to the opposite.
          - If the user specified both, their choices are used.
          - If neither specified, fall back to existing behaviour where a None
            directive is interpreted by _resolve_emission (usually min for low, max for high).
        """
        def _normalize_directive(d):
            if d is None:
                return None
            if isinstance(d, str):
                return d.strip().upper()
            return d

        def _compute_pair_directives(p):
            el = _normalize_directive(getattr(p, "emissions_low", None))
            eh = _normalize_directive(getattr(p, "emissions_high", None))

            # If both absent -> leave both as None (existing defaults will apply)
            if el is None and eh is None:
                return (None, None)

            # If only one is present and is a MINIMUM/MAXIMUM keyword, set the opposite
            if el is not None and eh is None:
                if isinstance(el, str) and el in ("MAXIMUM", "MINIMUM"):
                    eh = "MINIMUM" if el == "MAXIMUM" else "MAXIMUM"
                else:
                    eh = None
                return (el, eh)

            if eh is not None and el is None:
                if isinstance(eh, str) and eh in ("MAXIMUM", "MINIMUM"):
                    el = "MINIMUM" if eh == "MAXIMUM" else "MAXIMUM"
                else:
                    el = None
                return (el, eh)

            # Both present (maybe keywords or numeric); use as given
            return (el, eh)

        label = "sample_2" if low else "sample_3"
        sample = {"sample": label}

        for p in parameters:
            if not p.active:
                sample[p.token] = p.default
                continue

            # compute effective directives for this parameter (do not mutate p)
            directive_low, directive_high = _compute_pair_directives(p)

            directive = directive_low if low else directive_high

            # Now resolve the emission based on the effective directive
            sample[p.token] = self._resolve_emission(p, directive, low)

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
        dist = (p.distribution or "uniform").lower()
        u = float(np.clip(u, 0.0, 1.0))

        if dist == "uniform":
            raw = p.low_val + (p.high_val - p.low_val) * u

        elif dist == "triangular":
            ensure_mid_val(p)
            raw = self._triangular_ppf(u, p.low_val, p.mid_val, p.high_val)

        elif dist == "log-uniform":
            self._validate_positive_bounds(p)
            lo, hi = np.log(p.low_val), np.log(p.high_val)
            raw = float(np.exp(lo + (hi - lo) * u))

        elif dist == "log-triangular":
            ensure_mid_val(p)
            self._validate_positive_bounds(p, require_mid=True)
            lo, mid, hi = np.log(p.low_val), np.log(p.mid_val), np.log(p.high_val)
            log_raw = self._triangular_ppf(u, lo, mid, hi)
            raw = float(np.exp(log_raw))

        else:
            raise ValueError(f"Unsupported distribution: {p.distribution}")

        return round(raw, p.decimals)

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
        self._validate_discrete(p)
        cdf = np.cumsum(p.probabilities)
        idx = np.searchsorted(cdf, u, side="right")
        return p.values[min(idx, len(p.values) - 1)]

    # ------------------------------------------------------------------
    # Validation & helpers
    # ------------------------------------------------------------------

    def _validate_parameters(self, parameters):
        for p in parameters:
            if isinstance(p, DiscreteParameter):
                self._validate_discrete(p)

    @staticmethod
    def _validate_discrete(p):
        probs = np.asarray(p.probabilities, dtype=float)

        if len(probs) != len(p.values):
            raise ValueError(f"Probability mismatch for '{p.token}'.")

        if np.any(probs < 0) or probs.sum() <= 0:
            raise ValueError(f"Invalid probabilities for '{p.token}'.")

        p.probabilities = (probs / probs.sum()).tolist()

    @staticmethod
    def _resolve_emission(p, directive, low):
        if directive is None:
            return ParameterSampler._min_value(p) if low else ParameterSampler._max_value(p)

        if isinstance(directive, (int, float)):
            return directive

        d = str(directive).lower()
        if d in ("min", "minimum", "low", "low_val"):
            return ParameterSampler._min_value(p)
        if d in ("max", "maximum", "high", "high_val"):
            return ParameterSampler._max_value(p)
        if d in ("mid", "median", "mid_val") and isinstance(p, ContinuousParameter):
            return p.mid_val

        raise ValueError(f"Cannot interpret emissions directive '{directive}' for '{p.token}'.")

    @staticmethod
    def _validate_positive_bounds(p, require_mid=False):
        if p.low_val is None or p.high_val is None:
            raise ValueError(f"Missing bounds for '{p.token}'.")
        if p.low_val <= 0 or p.high_val <= 0:
            raise ValueError(f"Log distributions require positive bounds for '{p.token}'.")
        if p.low_val >= p.high_val:
            raise ValueError(f"Require low_val < high_val for '{p.token}'.")
        if require_mid:
            if p.mid_val is None:
                # assume arithmetic average if mid_val is missing, and log this
                avg = (p.low_val + p.high_val) / 2.0
                p.mid_val = avg
                logger.debug("Parameter '%s' missing mid_val: assuming mid_val = %s (average) for log-triangular",
                             p.token, avg)
            if p.mid_val <= 0:
                raise ValueError(f"Log-triangular requires mid_val > 0 for '{p.token}'.")

    @staticmethod
    def _min_value(p):
        if isinstance(p, ContinuousParameter):
            return p.low_val
        if not p.values:
            raise SamplingError(f"Cannot compute min for empty values in '{p.token}'")
        return min(p.values)

    @staticmethod
    def _max_value(p):
        if isinstance(p, ContinuousParameter):
            return p.high_val
        if not p.values:
            raise SamplingError(f"Cannot compute max for empty values in '{p.token}'")
        return max(p.values)

    @staticmethod
    def _set_random_seed(seed):
        if seed is not None:
            np.random.seed(seed)
