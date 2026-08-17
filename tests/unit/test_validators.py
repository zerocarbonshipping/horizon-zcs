# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for validation utilities.

Tests for validators in horizon/validation/validators.py
"""

import pytest

from horizon.exceptions import FileOperationError, ValidationError
from horizon.validation.validators import (
    validate_bounds_order,
    validate_file_exists,
    validate_non_empty,
    validate_numeric_range,
    validate_positive_integer,
)

# ============================================================================
# Test validate_file_exists
# ============================================================================


class TestValidateFileExists:
    """Test validate_file_exists validator."""

    def test_existing_file_passes(self, temp_hor_file):
        """Existing file should pass validation."""
        # Should not raise
        validate_file_exists(str(temp_hor_file))

    def test_nonexistent_file_raises_error(self, tmp_path):
        """Non-existent file should raise FileOperationError."""
        nonexistent = tmp_path / "does_not_exist.hor"

        with pytest.raises(FileOperationError, match="File not found"):
            validate_file_exists(str(nonexistent))

    def test_file_exists_with_context(self, tmp_path):
        """Error message should include context."""
        nonexistent = tmp_path / "missing.unc"

        with pytest.raises(FileOperationError, match="configuration file"):
            validate_file_exists(str(nonexistent), context="configuration file")

    def test_directory_raises_error(self, tmp_path):
        """Passing a directory should raise error."""
        with pytest.raises(FileOperationError, match="not a file"):
            validate_file_exists(str(tmp_path))


# ============================================================================
# Test validate_numeric_range
# ============================================================================

class TestValidateNumericRange:
    """Test validate_numeric_range validator."""

    def test_value_in_range_passes(self):
        """Value within range should pass."""
        # Should not raise
        validate_numeric_range(50.0, 0.0, 100.0, "temperature")

    def test_value_at_lower_bound_passes(self):
        """Value at lower bound should pass."""
        validate_numeric_range(0.0, 0.0, 100.0, "value")

    def test_value_at_upper_bound_passes(self):
        """Value at upper bound should pass."""
        validate_numeric_range(100.0, 0.0, 100.0, "value")

    def test_value_below_range_raises_error(self):
        """Value below range should raise ValidationError."""
        with pytest.raises(ValidationError, match="outside valid range"):
            validate_numeric_range(-10.0, 0.0, 100.0, "temperature")

    def test_value_above_range_raises_error(self):
        """Value above range should raise ValidationError."""
        with pytest.raises(ValidationError, match="outside valid range"):
            validate_numeric_range(150.0, 0.0, 100.0, "temperature")

    def test_negative_range(self):
        """Can validate negative ranges."""
        validate_numeric_range(-50.0, -100.0, 0.0, "value")

    def test_error_includes_parameter_name(self):
        """Error message should include parameter name."""
        with pytest.raises(ValidationError, match="temperature"):
            validate_numeric_range(150.0, 0.0, 100.0, "temperature")

    def test_error_includes_context(self):
        """Error message should include context."""
        with pytest.raises(ValidationError, match="sampling"):
            validate_numeric_range(150.0, 0.0, 100.0, "value", context="sampling")


# ============================================================================
# Test validate_non_empty
# ============================================================================

class TestValidateNonEmpty:
    """Test validate_non_empty validator."""

    def test_non_empty_list_passes(self):
        """Non-empty list should pass."""
        validate_non_empty([1, 2, 3], "parameters")

    def test_non_empty_dict_passes(self):
        """Non-empty dict should pass."""
        validate_non_empty({"key": "value"}, "config")

    def test_non_empty_set_passes(self):
        """Non-empty set should pass."""
        validate_non_empty({1, 2, 3}, "values")

    def test_empty_list_raises_error(self):
        """Empty list should raise ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_non_empty([], "parameters")

    def test_empty_dict_raises_error(self):
        """Empty dict should raise ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_non_empty({}, "config")

    def test_empty_set_raises_error(self):
        """Empty set should raise ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_non_empty(set(), "values")

    def test_error_includes_name(self):
        """Error message should include collection name."""
        with pytest.raises(ValidationError, match="parameters"):
            validate_non_empty([], "parameters")

    def test_error_includes_context(self):
        """Error message should include context."""
        with pytest.raises(ValidationError, match="initialization"):
            validate_non_empty([], "parameters", context="initialization")


