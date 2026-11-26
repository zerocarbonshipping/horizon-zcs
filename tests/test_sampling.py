"""
The sampling math: each distribution's inverse CDF, the reserved samples 2 and 3, and the automatic decimals.

Every expected value is read off by hand:

- Triangular(low, mode, high) has CDF (x - low)^2 / ((high - low)(mode - low)) below the mode, so for
  Triangular(0, 1, 2), u = 0.125 gives x = 0.5 and, by symmetry, u = 0.875 gives 1.5. Triangular(0, 1, 4)
  puts a quarter of its mass below the mode, so u = 0.25 gives the mode, 1.
- LOG-UNIFORM(1, 100) is uniform in log10 between 0 and 2, so its median is 10^1. LOG-TRIANGULAR(1, 10, 100)
  is the symmetric triangle in log10 from 0 to 2 with its mode at 1, so its median is the mode, 10.
- Probabilities 0.25, 0.5, 0.25 give a CDF of 0.25, 0.75, 1: u below 0.25 draws the first value, u from
  0.25 to just under 0.75 the second, and from 0.75 the third. Probabilities 1, 2, 1 are scaled to the same.
- The decimals are the three examples docs/configuration.md gives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from horizon.parameters import ContinuousParameter, DiscreteParameter, auto_decimals
from horizon.sampling import ParameterSampler
from horizon.study import sample_runs

if TYPE_CHECKING:
    from collections.abc import Callable

    from horizon.parser import HorFile

# a uniform range and three discrete values that sort otherwise as text ("10" < "40" < "5") than as numbers
UNIFORM = 'ContinuousParameter "C" {{ default = 1.0 low_val = 0.8 high_val = 1.5 distribution = UNIFORM {extra} }}\n'
DISCRETE = 'DiscreteParameter "D" {{ default = 10 values = [5, 10, 40] {extra} }}\n'


def _continuous(distribution: str, low: float, mid: float | None, high: float) -> ContinuousParameter:
    return ContinuousParameter("C", True, low, low, mid, high, distribution=distribution, decimals=6)


@pytest.mark.parametrize(
    ("distribution", "low", "mid", "high", "u", "expected"),
    [
        ("triangular", 0, 1, 2, 0.125, 0.5),
        ("triangular", 0, 1, 2, 0.875, 1.5),
        ("triangular", 0, 1, 4, 0.25, 1.0),
        ("log-uniform", 1, None, 100, 0.5, 10.0),
        ("log-triangular", 1, 10, 100, 0.5, 10.0),
    ],
)
def test_continuous_inverse_cdf(
    distribution: str, low: float, mid: float | None, high: float, u: float, expected: float
) -> None:
    parameter = _continuous(distribution, low, mid, high)
    assert ParameterSampler()._continuous_ppf(parameter, u) == pytest.approx(expected)


@pytest.mark.parametrize("probabilities", [[0.25, 0.5, 0.25], [1, 2, 1]])
@pytest.mark.parametrize(("u", "expected"), [(0.2, "a"), (0.25, "b"), (0.75, "c")])
def test_discrete_inverse_cdf(probabilities: list[float], u: float, expected: str) -> None:
    parameter = DiscreteParameter("D", True, "a", ["a", "b", "c"], list(probabilities))
    sampler = ParameterSampler()
    # every sampling call scales the probabilities first
    sampler._normalize_probabilities([parameter])
    assert sampler._discrete_ppf(parameter, u) == expected


def _reserved(read_study: Callable[..., HorFile], parameters: str) -> list[dict[str, str]]:
    """Return the values of samples 1 to 3 of a study without scenario parameters."""
    return [run["values"] for run in sample_runs(read_study(parameters), 3)]


def test_reserved_samples_take_the_bounds(read_study: Callable[..., HorFile]) -> None:
    """Without directives sample 2 takes each minimum and sample 3 each maximum; discrete values compare as numbers."""
    defaults, low, high = _reserved(read_study, UNIFORM.format(extra="") + DISCRETE.format(extra=""))

    assert defaults == {"C": "1.0", "D": "10"}
    assert low == {"C": "0.8", "D": "5"}
    assert high == {"C": "1.5", "D": "40"}


def test_one_keyword_sets_the_other_to_its_opposite(read_study: Callable[..., HorFile]) -> None:
    """emissions_high = MINIMUM alone makes emissions_low MAXIMUM: both reserved samples flip."""
    parameters = UNIFORM.format(extra="emissions_high = MINIMUM") + DISCRETE.format(extra="emissions_high = MINIMUM")
    _, low, high = _reserved(read_study, parameters)

    assert low == {"C": "1.5", "D": "40"}
    assert high == {"C": "0.8", "D": "5"}


def test_a_numeric_directive_is_taken_as_given(read_study: Callable[..., HorFile]) -> None:
    """A number in emissions_low is sample 2's value; sample 3, without a directive, still takes the maximum."""
    _, low, high = _reserved(read_study, UNIFORM.format(extra="emissions_low = 0.9"))

    assert low == {"C": "0.9"}
    assert high == {"C": "1.5"}


def test_an_inactive_parameter_keeps_its_default(read_study: Callable[..., HorFile]) -> None:
    parameters = UNIFORM.format(extra="active = FALSE") + DISCRETE.format(extra="active = FALSE")
    samples = _reserved(read_study, parameters)

    assert samples == [{"C": "1.0", "D": "10"}] * 3


@pytest.mark.parametrize(
    ("low", "high", "distribution", "expected"),
    [
        (100, 200, "uniform", 2),
        (0.0001, 0.001, "uniform", 8),
        (0.001, 0.12, "log-triangular", 6),
    ],
)
def test_decimals_follow_the_bounds(low: float, high: float, distribution: str, expected: int) -> None:
    assert auto_decimals(low, high, distribution) == expected
