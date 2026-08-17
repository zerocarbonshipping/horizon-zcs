# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""PRCC sensitivity-analysis orchestration.

Loads an ensemble directory (parameter samples CSV + simulation Excel
reports), computes Partial Rank Correlation Coefficients for user-selected
scalar metrics, and writes results plus diagnostic plots.

Expected ensemble directory layout::

    3_run/
      1_samples/samples.csv              # parameter samples
      run.hor                            # config file
      run.unc                            # template
      report.csv                         # collected report (optional, from horizon -c)
      s1_sample001/reports/report.xlsx   # individual reports
      s1_sample002/reports/report.xlsx
      ...

Usage from the CLI::

    horizon --sensitivity-analysis --samples-csv samples.csv /path/to/ensemble
    horizon --sensitivity-analysis --samples-csv samples.csv \\
            --report-csv report.csv /path/to/ensemble
    horizon --sensitivity-analysis --samples-csv samples.csv \\
            --metric "TotalEquivalentWTW@2050" /path/to/ensemble /path/to/output
"""

import concurrent.futures
import csv
import glob
import logging
import os
import re
import time
from collections import namedtuple

import numpy as np
import pandas as pd

from horizon.exceptions import (
    FileOperationError,
    SensitivityAnalysisError,
    ValidationError,
)
from horizon.sensitivity.plots import (
    _TOP_N,
    plot_scatter_diagnostics,
    plot_tornado,
)
from horizon.sensitivity.prcc import compute_prcc

logger = logging.getLogger(__name__)

MetricSpec = namedtuple(
    "MetricSpec", ["metric_key", "year", "display_name", "aggregation"],
)
MetricSpec.__new__.__defaults__ = ("point",)  # backward compatible

# Valid aggregation modes
_AGGREGATIONS = {"point", "difference", "cumulative"}

# Metrics available on the Global sheet (same as calibration/analyze.py)
_KNOWN_METRICS = {
    "TotalEquivalentWTW",
    "Expenses",
    "ConsumedEnergy",
    "InstalledPower",
}

# Default metrics when the user provides none.
# Each entry is (metric_key, aggregation).
_DEFAULT_METRICS = [
    ("TotalEquivalentWTW", "difference"),
    ("Expenses", "difference"),
    ("TotalEquivalentWTW", "cumulative"),
]

# Known CSV filenames for the parameter samples file
_CSV_NAMES = ["sampled_parameters.csv", "samples.csv"]

# Known filenames for a collected report (output of `horizon -c`)
_COLLECTED_REPORT_NAMES = [
    "report.csv", "report.xlsx",
    "collected_report.csv", "collected_report.xlsx",
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def prcc_analysis(ensemble_dir, metrics=None, output_dir=None,
                  samples_csv=None, report_csv=None):
    """Run PRCC sensitivity analysis on a completed simulation ensemble.

    Parameters
    ----------
    ensemble_dir : str
        Directory containing per-realization sub-folders with NavigaTE
        Excel reports.
    metrics : list[str] or None
        Metric specifications such as ``"TotalEquivalentWTW@2050"``.
        If *None*, analyses all default metrics at the final available year.
    output_dir : str or None
        Where to write results.  Defaults to ``{ensemble_dir}/prcc_analysis``.
    samples_csv : str or None
        Explicit path to the parameter samples CSV file.  When *None*,
        the CSV is located automatically via ``_find_csv()``.
    report_csv : str or None
        Path to a collected report (output of ``horizon -c``).  When
        provided, metric values are extracted from this single file
        instead of reading individual Excel reports.
    """
    ensemble_dir = os.path.abspath(ensemble_dir)
    if output_dir is None:
        output_dir = os.path.join(ensemble_dir, "prcc_analysis")
    else:
        output_dir = os.path.abspath(output_dir)

    t_start = time.monotonic()
    logger.info("Starting PRCC analysis for %s", ensemble_dir)

    # ------------------------------------------------------------------
    # 0. EARLY VALIDATION — verify all inputs before expensive I/O
    # ------------------------------------------------------------------
    # 0a. Resolve samples CSV path
    if samples_csv is None:
        raise FileOperationError(
            "A samples CSV path is required (--samples-csv)."
        )
    samples_csv = os.path.abspath(samples_csv)
    if not os.path.isfile(samples_csv):
        raise FileOperationError(
            f"Specified samples CSV not found: {samples_csv}"
        )
    logger.info("Using samples CSV: %s", samples_csv)

    # 0b. Resolve collected report
    collected_report_path = _resolve_collected_report(report_csv, ensemble_dir)
    use_collected = collected_report_path is not None

    # 0c. If no collected report, verify at least one individual report exists
    if not use_collected:
        _check_any_report_exists(ensemble_dir)

    os.makedirs(output_dir, exist_ok=True)
    logger.info(
        "Validation complete (%.1fs). Loading parameter matrix...",
        time.monotonic() - t_start,
    )

    # ------------------------------------------------------------------
    # 1. Load parameter matrix
    # ------------------------------------------------------------------
    X_df, scenario_df = _load_parameter_matrix_from_csv(samples_csv)
    param_names = list(X_df.columns)
    folder_names = list(X_df.index)
    logger.info(
        "Loaded %d samples with %d sample parameters.",
        len(X_df), len(param_names),
    )

    # ------------------------------------------------------------------
    # 2. Parse metric specifications
    # ------------------------------------------------------------------
    if use_collected:
        collected_df = _read_collected_report(collected_report_path)
        metric_specs = _resolve_metric_specs(
            metrics, lambda: _final_year_from_collected(collected_df),
        )
    else:
        metric_specs = _resolve_metric_specs(
            metrics, lambda: _probe_final_year(ensemble_dir, folder_names),
        )
    logger.info("Metrics to analyse: %s", [m.display_name for m in metric_specs])

    # ------------------------------------------------------------------
    # 3. Load all metric values
    # ------------------------------------------------------------------
    t_load = time.monotonic()
    logger.info("Loading metric values from %d folders...", len(folder_names))
    if use_collected:
        report_data = _load_metrics_from_collected_report(
            collected_df, folder_names, metric_specs
        )
    else:
        report_data = _load_all_metrics(ensemble_dir, folder_names, metric_specs)
    logger.info(
        "Read reports for %d folders (%.1fs).",
        len(report_data), time.monotonic() - t_load,
    )

    # 4. For each metric, compute PRCC and collect plot tasks
    all_results = []
    plot_tasks = []  # (callable, args) tuples for deferred parallel rendering

    for spec in metric_specs:
        logger.info("Processing metric: %s", spec.display_name)

        y_series = pd.Series(
            {fn: d.get(spec, np.nan) for fn, d in report_data.items()},
            dtype=float,
        )
        if y_series.dropna().empty:
            logger.warning(
                "No data found for metric '%s' — skipping.", spec.display_name
            )
            continue

        # Align X and Y (inner join — drop samples missing either)
        common = X_df.index.intersection(y_series.dropna().index)
        if len(common) < 4:
            logger.warning(
                "Only %d usable samples for '%s' — skipping.",
                len(common), spec.display_name,
            )
            continue

        X_aligned = X_df.loc[common]
        y_aligned = y_series.loc[common].values

        # Determine scenario groups
        groups = _scenario_groups(scenario_df, common)

        for scenario_label, idx in groups.items():
            Xg = X_aligned.loc[idx].values
            yg = y_aligned[np.isin(common, idx)]

            if len(Xg) < 4:
                logger.info(
                    "Scenario '%s': only %d samples — skipping.",
                    scenario_label, len(Xg),
                )
                continue

            prcc_df = compute_prcc(Xg, yg, param_names)
            prcc_df.insert(0, "scenario", scenario_label)
            prcc_df.insert(1, "metric", spec.metric_key)
            prcc_df.insert(2, "year", spec.year)
            prcc_df.insert(3, "aggregation", spec.aggregation)
            prcc_df["rank"] = range(1, len(prcc_df) + 1)
            all_results.append(prcc_df)

            _log_top(prcc_df, scenario_label, spec.display_name)

            # Tag for filenames: year for point, aggregation name otherwise
            year_tag = (
                str(spec.year) if spec.aggregation == "point"
                else spec.aggregation
            )

            # Queue tornado plot
            tornado_name = (
                f"prcc_tornado_{spec.metric_key}"
                f"_{year_tag}_{scenario_label}.png"
            )
            plot_tasks.append((
                plot_tornado,
                (prcc_df.copy(),
                 f"{spec.display_name} [{scenario_label}]",
                 os.path.join(output_dir, tornado_name)),
            ))

            # Queue scatter diagnostics for top parameters
            top_params = prcc_df.dropna(subset=["prcc"]).head(_TOP_N)
            for _, row in top_params.iterrows():
                pname = row["parameter"]
                if pname not in X_aligned.columns:
                    continue
                scatter_name = (
                    f"prcc_scatter_{spec.metric_key}"
                    f"_{year_tag}_{scenario_label}_{pname}.png"
                )
                x_vals = X_aligned.loc[idx, pname].values.copy()
                y_vals = yg.copy()
                plot_tasks.append((
                    plot_scatter_diagnostics,
                    (x_vals, y_vals, pname,
                     spec.display_name,
                     os.path.join(output_dir, scatter_name)),
                ))

    # Generate all plots in parallel
    if plot_tasks:
        t_plot = time.monotonic()
        logger.info("Generating %d plots...", len(plot_tasks))
        done_plots = 0
        with concurrent.futures.ThreadPoolExecutor() as pool:
            futures = [pool.submit(fn, *args) for fn, args in plot_tasks]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    logger.warning("Plot generation failed.", exc_info=True)
                done_plots += 1
                if done_plots % 50 == 0:
                    logger.info(
                        "Plots: %d / %d (%.1fs)...",
                        done_plots, len(plot_tasks),
                        time.monotonic() - t_plot,
                    )
        logger.info(
            "All %d plots generated (%.1fs).",
            len(plot_tasks), time.monotonic() - t_plot,
        )

    # 5. Write combined CSV
    if all_results:
        results_df = pd.concat(all_results, ignore_index=True)
        csv_path = os.path.join(output_dir, "prcc_results.csv")
        results_df.to_csv(csv_path, index=False)
        logger.info("PRCC results written to %s", csv_path)
    else:
        logger.warning("No PRCC results were produced.")

    elapsed = time.monotonic() - t_start
    logger.info(
        "PRCC analysis complete (%.1fs).  Output directory: %s",
        elapsed, output_dir,
    )


# ---------------------------------------------------------------------------
# Early validation helpers
# ---------------------------------------------------------------------------

def _resolve_collected_report(report_csv, ensemble_dir):
    """Return path to a collected report, or *None* if unavailable.

    If *report_csv* is given explicitly, validates it exists.
    Otherwise tries auto-detecting common filenames in *ensemble_dir*.
    """
    if report_csv is not None:
        path = os.path.abspath(report_csv)
        if not os.path.isfile(path):
            raise FileOperationError(
                f"Specified report CSV not found: {path}"
            )
        logger.info("Using collected report: %s", path)
        return path

    # Auto-detect
    for name in _COLLECTED_REPORT_NAMES:
        candidate = os.path.join(ensemble_dir, name)
        if os.path.isfile(candidate):
            logger.info("Auto-detected collected report: %s", candidate)
            return candidate

    return None


def _check_any_report_exists(ensemble_dir):
    """Quick sanity check that at least one xlsx report is findable."""
    try:
        entries = sorted(os.listdir(ensemble_dir))
    except OSError:
        entries = []
    for entry in entries[:50]:
        entry_path = os.path.join(ensemble_dir, entry)
        if os.path.isdir(entry_path) and _find_report(entry_path) is not None:
            return
    logger.warning(
        "No Excel reports found under %s. "
        "Consider providing a collected report via --report-csv.",
        ensemble_dir,
    )


# ---------------------------------------------------------------------------
# Metric specification parsing
# ---------------------------------------------------------------------------

def _parse_metric_spec(spec_str):
    """Parse a metric specification string.

    Formats supported::

        MetricKey@Year            point value (backward compatible)
        MetricKey@Year:point      explicit point
        MetricKey                 point, year resolved from data
        MetricKey:difference      last year minus first year
        MetricKey:cumulative      sum over all years

    Returns
    -------
    MetricSpec
    """
    # Split off aggregation suffix (last colon-separated token)
    aggregation = "point"
    if ":" in spec_str:
        head, tail = spec_str.rsplit(":", 1)
        tail_stripped = tail.strip().lower()
        if tail_stripped in _AGGREGATIONS:
            aggregation = tail_stripped
            spec_str = head
        # Otherwise the colon is part of the metric key (unlikely but safe)

    year = None
    if "@" in spec_str:
        parts = spec_str.split("@", 1)
        metric_key = parts[0].strip()
        try:
            year = int(parts[1].strip())
        except ValueError:
            raise ValidationError(
                f"Invalid year in metric spec '{spec_str}': "
                f"'{parts[1].strip()}' is not an integer."
            )
    else:
        metric_key = spec_str.strip()

    if not metric_key:
        raise ValidationError(f"Empty metric key in spec '{spec_str}'.")

    # For difference/cumulative, year is not applicable
    if aggregation in ("difference", "cumulative"):
        year = None

    display = _metric_display_name(metric_key, year, aggregation)
    return MetricSpec(
        metric_key=metric_key, year=year,
        display_name=display, aggregation=aggregation,
    )


def _metric_display_name(metric_key, year, aggregation):
    """Build a human-readable display name for a metric spec."""
    if aggregation == "point":
        return f"{metric_key}@{year}" if year else metric_key
    return f"{metric_key} ({aggregation})"


def _resolve_metric_specs(raw_specs, probe_year_fn):
    """Parse raw spec strings and fill in missing years from data.

    Parameters
    ----------
    raw_specs : list[str] | list[tuple] | list[MetricSpec] | None
        Metric specification strings (e.g. ``"TotalEquivalentWTW@2050"``),
        ``(key, aggregation)`` tuples (from defaults), or pre-built
        :class:`MetricSpec` objects (from a config file parser).
    probe_year_fn : callable
        Zero-argument callable returning the final year (int) or *None*.
    """
    if not raw_specs:
        raw_specs = list(_DEFAULT_METRICS)

    # Normalise heterogeneous input into MetricSpec objects.
    specs = []
    for item in raw_specs:
        if isinstance(item, MetricSpec):
            specs.append(item)
        elif isinstance(item, tuple):
            key, agg = item
            specs.append(MetricSpec(
                metric_key=key, year=None,
                display_name=_metric_display_name(key, None, agg),
                aggregation=agg,
            ))
        else:
            specs.append(_parse_metric_spec(item))

    # For *point* specs with year=None, probe one report to find the final
    # year.  Difference/cumulative specs never need a year.
    needs_year = [
        s for s in specs if s.aggregation == "point" and s.year is None
    ]
    if needs_year:
        final_year = probe_year_fn()
        if final_year is None:
            raise SensitivityAnalysisError(
                "Cannot determine final year from reports.  "
                "Please specify years explicitly "
                "(e.g. --metric 'TotalEquivalentWTW@2050')."
            )
        specs = [
            MetricSpec(
                s.metric_key, final_year,
                _metric_display_name(s.metric_key, final_year, s.aggregation),
                s.aggregation,
            )
            if s.aggregation == "point" and s.year is None else s
            for s in specs
        ]

    return specs


def _probe_final_year(ensemble_dir, folder_names):
    """Open the first available report and return the last year on the Global sheet."""
    for fname in folder_names[:50]:
        folder_path = os.path.join(ensemble_dir, fname)
        report_path = _find_report(folder_path)
        if report_path is None:
            continue
        df = _read_global_sheet(report_path)
        if df is None or len(df) < 5:
            continue
        for row_idx in range(len(df) - 1, 3, -1):
            y = _parse_year(df.iloc[row_idx, 0])
            if y is not None:
                return y
    return None


# ---------------------------------------------------------------------------
# Collected report parsing
# ---------------------------------------------------------------------------

def _final_year_from_collected(collected_df):
    """Extract the final year from the last data row of a collected report DataFrame."""
    if collected_df is None or len(collected_df) < 6:
        return None
    # Data rows start at row 5; scan backwards for a parseable year
    for row_idx in range(len(collected_df) - 1, 4, -1):
        y = _parse_year_from_date_str(collected_df.iloc[row_idx, 0])
        if y is not None:
            return y
    return None


def _parse_year_from_date_str(cell_value):
    """Extract year from date strings like ``'01/01/2050 00.00'`` or ``'01-01-2050'``."""
    if cell_value is None:
        return None
    s = str(cell_value).strip()
    # Try dd/mm/YYYY or dd-mm-YYYY format first
    parts = s.split()
    if parts:
        date_part = parts[0]
        for sep in ("/", "-"):
            segments = date_part.split(sep)
            if len(segments) == 3:
                try:
                    return int(segments[2])
                except ValueError:
                    pass
    # Fallback to generic year parsing
    return _parse_year(cell_value)


def _read_collected_report(report_path):
    """Read a collected report (CSV or Excel) into a raw DataFrame."""
    try:
        if report_path.endswith((".xlsx", ".xls")):
            return _read_excel_raw(report_path)
        else:
            return pd.read_csv(report_path, header=None, dtype=object)
    except Exception:
        logger.warning("Failed to read collected report: %s", report_path)
        return None


_SAMPLE_NUM_RE = re.compile(r"sample[_]?(\d+)")


def _boundary_prefix_match(prefix, full):
    """True if *full* starts with *prefix* at a name boundary.

    The character following the prefix must be non-alphanumeric (or absent),
    so ``sample1`` matches ``sample1_run`` but not ``sample10``.
    """
    if not full.startswith(prefix):
        return False

    return len(full) == len(prefix) or not full[len(prefix)].isalnum()


def _match_labels_to_folders(labels, folder_names):
    """Match collected-report labels to sample folder names.

    Tries three strategies in order:
    1. Direct / exact match.
    2. Boundary-aware prefix match (folder name is a prefix of label, or
       vice-versa, ending at a name boundary); the longest matching folder
       name wins, so ``sample100`` is never attributed to ``sample1``.
    3. Sample-number match — extract the numeric sample index from both
       sides (e.g. ``s1_sample001_mtc_metrics`` → 1, ``sample_1`` → 1).
    """
    folder_set = set(folder_names)
    label_to_folder = {}

    # Strategy 1+2: direct and prefix
    for label in labels:
        if label in folder_set:
            label_to_folder[label] = label
            continue

        candidates = [
            fn for fn in folder_names
            if _boundary_prefix_match(fn, label) or _boundary_prefix_match(label, fn)
        ]
        if candidates:
            # prefer the most specific (longest) folder name
            label_to_folder[label] = max(candidates, key=len)

    if label_to_folder:
        return label_to_folder

    # Strategy 3: match by sample number
    num_to_folder = {}
    for fn in folder_names:
        m = _SAMPLE_NUM_RE.search(fn)
        if m:
            num_to_folder[int(m.group(1))] = fn

    if not num_to_folder:
        return {}

    for label in labels:
        m = _SAMPLE_NUM_RE.search(label)
        if m:
            n = int(m.group(1))
            if n in num_to_folder:
                label_to_folder[label] = num_to_folder[n]

    return label_to_folder


def _load_metrics_from_collected_report(collected_df, folder_names, metric_specs):
    """Extract metric values from a pre-read collected report DataFrame.

    The collected report format (``horizon -c`` output):
    - Row 0: scenario labels (e.g. ``s1_sample001_mtc_metrics``)
    - Row 1: ``Date``, ``Time (days)``, sheet name (``global``), ...
    - Row 2: metric names (e.g. ``TotalEquivalentWTW``)
    - Row 3: sub-metric / fuel type (may be empty)
    - Row 4: blank spacer
    - Row 5+: data rows (col 0 = date string, col 1 = time_days, col 2+ = values)

    Returns
    -------
    dict[str, dict[MetricSpec, float]]
        folder_name → {spec → value}.
    """
    df = collected_df
    if df is None or len(df) < 6:
        logger.warning("Collected report is empty or too short.")
        return {}

    scenario_labels = list(df.iloc[0])  # row 0
    metric_names = list(df.iloc[2])     # row 2

    # Build column groups: map scenario_label → list of (col_idx, metric_name)
    # First, identify unique column groups by consecutive identical labels
    label_cols = {}  # scenario_label → [(col_idx, metric_name)]
    for ci in range(2, len(scenario_labels)):
        label = str(scenario_labels[ci]).strip() if scenario_labels[ci] is not None else ""
        if not label:
            continue
        label_cols.setdefault(label, []).append(
            (ci, str(metric_names[ci]).strip() if metric_names[ci] is not None else "")
        )

    # Map scenario labels to folder names
    label_to_folder = _match_labels_to_folders(
        list(label_cols.keys()), folder_names,
    )

    if not label_to_folder:
        logger.warning(
            "Could not match any collected report labels to sample folder names. "
            "Labels sample: %s; Folder names sample: %s",
            list(label_cols.keys())[:3], folder_names[:3],
        )
        return {}

    logger.info(
        "Matched %d / %d report labels to folder names.",
        len(label_to_folder), len(label_cols),
    )

    # Build year → row_idx mapping from data rows
    year_rows = {}  # year → row_idx
    for row_idx in range(5, len(df)):
        y = _parse_year_from_date_str(df.iloc[row_idx, 0])
        if y is not None and y not in year_rows:
            year_rows[y] = row_idx

    # Categorise specs by what data they need
    needed_keys = set()  # metric keys we need columns for
    point_specs = {}     # metric_key → set of target years
    ts_specs = []        # specs needing full time series (difference/cumulative)
    for spec in metric_specs:
        needed_keys.add(spec.metric_key)
        if spec.aggregation == "point":
            point_specs.setdefault(spec.metric_key, set()).add(spec.year)
        else:
            ts_specs.append(spec)

    sorted_years = sorted(year_rows.keys()) if year_rows else []
    first_year = sorted_years[0] if sorted_years else None
    last_year = sorted_years[-1] if sorted_years else None

    # Extract values
    result = {}
    for label, cols_info in label_cols.items():
        folder = label_to_folder.get(label)
        if folder is None:
            continue

        folder_metrics = result.setdefault(folder, {})

        for ci, mname in cols_info:
            if mname not in needed_keys:
                continue

            # --- point specs: single year lookup ---
            for year in point_specs.get(mname, ()):
                spec = MetricSpec(
                    mname, year,
                    _metric_display_name(mname, year, "point"), "point",
                )
                if spec in folder_metrics:
                    continue
                row_idx = year_rows.get(year)
                if row_idx is None:
                    continue
                folder_metrics[spec] = _safe_float(df.iloc[row_idx, ci])

            # --- time-series specs: read all years once per column ---
            ts_for_key = [s for s in ts_specs if s.metric_key == mname]
            if not ts_for_key or not sorted_years:
                continue
            # Skip if all specs for this key already resolved
            if all(s in folder_metrics for s in ts_for_key):
                continue

            series_vals = []
            for y in sorted_years:
                ri = year_rows[y]
                series_vals.append(_safe_float(df.iloc[ri, ci]))

            for spec in ts_for_key:
                if spec in folder_metrics:
                    continue
                if spec.aggregation == "difference":
                    first_val = _safe_float(df.iloc[year_rows[first_year], ci])
                    last_val = _safe_float(df.iloc[year_rows[last_year], ci])
                    if first_val is not None and last_val is not None:
                        folder_metrics[spec] = last_val - first_val
                elif spec.aggregation == "cumulative":
                    valid = [v for v in series_vals if v is not None]
                    if valid:
                        folder_metrics[spec] = sum(valid)

    return result


# ---------------------------------------------------------------------------
# CSV discovery
# ---------------------------------------------------------------------------

_CSV_NAMES_SET = {"sampled_parameters.csv", "samples.csv"}


def _find_csv(ensemble_dir):
    """Locate the parameter samples CSV in or near *ensemble_dir*.

    Search order (short-circuits on first hit):
    1. ``OutputPath`` from any ``.hor`` file in the directory.
    2. Known filenames in *ensemble_dir*.
    3. Known filenames in parent directory.
    4. Recursive search under *ensemble_dir*.
    5. Recursive search under parent directory.
    """
    def _check(*paths):
        for p in paths:
            if os.path.isfile(p):
                logger.info("Found parameter samples CSV: %s", p)
                return p
        return None

    logger.info("Searching for parameter samples CSV near %s...", ensemble_dir)

    # 1. Try reading OutputPath from .hor file if present (most reliable)
    for hor_file in glob.glob(os.path.join(ensemble_dir, "*.hor")):
        output_path = _extract_output_path_from_hor(hor_file)
        if output_path:
            hit = _check(output_path)
            if hit:
                return hit

    # 2. Known names in ensemble_dir
    hit = _check(*(os.path.join(ensemble_dir, n) for n in _CSV_NAMES))
    if hit:
        return hit

    # 3. Known names in parent directory
    parent = os.path.dirname(ensemble_dir)
    hit = _check(*(os.path.join(parent, n) for n in _CSV_NAMES))
    if hit:
        return hit

    # 4. Recursive search under ensemble_dir (expensive — only if above failed)
    logger.info(
        "CSV not found in standard locations; scanning %s recursively "
        "(this may be slow on large directories — use --samples-csv to skip)...",
        ensemble_dir,
    )
    t0 = time.monotonic()
    for name in _CSV_NAMES:
        matches = glob.glob(
            os.path.join(ensemble_dir, "**", name), recursive=True,
        )
        hit = _check(*matches)
        if hit:
            return hit

    # 5. Recursive search under parent (last resort)
    logger.info(
        "Scanning parent directory %s recursively (%.1fs so far)...",
        parent, time.monotonic() - t0,
    )
    for name in _CSV_NAMES:
        matches = glob.glob(
            os.path.join(parent, "**", name), recursive=True,
        )
        hit = _check(*matches)
        if hit:
            return hit

    raise FileOperationError(
        f"Cannot find parameter samples CSV in or near {ensemble_dir}. "
        f"Searched for: {', '.join(_CSV_NAMES)}. "
        f"Tip: ensure your .hor file's OutputPath points to the CSV."
    )


def _extract_output_path_from_hor(hor_path):
    """Extract ``OutputPath`` value from a ``.hor`` file."""
    try:
        with open(hor_path) as f:
            content = f.read()
    except OSError:
        return None
    match = re.search(r'OutputPath\s*=\s*"([^"]+)"', content)
    if match:
        path = match.group(1)
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(hor_path), path)
        return path
    return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_parameter_matrix(ensemble_dir, samples_csv=None):
    """Read the parameter samples CSV and return the parameter matrix.

    Prefer ``_load_parameter_matrix_from_csv()`` when the CSV path is
    already resolved (e.g. after early validation).

    Parameters
    ----------
    ensemble_dir : str
        Ensemble directory (used for CSV auto-discovery fallback).
    samples_csv : str or None
        Explicit path to the CSV.  Skips auto-discovery when provided.

    Returns
    -------
    X_df : pd.DataFrame
        Sample parameters (float), indexed by folder name.
    scenario_df : pd.DataFrame
        Scenario columns, indexed by folder name (may be empty).
    """
    if samples_csv is not None:
        csv_path = os.path.abspath(samples_csv)
        if not os.path.isfile(csv_path):
            raise FileOperationError(
                f"Specified samples CSV not found: {csv_path}"
            )
    else:
        csv_path = _find_csv(ensemble_dir)

    return _load_parameter_matrix_from_csv(csv_path)


def _sample_suffix(sample_name):
    """Convert ``sample_1`` → ``sample001`` (mirrors file_handler convention)."""
    m = re.match(r"sample_(\d+)$", sample_name)
    if m:
        return f"sample{int(m.group(1)):03d}"
    return sample_name


def _load_parameter_matrix_from_csv(csv_path):
    """Read a resolved CSV path and return the parameter matrix.

    Returns
    -------
    X_df : pd.DataFrame
        Sample parameters (float), indexed by folder name.
    scenario_df : pd.DataFrame
        Scenario columns, indexed by folder name (may be empty).
    """
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        type_row = next(reader)

    col_types = dict(zip(header, type_row))

    df = pd.read_csv(csv_path, skiprows=[1])  # skip the type row

    # Identify the sample name column
    sample_col = "sample" if "sample" in df.columns else "sample_number"

    # Separate scenario columns from sample-parameter columns.
    # First try the type row; fall back to non-numeric detection.
    skip = {sample_col, "sample", "sample_number"}
    scenario_cols = [
        c for c in header
        if c not in skip and col_types.get(c, "").strip().lower() == "scenarioparameter"
    ]
    if not scenario_cols:
        # Fallback: columns whose values are entirely non-numeric are
        # categorical (scenario) columns.  This covers CSVs where the
        # type row is blank for scenario columns.
        scenario_cols = [
            c for c in header
            if c not in skip and c and pd.to_numeric(df[c], errors="coerce").isna().all()
        ]
    sample_cols = [
        c for c in header
        if c not in skip and c not in scenario_cols and c
    ]

    # Build a unique index that mirrors the realization folder names on disk.
    # The file handler logic (file_handler.py):
    #   - If a SCENARIO column exists, use its value as the prefix
    #   - Otherwise, join all scenario token values with "_"
    # Then append the zero-padded sample suffix (sample001, sample002, …).
    raw_samples = df[sample_col].astype(str)
    if "SCENARIO" in df.columns and "SCENARIO" not in skip:
        scenario_prefix = df["SCENARIO"].astype(str)
        suffixes = raw_samples.apply(_sample_suffix)
        df.index = scenario_prefix + "_" + suffixes
    elif scenario_cols:
        scenario_prefix = df[scenario_cols].astype(str).apply("_".join, axis=1)
        suffixes = raw_samples.apply(_sample_suffix)
        df.index = scenario_prefix + "_" + suffixes
    else:
        df.index = raw_samples
    df.index.name = "folder"

    # Build scenario DataFrame
    scenario_df = df[scenario_cols].copy() if scenario_cols else pd.DataFrame(index=df.index)

    # Build X with sample parameters as floats
    X_df = df[sample_cols].copy()
    for col in X_df.columns:
        X_df[col] = pd.to_numeric(X_df[col], errors="coerce")

    # Drop columns that are entirely NaN (non-numeric discrete tokens etc.)
    X_df.dropna(axis=1, how="all", inplace=True)

    return X_df, scenario_df


# ---------------------------------------------------------------------------
# Single-pass report reading
# ---------------------------------------------------------------------------

def _load_all_metrics(ensemble_dir, folder_names, metric_specs):
    """Read all reports once and extract all requested metrics.

    Returns
    -------
    dict[str, dict[MetricSpec, float]]
        folder_name → {spec → value}.
    """
    # Build lookup structures for fast metric extraction
    metric_keys = {s.metric_key for s in metric_specs}
    year_specs = {}  # metric_key -> set of years
    for s in metric_specs:
        year_specs.setdefault(s.metric_key, set()).add(s.year)

    # Discover reports and read them in parallel (single thread pool for both)
    result = {}
    done = 0
    total = len(folder_names)

    def _find_and_read(fname):
        folder_path = os.path.join(ensemble_dir, fname)
        xlsx_path = _find_report(folder_path)
        if xlsx_path is None:
            return fname, {}
        return fname, _read_report_metrics(
            xlsx_path, metric_keys, year_specs, metric_specs,
        )

    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        for fname, metrics in pool.map(_find_and_read, folder_names):
            result[fname] = metrics
            done += 1
            if done % 200 == 0:
                logger.info(
                    "Read %d / %d reports (%.1fs)...",
                    done, total, time.monotonic() - t0,
                )
    logger.info(
        "Finished reading %d reports (%.1fs).", total, time.monotonic() - t0,
    )

    if not any(result.values()):
        logger.warning("No Excel reports found in any realization folder.")
    return result


def _read_report_metrics(xlsx_path, metric_keys, year_specs, metric_specs):
    """Read a single report and extract all requested metric values.

    Uses calamine engine (Rust, fast) with openpyxl fallback.

    Returns
    -------
    dict[MetricSpec, float]
    """
    df = _read_global_sheet(xlsx_path)
    if df is None or len(df) < 5:
        return {}

    row_metrics = list(df.iloc[1])

    # Find column indices for requested metrics
    col_map = {}  # metric_key -> col_idx
    for ci in range(2, len(row_metrics)):
        mk = row_metrics[ci]
        if mk in metric_keys and mk not in col_map:
            col_map[mk] = ci

    if not col_map:
        return {}

    # Separate point specs from time-series specs
    point_specs = [s for s in metric_specs if s.aggregation == "point"]
    ts_specs = [s for s in metric_specs if s.aggregation != "point"]

    # Single pass: collect point values and full time series
    result = {}
    # For time-series specs, accumulate {metric_key: [(year, value), ...]}
    ts_data = {}  # metric_key -> list of (year, value)

    for row_idx in range(4, len(df)):
        year = _parse_year(df.iloc[row_idx, 0])
        if year is None:
            continue

        for spec in point_specs:
            if spec.year == year and spec.metric_key in col_map:
                if spec not in result:
                    result[spec] = _safe_float(
                        df.iloc[row_idx, col_map[spec.metric_key]]
                    )

        for spec in ts_specs:
            if spec.metric_key in col_map:
                val = _safe_float(
                    df.iloc[row_idx, col_map[spec.metric_key]]
                )
                ts_data.setdefault(spec.metric_key, []).append((year, val))

    # Resolve time-series specs into scalar values
    for spec in ts_specs:
        entries = ts_data.get(spec.metric_key)
        if not entries:
            continue
        entries.sort(key=lambda t: t[0])
        if spec.aggregation == "difference":
            first_val, last_val = entries[0][1], entries[-1][1]
            if first_val is not None and last_val is not None:
                result[spec] = last_val - first_val
        elif spec.aggregation == "cumulative":
            valid = [v for _, v in entries if v is not None]
            if valid:
                result[spec] = sum(valid)

    return result


def _read_excel_raw(path, **kwargs):
    """Read an Excel file with calamine engine, falling back to openpyxl."""
    try:
        return pd.read_excel(
            path, header=None, dtype=object, engine="calamine", **kwargs,
        )
    except ImportError:
        return pd.read_excel(
            path, header=None, dtype=object, engine="openpyxl", **kwargs,
        )


def _read_global_sheet(xlsx_path):
    """Read the Global sheet from an Excel report.

    Returns
    -------
    pd.DataFrame or None
    """
    try:
        return _read_excel_raw(xlsx_path, sheet_name="Global")
    except Exception:
        return None


def _find_report(folder_path):
    """Find the first ``.xlsx`` report in a simulation folder."""
    xlsx_files = glob.glob(os.path.join(folder_path, "*", "*.xlsx"))
    if xlsx_files:
        return xlsx_files[0]
    xlsx_files = glob.glob(os.path.join(folder_path, "*.xlsx"))
    if xlsx_files:
        return xlsx_files[0]
    return None


def _parse_year(cell_value):
    """Extract an integer year from a cell value."""
    if cell_value is None:
        return None
    if hasattr(cell_value, "year"):
        return cell_value.year
    try:
        return int(pd.Timestamp(cell_value).year)
    except Exception:
        return None


def _safe_float(v):
    if v is None:
        return np.nan
    try:
        return float(v)
    except (ValueError, TypeError):
        return np.nan


# ---------------------------------------------------------------------------
# Scenario grouping
# ---------------------------------------------------------------------------

def _scenario_groups(scenario_df, common_index):
    """Build scenario groups from the scenario DataFrame.

    Returns
    -------
    dict[str, pd.Index]
        Mapping from scenario label → folder-name index.
        Always includes an ``"ALL"`` group with every sample.
    """
    groups = {"ALL": common_index}

    if scenario_df.empty or scenario_df.columns.empty:
        return groups

    # Filter to common index
    sdf = scenario_df.loc[scenario_df.index.intersection(common_index)]
    if sdf.empty:
        return groups

    # Group by unique scenario combination
    group_cols = list(sdf.columns)
    grouped = sdf.groupby(group_cols[0]).groups if len(group_cols) == 1 else sdf.groupby(group_cols).groups
    for combo_vals, idx in grouped.items():
        if not isinstance(combo_vals, tuple):
            combo_vals = (combo_vals,)
        label = "_".join(str(v) for v in combo_vals)
        filtered = idx.intersection(common_index)
        if len(filtered) >= 4:
            groups[label] = filtered

    return groups


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_top(prcc_df, scenario, metric_label, n=5):
    """Log the top-N parameters by |PRCC|."""
    top = prcc_df.dropna(subset=["prcc"]).head(n)
    lines = [f"  {r['parameter']:>25s}  PRCC={r['prcc']:+.3f}" for _, r in top.iterrows()]
    logger.info(
        "Top parameters for %s [%s]:\n%s",
        metric_label, scenario, "\n".join(lines),
    )
