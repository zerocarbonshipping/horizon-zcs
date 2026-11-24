"""The parameters of a study and their `if` overrides."""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass
from typing import Any, Self

logger = logging.getLogger(__name__)

DISTRIBUTIONS = ("uniform", "triangular", "log-uniform", "log-triangular")
DEFAULT_DISTRIBUTION = "log-triangular"

# Auto decimals: the rounding step is at most 1/RANGE_STEPS of the range, and log
# distributions also keep LOG_SIGNIFICANT_DIGITS significant digits at the low bound,
# where log sampling puts most of its values.
RANGE_STEPS = 10_000
LOG_SIGNIFICANT_DIGITS = 4

type Number = int | float
# a sampled value: a number of a continuous parameter, the text of a discrete or scenario one
type Value = Number | str
# MINIMUM, MAXIMUM or a value
type Emissions = Value | None
type Parameter = ContinuousParameter | DiscreteParameter


@dataclass(frozen=True)
class Condition:
    """A condition of an `if` block: `token = value`, or `token in (values)` when among."""

    token: str
    values: tuple[str, ...]
    among: bool

    def matches(self, scenario_map: dict[str, str]) -> bool:
        """Return whether the scenario combination gives the token one of the values."""
        return scenario_map.get(self.token) in self.values

    def __str__(self) -> str:
        return f"{self.token} in ({', '.join(self.values)})" if self.among else f"{self.token} = {self.values[0]}"


@dataclass
class Override:
    """An `if` block of a parameter: its conditions, the attributes it sets and its line in the .hor file."""

    conditions: list[Condition]
    attrs: dict[str, Any]
    line: int

    @property
    def label(self) -> str:
        """The conditions as a .hor file writes them, e.g. `policy in (a, b) and bio = low`."""
        return " and ".join(str(condition) for condition in self.conditions)

    def matches(self, scenario_map: dict[str, str]) -> bool:
        """Return whether every condition holds in the scenario combination."""
        return all(condition.matches(scenario_map) for condition in self.conditions)


def auto_decimals(low_val: Number, high_val: Number, distribution: str) -> int:
    """
    Find the number of decimals that keeps rounding small against the range.

    For log distributions, also against the low bound.
    """
    width = abs(high_val - low_val)
    if width == 0:
        # Fixed value (low == high, e.g. from an override): keep its significant digits.
        ref = abs(low_val)
        return max(0, LOG_SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(ref))) if ref > 0 else 0
    decimals = math.ceil(math.log10(RANGE_STEPS / width))
    if distribution.startswith("log") and low_val > 0:
        decimals = max(decimals, LOG_SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(low_val)))
    return max(0, decimals)


def round_value(value: Number, decimals: int) -> Number:
    """Round like the sampler does; whole numbers stay int when decimals is 0."""
    return round(value) if decimals == 0 else round(value, decimals)


class BaseParameter:
    """
    A parameter of the study.

    Attributes
    ----------
    token : str
        The block name, used as %token% in the simulation files.
    active : bool
        False fixes the parameter at its default.
    default : Value
        The value of sample_1 and of an inactive parameter.
    """

    def __init__(self, token: str, active: bool, default: Value) -> None:
        self.token = token
        self.active = active
        self.default = default

    def shallow_copy_with_overrides(self, attrs: dict[str, Any]) -> Self:
        """Return a shallow copy of this parameter with the attributes in attrs overridden."""
        new = copy.copy(self)  # shares lists/dicts by reference
        for k, v in attrs.items():
            setattr(new, k, v)
        return new


class ScenarioParameter(BaseParameter):
    """A scenario parameter: each of its values (strings) is a branch of the study."""

    default: str

    def __init__(self, token: str, values: list[str], active: bool, default: str) -> None:
        super().__init__(token, active, default)
        self.values = values


class ContinuousParameter(BaseParameter):
    """
    A continuous parameter with its range and distribution (one of DISTRIBUTIONS).

    Without a default, the default is mid_val, rounded, and follows it through the overrides: the `if` blocks of
    the .hor file, each applying to some scenario combinations.
    """

    default: Number

    def __init__(
        self,
        token: str,
        active: bool,
        default: Number | None,
        low_val: Number,
        mid_val: Number | None,
        high_val: Number,
        distribution: str = DEFAULT_DISTRIBUTION,
        decimals: int | None = None,
        emissions_low: Emissions = None,
        emissions_high: Emissions = None,
        overrides: list[Override] | None = None,
    ) -> None:
        # True when no decimals were set in the .hor file, so decimals follow the bounds
        self.decimals_auto = decimals is None
        self.decimals = auto_decimals(low_val, high_val, distribution) if decimals is None else decimals
        # True when no default was set in the .hor file, so default follows mid_val
        self.default_from_mid = default is None
        if default is None:
            if mid_val is None:
                raise ValueError(f"parameter '{token}' has no mid_val and no default")

            default = round_value(mid_val, self.decimals)

        super().__init__(token, active, default)
        self.low_val = low_val
        self.mid_val = mid_val
        self.high_val = high_val
        self.distribution = distribution
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []

    def shallow_copy_with_overrides(self, attrs: dict[str, Any]) -> Self:
        """
        Copy as BaseParameter.shallow_copy_with_overrides does, with the implicit settings following the override.

        Auto decimals are recomputed from new bounds or distribution, and an implicit default follows a new mid_val
        or new decimals.
        """
        attrs = dict(attrs)
        if self.decimals_auto and "decimals" not in attrs and any(k in attrs for k in ("low_val", "high_val", "distribution")):
            attrs["decimals"] = auto_decimals(
                attrs.get("low_val", self.low_val),
                attrs.get("high_val", self.high_val),
                attrs.get("distribution", self.distribution),
            )
        mid_val = attrs.get("mid_val", self.mid_val)
        if (
            self.default_from_mid
            and mid_val is not None
            and ("mid_val" in attrs or "decimals" in attrs)
            and "default" not in attrs
        ):
            attrs["default"] = round_value(mid_val, attrs.get("decimals", self.decimals))
        return super().shallow_copy_with_overrides(attrs)


class DiscreteParameter(BaseParameter):
    """
    A discrete parameter: a set of values with their probabilities.

    The overrides are the `if` blocks of the .hor file, each applying to some scenario combinations.
    """

    default: str

    def __init__(
        self,
        token: str,
        active: bool,
        default: str,
        values: list[str],
        probabilities: list[Number],
        emissions_low: Emissions = None,
        emissions_high: Emissions = None,
        overrides: list[Override] | None = None,
    ) -> None:
        super().__init__(token, active, default)
        self.values = values
        self.probabilities = probabilities
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []


def ensure_mid_val(p: ContinuousParameter) -> None:
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


def best_override(parameter: Parameter, scenario_map: dict[str, str]) -> Override | None:
    """
    Find the override of parameter that applies to scenario_map, or None.

    Among overrides whose conditions all match, the most specific (most conditions) wins and ties
    go to the last declared.
    """
    matches = [override for override in parameter.overrides if override.matches(scenario_map)]
    return max(reversed(matches), key=lambda override: len(override.conditions), default=None)


def resolve_parameters_for_scenario(parameters: list[Parameter], scenario_map: dict[str, str]) -> list[Parameter]:
    """Return shallow copies of parameters with the best-matching override applied (if any)."""
    resolved = []
    for p in parameters:
        best = best_override(p, scenario_map)
        resolved.append(p.shallow_copy_with_overrides(best.attrs if best else {}))
    return resolved
