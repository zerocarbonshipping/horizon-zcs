# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for custom exception hierarchy.

Tests for horizon/exceptions.py
"""

import pytest

from horizon.exceptions import (
    FileOperationError,
    HorizonError,
    ParameterError,
    ParseError,
    SamplingError,
    ValidationError,
)

# ============================================================================
# Test Exception Hierarchy
# ============================================================================


class TestExceptionHierarchy:
    """Test that all exceptions inherit from HorizonError."""

    def test_horizon_error_is_exception(self):
        """HorizonError should inherit from Exception."""
        assert issubclass(HorizonError, Exception)

    def test_parse_error_inherits_from_horizon_error(self):
        """ParseError should inherit from HorizonError."""
        assert issubclass(ParseError, HorizonError)

    def test_validation_error_inherits_from_horizon_error(self):
        """ValidationError should inherit from HorizonError."""
        assert issubclass(ValidationError, HorizonError)

    def test_file_operation_error_inherits_from_horizon_error(self):
        """FileOperationError should inherit from HorizonError."""
        assert issubclass(FileOperationError, HorizonError)

    def test_parameter_error_inherits_from_horizon_error(self):
        """ParameterError should inherit from HorizonError."""
        assert issubclass(ParameterError, HorizonError)

    def test_sampling_error_inherits_from_horizon_error(self):
        """SamplingError should inherit from HorizonError."""
        assert issubclass(SamplingError, HorizonError)


# ============================================================================
# Test Exception Creation and Messages
# ============================================================================

class TestExceptionMessages:
    """Test that exceptions can be created with messages."""

    def test_horizon_error_with_message(self):
        """Can create HorizonError with custom message."""
        error = HorizonError("Test error message")
        assert str(error) == "Test error message"

    def test_parse_error_with_message(self):
        """Can create ParseError with custom message."""
        error = ParseError("Failed to parse configuration")
        assert str(error) == "Failed to parse configuration"

    def test_validation_error_with_message(self):
        """Can create ValidationError with custom message."""
        error = ValidationError("Invalid parameter value")
        assert str(error) == "Invalid parameter value"

    def test_file_operation_error_with_message(self):
        """Can create FileOperationError with custom message."""
        error = FileOperationError("File not found: test.hor")
        assert str(error) == "File not found: test.hor"

    def test_parameter_error_with_message(self):
        """Can create ParameterError with custom message."""
        error = ParameterError("Empty values list")
        assert str(error) == "Empty values list"

    def test_sampling_error_with_message(self):
        """Can create SamplingError with custom message."""
        error = SamplingError("Cannot compute min for empty values")
        assert str(error) == "Cannot compute min for empty values"


# ============================================================================
# Test Exception Catching
# ============================================================================

class TestExceptionCatching:
    """Test that exceptions can be caught correctly."""

    def test_catch_specific_parse_error(self):
        """Can catch ParseError specifically."""
        with pytest.raises(ParseError):
            raise ParseError("Test")

    def test_catch_parse_error_as_horizon_error(self):
        """ParseError can be caught as HorizonError."""
        with pytest.raises(HorizonError):
            raise ParseError("Test")

    def test_catch_all_horizon_errors(self):
        """Can catch all Horizon errors with base class."""
        exceptions = [
            ParseError("Parse"),
            ValidationError("Validation"),
            FileOperationError("File"),
            ParameterError("Parameter"),
            SamplingError("Sampling"),
        ]

        for exc in exceptions:
            with pytest.raises(HorizonError):
                raise exc

    def test_specific_error_not_caught_by_other_specific(self):
        """ParseError should not be caught by ValidationError."""
        with pytest.raises(ParseError):
            with pytest.raises(ValidationError):
                raise ParseError("Test")


# ============================================================================
# Test Exception Usage Examples
# ============================================================================

class TestExceptionUsageExamples:
    """Test realistic usage patterns for exceptions."""

    def test_file_not_found_error(self):
        """Realistic file not found error."""
        file_path = "/path/to/missing.hor"
        with pytest.raises(FileOperationError, match="missing.hor"):
            raise FileOperationError(f"Configuration file not found: {file_path}")

    def test_parameter_validation_error(self):
        """Realistic parameter validation error."""
        param_name = "temperature"
        low_val = 100.0
        high_val = 0.0
        with pytest.raises(ValidationError, match="temperature.*100.0.*0.0"):
            raise ValidationError(
                f"Parameter '{param_name}': low_val ({low_val}) must be < high_val ({high_val})"
            )

    def test_parsing_error_with_context(self):
        """Realistic parsing error with context."""
        with pytest.raises(ParseError, match="Missing 'name' field"):
            raise ParseError("Missing 'name' field in parameter block")

    def test_sampling_error_for_empty_values(self):
        """Realistic sampling error for empty values."""
        token = "FUEL"
        with pytest.raises(SamplingError, match="empty values.*FUEL"):
            raise SamplingError(f"Cannot compute min for empty values in '{token}'")

    def test_parameter_error_for_empty_list(self):
        """Realistic parameter error for empty list."""
        param_name = "FuelTypes"
        with pytest.raises(ParameterError, match="FuelTypes.*empty"):
            raise ParameterError(f"DiscreteParameter '{param_name}' has empty values list")
