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
    "MetricSpec", ["metric_key", "year", "display_name", "aggregation"], defaults=("point",),
)

# Valid aggregation modes
_AGGREGATIONS = {"point", "difference", "cumulative"}

# default metrics when the user provides none
_DEFAULT_METRICS = [
    "TotalEquivalentWTW:difference",
    "Expenses:difference",
    "TotalEquivalentWTW:cumulative",
]

# Known CSV filenames for the parameter samples file
_CSV_NAMES = ["sampled_parameters.csv", "samples.csv"]

# Known filenames for a collected report (output of `horizon -c`)
_COLLECTED_REPORT_NAMES = [
    "report.csv", "report.xlsx",
    "collected_report.csv", "collected_report.xlsx",
]


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
    samples_csv : str
        Path to the parameter samples CSV file.
    report_csv : str or None
        Path to a collected report (output of ``horizon -c``).  When
        provided, metric values are extracted from this single file
        instead of reading individual Excel reports.
    """
    ensemble_dir = os.path.abspath(ensemble_dir)
    output_dir = os.path.abspath(output_dir or os.path.join(ensemble_dir, "prcc_analysis"))
    t_start = time.monotonic()
    logger.info("Starting PRCC analysis for %s", ensemble_dir)

    if samples_csv is None:
        raise FileOperationError(
            "A samples CSV path is required (--samples-csv)."
        )

    # verify all inputs before expensive I/O
    X_df, scenario_df = _load_parameter_matrix(ensemble_dir, samples_csv)
    collected_report_path = _resolve_collected_report(report_csv, ensemble_dir)
    if collected_report_path is None:
        _check_any_report_exists(ensemble_dir)

    os.makedirs(output_dir, exist_ok=True)

    param_names = list(X_df.columns)
    folder_names = list(X_df.index)
    logger.info(
        "Loaded %d samples with %d sample parameters.",
        len(X_df), len(param_names),
    )

    if collected_report_path is not None:
        collected_df = _read_collected_report(collected_report_path)
        metric_specs = _resolve_metric_specs(
            metrics, lambda: _final_year_from_collected(collected_df),
        )
        report_data = _load_metrics_from_collected_report(
            collected_df, folder_names, metric_specs
        )
    else:
        metric_specs = _resolve_metric_specs(
            metrics, lambda: _probe_final_year(ensemble_dir, folder_names),
        )
        report_data = _load_all_metrics(ensemble_dir, folder_names, metric_specs)
    logger.info("Metrics to analyse: %s", [m.display_name for m in metric_specs])
    logger.info("Read reports for %d folders.", len(report_data))

    all_results = []
    plot_tasks = []

    for spec in metric_specs:
        logger.info("Processing metric: %s", spec.display_name)

        y_series = pd.Series(
            {fn: d.get(spec, np.nan) for fn, d in report_data.items()},
            dtype=float,
        ).dropna()
        if y_series.empty:
            logger.warning(
                "No data found for metric '%s' — skipping.", spec.display_name
            )
            continue

        # inner join: drop samples missing either X or y
        common = X_df.index.intersection(y_series.index)
        if len(common) < 4:
            logger.warning(
                "Only %d usable samples for '%s' — skipping.",
                len(common), spec.display_name,
            )
            continue

        # year for point metrics, aggregation name otherwise
        tag = str(spec.year) if spec.aggregation == "point" else spec.aggregation

        for scenario_label, idx in _scenario_groups(scenario_df, common).items():
            if len(idx) < 4:
                logger.info(
                    "Scenario '%s': only %d samples — skipping.",
                    scenario_label, len(idx),
                )
                continue

            X_group = X_df.loc[idx]
            y_group = y_series.loc[idx].values
            prcc_df = compute_prcc(X_group.values, y_group, param_names)
            prcc_df.insert(0, "scenario", scenario_label)
            prcc_df.insert(1, "metric", spec.metric_key)
            prcc_df.insert(2, "year", spec.year)
            prcc_df.insert(3, "aggregation", spec.aggregation)
            prcc_df["rank"] = range(1, len(prcc_df) + 1)
            all_results.append(prcc_df)

            _log_top(prcc_df, scenario_label, spec.display_name)

            stem = f"{spec.metric_key}_{tag}_{scenario_label}"
            plot_tasks.append((plot_tornado, (
                prcc_df, f"{spec.display_name} [{scenario_label}]",
                os.path.join(output_dir, f"prcc_tornado_{stem}.png"),
            )))
            for pname in prcc_df.dropna(subset=["prcc"]).head(_TOP_N)["parameter"]:
                plot_tasks.append((plot_scatter_diagnostics, (
                    X_group[pname].values, y_group, pname, spec.display_name,
                    os.path.join(output_dir, f"prcc_scatter_{stem}_{pname}.png"),
                )))

    _render_plots(plot_tasks)

    if all_results:
        csv_path = os.path.join(output_dir, "prcc_results.csv")
        pd.concat(all_results, ignore_index=True).to_csv(csv_path, index=False)
        logger.info("PRCC results written to %s", csv_path)
    else:
        logger.warning("No PRCC results were produced.")

    logger.info(
        "PRCC analysis complete (%.1fs).  Output directory: %s",
        time.monotonic() - t_start, output_dir,
    )


def _render_plots(plot_tasks):
    """Run ``(function, args)`` plot tasks in a thread pool, logging failures."""
    def _run(task):
        function, args = task
        try:
            function(*args)
        except Exception:
            logger.warning("Plot generation failed.", exc_info=True)

    logger.info("Generating %d plots...", len(plot_tasks))
    with concurrent.futures.ThreadPoolExecutor() as pool:
        list(pool.map(_run, plot_tasks))


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

    return _make_metric_spec(metric_key, year, aggregation)


def _make_metric_spec(metric_key, year, aggregation, display_name=None):
    """Build a MetricSpec; difference/cumulative aggregations take no year."""
    if aggregation != "point":
        year = None

    return MetricSpec(
        metric_key, year,
        display_name or _metric_display_name(metric_key, year, aggregation),
        aggregation,
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
    raw_specs : list[str | MetricSpec] | None
        Metric specification strings (e.g. ``"TotalEquivalentWTW@2050"``) or
        pre-built :class:`MetricSpec` objects (from a config file parser).
    probe_year_fn : callable
        Zero-argument callable returning the final year (int) or *None*.
    """
    specs = [
        item if isinstance(item, MetricSpec) else _parse_metric_spec(item)
        for item in raw_specs or _DEFAULT_METRICS
    ]

    # point specs without a year take the final year found in the data
    if any(s.aggregation == "point" and s.year is None for s in specs):
        final_year = probe_year_fn()
        if final_year is None:
            raise SensitivityAnalysisError(
                "Cannot determine final year from reports.  "
                "Please specify years explicitly "
                "(e.g. --metric 'TotalEquivalentWTW@2050')."
            )
        specs = [
            _make_metric_spec(s.metric_key, final_year, "point")
            if s.aggregation == "point" and s.year is None else s
            for s in specs
        ]

    return specs


