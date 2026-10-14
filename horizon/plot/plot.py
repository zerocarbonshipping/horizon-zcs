# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Sampling diagnostics and plotting helpers.

Exports:
 - plot_sampled_parameters_histograms(sampled_parameters, output_csv_path)
 - analyze_sampled_parameters(sampled_parameters, parameters, output_csv_path)

Diagnostics are grouped by the scenario tokens that actually affect each
parameter and deduplicated by resolved definition, so identical plots are not
repeated. One row per diagnostic group goes to sampling_analysis_summary.csv.
"""
import csv
import logging
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

from horizon.parameters.parameter import ContinuousParameter, DiscreteParameter
from horizon.parameters.sampler import ensure_mid_val, resolve_parameters_for_scenario

logger = logging.getLogger(__name__)

_PERCENTILES = [1, 5, 25, 50, 75, 95, 99]


def _ensure_plot_dir(output_csv_path: str) -> Tuple[str, str]:
    """Create the output directory and its 'plots' subdirectory; return both paths."""
    output_dir = os.path.dirname(output_csv_path) or "."
    plots_path = os.path.join(output_dir, "plots")
    os.makedirs(plots_path, exist_ok=True)
    return output_dir, plots_path


def _parameter_dependent_tokens(p: Any) -> List[str]:
    """Sorted scenario tokens that the parameter's overrides depend on."""
    return sorted({
        condition["token"]
        for override in getattr(p, "overrides", []) or []
        for condition in override.get("conditions", [])
        if condition.get("token")
    })


def _safe_float(x: Any) -> float:
    """Coerce to float; nan on failure."""
    try:
        return float(x)
    except Exception:
        return float("nan")


def _to_floats(values: List[Any]) -> List[float]:
    """Floats parsed from *values*, skipping non-numeric entries."""
    floats = []
    for value in values:
        try:
            floats.append(float(value))
        except (TypeError, ValueError):
            pass
    return floats


def _signature(p: Any) -> Tuple[Any, ...]:
    """Hashable signature of a resolved parameter, used to deduplicate identical definitions."""
    if isinstance(p, ContinuousParameter):
        try:
            ensure_mid_val(p)
        except Exception:
            logger.exception(
                "Unable to infer mid_val for parameter '%s' when producing diagnostics; continuing.",
                p.token,
            )
        return ("c", p.distribution, _safe_float(p.low_val), _safe_float(p.mid_val),
                _safe_float(p.high_val), int(p.decimals))

    if isinstance(p, DiscreteParameter):
        return ("d", tuple(p.values), tuple(_safe_float(x) for x in p.probabilities))

    return ("u", p.token)


def _combo_label(tokens: List[str], combo_key: Tuple[Any, ...]) -> str:
    """Human-readable scenario assignment, e.g. 'bio=low;el=high'."""
    return ";".join(f"{token}={value}" for token, value in zip(tokens, combo_key))


def _safe_label(tokens: List[str], combo_keys: List[Tuple[Any, ...]]) -> str:
    """Filename-safe label for a group of scenario combinations."""
    if not tokens:
        return "all"

    label = "_".join(f"{token}-{value}" for token, value in zip(tokens, combo_keys[0]))
    return f"{label}_and_{len(combo_keys)}" if len(combo_keys) > 1 else label


