# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for parser functionality.

Tests for horizon/parser/parser.py focusing on:
- File validation
- Parameter parsing
- Parse-time validation (Phase 1 improvements)
- Error handling
"""

import pytest

from horizon.exceptions import FileOperationError, ParseError, ValidationError
from horizon.parser.parser import parse_hor_file

# ============================================================================
# Test File Validation
# ============================================================================


class TestFileValidation:
    """Test file existence validation (Phase 1 improvement)."""

    def test_parse_nonexistent_file_raises_error(self, tmp_path):
        """Parsing non-existent file should raise FileOperationError."""
        nonexistent = tmp_path / "does_not_exist.hor"

        with pytest.raises(FileOperationError, match="File not found"):
            parse_hor_file(str(nonexistent))

    def test_parse_existing_file_succeeds(self, temp_hor_file):
        """Parsing existing file should succeed."""
        # Should not raise
        result = parse_hor_file(str(temp_hor_file))
        assert result is not None


# ============================================================================
# Test Parameter Parsing
# ============================================================================

class TestParameterParsing:
    """Test basic parameter parsing functionality."""

    def test_parse_continuous_parameter_from_file(self, temp_hor_file):
        """Should successfully parse continuous parameter from .hor file."""
        result = parse_hor_file(str(temp_hor_file))

        # parse_hor_file returns a tuple; check it's non-empty and contains parameters
        assert isinstance(result, tuple)
        assert len(result) > 0

    def test_missing_name_field_defaults_to_block_name(self, invalid_hor_file):
        """Missing 'name' field falls back to the block header name."""
        params = parse_hor_file(str(invalid_hor_file))[4]
        assert params[0].name == "PlaceholderName"


# ============================================================================
# Test Parse-Time Validation (Phase 1)
# ============================================================================

class TestParseTimeValidation:
    """Test parse-time validation added in Phase 1."""

    def test_inverted_bounds_raises_validation_error(self, tmp_path):
        """Parameter with low_val > high_val should raise ValidationError."""
        hor_content = """
ContinuousParameter "InvalidParameter" {
    name = "InvalidParameter"
    token = "INVALID"
    active = TRUE
    default = 50.0
    low_val = 100.0
    high_val = 0.0
    decimals = 2
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("INVALID")
}
"""
        file_path = tmp_path / "invalid_bounds.hor"
        file_path.write_text(hor_content)

        with pytest.raises(ValidationError, match="low_val.*must be < high_val"):
            parse_hor_file(str(file_path))

    def test_equal_bounds_raises_validation_error(self, tmp_path):
        """Parameter with low_val == high_val should raise ValidationError."""
        hor_content = """
ContinuousParameter "ConstantParameter" {
    name = "ConstantParameter"
    token = "CONST"
    active = TRUE
    default = 50.0
    low_val = 50.0
    high_val = 50.0
    decimals = 2
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("CONST")
}
"""
        file_path = tmp_path / "equal_bounds.hor"
        file_path.write_text(hor_content)

        with pytest.raises(ValidationError, match="low_val.*must be < high_val"):
            parse_hor_file(str(file_path))

    def test_negative_decimals_raises_validation_error(self, tmp_path):
        """Negative decimals value should raise ValidationError."""
        hor_content = """
ContinuousParameter "InvalidDecimals" {
    name = "InvalidDecimals"
    token = "INVALID_DEC"
    active = TRUE
    default = 50.0
    low_val = 0.0
    high_val = 100.0
    decimals = -1
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("INVALID_DEC")
}
"""
        file_path = tmp_path / "negative_decimals.hor"
        file_path.write_text(hor_content)

        with pytest.raises(ValidationError, match="decimals must be non-negative"):
            parse_hor_file(str(file_path))

    def test_valid_bounds_succeeds(self, tmp_path):
        """Valid parameter bounds should parse successfully."""
        hor_content = """
ContinuousParameter "ValidParameter" {
    name = "ValidParameter"
    token = "VALID"
    active = TRUE
    default = 50.0
    low_val = 0.0
    high_val = 100.0
    decimals = 2
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("VALID")
}
"""
        file_path = tmp_path / "valid_bounds.hor"
        file_path.write_text(hor_content)

        # Should not raise
        result = parse_hor_file(str(file_path))
        assert result is not None


# ============================================================================
# Test Missing Required Fields
# ============================================================================

class TestMissingFields:
    """Test handling of missing required fields in parameters."""

    def test_missing_token_with_invalid_block_name_raises_error(self, tmp_path):
        """Missing 'token' with a block name that is not a valid token should raise ParseError."""
        hor_content = """
