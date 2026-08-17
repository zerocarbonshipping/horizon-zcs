# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# File: horizon/plot/plot.py
"""
Sampling diagnostics and plotting helpers.

Exports:
 - plot_sampled_parameters_histograms(sampled_parameters, output_csv_path)
 - analyze_sampled_parameters(sampled_parameters, parameters, output_csv_path)

This module:
 - groups samples by the scenario tokens that actually affect each parameter,
 - deduplicates identical resolved-parameter definitions (so identical plots aren't repeated),
 - produces per-parameter-per-unique-signature diagnostics (histogram+PDF, ECDF/CDF for continuous;
   observed-vs-expected for discrete),
 - writes a sampling_analysis_summary.csv with one row per diagnostic group.
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


def _ensure_plot_dir(output_csv_path: str) -> Tuple[str, str]:
    """
    Ensure the output directory and a 'plots' subdirectory exist.

    Returns:
        (output_dir, plots_path)
    """
    output_dir = os.path.dirname(output_csv_path) or "."
    plots_path = os.path.join(output_dir, "plots")
    os.makedirs(plots_path, exist_ok=True)
    return output_dir, plots_path


# -----------------------------
# Helpers for grouping/deduping
# -----------------------------
def _parameter_dependent_tokens(p: Any) -> List[str]:
    """
    Return a sorted list of scenario tokens that the parameter depends on,
    inferred from its parsed overrides' conditions. If none, return [].
    """
    tokens = set()
    for overrides in getattr(p, "overrides", []) or []:
        for condition in overrides.get("conditions", []):
            token = condition.get("token")
            if token:
                tokens.add(token)
    return sorted(tokens)


def _safe_float(x: Any) -> float:
    """
    Coerce numeric-like values to float; return nan on failure.
    """
    try:
        return float(x)
    except Exception:
        return float("nan")


def _continuous_signature(p: ContinuousParameter) -> Tuple[Any, ...]:
    """
    Build a hashable signature for a resolved continuous parameter, robust to
    non-numeric attributes. The signature is designed to be comparable and
    stable for deduplication.
    """
    return (
        getattr(p, "distribution", None),
        _safe_float(getattr(p, "low_val", float("nan"))),
        _safe_float(getattr(p, "mid_val", float("nan"))),
        _safe_float(getattr(p, "high_val", float("nan"))),
        int(getattr(p, "decimals", 0)),
    )


def _discrete_signature(p: DiscreteParameter) -> Tuple[Any, ...]:
    """
    Hashable signature for a resolved discrete parameter.
    """
    vals = tuple(getattr(p, "values", []))
    probs_raw = getattr(p, "probabilities", [])
    probs = tuple(_safe_float(x) for x in probs_raw)
    return vals, probs


# -----------------------------
# Public: simple histograms
# -----------------------------
def plot_sampled_parameters_histograms(sampled_parameters: List[Dict[str, Any]], output_csv_path: str):
    """
    Saves histograms for numeric parameters (compatibility wrapper).
    """
    output_dir, plots_path = _ensure_plot_dir(output_csv_path)

    # Build numeric dictionary
    parameters_dict: Dict[str, List[float]] = {}
    for sample in sampled_parameters:
        for key, value in sample.items():
            if key.lower() in ("sample", "sample_number"):
                continue
            parameters_dict.setdefault(key, [])
            try:
                parameters_dict[key].append(float(value))
            except (ValueError, TypeError):
                pass

    for param, values in parameters_dict.items():
        if not values:
            continue
        values = np.array(values)

        if len(values) > 1:
            q75 = float(np.percentile(values, 75))
            q25 = float(np.percentile(values, 25))
            iqr = q75 - q25
            # Freedman-Diaconis rule
            bin_width = 2 * iqr * (len(values) ** (-1 / 3)) if iqr > 0 else None
            if bin_width and bin_width > 0:
                bins = max(1, int(math.ceil((values.max() - values.min()) / bin_width)))
            else:
                bins = min(50, max(1, len(values) // 2))
        else:
            bins = 1

        plt.figure(figsize=(10, 4))
        plt.hist(values, bins=bins, alpha=0.75)
        plt.title(f"Histogram of {param}")
        plt.xlabel(param)
        plt.ylabel("Frequency")
        plt.tight_layout()
        plot_filename = os.path.join(plots_path, f"histogram_{param}.png")
        plt.savefig(plot_filename)
        plt.close()

    logger.info(f"Histogram plots saved to {plots_path}")


# -----------------------------
# Analysis + diagnostics (per-parameter, deduped by relevant scenario tokens)
# -----------------------------
def analyze_sampled_parameters(sampled_parameters: List[Dict[str, Any]],
                               parameters: List[Any],
                               output_csv_path: str):
    """
    Produce diagnostics grouped intelligently to avoid redundant plots.

    Strategy:
      - For each parameter, find which scenario tokens it depends on (via overrides).
      - Group samples by only those tokens.
      - Resolve the parameter per group, compute a signature and deduplicate identical resolved
        definitions (one diagnostic per unique signature).
      - Produce per-parameter-per-signature diagnostics and a CSV summary with a `scenario`
        column that lists the covered combinations.
    """
    output_dir, plots_path = _ensure_plot_dir(output_csv_path)
    summary_rows = []

    # Set of parameter tokens (so we can recognise scenario keys in samples)
    param_tokens = {p.token for p in parameters}

    # Discover scenario tokens present in sampled rows
    candidate_tokens = set()
    for s in sampled_parameters:
        for k in s.keys():
            if k not in param_tokens and k not in ("sample", "sample_number"):
                candidate_tokens.add(k)
    # note: we intentionally do not need `all_scenario_tokens` beyond discovering candidates

    # Index samples for quick access
    samples_list = list(sampled_parameters)

    # Process each parameter independently
    for p in parameters:
        token = p.token
        dependent_tokens = _parameter_dependent_tokens(p)

        # If no dependent tokens, treat as global single-group
        if not dependent_tokens:
            combo_map = {tuple(): samples_list}
            combo_keys = [tuple()]
        else:
            combo_map: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
            for s in samples_list:
                combo_key = tuple(s.get(tok) for tok in dependent_tokens)
                combo_map[combo_key].append(s)
            combo_keys = list(combo_map.keys())

        # For each combo_key, resolve the parameter and group by signature
        signature_map: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for combo_key in combo_keys:
            scenario_map = {tok: val for tok, val in zip(dependent_tokens, combo_key)} if dependent_tokens else {}
            p_resolved = resolve_parameters_for_scenario([p], scenario_map)[0]

            if isinstance(p_resolved, ContinuousParameter):
                try:
                    ensure_mid_val(p_resolved)
                except Exception:
                    logger.exception(
                        "Unable to infer mid_val for parameter '%s' when producing diagnostics; continuing.",
                        p_resolved.token,
                    )
                sig = ("c",) + _continuous_signature(p_resolved)

            elif isinstance(p_resolved, DiscreteParameter):
                sig = ("d",) + _discrete_signature(p_resolved)
            else:
                sig = ("u", token)

            if sig not in signature_map:
                signature_map[sig] = {"p_res": p_resolved, "combo_keys": [], "samples": []}
            signature_map[sig]["combo_keys"].append(combo_key)
            signature_map[sig]["samples"].extend(combo_map.get(combo_key, []))

        # --- Overlay histogram for continuous parameters across signature groups ---
        # Build a safe_label for filenames that describes this parameter group
        if dependent_tokens:
            # derive safe_label using the first combo_key from the first signature present
            first_sig = next(iter(signature_map.values()))
            first_ck = first_sig["combo_keys"][0] if first_sig["combo_keys"] else tuple()
            safe_label_overlay = "_".join(f"{tok}-{str(first_ck[i])}" for i, tok in enumerate(dependent_tokens))
            if len(first_sig["combo_keys"]) > 1:
                safe_label_overlay = f"{safe_label_overlay}_and_{len(first_sig['combo_keys'])}"
        else:
            safe_label_overlay = "all"

        # Only attempt overlay when the parameter is continuous. Prepare groups per signature.
        # Each group label will be the joined scenario token assignments (e.g., "bio=low_bio;el=low_el").
        if isinstance(p, ContinuousParameter):
            groups_for_overlay = []
            for _sig, grp in signature_map.items():
                # Build human-readable label from combo_keys
                if dependent_tokens:
                    # Use first combo_key to label this signature; mention if multiple combos map to this signature
                    first_ck = grp["combo_keys"][0] if grp["combo_keys"] else tuple()
                    label = ";".join(f"{tok}={val}" for tok, val in zip(dependent_tokens, first_ck))
                    if len(grp["combo_keys"]) > 1:
                        label = f"{label} (+{len(grp['combo_keys']) - 1})"
                else:
                    label = "all"

                # raw sample values for this signature group
                group_values = [s.get(token) for s in grp["samples"]]
                groups_for_overlay.append((label, group_values))

            # If we have more than one group, create the overlay histogram.
            if len(groups_for_overlay) > 1:
                try:
                    _plot_continuous_histogram_overlay(groups_for_overlay, token, plots_path, safe_label_overlay)
                except Exception:
                    logger.exception("Failed to plot overlay histogram for token %s", token)

        # Emit diagnostics for each unique signature group
        for _sig, grp in signature_map.items():
            p_res = grp["p_res"]
            grp_samples = grp["samples"]

            if dependent_tokens:
                combo_labels = []
                for ck in grp["combo_keys"]:
                    combo_labels.append(";".join(f"{tok}={val}" for tok, val in zip(dependent_tokens, ck)))
                scenario_label = " | ".join(combo_labels)
                # derive a safe short label for filenames (use first combo_key)
                first_ck = grp["combo_keys"][0]
                safe_label = "_".join(f"{tok}-{str(first_ck[i])}" for i, tok in enumerate(dependent_tokens))
                if len(grp["combo_keys"]) > 1:
                    safe_label = f"{safe_label}_and_{len(grp['combo_keys'])}"
            else:
                scenario_label = ""
                safe_label = "all"

            raw_values = [s.get(token) for s in grp_samples]

            if isinstance(p_res, ContinuousParameter):
                numeric = []
                for v in raw_values:
                    try:
                        numeric.append(float(v))
                    except (TypeError, ValueError):
                        pass
                n = len(numeric)
                if n == 0:
                    summary_rows.append({
                        "token": token,
                        "scenario": scenario_label,
                        "type": "continuous",
                        "active": getattr(p, "active", True),
                        "n": 0
                    })
                    continue

                arr = np.array(numeric)
                mean = float(np.mean(arr))
                median = float(np.median(arr))
                std = float(np.std(arr, ddof=0))
                mn = float(np.min(arr))
                mx = float(np.max(arr))
                percentiles = np.percentile(arr, [1, 5, 25, 50, 75, 95, 99]).tolist()
                skew = float(np.sum((arr - mean) ** 3) / (n * (std ** 3))) if std > 0 else 0.0

                sorted_x = np.sort(arr)
                ecdf = np.arange(1, n + 1) / n
                theoretical_cdf_vals = np.array([_theoretical_cdf(p_res, x) for x in sorted_x])
                ks_stat = float(np.max(np.abs(ecdf - theoretical_cdf_vals)))

                _plot_continuous_diagnostics(arr, p_res, plots_path, safe_label)

                summary_rows.append({
                    "token": token,
                    "scenario": scenario_label,
                    "type": "continuous",
                    "active": getattr(p, "active", True),
                    "n": n,
                    "mean": mean,
                    "median": median,
                    "std": std,
                    "min": mn,
                    "max": mx,
                    "skew": skew,
                    "percentile_1": percentiles[0],
                    "percentile_5": percentiles[1],
                    "percentile_25": percentiles[2],
                    "percentile_50": percentiles[3],
                    "percentile_75": percentiles[4],
                    "percentile_95": percentiles[5],
                    "percentile_99": percentiles[6],
                    "ks_statistic": ks_stat,
                    "distribution": getattr(p_res, "distribution", "unknown"),
                    "low_val": getattr(p_res, "low_val", ""),
                    "mid_val": getattr(p_res, "mid_val", ""),
                    "high_val": getattr(p_res, "high_val", "")
                })

            elif isinstance(p_res, DiscreteParameter):
                labels = list(p_res.values)
                counts = {label: 0 for label in labels}
                for v in raw_values:
                    counts[v] = counts.get(v, 0) + 1
                n = sum(counts.values())
                observed_props = {k: counts[k] / n if n > 0 else 0.0 for k in counts}
                expected_probs = getattr(p_res, "probabilities", [1.0 / len(labels)] * len(labels))
                expected_counts = {label: expected_probs[i] * n for i, label in enumerate(labels)}

                chi2 = 0.0
                for _i, label in enumerate(labels):
                    exp = expected_counts[label]
                    obs = counts.get(label, 0)
                    if exp > 0:
                        chi2 += (obs - exp) ** 2 / exp

                _plot_discrete_diagnostics(counts, labels, expected_counts, token, plots_path, safe_label)

                summary_rows.append({
                    "token": token,
                    "scenario": scenario_label,
                    "type": "discrete",
                    "active": getattr(p, "active", True),
                    "n": n,
                    "counts": counts,
                    "observed_props": observed_props,
                    "expected_counts": expected_counts,
                    "chi2": chi2
                })

            else:
                continue

    # Write a CSV summary
    summary_csv = os.path.join(output_dir, "sampling_analysis_summary.csv")
    _write_summary_csv(summary_rows, summary_csv)

    logger.info(f"Sampling analysis complete. Plots in {plots_path}, summary in {summary_csv}")


# -----------------------------
# Helpers: plotting and math
# -----------------------------
def _write_summary_csv(rows: List[Dict[str, Any]], csv_path: str):
    if not rows:
        return
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    keys = sorted(all_keys)
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            flat = {}
            for k in keys:
                v = r.get(k, "")
                if isinstance(v, dict):
                    flat[k] = ";".join(f"{kk}={v[kk]}" for kk in sorted(v.keys()))
                else:
                    flat[k] = v
            writer.writerow(flat)


def _plot_continuous_diagnostics(arr: np.ndarray, p: ContinuousParameter, plots_path: str, scenario_safe_label: str = "all"):
    token = p.token
    n = len(arr)
    x_min = float(arr.min())
    x_max = float(arr.max())
    xs = np.linspace(x_min, x_max, 200)

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
    """
    Plot an overlay histogram for multiple groups.

    Args:
        groups: list of (label, values_list) where values_list are raw values (may include non-numeric)
        token: the parameter token (used for title/filename)
        plots_path: path where to save plot
        safe_label: filename-safe label for grouping (derived elsewhere)
    """
    import os

    import matplotlib.pyplot as plt
    import numpy as np

    # Convert to numeric arrays, skip groups with no numeric data
    numeric_groups = []
    labels = []
    for label, vals in groups:
        arr = []
        for v in vals:
            try:
                arr.append(float(v))
            except Exception:
                # skip non-numeric entries
                pass
        if len(arr) > 0:
            numeric_groups.append(np.array(arr))
            labels.append(label)

    if len(numeric_groups) == 0:
        return

    # Combine to compute common bins using Freedman-Diaconis on combined data
    combined = np.concatenate(numeric_groups)
    if len(combined) <= 1:
        bins = 1
    else:
        q75 = float(np.percentile(combined, 75))
        q25 = float(np.percentile(combined, 25))
        iqr = q75 - q25
        if iqr > 0:
            bin_width = 2 * iqr * (len(combined) ** (-1 / 3))
            bins = max(1, int(np.ceil((combined.max() - combined.min()) / bin_width)))
        else:
            bins = min(50, max(1, len(combined) // 2))

    # Plot overlayed density histograms
    cmap = plt.get_cmap("tab10")
    plt.figure(figsize=(10, 5))

    for i, arr in enumerate(numeric_groups):
        color = cmap(i % 10)
        plt.hist(arr, bins=bins, density=True, alpha=0.45, label=labels[i], color=color, edgecolor="none")

    plt.title(f"Overlay histogram of {token}")
    plt.xlabel(token)
    plt.ylabel("Density")
    plt.legend(loc="best", fontsize="small")
    plt.tight_layout()

    out_file = os.path.join(plots_path, f"histogram_overlay_{token}_{safe_label}.png")
    plt.savefig(out_file)
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


# -----------------------------
# Theoretical distributions (CDF / PDF)
# -----------------------------
def _theoretical_cdf(p: Any, x: Any) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0

    dist = (getattr(p, "distribution", "uniform") or "uniform").lower()

    if dist == "uniform":
        a = getattr(p, "low_val", None)
        b = getattr(p, "high_val", None)
        if a is None or b is None or b == a:
            return 0.0
        if x <= a:
            return 0.0
        if x >= b:
            return 1.0
        return (x - a) / (b - a)

    if dist == "triangular":
        a = p.low_val
        c = p.mid_val
        b = p.high_val
        if a is None or b is None or c is None:
            return 0.0
        if x <= a:
            return 0.0
        if x >= b:
            return 1.0
        if x <= c:
            return ((x - a) ** 2) / ((b - a) * (c - a)) if (c > a) else 0.0
        else:
            return 1.0 - ((b - x) ** 2) / ((b - a) * (b - c)) if (b > c) else 1.0

    if dist == "log-uniform":
        if x <= 0:
            return 0.0
        a = math.log(p.low_val)
        b = math.log(p.high_val)
        lx = math.log(x)
        if lx <= a:
            return 0.0
        if lx >= b:
            return 1.0
        return (lx - a) / (b - a)

    if dist == "log-triangular":
        if x <= 0:
            return 0.0
        a = math.log(p.low_val)
        c = math.log(p.mid_val)
        b = math.log(p.high_val)
        if any(math.isnan(v) for v in (a, b, c)):
            return 0.0
        lx = math.log(x)
        if lx <= a:
            return 0.0
        if lx >= b:
            return 1.0
        if lx <= c:
            return ((lx - a) ** 2) / ((b - a) * (c - a)) if (c > a) else 0.0
        else:
            return 1.0 - ((b - lx) ** 2) / ((b - a) * (b - c)) if (b > c) else 1.0

    return 0.0


def _theoretical_pdf(p: Any, x: Any) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0

    dist = (getattr(p, "distribution", "uniform") or "uniform").lower()

    if dist == "uniform":
        a = getattr(p, "low_val", None)
        b = getattr(p, "high_val", None)
        if a is None or b is None or b == a:
            return 0.0
        return 1.0 / (b - a) if a <= x <= b else 0.0

    if dist == "triangular":
        a = p.low_val
        c = p.mid_val
        b = p.high_val
        if a is None or b is None or a == b:
            return 0.0
        if x < a or x > b:
            return 0.0
        if x <= c:
            return 2 * (x - a) / ((b - a) * (c - a)) if (c > a) else 0.0
        else:
            return 2 * (b - x) / ((b - a) * (b - c)) if (b > c) else 0.0

    if dist == "log-uniform":
        if x <= 0:
            return 0.0
        a = p.low_val
        b = p.high_val
        if a is None or b is None or a <= 0:
            return 0.0
        return 1.0 / (x * (math.log(b) - math.log(a))) if a <= x <= b else 0.0

    if dist == "log-triangular":
        if x <= 0:
            return 0.0
        a = p.low_val
        c = p.mid_val
        b = p.high_val
        if a is None or b is None or c is None or a <= 0 or b <= 0:
            return 0.0
        a_log = math.log(a)
        c_log = math.log(c)
        b_log = math.log(b)
        lx = math.log(x)
        if lx < a_log or lx > b_log:
            return 0.0
        if lx <= c_log:
            pdf_log = 2 * (lx - a_log) / ((b_log - a_log) * (c_log - a_log)) if (c_log > a_log) else 0.0
        else:
            pdf_log = 2 * (b_log - lx) / ((b_log - a_log) * (b_log - c_log)) if (b_log > c_log) else 0.0
        return pdf_log / x

    return 0.0
