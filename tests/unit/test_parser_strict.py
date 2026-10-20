# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the strict parser: unknown keys, `if` detection, range and condition checks."""

import pytest

from horizon.exceptions import ParseError
from horizon.parser.parser import parse_hor_file

POLICY = """
ScenarioParameter "policy" {
    values = [bau, nzf, strict]
}
"""

HORIZON = """
Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 10
%s}
"""


def _parse(tmp_path, body, horizon_extra=""):
    """(scenario parameters, sample parameters) of a .hor file made from body."""
    hor = tmp_path / "run.hor"
    hor.write_text(body + HORIZON % horizon_extra, encoding="utf-8")
    result = parse_hor_file(str(hor))
    return result[3], result[4]


def _error(tmp_path, body, horizon_extra=""):
    with pytest.raises(ParseError) as info:
        _parse(tmp_path, body, horizon_extra)
    return info.value


class TestUnknownKeys:

    def test_typo_in_parameter_block_names_file_line_block_and_suggestion(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.1
    hgh_val = 0.5
    mid_val = 0.2
}
""")
        assert error.location == f"{tmp_path / 'run.hor'}:4"
        assert "unknown key 'hgh_val' in ContinuousParameter \"a\"" in error.message
        assert "Did you mean 'high_val'?" in error.message
        assert str(error).startswith(f"{tmp_path / 'run.hor'}:4: error: ")

    def test_typo_of_emissions_key(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
    emisions_low = MAXIMUM
}
""")
        assert "Did you mean 'emissions_low'?" in error.message

    def test_typo_in_if_body_names_the_if_block(self, tmp_path):
        error = _error(tmp_path, POLICY + """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
    if policy = nzf {
        hih_val = 0.9
    }
}
""")
        assert error.location.endswith(":11")
        assert "in an 'if' block of ContinuousParameter \"a\"" in error.message
        assert "Did you mean 'high_val'?" in error.message

    def test_key_without_close_match_lists_valid_keys(self, tmp_path):
        error = _error(tmp_path, """
DiscreteParameter "d" {
    values = [1, 2]
    default = 1
    low_val = 0
}
""")
        assert "unknown key 'low_val' in DiscreteParameter \"d\"" in error.message
        assert "Valid keys:" in error.message and "probabilities" in error.message

    def test_typo_in_horizon_block(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
}
""", horizon_extra="    RandomSed = 4\n")
        assert "unknown key 'RandomSed' in the Horizon block" in error.message
        assert "Did you mean 'RandomSeed'?" in error.message
        assert error.location.endswith(":12")

    def test_all_known_keys_are_accepted(self, tmp_path):
        scenario, params = _parse(tmp_path, POLICY + """
ScenarioParameter "other" {
    token = "other"
    name = "Other"
    active = FALSE
    default = "x"
    values = [x, y]
}
ContinuousParameter "a" {
    token = "a"
    name = "A"
    active = TRUE
    default = 0.2
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
    distribution = TRIANGULAR
    decimals = 2
    emissions_low = MAXIMUM
    emissions_high = MINIMUM
    if policy = nzf {
        low_val = 0.1
        mid_val = 0.2
        high_val = 0.4
        default = 0.2
        decimals = 3
        distribution = UNIFORM
        emissions_low = MINIMUM
        emissions_high = MAXIMUM
    }
}
DiscreteParameter "d" {
    token = "d"
    name = "D"
    active = TRUE
    default = 1
    values = [1, 2]
    probabilities = [0.5, 0.5]
    emissions_low = MAXIMUM
    emissions_high = MINIMUM
}
""", horizon_extra=(
            '    SamplingMethod = MC\n    RandomSeed = 1\n    SampleOnly = TRUE\n'
            '    MaxParallelWorkers = 2\n    Plot = FALSE\n    Exclude(policy = strict)\n'))
        assert len(scenario) == 2 and len(params) == 2


class TestIfDetection:

    def test_if_inside_a_quoted_string_is_not_an_override(self, tmp_path):
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    name = "Cost if any"
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
}
""")
        assert params[0].name == "Cost if any" and params[0].overrides == []

    def test_if_inside_a_list_is_not_an_override(self, tmp_path):
        scenario, _ = _parse(tmp_path, """
ScenarioParameter "s" {
    values = [a, "if b", c]
}
""")
        assert scenario[0].values == ["a", "if b", "c"]

    def test_if_must_start_the_line(self, tmp_path):
        _, params = _parse(tmp_path, POLICY + """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
        if policy = nzf {
        low_val = 0.15
    }
}
""")
        assert len(params[0].overrides) == 1


