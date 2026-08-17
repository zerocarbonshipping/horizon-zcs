# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Custom exception hierarchy for Horizon.

This module defines all custom exceptions used throughout the Horizon codebase
to provide structured error handling and clear error messages.
"""


class HorizonError(Exception):
    """Base exception for all Horizon-specific errors.

    All custom exceptions in Horizon should inherit from this class.
    This allows catching all Horizon-related errors with a single except clause.
    """
    pass


class ParseError(HorizonError):
    """Raised when parsing .hor configuration files fails.

    This includes:
    - Syntax errors in .hor files
    - Missing required fields
    - Invalid parameter block structure
    - Malformed override blocks
    """
    pass


class ValidationError(HorizonError):
    """Raised when configuration or parameter constraints are violated.

    This includes:
    - Parameter bounds violations (low_val >= high_val)
    - Invalid numerical ranges
    - Constraint violations
    - Invalid configuration values
    """
    pass


class FileOperationError(HorizonError):
    """Raised when file I/O operations fail.

    This includes:
    - Missing input files (.hor, .unc, .inc)
    - Permission errors
    - File read/write failures
    - Directory creation failures
    """
    pass


class ParameterError(HorizonError):
    """Raised when parameter definitions are invalid.

    This includes:
    - Empty parameter value lists
    - Invalid parameter types
    - Missing required parameter fields
    - Incompatible parameter configurations
    """
    pass


class SamplingError(HorizonError):
    """Raised during sampling operations.

    This includes:
    - Invalid sampling method
    - Numerical errors during sampling
    - Empty parameter sets
    - Distribution calculation failures
    """
    pass


class SensitivityAnalysisError(HorizonError):
    """Raised when PRCC sensitivity analysis encounters errors."""
    pass
