# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for parameter classes.

Tests for:
- ContinuousParameter
- DiscreteParameter
- ScenarioParameter
- Parameter validation
"""

import pytest

from horizon.exceptions import ParameterError
from horizon.parameters.parameter import (
    ContinuousParameter,
    DiscreteParameter,
    ScenarioParameter,
)

# ============================================================================
# Test ContinuousParameter
# ============================================================================


class TestContinuousParameter:
    """Test ContinuousParameter class."""

    def test_create_continuous_parameter(self):
        """Creating a basic continuous parameter should work."""
        param = ContinuousParameter(
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

        assert param.name == "Temperature"
        assert param.token == "TEMP"
        assert param.active is True
        assert param.default == 50.0
        assert param.low_val == 0.0
        assert param.mid_val == 50.0
        assert param.high_val == 100.0
        assert param.distribution == "uniform"
        assert param.decimals == 2

    def test_continuous_parameter_without_mid_val(self):
        """Mid value can be None."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=50.0,
            low_val=0.0,
            mid_val=None,
            high_val=100.0,
            distribution="triangular",
            decimals=2,
        )

        assert param.mid_val is None

    def test_continuous_parameter_with_overrides(self):
        """Can create continuous parameter with overrides."""
        overrides = [
            {
                "conditions": [{"token": "POLICY", "value": "NetZero"}],
                "attrs": {"low_val": 10.0},
                "start": 0,
                "end": 0,
            }
        ]

        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=50.0,
            low_val=0.0,
            mid_val=50.0,
            high_val=100.0,
            distribution="uniform",
            decimals=2,
            overrides=overrides,
        )

        assert param.overrides == overrides

    def test_continuous_parameter_repr(self, simple_continuous_param):
        """String representation should be informative."""
        repr_str = repr(simple_continuous_param)
        assert "ContinuousParameter" in repr_str
        assert "Temperature" in repr_str
        assert "TEMP" in repr_str

    def test_continuous_parameter_different_distributions(self):
        """Should support different distribution types."""
        distributions = ["uniform", "triangular", "log-triangular"]

        for dist in distributions:
            param = ContinuousParameter(
                name="Test",
                token="TEST",
                active=True,
                default=50.0,
                low_val=10.0,
                mid_val=50.0,
                high_val=100.0,
                distribution=dist,
                decimals=2,
            )
            assert param.distribution == dist


# ============================================================================
# Test DiscreteParameter
# ============================================================================

class TestDiscreteParameter:
    """Test DiscreteParameter class."""

    def test_create_discrete_parameter_uniform(self):
        """Creating discrete parameter with uniform probabilities."""
        param = DiscreteParameter(
            name="FuelType",
            token="FUEL",
            active=True,
            default="HFO",
            values=["HFO", "LNG", "Methanol", "Ammonia"],
            probabilities="uniform",
        )

        assert param.name == "FuelType"
        assert param.token == "FUEL"
        assert param.values == ["HFO", "LNG", "Methanol", "Ammonia"]
        # Should compute uniform probabilities
        assert len(param.probabilities) == 4
        assert all(p == 0.25 for p in param.probabilities)

    def test_create_discrete_parameter_weighted(self):
        """Creating discrete parameter with explicit probabilities."""
        probs = [0.5, 0.3, 0.15, 0.05]
        param = DiscreteParameter(
            name="Weather",
            token="WEATHER",
            active=True,
            default="Calm",
            values=["Calm", "Moderate", "Rough", "Storm"],
            probabilities=probs,
        )

        assert param.probabilities == probs

    def test_discrete_parameter_empty_values_raises_error(self):
        """Empty values list should raise ParameterError."""
        with pytest.raises(ParameterError, match="empty values list"):
            DiscreteParameter(
                name="Empty",
                token="EMPTY",
                active=True,
                default=None,
                values=[],
                probabilities="uniform",
            )

    def test_discrete_parameter_with_emissions(self):
        """Can create discrete parameter with emissions directives."""
        param = DiscreteParameter(
            name="Scenario",
            token="SCENARIO",
            active=True,
            default="Base",
            values=["Low", "Base", "High"],
            probabilities="uniform",
            emissions_low="MINIMUM",
            emissions_high="MAXIMUM",
        )

        assert param.emissions_low == "MINIMUM"
        assert param.emissions_high == "MAXIMUM"

    def test_discrete_parameter_with_overrides(self):
        """Can create discrete parameter with overrides."""
        overrides = [
            {
                "conditions": [{"token": "POLICY", "value": "NetZero"}],
                "attrs": {"values": ["LNG", "Methanol", "Ammonia"]},
                "start": 0,
                "end": 0,
            }
        ]

        param = DiscreteParameter(
            name="Fuel",
            token="FUEL",
            active=True,
            default="HFO",
            values=["HFO", "LNG", "Methanol"],
            probabilities="uniform",
            overrides=overrides,
        )

        assert param.overrides == overrides

    def test_discrete_parameter_repr(self, simple_discrete_param):
        """String representation should be informative."""
        repr_str = repr(simple_discrete_param)
        assert "DiscreteParameter" in repr_str
        assert "FuelType" in repr_str
        assert "FUEL" in repr_str


