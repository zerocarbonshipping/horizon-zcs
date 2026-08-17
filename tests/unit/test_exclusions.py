# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
Tests for horizon.parser.exclusions module.

Covers:
- parse_exclude_directive: key=value parsing with quoted/unquoted values
- parse_all_exclusions: extracting Exclude() directives from Horizon block content
- parse_all_inclusions: extracting Include() directives from Horizon block content
- is_combination_excluded: partial matching logic
- should_skip_combination: unified inclusion/exclusion filter logic
"""

import pytest

from horizon.exceptions import ParseError
from horizon.parser.exclusions import (
    get_scenario_label,
    is_combination_excluded,
    parse_all_exclusions,
    parse_all_inclusions,
    parse_exclude_directive,
    should_skip_combination,
)


# ---------------------------------------------------------------------------
# parse_exclude_directive
# ---------------------------------------------------------------------------
class TestParseExcludeDirective:
    """Tests for parse_exclude_directive()."""

    def test_single_pair_unquoted(self):
        result = parse_exclude_directive('BIO = high')
        assert result == {"BIO": "high"}

    def test_single_pair_double_quoted(self):
        result = parse_exclude_directive('BIO = "high"')
        assert result == {"BIO": "high"}

    def test_single_pair_single_quoted(self):
        result = parse_exclude_directive("BIO = 'high'")
        assert result == {"BIO": "high"}

    def test_multiple_pairs(self):
        result = parse_exclude_directive('BIO = "high", ELEC = "high"')
        assert result == {"BIO": "high", "ELEC": "high"}

    def test_multiple_pairs_unquoted(self):
        result = parse_exclude_directive('BIO = high, ELEC = low')
        assert result == {"BIO": "high", "ELEC": "low"}

    def test_mixed_quoting(self):
        result = parse_exclude_directive('BIO = "high", ELEC = low')
        assert result == {"BIO": "high", "ELEC": "low"}

    def test_whitespace_variations(self):
        result = parse_exclude_directive('  BIO  =  high  ,  ELEC  =  low  ')
        assert result == {"BIO": "high", "ELEC": "low"}

    def test_three_pairs(self):
        result = parse_exclude_directive('BIO = high, ELEC = low, POLICY = strict')
        assert result == {"BIO": "high", "ELEC": "low", "POLICY": "strict"}

    def test_underscore_in_token(self):
        result = parse_exclude_directive('MY_TOKEN = value_1')
        assert result == {"MY_TOKEN": "value_1"}

    def test_numeric_value(self):
        result = parse_exclude_directive('PARAM = 42')
        assert result == {"PARAM": "42"}

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="No valid key=value pairs"):
            parse_exclude_directive('')

    def test_garbage_input_raises(self):
        with pytest.raises(ValueError, match="No valid key=value pairs"):
            parse_exclude_directive('no pairs here')


# ---------------------------------------------------------------------------
# parse_all_exclusions
# ---------------------------------------------------------------------------
class TestParseAllExclusions:
    """Tests for parse_all_exclusions()."""

    def test_no_exclusions(self):
        content = """
        UncFilePath = "file.unc"
        OutputPath = "output/"
        NumberOfSamples = 100
        """
        assert parse_all_exclusions(content) == []

    def test_single_exclusion(self):
        content = """
        UncFilePath = "file.unc"
        Exclude(BIO = "high", ELEC = "high")
        NumberOfSamples = 100
        """
        result = parse_all_exclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high", "ELEC": "high"}

    def test_multiple_exclusions(self):
        content = """
        Exclude(BIO = "high", ELEC = "high")
        Exclude(BIO = "low", POLICY = "strict")
        """
        result = parse_all_exclusions(content)
        assert len(result) == 2
        assert result[0] == {"BIO": "high", "ELEC": "high"}
        assert result[1] == {"BIO": "low", "POLICY": "strict"}

    def test_mixed_with_other_directives(self):
        content = """
        UncFilePath = "file.unc"
        ScenarioParameter("BIO")
        Exclude(BIO = high, ELEC = high)
        ScenarioParameter("ELEC")
        OutputPath = "output/"
        Exclude(POLICY = strict)
        NumberOfSamples = 50
        """
        result = parse_all_exclusions(content)
        assert len(result) == 2
        assert result[0] == {"BIO": "high", "ELEC": "high"}
        assert result[1] == {"POLICY": "strict"}

    def test_case_insensitive_keyword(self):
        content = 'exclude(BIO = high)'
        result = parse_all_exclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high"}

    def test_empty_content(self):
        assert parse_all_exclusions("") == []

    def test_exclude_with_extra_whitespace(self):
        content = '  Exclude  (  BIO = high  ,  ELEC = low  )  '
        result = parse_all_exclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high", "ELEC": "low"}


# ---------------------------------------------------------------------------
# is_combination_excluded
# ---------------------------------------------------------------------------
class TestIsCombinationExcluded:
    """Tests for is_combination_excluded()."""

    def test_no_rules_returns_false(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        assert is_combination_excluded(scenario, []) is False

    def test_none_rules_returns_false(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        assert is_combination_excluded(scenario, None) is False

    def test_exact_match(self):
        scenario = {"BIO": "high", "ELEC": "high"}
        rules = [{"BIO": "high", "ELEC": "high"}]
        assert is_combination_excluded(scenario, rules) is True

    def test_partial_match_single_token(self):
        """A rule with one token should match any combo containing that token=value."""
        scenario = {"BIO": "high", "ELEC": "low", "POLICY": "strict"}
        rules = [{"BIO": "high"}]
        assert is_combination_excluded(scenario, rules) is True

    def test_partial_match_two_tokens(self):
        scenario = {"BIO": "high", "ELEC": "high", "POLICY": "relaxed"}
        rules = [{"BIO": "high", "ELEC": "high"}]
        assert is_combination_excluded(scenario, rules) is True

    def test_no_match(self):
        scenario = {"BIO": "low", "ELEC": "low"}
        rules = [{"BIO": "high", "ELEC": "high"}]
        assert is_combination_excluded(scenario, rules) is False

    def test_partial_no_match(self):
        """Rule requires both BIO=high AND ELEC=high, but ELEC=low."""
        scenario = {"BIO": "high", "ELEC": "low"}
        rules = [{"BIO": "high", "ELEC": "high"}]
        assert is_combination_excluded(scenario, rules) is False

    def test_multiple_rules_first_matches(self):
        scenario = {"BIO": "high", "ELEC": "high"}
        rules = [
            {"BIO": "high", "ELEC": "high"},
            {"BIO": "low", "POLICY": "strict"},
        ]
        assert is_combination_excluded(scenario, rules) is True

    def test_multiple_rules_second_matches(self):
        scenario = {"BIO": "low", "ELEC": "medium", "POLICY": "strict"}
        rules = [
            {"BIO": "high", "ELEC": "high"},
            {"BIO": "low", "POLICY": "strict"},
        ]
        assert is_combination_excluded(scenario, rules) is True

    def test_multiple_rules_none_match(self):
        scenario = {"BIO": "medium", "ELEC": "medium"}
        rules = [
            {"BIO": "high", "ELEC": "high"},
            {"BIO": "low", "POLICY": "strict"},
        ]
        assert is_combination_excluded(scenario, rules) is False

    def test_rule_token_not_in_scenario(self):
        """If rule references a token not in scenario_map, it should not match."""
        scenario = {"BIO": "high", "ELEC": "low"}
        rules = [{"POLICY": "strict"}]
        assert is_combination_excluded(scenario, rules) is False

    def test_empty_scenario_map(self):
        rules = [{"BIO": "high"}]
        assert is_combination_excluded({}, rules) is False

    def test_empty_both(self):
        assert is_combination_excluded({}, []) is False
        assert is_combination_excluded({}, None) is False


# ---------------------------------------------------------------------------
# parse_all_inclusions
# ---------------------------------------------------------------------------
class TestParseAllInclusions:
    """Tests for parse_all_inclusions()."""

    def test_no_inclusions(self):
        content = """
        UncFilePath = "file.unc"
        OutputPath = "output/"
        NumberOfSamples = 100
        """
        assert parse_all_inclusions(content) == []

    def test_single_inclusion(self):
        content = """
        UncFilePath = "file.unc"
        Include(BIO = "high")
        NumberOfSamples = 100
        """
        result = parse_all_inclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high"}

    def test_multiple_inclusions(self):
        content = """
        Include(BIO = "high", ELEC = "low")
        Include(BIO = "low", POLICY = "strict")
        """
        result = parse_all_inclusions(content)
        assert len(result) == 2
        assert result[0] == {"BIO": "high", "ELEC": "low"}
        assert result[1] == {"BIO": "low", "POLICY": "strict"}

    def test_mixed_with_exclude_and_other_directives(self):
        content = """
        UncFilePath = "file.unc"
        ScenarioParameter("BIO")
        Include(BIO = high)
        Exclude(BIO = high, ELEC = high)
        ScenarioParameter("ELEC")
        Include(POLICY = strict)
        NumberOfSamples = 50
        """
        result = parse_all_inclusions(content)
        assert len(result) == 2
        assert result[0] == {"BIO": "high"}
        assert result[1] == {"POLICY": "strict"}

    def test_case_insensitive_keyword(self):
        content = 'include(BIO = high)'
        result = parse_all_inclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high"}

    def test_empty_content(self):
        assert parse_all_inclusions("") == []

    def test_include_with_extra_whitespace(self):
        content = '  Include  (  BIO = high  ,  ELEC = low  )  '
        result = parse_all_inclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high", "ELEC": "low"}

    def test_include_does_not_capture_exclude(self):
        """Include parser should not pick up Exclude directives."""
        content = """
        Exclude(BIO = "high")
        Include(ELEC = "low")
        """
        result = parse_all_inclusions(content)
        assert len(result) == 1
        assert result[0] == {"ELEC": "low"}


# ---------------------------------------------------------------------------
# should_skip_combination
# ---------------------------------------------------------------------------
class TestShouldSkipCombination:
    """Tests for should_skip_combination()."""

    def test_no_rules_dont_skip(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        assert should_skip_combination(scenario, [], []) is False

    def test_no_rules_none_dont_skip(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        assert should_skip_combination(scenario, None, None) is False

    # --- Exclusion only (backward compat) ---

    def test_exclusion_only_match_skips(self):
        scenario = {"BIO": "high", "ELEC": "high"}
        exclusions = [{"BIO": "high", "ELEC": "high"}]
        assert should_skip_combination(scenario, exclusions, None) is True

    def test_exclusion_only_no_match_keeps(self):
        scenario = {"BIO": "low", "ELEC": "low"}
        exclusions = [{"BIO": "high", "ELEC": "high"}]
        assert should_skip_combination(scenario, exclusions, None) is False

    def test_exclusion_only_partial_match_skips(self):
        scenario = {"BIO": "high", "ELEC": "low", "POLICY": "strict"}
        exclusions = [{"BIO": "high"}]
        assert should_skip_combination(scenario, exclusions, None) is True

    # --- Inclusion only (whitelist) ---

    def test_inclusion_only_match_keeps(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        inclusions = [{"BIO": "high"}]
        assert should_skip_combination(scenario, None, inclusions) is False

    def test_inclusion_only_no_match_skips(self):
        scenario = {"BIO": "low", "ELEC": "low"}
        inclusions = [{"BIO": "high"}]
        assert should_skip_combination(scenario, None, inclusions) is True

    def test_inclusion_only_partial_match_keeps(self):
        """Include(BIO=high) should keep any combo where BIO=high, regardless of other tokens."""
        scenario = {"BIO": "high", "ELEC": "high", "POLICY": "strict"}
        inclusions = [{"BIO": "high"}]
        assert should_skip_combination(scenario, None, inclusions) is False

    def test_inclusion_only_multiple_rules_any_matches(self):
        """If any inclusion rule matches, the combo is kept."""
        scenario = {"BIO": "low", "ELEC": "high"}
        inclusions = [{"BIO": "high"}, {"ELEC": "high"}]
        assert should_skip_combination(scenario, None, inclusions) is False

    def test_inclusion_only_multiple_rules_none_match(self):
        scenario = {"BIO": "medium", "ELEC": "medium"}
        inclusions = [{"BIO": "high"}, {"ELEC": "high"}]
        assert should_skip_combination(scenario, None, inclusions) is True

    # --- Both inclusion + exclusion ---

    def test_both_include_narrows_exclude_removes(self):
        """Include keeps BIO=high combos, Exclude removes the ELEC=high subset."""
        inclusions = [{"BIO": "high"}]
        exclusions = [{"ELEC": "high"}]

        # BIO=high, ELEC=low -> included and not excluded -> keep
        assert should_skip_combination({"BIO": "high", "ELEC": "low"}, exclusions, inclusions) is False

        # BIO=high, ELEC=high -> included but then excluded -> skip
        assert should_skip_combination({"BIO": "high", "ELEC": "high"}, exclusions, inclusions) is True

        # BIO=low, ELEC=low -> not included -> skip
        assert should_skip_combination({"BIO": "low", "ELEC": "low"}, exclusions, inclusions) is True

    def test_both_not_included_skips_even_if_not_excluded(self):
        """If combo doesn't match inclusion, it's skipped regardless of exclusion."""
        inclusions = [{"BIO": "high"}]
        exclusions = [{"BIO": "low"}]  # Would exclude BIO=low, but inclusion filter kicks first

        scenario = {"BIO": "medium", "ELEC": "low"}
        assert should_skip_combination(scenario, exclusions, inclusions) is True

    def test_empty_scenario_map(self):
        assert should_skip_combination({}, [], []) is False
        assert should_skip_combination({}, [{"BIO": "high"}], None) is False
        assert should_skip_combination({}, None, [{"BIO": "high"}]) is True


# ---------------------------------------------------------------------------
# Include _name label support
# ---------------------------------------------------------------------------
class TestIncludeNameParsing:
    """Tests for _name parsing in Include() directives."""

    def test_name_stays_in_rule_dict(self):
        content = 'Include(BIO = "high", ELEC = "low", _name = "s1")'
        result = parse_all_inclusions(content)
        assert len(result) == 1
        assert result[0] == {"BIO": "high", "ELEC": "low", "_name": "s1"}

    def test_multiple_rules_with_names(self):
        content = """
        Include(BIO = "high", ELEC = "low", _name = "s1")
        Include(BIO = "low", ELEC = "high", _name = "s2")
        """
        result = parse_all_inclusions(content)
        assert len(result) == 2
        assert result[0]["_name"] == "s1"
        assert result[1]["_name"] == "s2"

    def test_mixed_name_raises_parse_error(self):
        """If some Include rules have _name and others don't, raise ParseError."""
        content = """
        Include(BIO = "high", _name = "s1")
        Include(BIO = "low")
        """
        with pytest.raises(ParseError, match="Mixed _name usage"):
            parse_all_inclusions(content)

    def test_no_names_is_fine(self):
        content = """
        Include(BIO = "high")
        Include(BIO = "low")
        """
        result = parse_all_inclusions(content)
        assert len(result) == 2
        assert "_name" not in result[0]
        assert "_name" not in result[1]


