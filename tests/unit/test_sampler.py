# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ParameterSampler and sampling operations.

This module tests the critical mathematical operations in sampler.py,
particularly focusing on:
- Triangular PPF (percent point function)
- Continuous parameter sampling
- Parameter resolution with overrides
- Edge cases and degenerate distributions
"""

import numpy as np
import pytest

from horizon.exceptions import SamplingError
from horizon.parameters.parameter import ContinuousParameter, DiscreteParameter
from horizon.parameters.sampler import (
    ParameterSampler,
    _override_matches,
    ensure_mid_val,
    resolve_parameters_for_scenario,
)

# ============================================================================
# Test Triangular PPF (Percent Point Function)
# ============================================================================


class TestTriangularPPF:
    """Test the triangular distribution percent point function."""

    def test_ppf_returns_low_at_zero(self):
        """PPF should return low value when u=0."""
        result = ParameterSampler._triangular_ppf(u=0.0, a=10.0, c=50.0, b=90.0)
        assert result == pytest.approx(10.0)

    def test_ppf_returns_high_at_one(self):
        """PPF should return high value when u=1."""
        result = ParameterSampler._triangular_ppf(u=1.0, a=10.0, c=50.0, b=90.0)
        assert result == pytest.approx(90.0)

    def test_ppf_returns_mode_at_fc(self):
        """PPF should return mode (c) when u equals Fc."""
        a, c, b = 10.0, 50.0, 90.0
        Fc = (c - a) / (b - a)  # CDF at mode
        result = ParameterSampler._triangular_ppf(u=Fc, a=a, c=c, b=b)
        assert result == pytest.approx(c)

    def test_ppf_degenerate_distribution(self):
        """PPF should handle degenerate case where a==c==b."""
        result = ParameterSampler._triangular_ppf(u=0.5, a=42.0, c=42.0, b=42.0)
        assert result == pytest.approx(42.0)

    def test_ppf_degenerate_at_boundaries(self):
        """Degenerate distribution should return constant at u=0 and u=1."""
        assert ParameterSampler._triangular_ppf(u=0.0, a=42.0, c=42.0, b=42.0) == pytest.approx(42.0)
        assert ParameterSampler._triangular_ppf(u=1.0, a=42.0, c=42.0, b=42.0) == pytest.approx(42.0)

    @pytest.mark.parametrize("u", [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    def test_ppf_returns_value_in_range(self, u):
        """PPF should always return value within [a, b]."""
        a, c, b = 10.0, 50.0, 90.0
        result = ParameterSampler._triangular_ppf(u, a, c, b)
        assert a <= result <= b

    @pytest.mark.parametrize("u", [0.1, 0.3, 0.5, 0.7, 0.9])
    def test_ppf_symmetric_distribution(self, u):
        """PPF of symmetric triangular distribution should be symmetric around mode."""
        a, c, b = 0.0, 50.0, 100.0  # Symmetric around 50
        result_low = ParameterSampler._triangular_ppf(u, a, c, b)
        result_high = ParameterSampler._triangular_ppf(1 - u, a, c, b)
        # Check symmetry: distance from mode should be equal
        assert abs(result_low - c) == pytest.approx(abs(result_high - c), rel=1e-10)

    def test_ppf_monotonically_increasing(self):
        """PPF should be monotonically increasing in u."""
        a, c, b = 10.0, 50.0, 90.0
        u_values = np.linspace(0, 1, 11)
        results = [ParameterSampler._triangular_ppf(u, a, c, b) for u in u_values]

        # Check that results are monotonically increasing
        for i in range(len(results) - 1):
            assert results[i] <= results[i + 1]

    def test_ppf_skewed_left(self):
        """PPF with mode near high value (left-skewed)."""
        a, c, b = 10.0, 80.0, 90.0  # Mode near high end
        result = ParameterSampler._triangular_ppf(u=0.5, a=a, c=c, b=b)
        # Result should be in range
        assert a <= result <= b

    def test_ppf_skewed_right(self):
        """PPF with mode near low value (right-skewed)."""
        a, c, b = 10.0, 20.0, 90.0  # Mode near low end
        result = ParameterSampler._triangular_ppf(u=0.5, a=a, c=c, b=b)
        # Result should be in range
        assert a <= result <= b


# ============================================================================
# Test Continuous Parameter Sampling
# ============================================================================

class TestContinuousSampling:
    """Test continuous parameter sampling methods."""

    def test_continuous_ppf_uniform(self, simple_continuous_param):
        """Uniform distribution should map u linearly to [low, high]."""
        sampler = ParameterSampler()
        u = 0.5
        result = sampler._continuous_ppf(simple_continuous_param, u)
        expected = 0.5 * (simple_continuous_param.low_val + simple_continuous_param.high_val)
        assert result == pytest.approx(expected)

    def test_continuous_ppf_uniform_boundaries(self, simple_continuous_param):
        """Uniform distribution boundaries."""
        sampler = ParameterSampler()
        assert sampler._continuous_ppf(simple_continuous_param, 0.0) == pytest.approx(
            simple_continuous_param.low_val
        )
        assert sampler._continuous_ppf(simple_continuous_param, 1.0) == pytest.approx(
            simple_continuous_param.high_val
        )

    def test_continuous_ppf_triangular(self, triangular_param):
        """Triangular distribution should use triangular PPF."""
        sampler = ParameterSampler()
        u = 0.5
        result = sampler._continuous_ppf(triangular_param, u)
        # Should be in range
        assert triangular_param.low_val <= result <= triangular_param.high_val

    def test_sample_continuous_mc_uniform_distribution(self, simple_continuous_param):
        """Monte Carlo sampling should produce values in the correct range."""
        sampler = ParameterSampler()
        np.random.seed(42)

        results = [
            sampler._sample_continuous_mc(simple_continuous_param)
            for _ in range(5)
        ]

        # All results should be in range
        for result in results:
            assert simple_continuous_param.low_val <= result <= simple_continuous_param.high_val

    def test_sample_continuous_mc_respects_decimals(self, simple_continuous_param):
        """Sampled values should be rounded to specified decimals."""
        sampler = ParameterSampler()
        np.random.seed(42)
        result = sampler._sample_continuous_mc(simple_continuous_param)

        # Check that result has at most 'decimals' decimal places
        decimals = simple_continuous_param.decimals
        rounded = round(result, decimals)
        assert result == pytest.approx(rounded)

    def test_sample_continuous_mc_degenerate(self, degenerate_param):
        """Degenerate distribution should always return the constant value."""
        sampler = ParameterSampler()
        np.random.seed(42)

        # Test multiple draws — degenerate (low==high) always returns the constant
        for _ in range(5):
            result = sampler._sample_continuous_mc(degenerate_param)
            assert result == pytest.approx(42.0)


# ============================================================================
# Test Min/Max Value Operations
# ============================================================================

class TestMinMaxOperations:
    """Test _min_value and _max_value helper methods."""

    def test_min_value_continuous(self, simple_continuous_param):
        """Min value of continuous parameter should be low_val."""
        result = ParameterSampler._min_value(simple_continuous_param)
        assert result == simple_continuous_param.low_val

    def test_max_value_continuous(self, simple_continuous_param):
        """Max value of continuous parameter should be high_val."""
        result = ParameterSampler._max_value(simple_continuous_param)
        assert result == simple_continuous_param.high_val

    def test_min_value_discrete(self, weighted_discrete_param):
        """Min value of discrete parameter should be min of values."""
        # weighted_discrete_param has values: ["Calm", "Moderate", "Rough", "Storm"]
        # These are strings, so min will return "Calm" alphabetically
        result = ParameterSampler._min_value(weighted_discrete_param)
        assert result == "Calm"

    def test_max_value_discrete(self, weighted_discrete_param):
        """Max value of discrete parameter should be max of values."""
        # Max alphabetically would be "Storm"
        result = ParameterSampler._max_value(weighted_discrete_param)
        assert result == "Storm"

    def test_min_value_empty_raises_error(self):
        """Min value of discrete parameter with empty values should raise SamplingError."""
        # DiscreteParameter validates non-empty at construction, so create with
        # a dummy value and then clear the list to simulate the edge case.
        empty_discrete = DiscreteParameter(
            name="Empty", token="EMPTY", active=True, default=None,
            values=["dummy"], probabilities=[1.0],
        )
        empty_discrete.values = []

        with pytest.raises(SamplingError, match="Cannot compute min for empty values"):
            ParameterSampler._min_value(empty_discrete)

    def test_max_value_empty_raises_error(self):
        """Max value of discrete parameter with empty values should raise SamplingError."""
        empty_discrete = DiscreteParameter(
            name="Empty", token="EMPTY", active=True, default=None,
            values=["dummy"], probabilities=[1.0],
        )
        empty_discrete.values = []

        with pytest.raises(SamplingError, match="Cannot compute max for empty values"):
            ParameterSampler._max_value(empty_discrete)


# ============================================================================
# Test ensure_mid_val
# ============================================================================

class TestEnsureMidVal:
    """Test ensure_mid_val function for triangular distributions."""

    def test_ensure_mid_val_with_existing_mid_val(self, triangular_param):
        """If mid_val exists, it should not be changed."""
        original_mid = triangular_param.mid_val
        ensure_mid_val(triangular_param)
        assert triangular_param.mid_val == original_mid

    def test_ensure_mid_val_triangular_without_mid(self):
        """Triangular distribution without mid_val should get arithmetic mean."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=50.0,
            low_val=10.0,
            mid_val=None,
            high_val=90.0,
            distribution="triangular",
            decimals=2,
        )

        ensure_mid_val(param)

        expected_mid = (10.0 + 90.0) / 2.0
        assert param.mid_val == pytest.approx(expected_mid)

    def test_ensure_mid_val_log_triangular_without_mid(self):
        """Log-triangular without mid_val should get geometric mean."""
        param = ContinuousParameter(
            name="Test",
            token="TEST",
            active=True,
            default=1000.0,
            low_val=100.0,
            mid_val=None,
            high_val=10000.0,
            distribution="log-triangular",
            decimals=0,
        )

        ensure_mid_val(param)

        expected_mid = np.sqrt(100.0 * 10000.0)
        assert param.mid_val == pytest.approx(expected_mid)

    def test_ensure_mid_val_uniform_unchanged(self, simple_continuous_param):
        """Uniform distribution should not be affected by ensure_mid_val."""
        original_mid = simple_continuous_param.mid_val
        ensure_mid_val(simple_continuous_param)
        # mid_val should remain unchanged for uniform distribution
        assert simple_continuous_param.mid_val == original_mid


