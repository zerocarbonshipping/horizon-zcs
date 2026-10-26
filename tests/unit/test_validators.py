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
