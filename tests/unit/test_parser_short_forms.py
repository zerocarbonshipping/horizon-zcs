# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the one-line .hor forms: distribution calls, one-line overrides, one-line scenarios."""

from pathlib import Path

import pytest

from horizon.exceptions import ParseError
from horizon.misc.util import output_sampled_parameters_to_csv
from horizon.parameters.sampler import resolve_parameters_for_scenario
from horizon.parser.parser import parse_hor_file
from horizon.test_manager.create_files import _sample_for_active_combos, construct_parameter_types

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

HORIZON = """
Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 10
}
"""

POLICY = 'ScenarioParameter "policy" = [bau, nzf, strict, other] { default = "bau" }\n'


def _parse(tmp_path, body):
    """(scenario parameters, sample parameters) of a .hor file made from body."""
    hor = tmp_path / "run.hor"
    hor.write_text(body + HORIZON, encoding="utf-8")
    result = parse_hor_file(str(hor))
    return result[3], result[4]


def _error(tmp_path, body):
    with pytest.raises(ParseError) as info:
        _parse(tmp_path, body)
    return info.value


def _one(tmp_path, body):
    """The first sample parameter of a file with the POLICY scenario and body."""
    return _parse(tmp_path, POLICY + body)[1][0]


def _bounds(parameter):
    return parameter.low_val, parameter.mid_val, parameter.high_val, parameter.distribution


