"""
The parser: a study runs the scenario combinations its rules leave, and a broken study names the line at fault.

The combinations of two scenario parameters, A in (x, y) and B in (p, q), are listed by hand: A varies slowest,
each is labelled with its values joined by _, an Include rule keeps what it matches, any Include rule
matching is enough, and Exclude removes from what Include kept.

An error's location is `<file>:<line>`, the line of the entry at fault: the key, the value or the `if` block,
else the block that lacks something.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from horizon.exceptions import ParseError
from horizon.parser import read_hor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from horizon.parser import HorFile

SCENARIOS = """
ScenarioParameter "A" {
    values = [x, y]
}
ScenarioParameter "B" {
    values = [p, q]
}
"""

CONTINUOUS = """
ContinuousParameter "C" {{
    default = 1
    low_val = 0
    high_val = 2
    distribution = UNIFORM
    {extra}
}}
"""

DECK = 'UncFilePath = "deck.unc"'
OUTPUT = 'OutputPath = "output"'


def _horizon(*entries: str) -> str:
    return "Horizon {\n" + "".join(f"    {entry}\n" for entry in entries) + "}\n"


def _line_of(path: Path, needle: str) -> int:
    """Return the number of the first line of the file holding needle."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return next(number for number, line in enumerate(lines, 1) if needle in line)


@pytest.mark.parametrize(
    ("rules", "labels"),
    [
        ("", ["x_p", "x_q", "y_p", "y_q"]),
        ("Include(A = x)", ["x_p", "x_q"]),
        ("Include(A = x) Include(B = q)", ["x_p", "x_q", "y_q"]),
        ("Exclude(B = q)", ["x_p", "y_p"]),
        ("Include(A = x) Exclude(B = q)", ["x_p"]),
        ("Include(A = x, B = p, _name = s1) Include(A = y, B = q, _name = s2)", ["s1", "s2"]),
    ],
)
def test_rules_choose_the_combinations(read_study: Callable[..., HorFile], rules: str, labels: list[str]) -> None:
    hor = read_study(SCENARIOS, rules=rules)
    assert [combination.label for combination in hor.combinations] == labels


def test_a_name_fills_the_scenario_token(read_study: Callable[..., HorFile]) -> None:
    hor = read_study(SCENARIOS, rules="Include(A = y, B = q, _name = s2)")
    assert hor.combinations[0].scenario_map == {"A": "y", "B": "q", "SCENARIO": "s2"}


# input:    | ContinuousParameter "C" {      (line 2)
#           |     ...
#           |     foo = 3                    (line 7)
# expected: -> ParseError at <file>:7, "unknown key 'foo' in ContinuousParameter "C". ..."
@pytest.mark.parametrize(
    ("parameters", "rules", "horizon", "needle", "message"),
    [
        (CONTINUOUS.format(extra="foo = 3"), "", None, "foo", "unknown key 'foo'"),
        (CONTINUOUS.format(extra="low_val = 1"), "", None, "low_val = 1", "low_val is set twice"),
        (CONTINUOUS.format(extra="decimals = -1"), "", None, "decimals", "decimals must be a whole number"),
        (CONTINUOUS.format(extra="high_val = = 3"), "", None, "high_val = =", "unexpected '='"),
        (
            'ContinuousParameter "C" {\n    low_val = 3\n    high_val = 2\n}\n',
            "",
            None,
            "low_val",
            "must be below high_val",
        ),
        (
            CONTINUOUS.format(extra="") + 'ContinuousParameter "C" = UNIFORM(0, 2) { default = 1 }\n',
            "",
            None,
            '"C" = UNIFORM',
            "defined twice",
        ),
        (
            SCENARIOS + CONTINUOUS.format(extra="if A = x {\n        bar = 1\n    }"),
            "",
            None,
            "bar",
            "unknown key 'bar'",
        ),
        (
            SCENARIOS + CONTINUOUS.format(extra="if A = x {\n        low_val = 5\n    }"),
            "",
            None,
            "if A = x",
            "low_val (5) must be <= high_val (2)",
        ),
        (
            SCENARIOS + CONTINUOUS.format(extra="if A = z {\n        low_val = 1\n    }"),
            "",
            None,
            "if A = z",
            "'z' is not a value of ScenarioParameter 'A'",
        ),
        (
            'DiscreteParameter "D" {\n    default = a\n    values = [a, b, c]\n    probabilities = [0.5, 0.5]\n}\n',
            "",
            None,
            "probabilities",
            "2 probabilities for 3 values",
        ),
        (SCENARIOS, "Include(A = x, _name = s1)\n    Include(A = y)", None, "Include(A = y)", "all must have it"),
        (SCENARIOS, "Exclude(A = x, _name = s1)", None, "Exclude", "_name labels Include rules only"),
        (SCENARIOS, "Include(A = x)\n    Exclude(A = x)", None, "Horizon {", "leave no scenario combination"),
        ("", "", _horizon(DECK, OUTPUT), "Horizon {", "needs NumberOfSamples"),
        (
            "",
            "",
            _horizon(DECK, OUTPUT, "NumberOfSamples = 1", "SamplingMethod = FOO"),
            "SamplingMethod",
            "SamplingMethod must be one of",
        ),
        (
            "",
            "",
            _horizon(DECK, OUTPUT, "NumberOfSamples = 1") + "Horizon {  # the second\n}\n",
            "# the second",
            "a second Horizon block",
        ),
    ],
)
def test_an_error_names_its_line(
    write_study: Callable[..., Path], parameters: str, rules: str, horizon: str | None, needle: str, message: str
) -> None:
    study = write_study(parameters, rules=rules, horizon=horizon)
    with pytest.raises(ParseError, match=re.escape(message)) as error:
        read_hor(study)

    assert error.value.location == f"{study}:{_line_of(study, needle)}"