def _histogram_bins(values: np.ndarray) -> int:
    """Bin count by the Freedman-Diaconis rule; n // 2 (at most 50) when the IQR is zero."""
    n = len(values)
    if n <= 1:
        return 1

    iqr = np.percentile(values, 75) - np.percentile(values, 25)
    if iqr > 0:
        return max(1, int(math.ceil((values.max() - values.min()) / (2 * iqr * n ** (-1 / 3)))))

    return min(50, max(1, n // 2))


def plot_sampled_parameters_histograms(sampled_parameters: List[Dict[str, Any]], output_csv_path: str):
    """Save a histogram for every numeric sampled parameter."""
    _, plots_path = _ensure_plot_dir(output_csv_path)

    columns: Dict[str, List[Any]] = defaultdict(list)
    for sample in sampled_parameters:
        for key, value in sample.items():
            if key.lower() not in ("sample", "sample_number"):
                columns[key].append(value)

    for param, raw_values in columns.items():
        values = np.array(_to_floats(raw_values))
        if len(values) == 0:
            continue

        plt.figure(figsize=(10, 4))
        plt.hist(values, bins=_histogram_bins(values), alpha=0.75)
        plt.title(f"Histogram of {param}")
        plt.xlabel(param)
        plt.ylabel("Frequency")
        plt.tight_layout()
        plt.savefig(os.path.join(plots_path, f"histogram_{param}.png"))
        plt.close()

    logger.info("Histogram plots saved to %s", plots_path)


def analyze_sampled_parameters(sampled_parameters: List[Dict[str, Any]],
                               parameters: List[Any],
                               output_csv_path: str):
    """Produce per-parameter diagnostics and a CSV summary, deduplicated by resolved definition.

    For each parameter, samples are grouped by only the scenario tokens it
    depends on (via overrides), the parameter is resolved per group, and
    groups with identical resolved definitions share one diagnostic. The
    summary's `scenario` column lists the combinations each row covers.
    """
    output_dir, plots_path = _ensure_plot_dir(output_csv_path)
    summary_rows = []

    for p in parameters:
        token = p.token
        dependent_tokens = _parameter_dependent_tokens(p)

        combo_map = defaultdict(list)
        for s in sampled_parameters:
            combo_map[tuple(s.get(tok) for tok in dependent_tokens)].append(s)

        groups = {}
        for combo_key, combo_samples in combo_map.items():
            p_resolved = resolve_parameters_for_scenario([p], dict(zip(dependent_tokens, combo_key)))[0]
            group = groups.setdefault(
                _signature(p_resolved), {"p_res": p_resolved, "combo_keys": [], "samples": []},
            )
            group["combo_keys"].append(combo_key)
            group["samples"].extend(combo_samples)

        if isinstance(p, ContinuousParameter) and len(groups) > 1:
            overlay_groups = []
            for grp in groups.values():
                label = _combo_label(dependent_tokens, grp["combo_keys"][0])
                if len(grp["combo_keys"]) > 1:
                    label = f"{label} (+{len(grp['combo_keys']) - 1})"
                overlay_groups.append((label, [s.get(token) for s in grp["samples"]]))

            safe_label = _safe_label(dependent_tokens, next(iter(groups.values()))["combo_keys"])
            try:
                _plot_continuous_histogram_overlay(overlay_groups, token, plots_path, safe_label)
            except Exception:
                logger.exception("Failed to plot overlay histogram for token %s", token)

        for grp in groups.values():
            p_res = grp["p_res"]
            safe_label = _safe_label(dependent_tokens, grp["combo_keys"])
            raw_values = [s.get(token) for s in grp["samples"]]
            row = {
                "token": token,
                "scenario": " | ".join(_combo_label(dependent_tokens, ck) for ck in grp["combo_keys"]),
                "active": p.active,
            }

            if isinstance(p_res, ContinuousParameter):
                arr = np.array(_to_floats(raw_values))
                n = len(arr)
                row.update(type="continuous", n=n)
                summary_rows.append(row)
                if n == 0:
                    continue

                mean = float(np.mean(arr))
                std = float(np.std(arr, ddof=0))
                sorted_x = np.sort(arr)
                theoretical_cdf = np.array([_theoretical_cdf(p_res, x) for x in sorted_x])
                row.update({
                    "mean": mean,
                    "median": float(np.median(arr)),
                    "std": std,
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "skew": float(np.sum((arr - mean) ** 3) / (n * (std ** 3))) if std > 0 else 0.0,
                    "ks_statistic": float(np.max(np.abs(np.arange(1, n + 1) / n - theoretical_cdf))),
                    "distribution": p_res.distribution,
                    "low_val": p_res.low_val,
                    "mid_val": p_res.mid_val,
                    "high_val": p_res.high_val,
                })
                for q, value in zip(_PERCENTILES, np.percentile(arr, _PERCENTILES).tolist()):
                    row[f"percentile_{q}"] = value

                _plot_continuous_diagnostics(arr, p_res, plots_path, safe_label)

            elif isinstance(p_res, DiscreteParameter):
                counts = dict.fromkeys(p_res.values, 0)
                for v in raw_values:
                    counts[v] = counts.get(v, 0) + 1
                n = sum(counts.values())
                expected_counts = {
                    label: prob * n for label, prob in zip(p_res.values, p_res.probabilities)
                }
                row.update({
                    "type": "discrete",
                    "n": n,
                    "counts": counts,
                    "observed_props": {k: counts[k] / n if n > 0 else 0.0 for k in counts},
                    "expected_counts": expected_counts,
                    "chi2": sum((counts[k] - e) ** 2 / e for k, e in expected_counts.items() if e > 0),
                })
                summary_rows.append(row)

                _plot_discrete_diagnostics(counts, list(p_res.values), expected_counts, token, plots_path, safe_label)

    summary_csv = os.path.join(output_dir, "sampling_analysis_summary.csv")
    _write_summary_csv(summary_rows, summary_csv)

    logger.info("Sampling analysis complete. Plots in %s, summary in %s", plots_path, summary_csv)


def _write_summary_csv(rows: List[Dict[str, Any]], csv_path: str):
    """Write summary rows over the union of their keys; dict values flatten to 'k=v;k=v'."""
    if not rows:
        return

    keys = sorted({k for r in rows for k in r})
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                k: ";".join(f"{kk}={v[kk]}" for kk in sorted(v)) if isinstance(v := r.get(k, ""), dict) else v
                for k in keys
            })