class TestCall:

    def test_continuous_call_equals_block_form(self, tmp_path):
        call = _one(tmp_path, 'ContinuousParameter "x" = LOG-TRIANGULAR(0.6, 1.0, 1.8)\n')
        block = _one(tmp_path, 'ContinuousParameter "x" {\n low_val = 0.6\n mid_val = 1.0\n high_val = 1.8\n}\n')
        assert vars(call) == vars(block)
        assert _bounds(call) == (0.6, 1.0, 1.8, "log-triangular")
        assert call.default == 1.0 and call.decimals_auto and call.emissions_low is None

    def test_uniform_and_attributes_on_one_line(self, tmp_path):
        parameter = _one(tmp_path, 'ContinuousParameter "x" = UNIFORM(0.0, 1.0) { default = 0.5, emissions_low = MAXIMUM }\n')
        assert _bounds(parameter) == (0.0, None, 1.0, "uniform")
        assert parameter.emissions_low == "MAXIMUM"

    def test_comma_separated_attributes(self, tmp_path):
        parameter = _one(tmp_path, 'ContinuousParameter "x" = TRIANGULAR(0.1, 0.2, 0.3) '
                                   '{ default = 0.25, emissions_low = MAXIMUM, decimals = 3, name = "X" }\n')
        assert (parameter.default, parameter.emissions_low, parameter.decimals, parameter.name) == (
            "0.25", "MAXIMUM", 3, "X")

    def test_multiline_body_with_attributes_and_overrides(self, tmp_path):
        parameter = _one(tmp_path, """ContinuousParameter "x" = TRIANGULAR(0.10, 0.15, 0.20) {
    emissions_low = MAXIMUM
    default = 0.12
    if policy in (bau, nzf) = (0.05, 0.10, 0.15)
}
""")
        assert (parameter.emissions_low, parameter.default) == ("MAXIMUM", "0.12")
        assert parameter.overrides[0]["attrs"] == {"low_val": 0.05, "mid_val": 0.1, "high_val": 0.15}

    def test_call_may_span_lines_and_line_numbers_are_kept(self, tmp_path):
        error = _error(tmp_path, POLICY + 'ContinuousParameter "x" = TRIANGULAR(0.1,\n 0.2,\n 0.3) {\n'
                                          ' default = 0.2\n emisions_low = MAXIMUM\n}\n')
        assert error.location.endswith("run.hor:6") and "unknown key 'emisions_low'" in error.message

    def test_discrete_with_probabilities(self, tmp_path):
        parameter = _one(tmp_path, 'DiscreteParameter "y" = DISCRETE([3, 4, 5, 6], [0.15, 0.35, 0.35, 0.15]) '
                                   '{ default = 4 }\n')
        assert parameter.values == ["3", "4", "5", "6"]
        assert parameter.probabilities == [0.15, 0.35, 0.35, 0.15] and parameter.default == "4"

    def test_discrete_without_probabilities_is_uniform(self, tmp_path):
        parameter = _one(tmp_path, 'DiscreteParameter "y" = DISCRETE([3, 4, 5]) { default = 4 }\n')
        assert parameter.probabilities == pytest.approx([1 / 3] * 3)

    def test_discrete_still_needs_default(self, tmp_path):
        assert "no default" in _error(tmp_path, POLICY + 'DiscreteParameter "y" = DISCRETE([3, 4])\n').message

    def test_forms_can_be_mixed_in_one_file(self, tmp_path):
        parameters = _parse(tmp_path, POLICY + 'ContinuousParameter "a" = UNIFORM(0, 1) { default = 0.5 }\n'
                                               'ContinuousParameter "b" {\n low_val = 1\n high_val = 2\n'
                                               ' distribution = UNIFORM\n default = 1.5\n}\n')[1]
        assert [p.token for p in parameters] == ["a", "b"]

    @pytest.mark.parametrize("call, text", [
        ("TRIANGULAR(0.1, 0.3)", "TRIANGULAR takes 3 numbers (low, mode, high)"),
        ("LOG-TRIANGULAR(1)", "LOG-TRIANGULAR takes 3 numbers"),
        ("UNIFORM(0, 0.5, 1)", "UNIFORM takes 2 numbers (low, high)"),
        ("LOG-UNIFORM(1, a)", "LOG-UNIFORM takes 2 numbers"),
        ("NORMAL(0, 1)", "Unknown distribution 'NORMAL'"),
        ("DISCRETE([1, 2])", "Unknown distribution 'DISCRETE'"),
    ])
    def test_bad_call(self, tmp_path, call, text):
        error = _error(tmp_path, f'\nContinuousParameter "x" = {call}\n')
        assert text in error.message and error.location.endswith("run.hor:2")

    def test_bad_discrete_call(self, tmp_path):
        assert "expected DISCRETE" in _error(tmp_path, 'DiscreteParameter "y" = UNIFORM(0, 1)\n').message

    @pytest.mark.parametrize("extra", ["low_val = 0.5", "mid_val = 1", "distribution = UNIFORM", "high_val = 2"])
    def test_call_and_long_keys_in_one_block_is_an_error(self, tmp_path, extra):
        error = _error(tmp_path, f'\nContinuousParameter "x" = UNIFORM(0, 1) {{\n    default = 0.5\n    {extra}\n}}\n')
        assert error.location.endswith("run.hor:4")
        assert f"'{extra.split()[0]}' cannot be set next to the call" in error.message

    def test_call_and_probabilities_in_discrete_block_is_an_error(self, tmp_path):
        error = _error(tmp_path, 'DiscreteParameter "y" = DISCRETE([1, 2]) { default = 1, probabilities = [1, 1] }\n')
        assert "'probabilities' cannot be set next to the call" in error.message

    def test_unknown_key_in_braces(self, tmp_path):
        error = _error(tmp_path, '\nContinuousParameter "x" = UNIFORM(0, 1) { emisions_low = MAXIMUM }\n')
        assert "unknown key 'emisions_low'" in error.message and "Did you mean 'emissions_low'" in error.message
        assert error.location.endswith("run.hor:2")

    def test_long_keys_inside_if_block_are_fine(self, tmp_path):
        parameter = _one(tmp_path, 'ContinuousParameter "x" = UNIFORM(0, 1) {\n default = 0.5\n if policy = nzf {\n'
                                   '  low_val = 0.2\n  high_val = 0.4\n  default = 0.3\n }\n}\n')
        assert parameter.overrides[0]["attrs"]["low_val"] == 0.2


