# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Core validation functions for Horizon.

This module provides reusable validation utilities that raise appropriate
exceptions when validation fails. All validators follow a consistent pattern:
- Accept the value to validate
- Accept a descriptive name for error messages
- Accept an optional context string for additional error information
- Raise specific exception types on validation failure
"""

import os
from typing import Any, Collection

from horizon.exceptions import FileOperationError, ValidationError


def validate_file_exists(path: str, context: str = "") -> None:
    """Validate that a file exists at the given path.

    Args:
        path: The file path to check
        context: Optional context string for error message (e.g., "configuration file", "UNC template")

    Raises:
        FileOperationError: If the file does not exist

    Example:
        >>> validate_file_exists("/path/to/config.hor", "configuration file")
    """
    if not os.path.exists(path):
        context_msg = f" ({context})" if context else ""
        raise FileOperationError(f"File not found{context_msg}: {path}")

    if not os.path.isfile(path):
        context_msg = f" ({context})" if context else ""
        raise FileOperationError(f"Path exists but is not a file{context_msg}: {path}")


def validate_numeric_range(
    value: float,
    min_val: float,
    max_val: float,
    name: str,
    context: str = ""
) -> None:
    """Validate that a numeric value is within a specified range.

    Args:
        value: The value to validate
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
        name: Descriptive name of the parameter for error messages
        context: Optional context string for error message

    Raises:
        ValidationError: If value is outside the specified range

    Example:
        >>> validate_numeric_range(50.0, 0.0, 100.0, "temperature", "parameter validation")
    """
    if not (min_val <= value <= max_val):
        context_msg = f" ({context})" if context else ""
        raise ValidationError(
            f"{name}{context_msg} value {value} is outside valid range [{min_val}, {max_val}]"
        )


def validate_non_empty(collection: Collection, name: str, context: str = "") -> None:
    """Validate that a collection is non-empty.

    Args:
        collection: The collection to check (list, set, dict, etc.)
        name: Descriptive name of the collection for error messages
        context: Optional context string for error message

    Raises:
        ValidationError: If the collection is empty

    Example:
        >>> validate_non_empty(parameter_list, "parameters", "sampling")
    """
    if not collection:
        context_msg = f" ({context})" if context else ""
        raise ValidationError(f"{name}{context_msg} cannot be empty")


def validate_positive_integer(value: Any, name: str, context: str = "") -> None:
    """Validate that a value is a positive integer.

    Args:
        value: The value to validate
        name: Descriptive name of the parameter for error messages
        context: Optional context string for error message

    Raises:
        ValidationError: If value is not a positive integer

    Example:
        >>> validate_positive_integer(max_workers, "max_parallel_workers", "configuration")
    """
    if not isinstance(value, int) or value <= 0:
        context_msg = f" ({context})" if context else ""
        raise ValidationError(
            f"{name}{context_msg} must be a positive integer, got: {value} ({type(value).__name__})"
        )


def validate_bounds_order(
    low_val: float,
    high_val: float,
    param_name: str,
    allow_equal: bool = False,
    context: str = ""
) -> None:
    """Validate that low_val < high_val (or <= if allow_equal=True).

    Args:
        low_val: The lower bound
        high_val: The upper bound
        param_name: Descriptive name of the parameter for error messages
        allow_equal: If True, allow low_val == high_val (for degenerate distributions)
        context: Optional context string for error message

    Raises:
        ValidationError: If bounds are in wrong order

    Example:
        >>> validate_bounds_order(0.0, 100.0, "temperature", context="parameter definition")
    """
    context_msg = f" ({context})" if context else ""

    if allow_equal:
        if low_val > high_val:
            raise ValidationError(
                f"Parameter '{param_name}'{context_msg}: low_val ({low_val}) must be <= high_val ({high_val})"
            )
    else:
        if low_val >= high_val:
            raise ValidationError(
                f"Parameter '{param_name}'{context_msg}: low_val ({low_val}) must be < high_val ({high_val})"
            )
