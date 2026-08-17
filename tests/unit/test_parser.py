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

    def test_parse_missing_name_field_raises_error(self, invalid_hor_file):
        """Missing 'name' field should raise ParseError."""
        with pytest.raises(ParseError, match="Missing 'name' field"):
            parse_hor_file(str(invalid_hor_file))


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

    def test_missing_token_field_raises_error(self, tmp_path):
        """Missing 'token' field should raise ParseError."""
        hor_content = """
ContinuousParameter "MissingToken" {
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

        with pytest.raises(ParseError, match="Missing 'token' field"):
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
