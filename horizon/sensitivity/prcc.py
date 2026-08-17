# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Partial Rank Correlation Coefficients (PRCC) computation.

Pure computation module — no file I/O.  Given an (n × p) parameter matrix X
and an (n,) response vector y, computes the PRCC of each parameter with the
response, controlling for all other parameters.

Uses the precision-matrix (inverse correlation matrix) method for efficiency:
a single O(p³) matrix inversion replaces p separate OLS regressions.
"""

import logging
import warnings

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def compute_prcc(X, y, param_names):
    """Compute Partial Rank Correlation Coefficients.

    Parameters
    ----------
    X : array-like, shape (n_samples, n_params)
        Input parameter matrix.
    y : array-like, shape (n_samples,)
        Response (metric) vector.
    param_names : list[str]
        Names for each column of *X*.

    Returns
    -------
    pd.DataFrame
        Columns: parameter, prcc, abs_prcc, p_value.
        Sorted by abs_prcc descending.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, p = X.shape

    if n != y.shape[0]:
        raise ValueError(
            f"X has {n} rows but y has {y.shape[0]} elements"
        )
    if p != len(param_names):
        raise ValueError(
            f"X has {p} columns but {len(param_names)} param_names given"
        )

    if n <= p + 2:
        logger.warning(
            "Only %d samples for %d parameters — PRCC estimates will be "
            "unreliable (need at least n > p + 2).",
            n, p,
        )

    # Identify constant columns (will be skipped)
    active_mask = [np.ptp(X[:, j]) > 0 for j in range(p)]
    active_indices = [j for j, m in enumerate(active_mask) if m]

    for j in range(p):
        if not active_mask[j]:
            logger.info("Parameter '%s' is constant — skipping.", param_names[j])

    # Single parameter: PRCC degenerates to Spearman correlation
    if len(active_indices) == 1:
        j = active_indices[0]
        corr, pval = stats.spearmanr(X[:, j], y)
        results = []
        for k in range(p):
            if k == j:
                results.append((param_names[k], corr, abs(corr), pval))
            else:
                results.append((param_names[k], np.nan, np.nan, np.nan))
        df_out = pd.DataFrame(results, columns=["parameter", "prcc", "abs_prcc", "p_value"])
        df_out.sort_values("abs_prcc", ascending=False, ignore_index=True, inplace=True)
        return df_out

    if not active_indices:
        # All columns constant
        results = [(param_names[j], np.nan, np.nan, np.nan) for j in range(p)]
        return pd.DataFrame(results, columns=["parameter", "prcc", "abs_prcc", "p_value"])

    # Rank-transform active columns + y
    R_active = np.column_stack([stats.rankdata(X[:, j]) for j in active_indices])
    r_y = stats.rankdata(y)
    R = np.column_stack([R_active, r_y])  # (n, k+1) where k = len(active_indices)

    # Correlation matrix of ranks
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        C = np.corrcoef(R, rowvar=False)  # (k+1, k+1)

    # Invert to get precision matrix
    try:
        P = np.linalg.inv(C)
    except np.linalg.LinAlgError:
        P = np.linalg.pinv(C)

    # Partial correlations from precision matrix:
    # PRCC(X_j, Y | rest) = -P[j, -1] / sqrt(P[j,j] * P[-1,-1])
    d = np.sqrt(np.abs(np.diag(P)))
    k = len(active_indices)
    prcc_active = np.full(k, np.nan)
    for i in range(k):
        if d[i] > 0 and d[-1] > 0:
            prcc_active[i] = -P[i, -1] / (d[i] * d[-1])

    # Clip to [-1, 1] for numerical safety
    prcc_active = np.clip(prcc_active, -1.0, 1.0)

    # Build results for all parameters (including inactive ones).
    # The partial correlation for each active parameter controls for the
    # k - 1 other active parameters, so df = n - 2 - (k - 1) = n - k - 1.
    # Constant columns are excluded from the correlation matrix and must not
    # count against the degrees of freedom.
    df_freedom = n - k - 1
    results = []
    active_pos = 0
    for j in range(p):
        name = param_names[j]
        if not active_mask[j]:
            results.append((name, np.nan, np.nan, np.nan))
        else:
            prcc_val = prcc_active[active_pos]
            if np.isnan(prcc_val):
                results.append((name, np.nan, np.nan, np.nan))
            else:
                pval = _prcc_pvalue(prcc_val, df_freedom)
                results.append((name, prcc_val, abs(prcc_val), pval))
            active_pos += 1

    df_out = pd.DataFrame(results, columns=["parameter", "prcc", "abs_prcc", "p_value"])
    df_out.sort_values("abs_prcc", ascending=False, ignore_index=True, inplace=True)
    return df_out


def _prcc_pvalue(prcc_val, df):
    """Two-sided p-value for a PRCC via the t-distribution."""
    if df <= 0:
        return np.nan
    denom = 1.0 - prcc_val ** 2
    if denom <= 0:
        return 0.0  # perfect correlation
    t_stat = prcc_val * np.sqrt(df / denom)
    return float(2.0 * stats.t.sf(abs(t_stat), df))
