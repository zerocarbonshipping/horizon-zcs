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

    # constant columns are skipped and reported as NaN
    active = np.flatnonzero(np.ptp(X, axis=0) > 0)
    for j in np.setdiff1d(np.arange(p), active):
        logger.info("Parameter '%s' is constant — skipping.", param_names[j])

    prcc = np.full(p, np.nan)
    p_value = np.full(p, np.nan)
    k = len(active)

    if k == 1:
        # a single parameter degenerates to Spearman correlation
        prcc[active[0]], p_value[active[0]] = stats.spearmanr(X[:, active[0]], y)
    elif k > 1:
        ranks = np.column_stack([stats.rankdata(X[:, j]) for j in active] + [stats.rankdata(y)])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            C = np.corrcoef(ranks, rowvar=False)
        try:
            P = np.linalg.inv(C)
        except np.linalg.LinAlgError:
            P = np.linalg.pinv(C)

        # PRCC(X_j, Y | rest) = -P[j, -1] / sqrt(P[j,j] * P[-1,-1])
        d = np.sqrt(np.abs(np.diag(P)))
        valid = (d[:-1] > 0) & (d[-1] > 0)
        partial = np.full(k, np.nan)
        partial[valid] = -P[:-1, -1][valid] / (d[:-1][valid] * d[-1])
        prcc[active] = np.clip(partial, -1.0, 1.0)

        # each partial correlation controls for the other k - 1 active
        # parameters, so df = n - 2 - (k - 1)
        for j in active:
            if not np.isnan(prcc[j]):
                p_value[j] = _prcc_pvalue(prcc[j], n - k - 1)

    df_out = pd.DataFrame({
        "parameter": param_names, "prcc": prcc, "abs_prcc": np.abs(prcc), "p_value": p_value,
    })
    return df_out.sort_values("abs_prcc", ascending=False, ignore_index=True)


def _prcc_pvalue(prcc_val, df):
    """Two-sided p-value for a PRCC via the t-distribution."""
    if df <= 0:
        return np.nan
    denom = 1.0 - prcc_val ** 2
    if denom <= 0:
        return 0.0  # perfect correlation
    t_stat = prcc_val * np.sqrt(df / denom)
    return float(2.0 * stats.t.sf(abs(t_stat), df))
