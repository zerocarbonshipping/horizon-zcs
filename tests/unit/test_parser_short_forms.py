# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The one-line distribution call: `ContinuousParameter "x" = LOG-TRIANGULAR(0.6, 1.0, 1.8) { ... }`."""

import pytest

from horizon.exceptions import ParseError
from horizon.parser.parser import parse_hor_file

HEAD = """
Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 10
}
ScenarioParameter "policy" {
    values = [bau, nzf]
    default = "bau"
}
"""

ONE_LINE = """
ContinuousParameter "a" = LOG-TRIANGULAR(0.6, 1.0, 1.8)
ContinuousParameter "b" = UNIFORM(0, 10) { emissions_low = MAXIMUM, default = 5 }
ContinuousParameter "c" = TRIANGULAR(0.10, 0.15, 0.20) {
    decimals = 3
    if policy = nzf {
        low_val = 0.15
        mid_val = 0.20
        high_val = 0.25
    }
}
DiscreteParameter "d" = DISCRETE([3, 4, 5, 6], [0.15, 0.35, 0.35, 0.15]) { default = 4 }
DiscreteParameter "e" = DISCRETE([1, 2, 3]) { default = 2 }
"""

BLOCKS = """
ContinuousParameter "a" {
    low_val = 0.6
    mid_val = 1.0
    high_val = 1.8
}
ContinuousParameter "b" {
    distribution = UNIFORM
    low_val = 0
    high_val = 10
    emissions_low = MAXIMUM
    default = 5
}
ContinuousParameter "c" {
    distribution = TRIANGULAR
    low_val = 0.10
    mid_val = 0.15
    high_val = 0.20
    decimals = 3
    if policy = nzf {
        low_val = 0.15
        mid_val = 0.20
        high_val = 0.25
    }
}
DiscreteParameter "d" {
    values = [3, 4, 5, 6]
    probabilities = [0.15, 0.35, 0.35, 0.15]
    default = 4
}
DiscreteParameter "e" {
    values = [1, 2, 3]
    default = 2
}
"""


def _parse(tmp_path, body):
    """The sample parameters of a .hor file made from HEAD + body."""
    hor = tmp_path / "run.hor"
    hor.write_text(HEAD + body, encoding="utf-8")
    return parse_hor_file(str(hor))[4]


def _error(tmp_path, body):
    with pytest.raises(ParseError) as info:
        _parse(tmp_path, body)
    return str(info.value)


def _state(parameter):
    """Comparable view of a parameter (override line numbers differ between the two spellings)."""
    state = dict(vars(parameter))
    state["overrides"] = [{k: v for k, v in o.items() if k != "line"} for o in state.get("overrides", [])]
    return state


def test_one_line_file_parses_like_the_block_form(tmp_path):
    one_line = [_state(p) for p in _parse(tmp_path, ONE_LINE)]
    blocks = [_state(p) for p in _parse(tmp_path, BLOCKS)]
    assert one_line == blocks


def test_forms_mix_in_one_file(tmp_path):
    mixed = ONE_LINE.split('ContinuousParameter "c"')[0] + 'ContinuousParameter "c"' + BLOCKS.split(
        'ContinuousParameter "c"')[1]
    assert [p.token for p in _parse(tmp_path, mixed)] == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize("call, expected", [
    ("UNIFORM(0, 5, 10)", "UNIFORM takes 2 numbers (low, high), got (0, 5, 10)"),
    ("TRIANGULAR(1, 2)", "TRIANGULAR takes 3 numbers (low, mode, high), got (1, 2)"),
    ("TRIANGULAR(1, x, 3)", "TRIANGULAR takes 3 numbers"),
    ("NORMAL(0, 1)", "NORMAL"),
])
def test_bad_continuous_call_is_an_error_on_its_line(tmp_path, call, expected):
    message = _error(tmp_path, f'ContinuousParameter "x" = {call} {{ default = 1 }}\n')
    assert "run.hor:11:" in message and expected in message


def test_discrete_needs_the_discrete_call(tmp_path):
    assert "expected DISCRETE([values], [probabilities])" in _error(
        tmp_path, 'DiscreteParameter "x" = UNIFORM(1, 2) { default = 1 }\n')


def test_call_and_long_form_keys_cannot_mix(tmp_path):
    message = _error(tmp_path, 'ContinuousParameter "x" = UNIFORM(0, 1) {\n    default = 0.5\n    low_val = 0.2\n}\n')
    assert "run.hor:13:" in message and "'low_val' cannot be set next to the call" in message


def test_unknown_key_after_the_call_is_an_error(tmp_path):
    assert "Did you mean 'emissions_low'?" in _error(
        tmp_path, 'ContinuousParameter "x" = UNIFORM(0, 1) { emisions_low = MAXIMUM, default = 0.5 }\n')


def test_line_numbers_after_a_multi_line_call_stay_right(tmp_path):
    body = ('DiscreteParameter "d" = DISCRETE(\n    [1, 2, 3],\n    [0.2, 0.5, 0.3]) { default = 2 }\n'
            'ContinuousParameter "x" = UNIFORM(0, 1) { hgh_val = 2, default = 0.5 }\n')
    assert "run.hor:14:" in _error(tmp_path, body)