class TestRanges:

    def test_inverted_override_bounds(self, tmp_path):
        error = _error(tmp_path, POLICY + """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.15
    high_val = 0.2
    if policy = nzf {
        low_val = 0.5
    }
}
""")
        assert error.location.endswith(":10")
        assert "Parameter 'a' in an 'if' block (policy = nzf)" in error.message
        assert "low_val (0.5) must be <= high_val (0.2)" in error.message

    def test_override_mode_outside_bounds(self, tmp_path):
        error = _error(tmp_path, POLICY + """
ContinuousParameter "a" {
    distribution = TRIANGULAR
    low_val = 0.1
    mid_val = 0.15
    high_val = 0.2
    if policy in (nzf, strict) {
        mid_val = 0.9
    }
}
""")
        assert "'if' block (policy in (nzf, strict))" in error.message
        assert "mid_val (0.9) must lie within [0.1, 0.2]" in error.message

    def test_base_mode_above_high(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    distribution = TRIANGULAR
    low_val = 0.1
    mid_val = 0.5
    high_val = 0.2
    default = 0.15
}
""")
        assert "Parameter 'a': mid_val (0.5) must lie within [0.1, 0.2]" in error.message

    def test_base_default_out_of_range(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    low_val = 0
    high_val = 1
    default = 5
    distribution = UNIFORM
}
""")
        assert "Parameter 'a': default (5) must lie within [0.0, 1.0]" in error.message

    def test_base_default_outside_override_bounds_hints_at_override(self, tmp_path):
        error = _error(tmp_path, POLICY + """
ContinuousParameter "a" {
    low_val = 0.8
    mid_val = 1.0
    high_val = 1.5
    default = 0.9
    if policy = strict {
        low_val = 1.0
        high_val = 2.0
    }
}
""")
        assert "Parameter 'a' in an 'if' block (policy = strict): default (0.9)" in error.message
        assert "set default inside this 'if' block" in error.message

    def test_override_default_out_of_range(self, tmp_path):
        error = _error(tmp_path, POLICY + """
ContinuousParameter "a" {
    low_val = 0.8
    mid_val = 1.0
    high_val = 1.5
    if policy = strict {
        default = 3
    }
}
""")
        assert "default (3) must lie within [0.8, 1.5]" in error.message
        assert "set default inside" not in error.message

    def test_default_must_be_a_number(self, tmp_path):
        error = _error(tmp_path, """
ContinuousParameter "a" {
    low_val = 0
    high_val = 1
    default = high
    distribution = UNIFORM
}
""")
        assert "default 'high' is not a number" in error.message

    def test_valid_override_with_equal_bounds_still_parses(self, tmp_path):
        _, params = _parse(tmp_path, POLICY + """
ContinuousParameter "a" {
    distribution = UNIFORM
    low_val = 0.1
    mid_val = 0.5
    high_val = 0.9
    if policy = nzf {
        low_val = 0.5
        high_val = 0.5
    }
}
""")
        assert len(params[0].overrides) == 1


class TestConditions:

    BLOCK = """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
    if %s {
        low_val = 0.15
    }
}
"""

    def test_unknown_value_lists_valid_values(self, tmp_path):
        error = _error(tmp_path, POLICY + self.BLOCK % "policy in (nzf, strcit)")
        assert error.location.endswith(":10")
        assert "'strcit' is not a value of ScenarioParameter 'policy'" in error.message
        assert "Valid values: bau, nzf, strict." in error.message

    def test_unknown_value_in_equality(self, tmp_path):
        error = _error(tmp_path, POLICY + self.BLOCK % "policy = nope")
        assert "'nope' is not a value" in error.message

    def test_unknown_scenario_token(self, tmp_path):
        error = _error(tmp_path, POLICY + self.BLOCK % "polcy = nzf")
        assert "'polcy' is not a ScenarioParameter. Did you mean 'policy'?" in error.message

    def test_no_scenario_parameter_at_all(self, tmp_path):
        error = _error(tmp_path, self.BLOCK % "policy = nzf")
        assert "Defined: none" in error.message

    def test_scenario_label_token_is_allowed(self, tmp_path):
        _parse(tmp_path, POLICY + self.BLOCK % "SCENARIO = s1")


class TestDiscrete:

    def test_probabilities_default_to_uniform(self, tmp_path):
        _, params = _parse(tmp_path, """
DiscreteParameter "d" {
    values = [3, 4, 5, 6]
    default = 4
}
""")
        assert params[0].probabilities == [0.25] * 4

    def test_probability_count_must_match_values(self, tmp_path):
        error = _error(tmp_path, """
DiscreteParameter "d" {
    values = [3, 4, 5]
    probabilities = [0.5, 0.5]
    default = 4
}
""")
        assert "3 values but 2 probabilities" in error.message

    def test_missing_values(self, tmp_path):
        error = _error(tmp_path, """
DiscreteParameter "d" {
    default = 4
}
""")
        assert "has no values" in error.message


def test_hor_file_is_read_as_utf8(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    name = "Fuel price — EU"
    low_val = 0.1
    mid_val = 0.2
    high_val = 0.5
}
""")
    assert params[0].name == "Fuel price — EU"


def test_scenario_parameter_rejects_if_blocks(tmp_path):
    error = _error(tmp_path, """
ScenarioParameter "s" {
    values = [a, b]
    if s = a {
    }
}
""")
    assert 'ScenarioParameter "s" cannot have `if` blocks' in error.message