# ============================================================================
# Test Override Matching
# ============================================================================

class TestOverrideMatching:
    """Test _override_matches helper function."""

    def test_override_matches_simple_condition(self):
        """Simple single condition should match correctly."""
        conditions = [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}]
        scenario_map = {"POLICY": "NetZero2050"}

        assert _override_matches(conditions, scenario_map) is True

    def test_override_not_matches_different_value(self):
        """Different value should not match."""
        conditions = [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}]
        scenario_map = {"POLICY": "BAU"}

        assert _override_matches(conditions, scenario_map) is False

    def test_override_matches_multiple_conditions(self):
        """Multiple conditions should all match (AND logic)."""
        conditions = [
            {"token": "POLICY", "op": "eq", "value": "NetZero2050"},
            {"token": "REGULATION", "op": "eq", "value": "High"},
        ]
        scenario_map = {"POLICY": "NetZero2050", "REGULATION": "High"}

        assert _override_matches(conditions, scenario_map) is True

    def test_override_not_matches_partial_conditions(self):
        """Partial match of conditions should not match."""
        conditions = [
            {"token": "POLICY", "op": "eq", "value": "NetZero2050"},
            {"token": "REGULATION", "op": "eq", "value": "High"},
        ]
        scenario_map = {"POLICY": "NetZero2050", "REGULATION": "Medium"}

        assert _override_matches(conditions, scenario_map) is False

    def test_override_matches_empty_conditions(self):
        """Empty conditions list should always match."""
        conditions = []
        scenario_map = {"POLICY": "NetZero2050"}

        assert _override_matches(conditions, scenario_map) is True

    def test_override_not_matches_missing_token(self):
        """If token not in scenario_map, should not match."""
        conditions = [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}]
        scenario_map = {"OTHER": "Value"}

        assert _override_matches(conditions, scenario_map) is False