# ============================================================================
# Test ScenarioParameter
# ============================================================================

class TestScenarioParameter:
    """Test ScenarioParameter class."""

    def test_create_scenario_parameter(self):
        """Creating a basic scenario parameter should work."""
        param = ScenarioParameter(
            name="PolicyScenario",
            token="POLICY",
            active=True,
            default="BAU",
            values=["BAU", "NetZero2050", "NetZero2040"],
        )

        assert param.name == "PolicyScenario"
        assert param.token == "POLICY"
        assert param.active is True
        assert param.default == "BAU"
        assert param.values == ["BAU", "NetZero2050", "NetZero2040"]

    def test_scenario_parameter_inactive(self):
        """Inactive scenario parameters don't expand combinations."""
        param = ScenarioParameter(
            name="Regulation",
            token="REGULATION",
            active=False,
            default="Medium",
            values=["Low", "Medium", "High"],
        )

        assert param.active is False
        assert param.default == "Medium"

    def test_scenario_parameter_repr(self, simple_scenario_param):
        """String representation should be informative."""
        repr_str = repr(simple_scenario_param)
        assert "ScenarioParameter" in repr_str
        assert "PolicyScenario" in repr_str
        assert "POLICY" in repr_str


# ============================================================================
# Test Parameter Validation Edge Cases
# ============================================================================

class TestParameterEdgeCases:
    """Test edge cases and validation in parameter creation."""

    def test_continuous_with_negative_values(self):
        """Continuous parameters can have negative bounds."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=-50.0,
            low_val=-100.0,
            mid_val=-50.0,
            high_val=0.0,
            distribution="uniform",
            decimals=2,
        )

        assert param.low_val == -100.0
        assert param.high_val == 0.0

    def test_continuous_with_large_decimals(self):
        """Can specify high precision with large decimals value."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=50.0,
            low_val=0.0,
            mid_val=50.0,
            high_val=100.0,
            distribution="uniform",
            decimals=10,
        )

        assert param.decimals == 10

    def test_continuous_with_zero_decimals(self):
        """Decimals can be zero for integer-like values."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=50.0,
            low_val=0.0,
            mid_val=50.0,
            high_val=100.0,
            distribution="uniform",
            decimals=0,
        )

        assert param.decimals == 0

    def test_discrete_with_single_value(self):
        """Discrete parameter with single value is valid."""
        param = DiscreteParameter(
            name="Constant",
            token="CONST",
            active=True,
            default="Only",
            values=["Only"],
            probabilities="uniform",
        )

        assert len(param.values) == 1
        assert param.probabilities == [1.0]

    def test_discrete_with_numeric_values(self):
        """Discrete parameters can have numeric values."""
        param = DiscreteParameter(
            name="Levels",
            token="LEVEL",
            active=True,
            default=1,
            values=[1, 2, 3, 4, 5],
            probabilities="uniform",
        )

        assert param.values == [1, 2, 3, 4, 5]

    def test_scenario_with_single_value(self):
        """Scenario parameter with single value is valid."""
        param = ScenarioParameter(
            name="Only",
            token="ONLY",
            active=True,
            default="Value",
            values=["Value"],
        )

        assert len(param.values) == 1