def _last_year(cells, parse):
    """Year of the last cell that *parse* understands, or None."""
    return next((y for y in map(parse, reversed(cells)) if y is not None), None)


def _probe_final_year(ensemble_dir, folder_names):
    """Open the first available report and return the last year on the Global sheet."""
    for fname in folder_names[:50]:
        report_path = _find_report(os.path.join(ensemble_dir, fname))
        if report_path is None:
            continue

        df = _read_global_sheet(report_path)
        if df is None:
            continue

        year = _last_year(df.iloc[4:, 0].tolist(), _parse_year)
        if year is not None:
            return year

    return None


def _final_year_from_collected(collected_df):
    """Final year of a collected report DataFrame (data rows start at row 5)."""
    if collected_df is None:
        return None

    return _last_year(collected_df.iloc[5:, 0].tolist(), _parse_year_from_date_str)


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
    num_to_folder = {
        int(m.group(1)): fn for fn in folder_names if (m := _SAMPLE_NUM_RE.search(fn))
    }
    for label in labels:
        m = _SAMPLE_NUM_RE.search(label)
        if m and int(m.group(1)) in num_to_folder:
            label_to_folder[label] = num_to_folder[int(m.group(1))]

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

    scenario_labels = list(df.iloc[0])
    metric_names = list(df.iloc[2])

    # scenario_label -> [(col_idx, metric_name)]
    label_cols = {}
    for ci in range(2, len(scenario_labels)):
        label = str(scenario_labels[ci]).strip() if scenario_labels[ci] is not None else ""
        if not label:
            continue
        label_cols.setdefault(label, []).append(
            (ci, str(metric_names[ci]).strip() if metric_names[ci] is not None else "")
        )

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

    # first data row of each year
    year_rows = {}
    for row_idx in range(5, len(df)):
        y = _parse_year_from_date_str(df.iloc[row_idx, 0])
        if y is not None and y not in year_rows:
            year_rows[y] = row_idx
    years = list(year_rows)
    rows = list(year_rows.values())

    specs_by_key = {}
    for spec in metric_specs:
        specs_by_key.setdefault(spec.metric_key, []).append(spec)

    result = {}
    for label, cols_info in label_cols.items():
        folder = label_to_folder.get(label)
        if folder is None:
            continue

        folder_metrics = result.setdefault(folder, {})
        for ci, mname in cols_info:
            specs = specs_by_key.get(mname)
            if not specs:
                continue

            entries = list(zip(years, map(_safe_float, df.iloc[rows, ci])))
            for spec in specs:
                value = _aggregate(spec, entries)
                if value is None:
                    continue

                if spec.aggregation == "point":
                    spec = spec._replace(
                        display_name=_metric_display_name(mname, spec.year, "point"),
                    )
                folder_metrics.setdefault(spec, value)

    return result