# ============================================================================
# Test resolve_parameters_for_scenario
# ============================================================================

class TestResolveParametersForScenario:
    """Test parameter resolution with scenario overrides."""

    def test_resolve_no_overrides(self, simple_continuous_param):
        """Parameters without overrides should be returned unchanged."""
        scenario_map = {"POLICY": "NetZero2050"}
        resolved = resolve_parameters_for_scenario([simple_continuous_param], scenario_map)

        assert len(resolved) == 1
        assert resolved[0].low_val == simple_continuous_param.low_val
        assert resolved[0].high_val == simple_continuous_param.high_val

    def test_resolve_with_matching_override(self):
        """Matching override should modify parameter attributes."""
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
            overrides=[
                {
                    "conditions": [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}],
                    "attrs": {"low_val": 10.0, "high_val": 90.0},
                    "start": 0,
                    "end": 0,
                }
            ],
        )

        scenario_map = {"POLICY": "NetZero2050"}
        resolved = resolve_parameters_for_scenario([param], scenario_map)

        assert len(resolved) == 1
        assert resolved[0].low_val == 10.0
        assert resolved[0].high_val == 90.0

    def test_resolve_with_non_matching_override(self):
        """Non-matching override should not modify parameter."""
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
            overrides=[
                {
                    "conditions": [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}],
                    "attrs": {"low_val": 10.0, "high_val": 90.0},
                    "start": 0,
                    "end": 0,
                }
            ],
        )

        scenario_map = {"POLICY": "BAU"}
        resolved = resolve_parameters_for_scenario([param], scenario_map)

        assert len(resolved) == 1
        assert resolved[0].low_val == 0.0  # Unchanged
        assert resolved[0].high_val == 100.0  # Unchanged

    def test_resolve_preserves_original_parameters(self):
        """Resolution should not modify original parameter objects."""
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
            overrides=[
                {
                    "conditions": [{"token": "POLICY", "op": "eq", "value": "NetZero2050"}],
                    "attrs": {"low_val": 10.0},
                    "start": 0,
                    "end": 0,
                }
            ],
        )

        original_low = param.low_val
        scenario_map = {"POLICY": "NetZero2050"}

        resolved = resolve_parameters_for_scenario([param], scenario_map)

        # Original should be unchanged
        assert param.low_val == original_low
        # Resolved should have new value
        assert resolved[0].low_val == 10.0


