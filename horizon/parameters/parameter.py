# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# File: horizon/parameters/parameter.py
"""
Parameter classes for Horizon.

Contains:
 - BaseParameter
 - ScenarioParameter
 - ContinuousParameter (added 'overrides' support)
 - DiscreteParameter (added 'overrides' support)

Overrides are stored as a list on the parameter instance. Each override is expected
to be a dict of the form parsed by the parser's parse_overrides() helper:
    { "conditions": [...], "attrs": {...}, "start": int, "end": int }
"""
import math

from horizon.exceptions import ParameterError

DISTRIBUTIONS = ("uniform", "triangular", "log-uniform", "log-triangular")
DEFAULT_DISTRIBUTION = "log-triangular"

# Auto decimals: the rounding step is at most 1/RANGE_STEPS of the range, and log
# distributions also keep LOG_SIGNIFICANT_DIGITS significant digits at the low bound,
# where log sampling puts most of its values.
RANGE_STEPS = 10_000
LOG_SIGNIFICANT_DIGITS = 4


def normalize_distribution(value):
    """Map a .hor distribution value (LOG-TRIANGULAR, "log-triangular", LOG_TRIANGULAR) to its canonical name."""
    name = str(value).strip().strip('"').strip("'").lower().replace("_", "-")
    if name not in DISTRIBUTIONS:
        raise ParameterError(
            f"Unknown distribution '{value}'. Use one of: {', '.join(d.upper() for d in DISTRIBUTIONS)}"
        )
    return name


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
    A base class for simulation parameters.

    Attributes:
        name (str): Human-readable name of the parameter.
        token (str): A unique identifier used in simulation templates.
        active (bool): Indicates if the parameter is active in the current simulation.
        default: Default value to use when parameter is inactive.
    """

    def __init__(self, name, token, active=True, default=None):
        self.name = name
        self.token = token
        self.active = active
        self.default = default

    def shallow_copy_with_overrides(self, attrs):
        """Create a shallow copy of this parameter with specific attributes overridden.

        Much faster than copy.deepcopy() since parameter objects are simple data
        holders with immutable or shared-safe fields. Only the override attrs
        (numeric values like low_val, high_val, mid_val, default) are changed.

        Parameters
        ----------
        attrs : dict
            Attribute names and values to override on the copy.

        Returns
        -------
        BaseParameter
            A new instance of the same class with overridden attributes.
        """
        import copy
        new = copy.copy(self)  # shallow copy — shares lists/dicts by reference
        for k, v in attrs.items():
            setattr(new, k, v)
        return new


class ScenarioParameter(BaseParameter):
    """
    Represents a scenario parameter within a range of values.

    Args:
        name (str)
        token (str)
        values (list): Possible scenario values (strings)
        active (bool)
        default: default scenario value
    """

    def __init__(self, name, token, values, active, default):
        super().__init__(name, token, active, default)
        self.values = values

    def __repr__(self):
        return (f"ScenarioParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"values={self.values})")


class ContinuousParameter(BaseParameter):
    """
    Represents a continuous parameter with range and sampling metadata.

    New:
        overrides (list): optional list of per-scenario override dicts parsed from .hor (see parser)
    """

    def __init__(self, name, token, active, default, low_val, mid_val, high_val,
                 distribution=DEFAULT_DISTRIBUTION, decimals=None, emissions_low=None, emissions_high=None,
                 overrides=None, default_from_mid=False):

        super().__init__(name, token, active, default)
        # True when no default was set in the .hor file, so default follows mid_val
        self.default_from_mid = default_from_mid
        self.low_val = low_val
        self.mid_val = mid_val
        self.high_val = high_val
        self.distribution = normalize_distribution(distribution)
        # True when no decimals were set in the .hor file, so decimals follow the bounds
        self.decimals_auto = decimals is None
        self.decimals = auto_decimals(low_val, high_val, self.distribution) if decimals is None else decimals
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []

    def shallow_copy_with_overrides(self, attrs):
        """Like BaseParameter.shallow_copy_with_overrides, plus the implicit settings follow
        the override: auto decimals are recomputed from new bounds or distribution, and an
        implicit default follows a new mid_val or new decimals."""
        attrs = dict(attrs)
        if "distribution" in attrs:
            attrs["distribution"] = normalize_distribution(attrs["distribution"])
        if (self.decimals_auto and "decimals" not in attrs
                and any(k in attrs for k in ("low_val", "high_val", "distribution"))):
            attrs["decimals"] = auto_decimals(attrs.get("low_val", self.low_val),
                                              attrs.get("high_val", self.high_val),
                                              attrs.get("distribution", self.distribution))
        if self.default_from_mid and ("mid_val" in attrs or "decimals" in attrs) and "default" not in attrs:
            attrs["default"] = round_value(attrs.get("mid_val", self.mid_val), attrs.get("decimals", self.decimals))
        return super().shallow_copy_with_overrides(attrs)

    def __repr__(self):
        return (f"ContinuousParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"low_val={self.low_val}, mid_val={self.mid_val}, high_val={self.high_val}, distribution="
                f"{self.distribution}, decimals={self.decimals}), emissions_low={self.emissions_low}, "
                f"emissions_high={self.emissions_high}, overrides={self.overrides}")


class DiscreteParameter(BaseParameter):
    """
    Represents a discrete parameter with a set of possible values and probabilities.

    New:
        overrides (list): optional list of per-scenario override dicts parsed from .hor (see parser)
    """

    def __init__(self, name, token, active, default, values, probabilities,
                 emissions_low=None, emissions_high=None, overrides=None):
        super().__init__(name, token, active, default)

        # Validate that values list is not empty
        if not values:
            raise ParameterError(f"DiscreteParameter '{name}' has empty values list")

        self.values = values
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high

        # Check if probabilities should be uniform
        if isinstance(probabilities, str) and probabilities.lower() == "uniform":
            uniform_probability = 1.0 / len(values)
            self.probabilities = [uniform_probability for _ in values]
        else:
            self.probabilities = probabilities

        self.overrides = overrides or []

    def __repr__(self):
        return (f"DiscreteParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"values={self.values}, probabilities={self.probabilities}, emissions_low={self.emissions_low}, "
                f"emissions_high={self.emissions_high}, overrides={self.overrides})")
