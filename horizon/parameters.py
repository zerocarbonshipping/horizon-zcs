# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
The parameters of a study and their `if` overrides.

Overrides are stored as a list on the parameter instance, each a dict built by the parser:
    {"conditions": [...], "attrs": {...}, "line": n}
A condition is {"token", "op": "eq", "value"} or {"token", "op": "in", "values"}.
"""

import copy
import logging
import math

logger = logging.getLogger(__name__)

DISTRIBUTIONS = ("uniform", "triangular", "log-uniform", "log-triangular")
DEFAULT_DISTRIBUTION = "log-triangular"

# Auto decimals: the rounding step is at most 1/RANGE_STEPS of the range, and log
# distributions also keep LOG_SIGNIFICANT_DIGITS significant digits at the low bound,
# where log sampling puts most of its values.
RANGE_STEPS = 10_000
LOG_SIGNIFICANT_DIGITS = 4


def auto_decimals(low_val, high_val, distribution):
    """Number of decimals that keeps rounding small against the range (and, for log
    distributions, against the low bound)."""
    width = abs(high_val - low_val)
    if width == 0:
        # Fixed value (low == high, e.g. from an override): keep its significant digits.
        ref = abs(low_val)
        return max(0, LOG_SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(ref))) if ref > 0 else 0
    decimals = math.ceil(math.log10(RANGE_STEPS / width))
    if distribution.startswith("log") and low_val > 0:
        decimals = max(decimals, LOG_SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(low_val)))
    return max(0, decimals)


def round_value(value, decimals):
    """Round like the sampler does; whole numbers stay int when decimals is 0."""
    return int(round(value)) if decimals == 0 else round(value, decimals)


class BaseParameter:
    """
    A parameter of the study.

    Attributes:
        token (str): The block name, used as %token% in the simulation files.
        active (bool): False fixes the parameter at its default.
        default: The value of sample_1 and of an inactive parameter.
    """

    def __init__(self, token, active, default):
        self.token = token
        self.active = active
        self.default = default

    def shallow_copy_with_overrides(self, attrs):
        """Return a shallow copy of this parameter with the attributes in attrs overridden."""
        new = copy.copy(self)  # shares lists/dicts by reference
        for k, v in attrs.items():
            setattr(new, k, v)
        return new


class ScenarioParameter(BaseParameter):
    """A scenario parameter: each of its values (strings) is a branch of the study."""

    def __init__(self, token, values, active, default):
        super().__init__(token, active, default)
        self.values = values


class ContinuousParameter(BaseParameter):
    """
    A continuous parameter with its range and distribution (one of DISTRIBUTIONS).

    overrides (list): optional per-scenario override dicts parsed from the .hor file
    """

    def __init__(self, token, active, default, low_val, mid_val, high_val,
                 distribution=DEFAULT_DISTRIBUTION, decimals=None, emissions_low=None, emissions_high=None,
                 overrides=None, default_from_mid=False):

        super().__init__(token, active, default)
        # True when no default was set in the .hor file, so default follows mid_val
        self.default_from_mid = default_from_mid
        self.low_val = low_val
        self.mid_val = mid_val
        self.high_val = high_val
        self.distribution = distribution
        # True when no decimals were set in the .hor file, so decimals follow the bounds
        self.decimals_auto = decimals is None
        self.decimals = auto_decimals(low_val, high_val, distribution) if decimals is None else decimals
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []

    def shallow_copy_with_overrides(self, attrs):
        """Like BaseParameter.shallow_copy_with_overrides, plus the implicit settings follow
        the override: auto decimals are recomputed from new bounds or distribution, and an
        implicit default follows a new mid_val or new decimals."""
        attrs = dict(attrs)
        if (self.decimals_auto and "decimals" not in attrs
                and any(k in attrs for k in ("low_val", "high_val", "distribution"))):
            attrs["decimals"] = auto_decimals(attrs.get("low_val", self.low_val),
                                              attrs.get("high_val", self.high_val),
                                              attrs.get("distribution", self.distribution))
        if self.default_from_mid and ("mid_val" in attrs or "decimals" in attrs) and "default" not in attrs:
            attrs["default"] = round_value(attrs.get("mid_val", self.mid_val), attrs.get("decimals", self.decimals))
        return super().shallow_copy_with_overrides(attrs)


class DiscreteParameter(BaseParameter):
    """
    A discrete parameter: a set of values with their probabilities.

    overrides (list): optional per-scenario override dicts parsed from the .hor file
    """

    def __init__(self, token, active, default, values, probabilities,
                 emissions_low=None, emissions_high=None, overrides=None):
        super().__init__(token, active, default)
        self.values = values
        self.probabilities = probabilities
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []


def ensure_mid_val(p):
    """
    Infer p.mid_val when missing, for triangular distributions only.

    Log-triangular uses the geometric mean, triangular the arithmetic mean. Degenerate bounds
    (low == high) give mid_val = low. Other distributions are left alone.
    """
    if p.mid_val is not None:
        return

    lo, hi = p.low_val, p.high_val
    if lo == hi:
        p.mid_val = lo
    elif p.distribution == "log-triangular":
        p.mid_val = math.sqrt(lo * hi)
    elif p.distribution == "triangular":
        p.mid_val = (lo + hi) / 2.0
    else:
        return

    logger.debug("Parameter '%s' missing mid_val for distribution '%s': assuming %s", p.token, p.distribution, p.mid_val)


def override_label(conditions: list[dict]) -> str:
    """Condition list as written in a .hor file, e.g. `policy in (a, b) and bio = low`."""
    return " and ".join(f"{c['token']} in ({', '.join(c['values'])})" if c["op"] == "in"
                        else f"{c['token']} = {c['value']}" for c in conditions)


def _override_matches(conditions, scenario_map):
    """Return True if all conditions of an override are satisfied by scenario_map (scenario_token -> value)."""
    for c in conditions:
        wanted = c["values"] if c["op"] == "in" else [c["value"]]
        if scenario_map.get(c["token"]) not in wanted:
            return False
    return True


def best_override(parameter, scenario_map):
    """
    The override of parameter that applies to scenario_map, or None.

    Among overrides whose conditions all match, the most specific (most conditions) wins and ties
    go to the last declared.
    """
    matches = [ov for ov in parameter.overrides if _override_matches(ov["conditions"], scenario_map)]
    return max(reversed(matches), key=lambda ov: len(ov["conditions"]), default=None)


def resolve_parameters_for_scenario(parameters, scenario_map):
    """Return shallow copies of parameters with the best-matching override applied (if any)."""
    resolved = []
    for p in parameters:
        best = best_override(p, scenario_map)
        resolved.append(p.shallow_copy_with_overrides(best["attrs"] if best else {}))
    return resolved