def _aggregate(spec, entries):
    """Reduce ``(year, value)`` rows to the scalar *spec* asks for, or None if absent."""
    if spec.aggregation == "point":
        return next((v for y, v in entries if y == spec.year), None)

    ordered = sorted(entries, key=lambda entry: entry[0])
    if not ordered:
        return None

    if spec.aggregation == "difference":
        return ordered[-1][1] - ordered[0][1]

    return sum(v for _, v in ordered)


def _find_csv(ensemble_dir):
    """Locate the parameter samples CSV in or near *ensemble_dir*.

    Search order (short-circuits on first hit): ``OutputPath`` of any ``.hor``
    file in the directory, known filenames in *ensemble_dir* then its parent,
    then recursive searches under *ensemble_dir* and its parent.
    """
    parent = os.path.dirname(ensemble_dir)

    def _candidates():
        for hor_file in glob.glob(os.path.join(ensemble_dir, "*.hor")):
            output_path = _extract_output_path_from_hor(hor_file)
            if output_path:
                yield output_path
        for folder in (ensemble_dir, parent):
            for name in _CSV_NAMES:
                yield os.path.join(folder, name)
        logger.info("CSV not found in standard locations; scanning recursively...")
        for folder in (ensemble_dir, parent):
            for name in _CSV_NAMES:
                yield from glob.glob(os.path.join(folder, "**", name), recursive=True)

    for path in _candidates():
        if os.path.isfile(path):
            logger.info("Found parameter samples CSV: %s", path)
            return path

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


def _load_parameter_matrix(ensemble_dir, samples_csv=None):
    """Read the samples CSV (auto-discovered near *ensemble_dir* when not given).

    Returns ``(X_df, scenario_df)``: float sample parameters and scenario
    columns, both indexed by realization folder name.
    """
    if samples_csv is None:
        samples_csv = _find_csv(ensemble_dir)
    elif not os.path.isfile(samples_csv):
        raise FileOperationError(
            f"Specified samples CSV not found: {os.path.abspath(samples_csv)}"
        )

    return _load_parameter_matrix_from_csv(samples_csv)


def _sample_suffix(sample_name):
    """Convert ``sample_1`` → ``sample001`` (mirrors file_handler convention)."""
    m = re.match(r"sample_(\d+)$", sample_name)
    if m:
        return f"sample{int(m.group(1)):03d}"
    return sample_name