# ============================================================================
# Test Random Seed Setting
# ============================================================================

class TestRandomSeed:
    """Test random seed management."""

    def test_set_random_seed(self):
        """Setting random seed should produce reproducible results."""
        ParameterSampler._set_random_seed(42)
        sample1 = np.random.rand(10)

        ParameterSampler._set_random_seed(42)
        sample2 = np.random.rand(10)

        np.testing.assert_array_equal(sample1, sample2)

    def test_set_random_seed_none(self):
        """Setting seed to None should not raise error."""
        ParameterSampler._set_random_seed(None)
        # Should work without error


# ============================================================================
# Integration Tests for Sampling Methods
# ============================================================================

@pytest.mark.unit
class TestSamplingIntegration:
    """Integration tests for complete sampling workflows."""

    def test_latin_hypercube_sampling_basic(self, continuous_only_set, fixed_seed):
        """LHS should produce correct number of samples."""
        sampler = ParameterSampler()
        num_samples = 10

        samples = sampler.sample_latin_hypercube(continuous_only_set, num_samples, seed=fixed_seed)

        # Should have correct number of samples (including 3 default samples)
        assert len(samples) >= num_samples

    def test_monte_carlo_sampling_basic(self, continuous_only_set, fixed_seed):
        """MC sampling should produce correct number of samples."""
        sampler = ParameterSampler()
        num_samples = 10

        samples = sampler.sample_group(continuous_only_set, num_samples, seed=fixed_seed)

        # Should have correct number of samples
        assert len(samples) >= num_samples

    def test_sampling_respects_inactive_parameters(self, inactive_continuous_param, fixed_seed):
        """Inactive parameters should use default value."""
        sampler = ParameterSampler()
        samples = sampler.sample_group([inactive_continuous_param], 5, seed=fixed_seed)

        # All samples should use default value for inactive parameter
        for sample in samples:
            if inactive_continuous_param.token in sample:
                assert sample[inactive_continuous_param.token] == inactive_continuous_param.default