def _plot_continuous_diagnostics(arr: np.ndarray, p: ContinuousParameter, plots_path: str, scenario_safe_label: str = "all"):
    token = p.token
    n = len(arr)
    xs = np.linspace(arr.min(), arr.max(), 200)

    plt.figure(figsize=(10, 4))
    plt.hist(arr, bins=min(50, max(5, n // 2)), density=True, alpha=0.6, label="empirical")
    pdf_vals = np.array([_theoretical_pdf(p, x) for x in xs])
    if np.isfinite(pdf_vals).all():
        plt.plot(xs, pdf_vals, lw=2, label="theoretical pdf")
    plt.title(f"Histogram & PDF: {token} [{scenario_safe_label}]")
    plt.xlabel(token)
    plt.ylabel("Density")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_path, f"hist_pdf_{token}_{scenario_safe_label}.png"))
    plt.close()

    sort_x = np.sort(arr)
    ecdf = np.arange(1, n + 1) / n
    theoretical = np.array([_theoretical_cdf(p, x) for x in sort_x])

    plt.figure(figsize=(8, 5))
    plt.step(sort_x, ecdf, where="post", label="empirical CDF")
    plt.plot(sort_x, theoretical, label="theoretical CDF", lw=2)
    plt.title(f"ECDF vs Theoretical CDF: {token} [{scenario_safe_label}]")
    plt.xlabel(token)
    plt.ylabel("CDF")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_path, f"ecdf_cdf_{token}_{scenario_safe_label}.png"))
    plt.close()


def _plot_continuous_histogram_overlay(groups, token, plots_path, safe_label):
    """Overlay density histograms of ``(label, raw_values)`` groups on common bins."""
    numeric_groups = [(label, np.array(_to_floats(values))) for label, values in groups]
    numeric_groups = [(label, arr) for label, arr in numeric_groups if len(arr) > 0]
    if not numeric_groups:
        return

    bins = _histogram_bins(np.concatenate([arr for _, arr in numeric_groups]))
    cmap = plt.get_cmap("tab10")
    plt.figure(figsize=(10, 5))
    for i, (label, arr) in enumerate(numeric_groups):
        plt.hist(arr, bins=bins, density=True, alpha=0.45, label=label, color=cmap(i % 10), edgecolor="none")

    plt.title(f"Overlay histogram of {token}")
    plt.xlabel(token)
    plt.ylabel("Density")
    plt.legend(loc="best", fontsize="small")
    plt.tight_layout()
    plt.savefig(os.path.join(plots_path, f"histogram_overlay_{token}_{safe_label}.png"))
    plt.close()


def _plot_discrete_diagnostics(counts: Dict[Any, int], labels: List[Any], expected_counts: Dict[Any, float],
                               token: str, plots_path: str, scenario_safe_label: str = "all"):
    obs = [counts.get(lbl, 0) for lbl in labels]
    exp = [expected_counts.get(lbl, 0) for lbl in labels]

    x = np.arange(len(labels))
    width = 0.35
    plt.figure(figsize=(10, 4))
    plt.bar(x - width / 2, obs, width, label="observed")
    plt.bar(x + width / 2, exp, width, label="expected")
    plt.xticks(x, labels, rotation=45)
    plt.ylabel("Count")
    plt.title(f"Observed vs Expected: {token} [{scenario_safe_label}]")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_path, f"discrete_obs_exp_{token}_{scenario_safe_label}.png"))
    plt.close()


def _sampling_space(p: Any, x: float):
    """Map *x* and the bounds of *p* into the space where the distribution is uniform or triangular.

    Returns ``(distribution, x, low, mid, high)``, or None when the CDF/PDF is
    trivially 0 (unsupported distribution or non-positive x for a log one).
    """
    dist = (p.distribution or "uniform").lower()
    shape = dist.removeprefix("log-")
    if shape not in ("uniform", "triangular"):
        return None

    low, mid, high = p.low_val, p.mid_val, p.high_val
    if dist != shape:
        if x <= 0:
            return None

        x, low, high = math.log(x), math.log(low), math.log(high)
        mid = math.log(mid) if shape == "triangular" else mid

    return dist, x, low, mid, high


def _theoretical_cdf(p: Any, x: Any) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0

    space = _sampling_space(p, x)
    if space is None:
        return 0.0

    dist, x, low, mid, high = space
    if x <= low or (dist == "uniform" and low == high):
        return 0.0

    if x >= high:
        return 1.0

    if dist.endswith("uniform"):
        return (x - low) / (high - low)

    if x <= mid:
        return ((x - low) ** 2) / ((high - low) * (mid - low)) if mid > low else 0.0

    return 1.0 - ((high - x) ** 2) / ((high - low) * (high - mid)) if high > mid else 1.0


def _theoretical_pdf(p: Any, x: Any) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0

    space = _sampling_space(p, x)
    if space is None:
        return 0.0

    dist, position, low, mid, high = space
    if not low <= position <= high or low == high:
        return 0.0

    if dist.endswith("uniform"):
        density = 1.0 / (high - low)
    elif position <= mid:
        density = 2 * (position - low) / ((high - low) * (mid - low)) if mid > low else 0.0
    else:
        density = 2 * (high - position) / ((high - low) * (high - mid)) if high > mid else 0.0

    # a log distribution is expressed in log x, so divide by dx/dlog(x) = x
    return density / x if dist.startswith("log-") else density