def _load_parameter_matrix_from_csv(csv_path):
    """Read a samples CSV into ``(X_df, scenario_df)`` indexed by realization folder name."""
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

    # index mirrors the realization folder names: scenario values joined
    # with "_" (the SCENARIO column wins when present), then sampleNNN
    raw_samples = df[sample_col].astype(str)
    if "SCENARIO" in df.columns:
        scenario_prefix = df["SCENARIO"].astype(str)
    elif scenario_cols:
        scenario_prefix = df[scenario_cols].astype(str).apply("_".join, axis=1)
    else:
        scenario_prefix = None
    if scenario_prefix is None:
        df.index = raw_samples
    else:
        df.index = scenario_prefix + "_" + raw_samples.apply(_sample_suffix)
    df.index.name = "folder"

    # all-NaN columns are non-numeric discrete tokens
    X_df = df[sample_cols].apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
    return X_df, df[scenario_cols]


def _load_all_metrics(ensemble_dir, folder_names, metric_specs):
    """Read every realization's report once and extract all requested metrics.

    Returns
    -------
    dict[str, dict[MetricSpec, float]]
        folder_name → {spec → value}.
    """
    def _read_folder(folder_name):
        xlsx_path = _find_report(os.path.join(ensemble_dir, folder_name))
        if xlsx_path is None:
            return {}
        return _read_report_metrics(xlsx_path, metric_specs)

    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = dict(zip(folder_names, pool.map(_read_folder, folder_names)))
    logger.info(
        "Finished reading %d reports (%.1fs).", len(folder_names), time.monotonic() - t0,
    )

    if not any(result.values()):
        logger.warning("No Excel reports found in any realization folder.")
    return result


def _read_report_metrics(xlsx_path, metric_specs):
    """Read the Global sheet of one report and extract all requested metric values.

    Returns
    -------
    dict[MetricSpec, float]
    """
    df = _read_global_sheet(xlsx_path)
    if df is None or len(df) < 5:
        return {}

    keys = {s.metric_key for s in metric_specs}
    col_map = {}
    for ci, mk in enumerate(df.iloc[1]):
        if ci >= 2 and mk in keys:
            col_map.setdefault(mk, ci)

    years = {r: _parse_year(df.iat[r, 0]) for r in range(4, len(df))}
    rows = [r for r, year in years.items() if year is not None]

    result = {}
    for spec in metric_specs:
        ci = col_map.get(spec.metric_key)
        if ci is None:
            continue

        entries = [(years[r], _safe_float(v)) for r, v in zip(rows, df.iloc[rows, ci])]
        value = _aggregate(spec, entries)
        if value is not None:
            result[spec] = value

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
    for pattern in (os.path.join("*", "*.xlsx"), "*.xlsx"):
        xlsx_files = glob.glob(os.path.join(folder_path, pattern))
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


def _scenario_groups(scenario_df, common_index):
    """Build scenario groups from the scenario DataFrame.

    Returns
    -------
    dict[str, pd.Index]
        Mapping from scenario label → folder-name index.
        Always includes an ``"ALL"`` group with every sample.
    """
    groups = {"ALL": common_index}
    if scenario_df.columns.empty:
        return groups

    sdf = scenario_df.loc[scenario_df.index.intersection(common_index)]
    by = list(sdf.columns) if len(sdf.columns) > 1 else sdf.columns[0]
    for combo, idx in sdf.groupby(by).groups.items():
        label = "_".join(str(v) for v in (combo if isinstance(combo, tuple) else (combo,)))
        filtered = idx.intersection(common_index)
        if len(filtered) >= 4:
            groups[label] = filtered

    return groups


def _log_top(prcc_df, scenario, metric_label, n=5):
    """Log the top-N parameters by |PRCC|."""
    top = prcc_df.dropna(subset=["prcc"]).head(n)
    lines = [f"  {r['parameter']:>25s}  PRCC={r['prcc']:+.3f}" for _, r in top.iterrows()]
    logger.info(
        "Top parameters for %s [%s]:\n%s",
        metric_label, scenario, "\n".join(lines),
    )