# ============================================================================
# Test validate_positive_integer
# ============================================================================

class TestValidatePositiveInteger:
    """Test validate_positive_integer validator."""

    def test_positive_integer_passes(self):
        """Positive integer should pass."""
        validate_positive_integer(10, "max_workers")

    def test_one_passes(self):
        """Value of 1 should pass."""
        validate_positive_integer(1, "count")

    def test_large_integer_passes(self):
        """Large positive integer should pass."""
        validate_positive_integer(1000000, "iterations")

    def test_zero_raises_error(self):
        """Zero should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be a positive integer"):
            validate_positive_integer(0, "max_workers")

    def test_negative_raises_error(self):
        """Negative integer should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be a positive integer"):
            validate_positive_integer(-5, "max_workers")

    def test_float_raises_error(self):
        """Float should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be a positive integer"):
            validate_positive_integer(10.5, "max_workers")

    def test_string_raises_error(self):
        """String should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be a positive integer"):
            validate_positive_integer("10", "max_workers")

    def test_none_raises_error(self):
        """None should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be a positive integer"):
            validate_positive_integer(None, "max_workers")

    def test_error_includes_name(self):
        """Error message should include parameter name."""
        with pytest.raises(ValidationError, match="max_workers"):
            validate_positive_integer(0, "max_workers")

    def test_error_includes_context(self):
        """Error message should include context."""
        with pytest.raises(ValidationError, match="configuration"):
            validate_positive_integer(0, "max_workers", context="configuration")

    def test_error_shows_actual_value(self):
        """Error message should show the actual value provided."""
        with pytest.raises(ValidationError, match="got: -5"):
            validate_positive_integer(-5, "max_workers")

    def test_error_shows_actual_type(self):
        """Error message should show the actual type provided."""
        with pytest.raises(ValidationError, match="str"):
            validate_positive_integer("10", "max_workers")


# ============================================================================
# Test validate_bounds_order
# ============================================================================

class TestValidateBoundsOrder:
    """Test validate_bounds_order validator."""

    def test_low_less_than_high_passes(self):
        """low < high should pass."""
        validate_bounds_order(0.0, 100.0, "temperature")

    def test_low_much_less_than_high_passes(self):
        """Large difference should pass."""
        validate_bounds_order(0.0, 1000000.0, "value")

    def test_negative_bounds_passes(self):
        """Negative bounds in correct order should pass."""
        validate_bounds_order(-100.0, -10.0, "temperature")

    def test_low_greater_than_high_raises_error(self):
        """low > high should raise ValidationError."""
        with pytest.raises(ValidationError, match="must be < high_val"):
            validate_bounds_order(100.0, 0.0, "temperature")

    def test_low_equal_high_raises_error_by_default(self):
        """low == high should raise error by default."""
        with pytest.raises(ValidationError, match="must be < high_val"):
            validate_bounds_order(50.0, 50.0, "temperature")

    def test_low_equal_high_passes_when_allowed(self):
        """low == high should pass when allow_equal=True."""
        # Should not raise
        validate_bounds_order(50.0, 50.0, "temperature", allow_equal=True)

    def test_low_greater_than_high_raises_error_even_when_equal_allowed(self):
        """low > high should still raise even with allow_equal=True."""
        with pytest.raises(ValidationError, match="must be <= high_val"):
            validate_bounds_order(100.0, 50.0, "temperature", allow_equal=True)

    def test_error_includes_parameter_name(self):
        """Error message should include parameter name."""
        with pytest.raises(ValidationError, match="temperature"):
            validate_bounds_order(100.0, 0.0, "temperature")

    def test_error_includes_context(self):
        """Error message should include context."""
        with pytest.raises(ValidationError, match="parameter definition"):
            validate_bounds_order(100.0, 0.0, "temperature", context="parameter definition")

    def test_error_shows_actual_values(self):
        """Error message should show the actual bound values."""
        with pytest.raises(ValidationError, match="100.0.*0.0"):
            validate_bounds_order(100.0, 0.0, "temperature")

    def test_very_close_values_pass(self):
        """Very close values (low < high) should pass."""
        validate_bounds_order(0.0, 0.000001, "temperature")

    def test_infinitesimal_difference(self):
        """Even tiny positive difference should pass."""
        validate_bounds_order(0.0, 1e-100, "value")