ContinuousParameter "Missing token" {
    name = "MissingToken"
    active = TRUE
    default = 50.0
    low_val = 0.0
    high_val = 100.0
    decimals = 2
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
}
"""
        file_path = tmp_path / "missing_token.hor"
        file_path.write_text(hor_content)

        with pytest.raises(ParseError, match="has no 'token' field"):
            parse_hor_file(str(file_path))


# ============================================================================
# Test Edge Cases
# ============================================================================

class TestParserEdgeCases:
    """Test edge cases in parser."""

    def test_parse_parameter_with_negative_bounds(self, tmp_path):
        """Should handle negative parameter bounds."""
        hor_content = """
ContinuousParameter "NegativeParameter" {
    name = "NegativeParameter"
    token = "NEG_PARAM"
    active = TRUE
    default = -50.0
    low_val = -100.0
    high_val = 0.0
    decimals = 2
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("NEG_PARAM")
}
"""
        file_path = tmp_path / "negative_bounds.hor"
        file_path.write_text(hor_content)

        # Should not raise
        result = parse_hor_file(str(file_path))
        assert result is not None

    def test_parse_parameter_with_zero_decimals(self, tmp_path):
        """Should handle decimals = 0."""
        hor_content = """
ContinuousParameter "IntegerParameter" {
    name = "IntegerParameter"
    token = "INT_PARAM"
    active = TRUE
    default = 50.0
    low_val = 0.0
    high_val = 100.0
    decimals = 0
    distribution = "uniform"
}

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("INT_PARAM")
}
"""
        file_path = tmp_path / "zero_decimals.hor"
        file_path.write_text(hor_content)

        # Should not raise
        result = parse_hor_file(str(file_path))
        assert result is not None

    def test_parse_comment_lines_ignored(self, tmp_path):
        """Comment lines should be ignored."""
        hor_content = """
# This is a comment

# Comment before parameter
ContinuousParameter "TestParameter" {
    name = "TestParameter"
    token = "TEST_PARAM"
    active = TRUE
    default = 50.0
    low_val = 0.0
    high_val = 100.0
    decimals = 2
    distribution = "uniform"
}

# Another comment
Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("TEST_PARAM")
}
"""
        file_path = tmp_path / "with_comments.hor"
        file_path.write_text(hor_content)

        # Should not raise
        result = parse_hor_file(str(file_path))
        assert result is not None


# ============================================================================
# Test Short Format (optional token/name/default, no parameter list)
# ============================================================================

def _parse(tmp_path, body, horizon_extra=""):
    hor = tmp_path / "short.hor"
    hor.write_text(body + f"""
Horizon {{
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    {horizon_extra}
}}
""")
    result = parse_hor_file(str(hor))
    return result[3], result[4]


class TestShortFormat:
    """Optional token/name/default and implicit parameter list."""

    def test_token_and_name_default_to_block_name(self, tmp_path):
        _, params = _parse(tmp_path, """
ContinuousParameter "fuel_cost" {
    low_val = 1.0
    mid_val = 2.0
    high_val = 4.0
    decimals = 2
    distribution = "triangular"
}
""")
        assert params[0].token == "fuel_cost"
        assert params[0].name == "fuel_cost"

    def test_explicit_token_and_name_win(self, tmp_path):
        _, params = _parse(tmp_path, """
