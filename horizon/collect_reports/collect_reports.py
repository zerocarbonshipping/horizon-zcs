# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Collect and combine Excel report files into a single CSV.

The functions in this module search for report spreadsheets, infer a common
timeline (from the input with the most timepoints; tie -> longest span),
align all inputs to that timeline, and concatenate everything into one output
file. Input Excel files are **never modified**; reindexing happens only in
memory for the combined CSV.
"""

import concurrent.futures
import fnmatch
import logging
import os
from datetime import datetime
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROGRESS_HEARTBEAT_INTERVAL = 50


# ----------------------------- IO helpers -----------------------------

def process_excel_file(file_path: str, scenario_name: str) -> pd.DataFrame | None:
    """Load a single Excel spreadsheet as raw cell values.

    The function reads the sheet without applying any automatic type
    coercion.  Dates, in particular, are preserved in their original form
    so they can be parsed in a controlled manner later in the pipeline.

    Uses the ``calamine`` engine (Rust-based) for significantly faster
    reads, falling back to ``openpyxl`` when ``python-calamine`` is not
    installed.

    Args:
        file_path: Absolute path to the report file to load.
        scenario_name: Name used to identify the scenario represented by
            this report.  The value is only logged and does not influence
            the parsing.

    Returns:
        A :class:`pandas.DataFrame` containing the raw cell values if the
        file could be read.  ``None`` is returned when the file is missing
        or cannot be parsed.
    """
    try:
        try:
            df = pd.read_excel(
                file_path,
                header=None,
                dtype=object,
                engine="calamine",
            )
        except ImportError:
            logger.debug("calamine not available, falling back to openpyxl for %s", file_path)
            df = pd.read_excel(
                file_path,
                header=None,
                dtype=object,
                engine="openpyxl",
            )
        logger.debug(f"Processed {file_path}")
        return df
    except Exception as e:
        logger.error(f"Error processing {file_path}: {e}")
        return None


def _read_and_parse(
    file_path: str, scenario_name: str
) -> Tuple[pd.DataFrame | None, Tuple[int, pd.Series] | None]:
    """Read an Excel file and parse its dates in one step.

    Combining reading and date parsing into a single callable lets the
    thread-pool executor perform both stages concurrently, eliminating the
    sequential date-parsing loop.

    Args:
        file_path: Path to the report file.
        scenario_name: Scenario label (forwarded to :func:`process_excel_file`).

    Returns:
        A tuple ``(df, date_cache_entry)`` where ``df`` is the raw
        DataFrame (or ``None``) and ``date_cache_entry`` is the result of
        :func:`_parse_file_dates` (or ``None`` when the file could not be
        read).
    """
    df = process_excel_file(file_path, scenario_name)
    if df is not None and not df.empty:
        return df, _parse_file_dates(df)
    return df, None


def collect_files(
    directories: list[str], suffix: str, name_pattern: str | None = None
) -> list[tuple[str, str]]:
    """Locate report files within the supplied directories.

    Args:
        directories: Iterable of paths that should be scanned recursively
            for report files.
        suffix: File name suffix used to identify reports (for example
            ``".xlsx"``).
        name_pattern: Optional filename glob pattern (e.g. ``"summary*"``).
            Only files whose name matches the pattern are returned.  If the
            pattern does not already end with ``suffix``, the suffix is
            appended automatically.

    Returns:
        A list of ``(path, scenario_name)`` tuples where ``path`` points to
        the file and ``scenario_name`` is derived from the file name by
        removing ``suffix``.
    """
    # Normalise the glob pattern so it always includes the suffix
    if name_pattern is not None and not name_pattern.endswith(suffix):
        name_pattern = name_pattern + suffix

    files_to_process = []
    for directory in directories:
        for root, _, files in os.walk(directory):
            for file in files:
                if not file.endswith(suffix):
                    continue
                if name_pattern is not None and not fnmatch.fnmatch(file, name_pattern):
                    continue
                scenario_name = file.replace(suffix, "")
                file_path = os.path.join(root, file)
                files_to_process.append((file_path, scenario_name))
    return files_to_process


def _atomic_write_csv(df: pd.DataFrame, target_file: str, **to_csv_kwargs) -> None:
    """Write a CSV file atomically.

    The DataFrame is first written to a temporary file which is then
    moved into place.  If the target file is locked or otherwise cannot be
    overwritten, the data are written to a timestamped fallback file.

    Args:
        df: Data frame containing the combined report.
        target_file: Destination path for the CSV file.
        **to_csv_kwargs: Additional keyword arguments forwarded to
            :meth:`pandas.DataFrame.to_csv`.

    Raises:
        Exception: Any error raised by :meth:`pandas.DataFrame.to_csv`
            apart from :class:`PermissionError` is re-raised after the
            temporary file has been cleaned up.
    """
    directory = os.path.dirname(target_file)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".~tmp_{os.path.basename(target_file)}")

    try:
        df.to_csv(tmp, **to_csv_kwargs)
        os.replace(tmp, target_file)
        logger.info(f"Combined report saved to: {target_file}")
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = os.path.join(
            directory,
            f"{os.path.splitext(os.path.basename(target_file))[0]}_{ts}.csv"
        )
        try:
            df.to_csv(fallback, **to_csv_kwargs)
            logger.warning("Target file appears to be in use. Wrote to fallback: %s", fallback)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        raise e


# ----------------------- date/timeline utilities -----------------------

def _build_quarterly_index(start="2025-01-01", end="2050-01-01") -> pd.DatetimeIndex:
    """Create a quarterly :class:`~pandas.DatetimeIndex`.

    Args:
        start: Start date for the index.  The value can be anything accepted
            by :func:`pandas.date_range`.
        end: End date for the index.

    Returns:
        A ``DatetimeIndex`` where each entry represents the first day of a
        quarter (January, April, July, and October).
    """
    idx = pd.date_range(start=start, end=end, freq="QS")
    return idx[idx.month.isin([1, 4, 7, 10])]


def _parse_datetime_series(s: pd.Series) -> pd.Series:
    """Parse a date column from various Excel formats robustly.

    Strategy:
        1) Treat plausible Excel serials only (range-gated) as serial days.
        2) Parse common exact string formats quickly (ISO first).
        3) Fallback to a general parser with ``dayfirst=True``.
        4) Normalize to midnight.

    This avoids NumPy overflow by never feeding absurd numbers into the
    ``unit="d"`` conversion path.

    Args:
        s: Series containing the raw date values to parse.

    Returns:
        A ``Series`` of ``datetime64`` values normalized to midnight.
    """
    s = s.copy()

    # Identify numeric-looking cells
    num = pd.to_numeric(s, errors="coerce")
    is_num = num.notna()

    # Range-gate plausible Excel day serials (~1899-12-30 .. ~2173-10-16).
    plausible_serial = is_num & num.between(-60, 100000)

    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # 1) Convert only the plausible serials via Excel epoch
    if plausible_serial.any():
        out.loc[plausible_serial] = pd.to_datetime(
            num.loc[plausible_serial],
            unit="d",
            origin="1899-12-30",
            errors="coerce",
        )

    # 2) Parse remaining as strings with exact formats first
    remaining = s[~plausible_serial]
    if not remaining.empty:
        # ISO YYYY-MM-DD (matches your screenshot)
        iso = pd.to_datetime(remaining, format="%Y-%m-%d", errors="coerce")

        # dd/mm/YYYY HH.MM (e.g., '01/01/2025 00.00')
        dmy_hm = pd.to_datetime(remaining, format="%d/%m/%Y %H.%M", errors="coerce")

        # dd/mm/YYYY
        dmy = pd.to_datetime(remaining, format="%d/%m/%Y", errors="coerce")

        combined = iso.combine_first(dmy_hm).combine_first(dmy)

        # 3) General fallback (handles oddballs); dayfirst=True for EU-style dates
        fallback_mask = combined.isna()
        if fallback_mask.any():
            fallback = pd.to_datetime(remaining[fallback_mask], errors="coerce", dayfirst=True)
            combined.loc[fallback.index] = fallback

        out.loc[combined.notna()] = combined.dropna()

    # 4) Normalize
    return out.dt.normalize()


def _parse_file_dates(df: pd.DataFrame) -> Tuple[int, pd.Series]:
    """Parse dates for the first column once, returning cached results.

    Args:
        df: Raw DataFrame as read from an individual report.

    Returns:
        A tuple ``(first_data_row, parsed_dates)`` where ``first_data_row``
        is the index of the first row with a valid date and ``parsed_dates``
        is the full parsed datetime Series for column 0.
    """
    parsed = _parse_datetime_series(df.iloc[:, 0])
    mask = parsed.notna()
    if not mask.any():
        return len(df), parsed
    return mask.idxmax(), parsed


def _extract_timeline_from_cache(
    first_data_row: int, parsed_dates: pd.Series, df_len: int
) -> pd.DatetimeIndex:
    """Derive a timeline from pre-parsed date data.

    Args:
        first_data_row: Index of the first data row (from ``_parse_file_dates``).
        parsed_dates: Pre-parsed datetime Series (from ``_parse_file_dates``).
        df_len: Length of the original DataFrame.

    Returns:
        A unique and sorted ``DatetimeIndex`` covering all timestamps in the
        report.  An empty index is returned if no dates could be extracted.
    """
    if first_data_row >= df_len:
        return pd.DatetimeIndex([])
    dates = parsed_dates.iloc[first_data_row:].dropna()
    if dates.empty:
        return pd.DatetimeIndex([])
    tl = pd.DatetimeIndex(sorted(dates.unique()))
    tl = tl[~tl.duplicated(keep="first")]
    return tl


def _choose_master_index(
    dfs: list[pd.DataFrame],
    date_cache: list[Tuple[int, pd.Series] | None],
    fallback_start="2025-01-01",
    fallback_end="2050-01-01",
) -> pd.DatetimeIndex:
    """Pick the timeline with the most points.

    If multiple timelines have the same number of points, the one spanning
    the longest period is selected.  When no valid timeline can be derived
    from the inputs a quarterly index covering the fallback range is used.

    Args:
        dfs: List of data frames representing the raw report files.
        date_cache: Pre-parsed date results from ``_parse_file_dates``, one
            entry per DataFrame in ``dfs``.
        fallback_start: Start date for the fallback index.
        fallback_end: End date for the fallback index.

    Returns:
        The ``DatetimeIndex`` chosen to serve as the master timeline for the
        combined output.
    """
    best = None
    best_count = -1
    best_span = pd.Timedelta(0)

    for df, cached in zip(dfs, date_cache):
        if df is None or df.empty or cached is None:
            continue
        first_row, parsed = cached
        tl = _extract_timeline_from_cache(first_row, parsed, len(df))
        if len(tl) == 0:
            continue
        span = tl[-1] - tl[0] if len(tl) > 1 else pd.Timedelta(0)
        if (len(tl) > best_count) or (len(tl) == best_count and span > best_span):
            best = tl
            best_count = len(tl)
            best_span = span

    if best is None or len(best) == 0:
        logger.warning(
            "Could not infer a timeline from inputs; falling back to quarterly index."
        )
        return _build_quarterly_index(start=fallback_start, end=fallback_end)

    logger.info(
        "Selected master timeline with %d points, span %s (%s → %s).",
        len(best),
        str(best[-1] - best[0]) if len(best) > 1 else "0 days",
        best[0].date(),
        best[-1].date(),
    )
    return best


def _align_block_to_index(
    df: pd.DataFrame,
    target_index: pd.DatetimeIndex,
    cached_dates: Tuple[int, pd.Series] | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split a report into header and data aligned to ``target_index``.

    Args:
        df: Raw DataFrame of a single report.
        target_index: Timeline to which the data rows should be aligned.
        cached_dates: Optional pre-parsed ``(first_data_row, parsed_dates)``
            tuple from ``_parse_file_dates``.  When provided the expensive
            date-parsing step is skipped entirely.

    Returns:
        A tuple ``(header, aligned_data)`` where ``header`` contains the
        top three rows of ``df`` and ``aligned_data`` holds the data reindexed
        to ``target_index``.  No disk I/O occurs; all operations happen in
        memory.
    """
    if cached_dates is not None:
        start, parsed = cached_dates
    else:
        start, parsed = _parse_file_dates(df)

    header = df.iloc[:start, :].copy()

    # Normalize header to exactly 3 rows
    if header.shape[0] < 3:
        pad = pd.DataFrame([[""] * header.shape[1]] * (3 - header.shape[0]))
        header = pd.concat([header, pad], ignore_index=True)
    elif header.shape[0] > 3:
        header = header.iloc[:3, :].copy()

    # Use pre-parsed dates for alignment
    values = df.iloc[start:, :].copy()
    values.index = parsed.iloc[start:]
    values = values[~values.index.isna()]
    values = values[~values.index.duplicated(keep="last")]

    aligned = values.reindex(target_index)
    return header, aligned


