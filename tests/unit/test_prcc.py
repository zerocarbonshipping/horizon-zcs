# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for PRCC (Partial Rank Correlation Coefficients) computation."""

import numpy as np
import pytest
from scipy import stats

from horizon.sensitivity.prcc import _prcc_pvalue, compute_prcc


class TestComputePRCC:
    """Core PRCC computation tests."""

    def test_known_linear_relationship(self):
        """Y = 2*X0 - 0.5*X1 + noise; X2 irrelevant.

        X0 should have the highest |PRCC|, X2 the lowest.
        """
        rng = np.random.RandomState(42)
        n = 200
        X = rng.rand(n, 3)
        y = 2.0 * X[:, 0] - 0.5 * X[:, 1] + 0.05 * rng.randn(n)

        result = compute_prcc(X, y, ["X0", "X1", "X2"])

        assert list(result.columns) == ["parameter", "prcc", "abs_prcc", "p_value"]
        assert len(result) == 3

        # Sorted by abs_prcc descending
        assert result.iloc[0]["parameter"] == "X0"
        assert result.iloc[0]["prcc"] > 0.8  # strong positive
        assert result.iloc[1]["parameter"] == "X1"
        assert result.iloc[1]["prcc"] < -0.3  # negative
        assert result.iloc[2]["abs_prcc"] < result.iloc[1]["abs_prcc"]

    def test_sign_preservation(self):
        """Positive and negative effects should have correct signs."""
        rng = np.random.RandomState(7)
        n = 150
        X = rng.rand(n, 2)
        y = 3.0 * X[:, 0] - 2.0 * X[:, 1] + 0.1 * rng.randn(n)

        result = compute_prcc(X, y, ["pos_param", "neg_param"])
        pos_row = result[result["parameter"] == "pos_param"].iloc[0]
        neg_row = result[result["parameter"] == "neg_param"].iloc[0]

        assert pos_row["prcc"] > 0
        assert neg_row["prcc"] < 0

    def test_single_parameter_equals_spearman(self):
        """With one parameter, PRCC should equal Spearman correlation."""
        rng = np.random.RandomState(99)
        n = 50
        X = rng.rand(n, 1)
        y = 2 * X[:, 0] + 0.3 * rng.randn(n)

        result = compute_prcc(X, y, ["only_param"])
        prcc_val = result.iloc[0]["prcc"]

        spearman_corr, _ = stats.spearmanr(X[:, 0], y)
        assert prcc_val == pytest.approx(spearman_corr, abs=1e-10)

    def test_constant_column_skipped(self):
        """A constant parameter should get NaN PRCC."""
        rng = np.random.RandomState(5)
        n = 100
        X = np.column_stack([rng.rand(n), np.full(n, 3.14), rng.rand(n)])
        y = X[:, 0] + 0.1 * rng.randn(n)

        result = compute_prcc(X, y, ["varied", "constant", "noise"])
        const_row = result[result["parameter"] == "constant"].iloc[0]
        assert np.isnan(const_row["prcc"])
        assert np.isnan(const_row["p_value"])

    def test_uncorrelated_noise(self):
        """Independent X and Y should yield PRCC near zero."""
        rng = np.random.RandomState(11)
        n = 500
        X = rng.rand(n, 3)
        y = rng.rand(n)

        result = compute_prcc(X, y, ["a", "b", "c"])
        for _, row in result.iterrows():
            assert abs(row["prcc"]) < 0.15

    def test_perfect_positive_correlation(self):
        """Y ≈ X0 with tiny noise should give PRCC close to 1."""
        rng = np.random.RandomState(0)
        n = 200
        X = np.column_stack([rng.rand(n), rng.rand(n)])
        y = 5.0 * X[:, 0] + 0.01 * rng.randn(n)

        result = compute_prcc(X, y, ["target", "decoy"])
        target = result[result["parameter"] == "target"].iloc[0]
        assert target["prcc"] > 0.99

    def test_perfect_negative_correlation(self):
        """Y = -X0 + noise should give strongly negative PRCC."""
        rng = np.random.RandomState(0)
        n = 200
        X = np.column_stack([rng.rand(n), rng.rand(n)])
        y = -3.0 * X[:, 0] + 0.01 * rng.randn(n)

        result = compute_prcc(X, y, ["target", "decoy"])
        target = result[result["parameter"] == "target"].iloc[0]
        assert target["prcc"] < -0.9

    def test_p_values_significant_for_strong_effect(self):
        """Strong linear relationship should yield small p-values."""
        rng = np.random.RandomState(42)
        n = 100
        X = rng.rand(n, 2)
        y = 5 * X[:, 0] + 0.01 * rng.randn(n)

        result = compute_prcc(X, y, ["strong", "weak"])
        strong = result[result["parameter"] == "strong"].iloc[0]
        assert strong["p_value"] < 0.001

    def test_output_sorted_by_abs_prcc(self):
        """Results should be sorted by abs_prcc descending."""
        rng = np.random.RandomState(3)
        n = 200
        X = rng.rand(n, 4)
        y = 3 * X[:, 2] - X[:, 0] + 0.1 * rng.randn(n)

        result = compute_prcc(X, y, ["a", "b", "c", "d"])
        abs_vals = result["abs_prcc"].dropna().values
        assert all(abs_vals[i] >= abs_vals[i + 1] for i in range(len(abs_vals) - 1))

    def test_shape_mismatch_raises(self):
        """Mismatched X rows and y length should raise ValueError."""
        with pytest.raises(ValueError, match="rows"):
            compute_prcc(np.ones((10, 2)), np.ones(5), ["a", "b"])

    def test_param_names_mismatch_raises(self):
        """Wrong number of param_names should raise ValueError."""
        with pytest.raises(ValueError, match="param_names"):
            compute_prcc(np.ones((10, 2)), np.ones(10), ["a"])

    def test_few_samples_warns(self, caplog):
        """n <= p + 2 should trigger a warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            compute_prcc(np.random.rand(4, 3), np.random.rand(4), ["a", "b", "c"])
        assert "unreliable" in caplog.text.lower()


class TestPRCCPValue:
    """Tests for the p-value helper."""

    def test_zero_prcc_gives_pvalue_one(self):
        """PRCC of 0 should give p ≈ 1."""
        p = _prcc_pvalue(0.0, df=50)
        assert p == pytest.approx(1.0, abs=1e-5)

    def test_large_prcc_gives_small_pvalue(self):
        """PRCC near ±1 should give very small p-value."""
        p = _prcc_pvalue(0.99, df=50)
        assert p < 1e-10

    def test_negative_df_gives_nan(self):
        """Non-positive df should return NaN."""
        assert np.isnan(_prcc_pvalue(0.5, df=0))
        assert np.isnan(_prcc_pvalue(0.5, df=-1))

    def test_perfect_correlation_gives_zero(self):
        """PRCC of exactly ±1 should give p = 0."""
        assert _prcc_pvalue(1.0, df=10) == 0.0
        assert _prcc_pvalue(-1.0, df=10) == 0.0
