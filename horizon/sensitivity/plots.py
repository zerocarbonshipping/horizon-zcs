# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""QA diagnostic plots for PRCC sensitivity analysis.

Generates tornado charts for PRCC rankings and scatter/LOESS/binned-mean
plots for sanity-checking parameter–metric relationships.
"""

import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 (must follow matplotlib.use("Agg") backend selection)
import numpy as np  # noqa: E402 (kept adjacent to the matplotlib imports above)

logger = logging.getLogger(__name__)

# Number of top parameters to generate scatter diagnostics for
_TOP_N = 6


def plot_tornado(prcc_df, metric_label, output_path):
    """Horizontal bar chart of signed PRCC values sorted by magnitude.

    Parameters
    ----------
    prcc_df : pd.DataFrame
        Output of :func:`compute_prcc` (columns: parameter, prcc, abs_prcc).
    metric_label : str
        Human-readable metric name for the title.
    output_path : str
        Path for the output PNG file.
    """
    df = prcc_df.dropna(subset=["prcc"]).sort_values("abs_prcc", ascending=True)
    if df.empty:
        logger.warning("No valid PRCC values to plot for '%s'.", metric_label)
        return

    colors = ["#2c4068" if v >= 0 else "#a24040" for v in df["prcc"]]

    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(df))))
    ax.barh(df["parameter"], df["prcc"], color=colors, height=0.7)
    ax.tick_params(axis="y", labelsize=9)
    ax.set_xlabel("PRCC")
    ax.set_title(f"PRCC — {metric_label}", fontsize=12)
    ax.axvline(0, color="black", linewidth=0.5)
    for threshold in (-0.5, 0.5):
        ax.axvline(threshold, color="grey", linewidth=0.5, linestyle="--", alpha=0.6)
    ax.set_xlim(-1.05, 1.05)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info("Tornado plot saved to %s", output_path)


def plot_scatter_diagnostics(x_values, y_values, param_name, metric_label,
                             output_path):
    """Scatter + LOESS smooth + binned means for one parameter–metric pair.

    Parameters
    ----------
    x_values, y_values : array-like
        Raw (unranked) parameter and metric values.
    param_name : str
        Parameter token name.
    metric_label : str
        Metric display label.
    output_path : str
        Path for the output PNG file.
    """
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(x, y, s=12, alpha=0.4, color="#3c5e86", label="samples")

    smooth = _lowess(x, y)
    if smooth is not None:
        ax.plot(*smooth, color="#a2703c", linewidth=2, label="LOESS")

    _plot_binned_means(ax, x, y)

    ax.set_xlabel(param_name, fontsize=10)
    ax.set_ylabel(metric_label, fontsize=10)
    ax.set_title(f"{param_name} vs {metric_label}", fontsize=11)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _lowess(x, y, frac=0.3, n_grid=80):
    """Gaussian kernel-weighted local average (lightweight LOWESS).

    The bandwidth is *frac* of the data range.  Returns ``(x_grid, y_smooth)``
    or None when there are too few points or no spread.
    """
    x_range = x.max() - x.min()
    if len(x) < 4 or x_range == 0:
        return None

    x_grid = np.linspace(x.min(), x.max(), n_grid)
    weights = np.exp(-0.5 * ((x[np.newaxis, :] - x_grid[:, np.newaxis]) / (frac * x_range)) ** 2)
    return x_grid, weights @ y / weights.sum(axis=1)


def _plot_binned_means(ax, x, y, n_bins=8):
    """Overlay binned means with ±1 std error bars."""
    if x.max() == x.min() or len(x) < n_bins:
        return

    edges = np.linspace(x.min(), x.max(), n_bins + 1)
    # bins are half-open except the last, which includes the maximum
    bin_index = np.minimum(np.searchsorted(edges, x, side="right") - 1, n_bins - 1)
    mids, means, stds = [], [], []
    for i in range(n_bins):
        y_bin = y[bin_index == i]
        if len(y_bin) < 2:
            continue

        mids.append((edges[i] + edges[i + 1]) / 2)
        means.append(y_bin.mean())
        stds.append(y_bin.std())

    if mids:
        ax.errorbar(mids, means, yerr=stds, fmt="s-", color="#286464",
                    markersize=5, linewidth=1.2, capsize=3,
                    label="binned mean ± std", alpha=0.8)
