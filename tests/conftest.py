# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures for Horizon tests.

This module provides reusable pytest fixtures for testing parameters,
sampling, parsing, and other Horizon components.
"""

import numpy as np
import pytest

from horizon.parameters.parameter import (
    ContinuousParameter,
    DiscreteParameter,
    ScenarioParameter,
)

# ============================================================================
# Parameter Fixtures
# ============================================================================


@pytest.fixture
def simple_continuous_param():
    """Simple continuous parameter with uniform distribution."""
    return ContinuousParameter(
        name="Temperature",
        token="TEMP",
        active=True,
        default=50.0,
        low_val=0.0,
        mid_val=50.0,
        high_val=100.0,
        distribution="uniform",
        decimals=2,
    )


@pytest.fixture
def triangular_param():
    """Continuous parameter with triangular distribution."""
    return ContinuousParameter(
        name="Speed",
        token="SPEED",
        active=True,
        default=15.0,
        low_val=10.0,
        mid_val=15.0,
        high_val=20.0,
        distribution="triangular",
        decimals=1,
    )


@pytest.fixture
def log_triangular_param():
    """Continuous parameter with log-triangular distribution."""
    return ContinuousParameter(
        name="Cost",
        token="COST",
        active=True,
        default=1000.0,
        low_val=100.0,
        mid_val=1000.0,
        high_val=10000.0,
        distribution="log-triangular",
        decimals=0,
    )


@pytest.fixture
def degenerate_param():
    """Edge case: continuous parameter where low == mid == high."""
    return ContinuousParameter(
        name="ConstantValue",
        token="CONST",
        active=True,
        default=42.0,
        low_val=42.0,
        mid_val=42.0,
        high_val=42.0,
        distribution="uniform",
        decimals=0,
    )


@pytest.fixture
def inactive_continuous_param():
    """Inactive continuous parameter (should use default value)."""
    return ContinuousParameter(
        name="InactiveParam",
        token="INACTIVE",
        active=False,
        default=25.0,
        low_val=0.0,
        mid_val=25.0,
        high_val=50.0,
        distribution="uniform",
        decimals=1,
    )


@pytest.fixture
def simple_discrete_param():
    """Simple discrete parameter with uniform probabilities."""
    return DiscreteParameter(
        name="FuelType",
        token="FUEL",
        active=True,
        default="HFO",
        values=["HFO", "LNG", "Methanol", "Ammonia"],
        probabilities="uniform",
    )


@pytest.fixture
def weighted_discrete_param():
    """Discrete parameter with non-uniform probabilities."""
    return DiscreteParameter(
        name="WeatherCondition",
        token="WEATHER",
        active=True,
        default="Calm",
        values=["Calm", "Moderate", "Rough", "Storm"],
        probabilities=[0.5, 0.3, 0.15, 0.05],
    )


@pytest.fixture
def simple_scenario_param():
    """Simple scenario parameter."""
    return ScenarioParameter(
        name="PolicyScenario",
        token="POLICY",
        active=True,
        default="BAU",
        values=["BAU", "NetZero2050", "NetZero2040"],
    )


@pytest.fixture
def inactive_scenario_param():
    """Inactive scenario parameter (won't expand combinations)."""
    return ScenarioParameter(
        name="RegulationLevel",
        token="REGULATION",
        active=False,
        default="Medium",
        values=["Low", "Medium", "High"],
    )


# ============================================================================
# Parameter Sets for Combined Testing
# ============================================================================

@pytest.fixture
def mixed_parameter_set(
    simple_continuous_param,
    triangular_param,
    simple_discrete_param,
):
    """Set of mixed parameters for testing sampling methods."""
    return [
        simple_continuous_param,
        triangular_param,
        simple_discrete_param,
    ]


@pytest.fixture
def continuous_only_set(simple_continuous_param, triangular_param, log_triangular_param):
    """Set of continuous parameters only."""
    return [simple_continuous_param, triangular_param, log_triangular_param]


@pytest.fixture
def discrete_only_set(simple_discrete_param, weighted_discrete_param):
    """Set of discrete parameters only."""
    return [simple_discrete_param, weighted_discrete_param]


# ============================================================================
# Scenario Fixtures
# ============================================================================

@pytest.fixture
def simple_scenario_map():
    """Simple scenario mapping for testing override resolution."""
    return {"POLICY": "NetZero2050", "REGULATION": "High"}


@pytest.fixture
def empty_scenario_map():
    """Empty scenario mapping."""
    return {}


# ============================================================================
# Numeric Fixtures for Edge Cases
# ============================================================================

@pytest.fixture
def edge_case_values():
    """Common edge case values for numeric testing."""
    return {
        "zero": 0.0,
        "very_small_positive": 1e-10,
        "very_small_negative": -1e-10,
        "very_large": 1e10,
        "negative": -100.0,
    }


# ============================================================================
# Random Seed Fixture
# ============================================================================

@pytest.fixture
def fixed_seed():
    """Fixed random seed for reproducible tests."""
    seed = 42
    np.random.seed(seed)
    return seed


# ============================================================================
# Temporary File Fixtures
# ============================================================================

@pytest.fixture
def temp_hor_file(tmp_path):
    """Create a minimal .hor configuration file for testing."""
    hor_content = """
# Test configuration file

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

Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    SamplingMethod = LHS
    RandomSeed = 42
    ContinuousParameter("TEST_PARAM")
}
"""
    file_path = tmp_path / "test_config.hor"
    file_path.write_text(hor_content)
    return file_path


@pytest.fixture
def invalid_hor_file(tmp_path):
    """Create an invalid .hor file (missing required fields)."""
    hor_content = """
# Invalid configuration - missing name field inside block
ContinuousParameter "PlaceholderName" {
    token = "TEST_PARAM"
    # Missing name field!
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
    ContinuousParameter("TEST_PARAM")
}
"""
    file_path = tmp_path / "invalid_config.hor"
    file_path.write_text(hor_content)
    return file_path