ContinuousParameter "fuel_cost" {
    name = "Fuel cost"
    token = "FUEL"
    low_val = 1.0
    mid_val = 2.0
    high_val = 4.0
    decimals = 2
}
""")
        assert params[0].token == "FUEL"
        assert params[0].name == "Fuel cost"

    def test_default_falls_back_to_mid_val(self, tmp_path):
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 1.0
    mid_val = 2.5
    high_val = 4.0
    decimals = 2
}
ContinuousParameter "b" {
    low_val = 25
    mid_val = 50
    high_val = 100
    decimals = 0
}
""")
        assert params[0].default == 2.5
        assert params[1].default == 50 and isinstance(params[1].default, int)

    def test_explicit_default_kept(self, tmp_path):
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 1.0
    mid_val = 2.5
    high_val = 4.0
    default = 3.0
    decimals = 2
}
""")
        assert float(params[0].default) == 3.0

    def test_no_mid_val_and_no_default_raises(self, tmp_path):
        with pytest.raises(ParseError, match="no mid_val and no default"):
            _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.0
    high_val = 1.0
    decimals = 2
    distribution = "uniform"
}
""")

    def test_override_mid_val_moves_implicit_default(self, tmp_path):
        from horizon.parameters.sampler import resolve_parameters_for_scenario
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.10
    mid_val = 0.15
    high_val = 0.20
    decimals = 3
    distribution = "triangular"
    if policy in (bau, liberia) {
        low_val = 0.00
        mid_val = 0.05
        high_val = 0.10
    }
}
""")
        assert resolve_parameters_for_scenario(params, {"policy": "bau"})[0].default == 0.05
        assert resolve_parameters_for_scenario(params, {"policy": "nzf"})[0].default == 0.15

    def test_override_mid_val_keeps_explicit_default(self, tmp_path):
        from horizon.parameters.sampler import resolve_parameters_for_scenario
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.10
    mid_val = 0.15
    high_val = 0.20
    default = 0.12
    decimals = 3
    if policy = bau {
        mid_val = 0.18
    }
}
""")
        assert float(resolve_parameters_for_scenario(params, {"policy": "bau"})[0].default) == 0.12

    def test_duplicate_name_across_types_raises(self, tmp_path):
        with pytest.raises(ParseError, match="defined twice"):
            _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
}
DiscreteParameter "a" {
    values = [1, 2]
    probabilities = [0.5, 0.5]
    default = 1
}
""")

    def test_all_blocks_included_without_list(self, tmp_path):
        scen, params = _parse(tmp_path, """
ScenarioParameter "policy" {
    values = ["bau", "nzf"]
    default = "bau"
}
ContinuousParameter "a" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
}
ContinuousParameter "b" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
    active = FALSE
}
""")
        assert [s.token for s in scen] == ["policy"]
        assert [p.token for p in params] == ["a", "b"]
        assert params[1].active is False

    def test_legacy_list_still_filters_with_warning(self, tmp_path, caplog):
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
}
ContinuousParameter "b" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
}
""", horizon_extra='Parameters = [ContinuousParameter("a")]')
        assert [p.token for p in params] == ["a"]
        assert "deprecated" in caplog.text

    def test_legacy_list_of_one_kind_filters_both_kinds(self, tmp_path):
        scen, params = _parse(tmp_path, """
ScenarioParameter "S" {
    values = ["a", "b"]
    default = "a"
}
ContinuousParameter "X" {
    low_val = 0.0
    mid_val = 0.5
    high_val = 1.0
    decimals = 2
}
""", horizon_extra='ScenarioParameters = [ScenarioParameter("S")]')
        assert [s.token for s in scen] == ["S"]
        assert params == []

    def test_rules_validated_against_defined_scenarios(self, tmp_path, caplog):
        _parse(tmp_path, """
ScenarioParameter "POLICY" {
    values = ["baseline", "carbon_levy"]
    default = "baseline"
}
""", horizon_extra="Exclude(POLICY = carbon_levy)\n    Exclude(POLICY = typo)")
        assert "unknown scenario token" not in caplog.text
        assert "'typo'" in caplog.text

    def test_discrete_without_default_raises(self, tmp_path):
        with pytest.raises(ParseError, match="has no default"):
            _parse(tmp_path, """
DiscreteParameter "D" {
    values = [1, 2]
    probabilities = [0.5, 0.5]
}
""")

    def test_explicit_default_in_override_wins(self, tmp_path):
        from horizon.parameters.sampler import resolve_parameters_for_scenario
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.10
    mid_val = 0.15
    high_val = 0.20
    decimals = 3
    if policy = bau {
        mid_val = 0.18
        default = 0.11
    }
}
""")
        assert resolve_parameters_for_scenario(params, {"policy": "bau"})[0].default == 0.11

    def test_decimals_override_rerounds_implicit_default(self, tmp_path):
        from horizon.parameters.sampler import resolve_parameters_for_scenario
        _, params = _parse(tmp_path, """
ContinuousParameter "a" {
    low_val = 0.1
    mid_val = 0.123456
    high_val = 0.2
    decimals = 2
    if policy = b {
        decimals = 4
    }
}
""")
        assert params[0].default == 0.12
        assert resolve_parameters_for_scenario(params, {"policy": "b"})[0].default == 0.1235