class TestMatchesAnyRuleIgnoresName:
    """_matches_any_rule should ignore _name when matching."""

    def test_name_not_used_for_matching(self):
        """A rule with _name should match based on filter keys only."""
        scenario = {"BIO": "high", "ELEC": "low"}
        inclusions = [{"BIO": "high", "ELEC": "low", "_name": "s1"}]
        # Should match (not skip) since BIO and ELEC match
        assert should_skip_combination(scenario, None, inclusions) is False

    def test_name_does_not_cause_mismatch(self):
        """_name in rule should not cause a false negative."""
        scenario = {"BIO": "high"}
        inclusions = [{"BIO": "high", "_name": "s1"}]
        assert should_skip_combination(scenario, None, inclusions) is False

    def test_filter_keys_still_must_match(self):
        scenario = {"BIO": "low"}
        inclusions = [{"BIO": "high", "_name": "s1"}]
        assert should_skip_combination(scenario, None, inclusions) is True


class TestGetScenarioLabel:
    """Tests for get_scenario_label()."""

    def test_returns_matching_label(self):
        scenario = {"BIO": "high", "ELEC": "low"}
        rules = [
            {"BIO": "high", "ELEC": "low", "_name": "s1"},
            {"BIO": "low", "ELEC": "high", "_name": "s2"},
        ]
        assert get_scenario_label(scenario, rules) == "s1"

    def test_returns_second_label(self):
        scenario = {"BIO": "low", "ELEC": "high"}
        rules = [
            {"BIO": "high", "ELEC": "low", "_name": "s1"},
            {"BIO": "low", "ELEC": "high", "_name": "s2"},
        ]
        assert get_scenario_label(scenario, rules) == "s2"

    def test_returns_none_when_no_rules(self):
        assert get_scenario_label({"BIO": "high"}, None) is None
        assert get_scenario_label({"BIO": "high"}, []) is None

    def test_returns_none_when_no_names(self):
        rules = [{"BIO": "high"}]
        assert get_scenario_label({"BIO": "high"}, rules) is None

    def test_returns_none_when_no_match(self):
        rules = [{"BIO": "high", "_name": "s1"}]
        assert get_scenario_label({"BIO": "low"}, rules) is None

    def test_partial_match(self):
        """Rule with subset of tokens should match."""
        scenario = {"BIO": "high", "ELEC": "low", "POLICY": "strict"}
        rules = [{"BIO": "high", "_name": "s1"}]
        assert get_scenario_label(scenario, rules) == "s1"