# ----------------------------- sorting -----------------------------

def sort_combined_df(combined_df: pd.DataFrame) -> pd.DataFrame:
    """Sort the combined report by parameter and scenario.

    Args:
        combined_df: DataFrame produced by :func:`collect_reports` before
            sorting.

    Returns:
        The ``DataFrame`` with columns reordered so that parameters follow
        the order of the first scenario and scenarios are grouped together.
    """
    param_row_index = 2
    scenario_series = combined_df.iloc[0]
    param_series = combined_df.iloc[param_row_index]

    first_scenario = scenario_series.iloc[0]
    master_file_columns = []
    for i, sc in enumerate(scenario_series):
        if sc == first_scenario:
            master_file_columns.append(i)
        else:
            break
    master_params = param_series.iloc[master_file_columns].tolist()
    master_param_rank = {p: i for i, p in enumerate(master_params)}

    scenario_order_mapping = {}
    order = 0
    for sc in scenario_series:
        if sc not in scenario_order_mapping:
            scenario_order_mapping[sc] = order
            order += 1

    sort_keys = []
    for i in range(len(combined_df.columns)):
        param = param_series.iloc[i]
        param_rank = master_param_rank.get(param, len(master_params))
        scenario_val = scenario_series.iloc[i]
        scenario_ord = scenario_order_mapping[scenario_val]
        sort_keys.append((param_rank, scenario_ord, i))

    sorted_indices = sorted(range(len(sort_keys)), key=lambda i: sort_keys[i])
    return combined_df.iloc[:, sorted_indices]