class TestOneLineOverrides:
    BASE = 'ContinuousParameter "x" = TRIANGULAR(0.10, 0.15, 0.20) {\n    %s\n}\n'

    def test_bounds_keep_base_distribution(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % "if policy in (bau, nzf) = (0.00, 0.05, 0.10)")
        override = parameter.overrides[0]
        assert override["conditions"] == [{"token": "policy", "op": "in", "values": ["bau", "nzf"]}]
        assert override["attrs"] == {"low_val": 0.0, "mid_val": 0.05, "high_val": 0.1}
        assert parameter.shallow_copy_with_overrides(override["attrs"]).distribution == "triangular"

    def test_equality_condition(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % "if policy = strict = (0.15, 0.20, 0.25)")
        assert parameter.overrides[0]["attrs"]["high_val"] == 0.25

    def test_distribution_change(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % "if policy = other = UNIFORM(0.1, 0.3)")
        attrs = parameter.overrides[0]["attrs"]
        assert attrs == {"distribution": "uniform", "low_val": 0.1, "high_val": 0.3}
        resolved = parameter.shallow_copy_with_overrides(attrs)
        assert (resolved.distribution, resolved.low_val, resolved.high_val) == ("uniform", 0.1, 0.3)

    def test_log_distribution_change_is_validated(self, tmp_path):
        error = _error(tmp_path, POLICY + self.BASE % "if policy = other = LOG-UNIFORM(0, 1)")
        assert "needs bounds above 0" in error.message and "policy = other" in error.message

    def test_base_is_an_explicit_no_change(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % "if policy in (strict, other) = base")
        assert parameter.overrides[0]["attrs"] == {}
        assert parameter.overrides[0]["conditions"][0]["values"] == ["strict", "other"]

    def test_condition_may_span_lines_and_line_numbers_are_kept(self, tmp_path):
        error = _error(tmp_path, POLICY + 'ContinuousParameter "x" = TRIANGULAR(0.1, 0.2, 0.3) {\n'
                                          '    if policy in (bau,\n                  ghost) = base\n}\n')
        assert error.location.endswith("run.hor:3") and "'ghost' is not a value" in error.message

    def test_long_form_base_distribution_decides_arity(self, tmp_path):
        base = 'ContinuousParameter "x" {\n low_val = 1\n high_val = 2\n distribution = UNIFORM\n default = 1.5\n %s\n}\n'
        parameter = _one(tmp_path, base % "if policy = nzf = (1.5, 2.5)")
        assert parameter.overrides[0]["attrs"] == {"low_val": 1.5, "high_val": 2.5}
        error = _error(tmp_path, POLICY + base % "if policy = nzf = (1.5, 1.7, 2.5)")
        assert "UNIFORM takes 2 numbers" in error.message

    def test_long_form_default_distribution_takes_three_numbers(self, tmp_path):
        base = 'ContinuousParameter "x" {\n low_val = 1\n mid_val = 2\n high_val = 3\n %s\n}\n'
        parameter = _one(tmp_path, base % "if policy = nzf = (1.5, 2.5, 3.5)")
        assert len(parameter.overrides[0]["attrs"]) == 3

    def test_mixes_with_block_overrides(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % ("if policy = bau = (0, 0.05, 0.1)\n"
                                                "    if policy = nzf {\n        low_val = 0.12\n"
                                                "        high_val = 0.18\n    }\n    if policy = other = base"))
        assert [list(o["attrs"]) for o in parameter.overrides] == [["low_val", "mid_val", "high_val"],
                                                                   ["low_val", "high_val"], []]

    @pytest.mark.parametrize("line, text", [
        ("if policy = bau = (0.1, 0.2)", "TRIANGULAR takes 3 numbers"),
        ("if policy = bau = (0.1, 0.2, 0.3, 0.4)", "TRIANGULAR takes 3 numbers"),
        ("if policy = bau = UNIFORM(0.1, 0.2, 0.3)", "UNIFORM takes 2 numbers"),
        ("if policy = bau = TRIANGULAR(0.1, 0.2)", "TRIANGULAR takes 3 numbers"),
        ("if policy = bau = (0.1, x, 0.3)", "takes 3 numbers"),
        ("if policy = bau = NORMAL(0, 1)", "Unknown distribution 'NORMAL'"),
    ])
    def test_bad_override_arity_names_the_line(self, tmp_path, line, text):
        error = _error(tmp_path, POLICY + self.BASE % line)
        assert text in error.message and error.location.endswith("run.hor:3")

    @pytest.mark.parametrize("line, text", [
        ("if ghost = bau = base", "'ghost' is not a ScenarioParameter"),
        ("if policy = ghost = base", "'ghost' is not a value"),
        ("if policy = bau = (0.1, 0.05, 0.3)", "mid_val (0.05) must lie"),
        ("if policy = bau = (0.5, 0.6, 0.3)", "low_val (0.5) must be <= high_val"),
    ])
    def test_existing_validation_applies(self, tmp_path, line, text):
        error = _error(tmp_path, POLICY + self.BASE % line)
        assert text in error.message and error.location.endswith("run.hor:3")

    def test_missing_right_hand_side_is_a_condition_error(self, tmp_path):
        error = _error(tmp_path, POLICY + self.BASE % "if policy = base")
        assert "Cannot parse condition: 'policy'" in error.message and error.location.endswith("run.hor:3")

    def test_discrete_only_takes_base(self, tmp_path):
        parameter = _one(tmp_path, 'DiscreteParameter "y" = DISCRETE([1, 2]) {\n default = 1\n'
                                   ' if policy = bau = base\n}\n')
        assert parameter.overrides[0]["attrs"] == {}
        error = _error(tmp_path, POLICY + 'DiscreteParameter "y" = DISCRETE([1, 2]) {\n default = 1\n'
                                          ' if policy = bau = (1, 2)\n}\n')
        assert "takes `= base` or a block" in error.message

    def test_resolves_per_scenario(self, tmp_path):
        parameter = _one(tmp_path, self.BASE % "if policy in (bau, nzf) = (0.00, 0.05, 0.10)")
        by_policy = {policy: resolve_parameters_for_scenario([parameter], {"policy": policy})[0]
                     for policy in ("bau", "strict")}
        assert by_policy["bau"].mid_val == 0.05 and by_policy["bau"].default == 0.05
        assert by_policy["strict"].mid_val == 0.15