# --------------------------- main pipeline ---------------------------

def collect_reports(
    source_dirs,
    target_file,
    suffix=".xlsx",
    sort_flag=False,
    start_date="2025-01-01",
    end_date="2050-01-01",
    name_pattern=None,
):
    """Collect individual reports and write a combined CSV file.

    Args:
        source_dirs: Iterable of directories containing report files.
        target_file: Path to the resulting CSV file.
        suffix: File suffix that identifies report files.
        sort_flag: If ``True`` the combined DataFrame is sorted by parameter
            and scenario.
        start_date: Start date for the fallback quarterly timeline used when
            no dates can be inferred from the inputs.
        end_date: End date for the fallback quarterly timeline.
        name_pattern: Optional filename glob pattern (e.g. ``"summary*"``).
            Only files matching this pattern are collected.

    Returns:
        ``None``.  The combined report is written to ``target_file``.
    """
    files_to_process = collect_files(source_dirs, suffix, name_pattern=name_pattern)
    if not files_to_process:
        logger.error("No files to process found in the source directories.")
        return

    total = len(files_to_process)

    # Read files and parse dates in a single parallel pass.  The calamine
    # engine releases the GIL during its Rust parsing phase, so a thread
    # pool is sufficient to achieve good parallelism.
    results: list[pd.DataFrame | None] = [None] * total
    date_cache: list[Tuple[int, pd.Series] | None] = [None] * total

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(_read_and_parse, *args): idx
            for idx, args in enumerate(files_to_process)
        }
        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            df, cache = future.result()
            results[idx] = df
            date_cache[idx] = cache
            done_count += 1
            if (total > _PROGRESS_HEARTBEAT_INTERVAL
                    and done_count % _PROGRESS_HEARTBEAT_INTERVAL == 0
                    and done_count < total):
                logger.info("Reading progress: %d/%d files loaded...", done_count, total)
    logger.info("Reading and date parsing complete: %d/%d files loaded.", total, total)

    # Infer a master timeline from the most granular input (tie -> longest span)
    master_index = _choose_master_index(
        results, date_cache, fallback_start=start_date, fallback_end=end_date
    )

    all_columns = []
    all_scenario_labels = []
    n_data_rows = len(master_index)
    aligned_count = 0

    for idx, (raw_df, (_, scenario_name)) in enumerate(zip(results, files_to_process)):
        if raw_df is None or raw_df.empty:
            continue

        # Align to master_index using cached dates (no re-parsing)
        header3, aligned = _align_block_to_index(
            raw_df, master_index, cached_dates=date_cache[idx]
        )

        header_vals = header3.values          # shape (3, n_cols)
        aligned_vals = aligned.values         # shape (n_data_rows, n_cols)

        if aligned_count == 0:
            # Column 0: Date as "dd/mm/YYYY 00.00"
            date_strings = (master_index.strftime("%d/%m/%Y") + " 00.00").values
            # Column 1: Time (day ...) as integer days since first date
            time_days = (master_index - master_index[0]).days.values

            if aligned_vals.shape[1] >= 2:
                aligned_vals = aligned_vals.copy()
                aligned_vals[:, 0] = date_strings
                aligned_vals[:, 1] = time_days

            col_start = 0
        else:
            # For subsequent files drop the first two columns
            col_start = 2

        for j in range(col_start, header_vals.shape[1]):
            col = np.empty(3 + n_data_rows, dtype=object)
            col[:3] = header_vals[:, j]
            col[3:] = aligned_vals[:, j]
            all_columns.append(col)
            all_scenario_labels.append(scenario_name)

        aligned_count += 1
        if (total > _PROGRESS_HEARTBEAT_INTERVAL
                and aligned_count % _PROGRESS_HEARTBEAT_INTERVAL == 0
                and aligned_count < total):
            logger.info("Alignment progress: %d/%d files processed...", aligned_count, total)

    logger.info("Alignment complete: %d files processed.", aligned_count)

    if aligned_count == 0:
        logger.error("No data frames were created. Check the input files.")
        return

    logger.info("Combining %d columns from %d files...", len(all_columns), aligned_count)

    # Build the final array in one shot:
    # Row layout: scenario(0), header(1-3), spacer(4), data(5+)
    total_cols = len(all_columns)
    data_2d = np.column_stack(all_columns)                          # (3+n_data, total_cols)

    scenario_row = np.array(all_scenario_labels, dtype=object).reshape(1, -1)
    spacer_row = np.full((1, total_cols), "", dtype=object)

    final_array = np.vstack([
        scenario_row,       # row 0: scenario names
        data_2d[:3, :],     # rows 1-3: header rows
        spacer_row,         # row 4: blank spacer
        data_2d[3:, :],     # rows 5+: data rows
    ])

    combined_df = pd.DataFrame(final_array)

    # Blank out the parameter cells above Date & Time columns
    if combined_df.shape[1] >= 2:
        combined_df.iat[2, 0] = ""
        combined_df.iat[2, 1] = ""

        if sort_flag:
            combined_df = sort_combined_df(combined_df)

        # Replace NaNs only in row 3 (fuel/subcategory row) with blanks
        combined_df.iloc[3] = combined_df.iloc[3].where(pd.notna(combined_df.iloc[3]), "")

        _atomic_write_csv(
            combined_df,
            target_file,
            index=False,
            header=False,
            na_rep="#N/A",
        )