class TestScenarioOneLine:

    def test_list_with_default(self, tmp_path):
        scenario = _parse(tmp_path, 'ScenarioParameter "policy" = [a, "b", c] { default = "b" }\n')[0][0]
        assert (scenario.values, scenario.default, scenario.active) == (["a", "b", "c"], '"b"', True)

    def test_list_without_body_and_over_lines(self, tmp_path):
        scenario = _parse(tmp_path, 'ScenarioParameter "policy" = [\n  a, b,\n  c\n]\n')[0][0]
        assert scenario.values == ["a", "b", "c"] and scenario.default is None

    def test_matches_block_form(self, tmp_path):
        short = _parse(tmp_path, 'ScenarioParameter "policy" = [a, b] { default = "a", active = FALSE }\n')[0][0]
        block = _parse(tmp_path, 'ScenarioParameter "policy" {\n values = [a, b]\n default = "a"\n'
                                 ' active = FALSE\n}\n')[0][0]
        assert vars(short) == vars(block)

    def test_needs_a_list(self, tmp_path):
        assert "expected a list of values" in _error(tmp_path, 'ScenarioParameter "policy" = a\n').message

    def test_values_next_to_the_list_is_an_error(self, tmp_path):
        assert "'values' cannot be set" in _error(tmp_path, 'ScenarioParameter "p" = [a] { values = [b] }\n').message


def _comparable(parameter):
    """Parameter state without the file line of its overrides."""
    state = dict(vars(parameter))
    state["overrides"] = [{key: value for key, value in override.items() if key != "line"}
                          for override in state.get("overrides", [])]
    return state


class TestOctoberEquivalence:
    """october/run.hor in the block form and entirely in the one-line forms parse the same."""

    @pytest.fixture
    def parsed(self):
        return (parse_hor_file(str(FIXTURES / "october_long.hor")),
                parse_hor_file(str(FIXTURES / "october_oneline.hor")))

    def test_same_settings_and_parameters(self, parsed):
        long_form, one_line = parsed
        assert long_form[:3] == one_line[:3] and long_form[5:] == one_line[5:]
        assert [_comparable(p) for p in long_form[3]] == [_comparable(p) for p in one_line[3]]
        assert len(long_form[4]) == len(one_line[4]) == 17
        assert [_comparable(p) for p in long_form[4]] == [_comparable(p) for p in one_line[4]]

    def test_same_parameters_for_every_policy(self, parsed):
        long_form, one_line = parsed
        for policy in long_form[3][0].values:
            scenario = {"policy": policy}
            assert ([_comparable(p) for p in resolve_parameters_for_scenario(long_form[4], scenario)]
                    == [_comparable(p) for p in resolve_parameters_for_scenario(one_line[4], scenario)])

    def test_same_samples_csv(self, parsed, tmp_path):
        outputs = []
        for index, model in enumerate(parsed):
            sampled, _, _ = _sample_for_active_combos(
                scenario_parameters=model[3], parameters=model[4], number_of_samples=5,
                sampling_method=model[5], random_seed=model[8], max_files=None)
            path = tmp_path / f"samples{index}.csv"
            output_sampled_parameters_to_csv(sampled, construct_parameter_types(model[4]), str(path))
            outputs.append(path.read_text())

        assert outputs[0] == outputs[1] and len(outputs[0].splitlines()) > 50
