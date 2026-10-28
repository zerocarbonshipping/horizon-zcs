# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""What the Lantern file holds per scenario combination: the representative sample, and the median and band over
the samples of each row and date, the summary Lantern once computed from an ensemble (lantern-zcs fa50cd8)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.compute as pc

from horizon.results import as_written

logger = logging.getLogger(__name__)

# The series of kind global that choose the representative, by weight. Each weight is split evenly over the
# lines of its series, as Lantern split a plot's weight: WTW emissions, installed power by fuel, fuel consumed
# by fuel type and fuel-related expenses.
REPRESENTATIVE_WEIGHTS = (
    (0.4, ("total_equivalent_wtw",)),
    (0.3, ("installed_power",)),
    (0.2, ("fuel_type_energy", "shore_power_energy")),
    (0.1, ("total_fuel_expenses", "levy_expenses", "regulation_expenses")),
)
_WEIGHTED = pa.array([attribute for _, attributes in REPRESENTATIVE_WEIGHTS for attribute in attributes])
_LINE = ("attribute", "node", "key1", "key2")

# the band Lantern drew around an ensemble's median, and the suffixes of the runs that carry the summary, by the
# column Lantern reads each into
BAND = (0.05, 0.95)
SUMMARY_RUNS = {"median": "median", "low": "p05", "high": "p95"}
_ROW = ("kind", "node", "attribute", "key1", "key2", "date")


def weighted_rows(table: pa.Table) -> pa.Table:
    """The rows of a run that choose the representative, with plain string columns."""
    kind = table["kind"].cast(pa.string())
    attribute = table["attribute"].cast(pa.string())
    mask = pc.and_(pc.equal(kind, "global"), pc.is_in(attribute, value_set=_WEIGHTED))
    selected = table.filter(mask).select(["sample", *_LINE, "date", "value"])
    return pa.table({name: column.cast(pa.string()) if pa.types.is_dictionary(column.type) else column
                     for name, column in zip(selected.column_names, selected.columns)})


def representatives(weighted: pa.Table, run_ids: list[str], runs: dict[str, str]) -> dict[str, str]:
    """The representative run id of each scenario combination, in the order of the runs.

    At every date, each weighted series line ranks the samples of a combination as percentiles from 0 to 100,
    which makes series of any unit comparable. A sample's distance in a line is its mean squared distance from
    the 50th percentile over the dates; a weight group averages its lines, and the score is the weighted mean
    over the groups the sample has. The lowest score wins, a tie going to the first sample by name. Without
    weighted series, the first sample (all parameters at their default) represents its combination.
    """
    group = {attribute: index for index, (_, attributes) in enumerate(REPRESENTATIVE_WEIGHTS)
             for attribute in attributes}
    weight = {index: value for index, (value, _) in enumerate(REPRESENTATIVE_WEIGHTS)}
    labels = pl.DataFrame({"sample": list(runs), "run": list(runs.values())})

    frame = pl.from_arrow(weighted).join(labels, on="sample")
    point = ["run", *_LINE, "date"]
    count = pl.col("value").count().over(point)
    rank = pl.col("value").rank("average").over(point)
    percentile = pl.when(count > 1).then((rank - 1) / (count - 1) * 100).otherwise(50.0)
    scores = (
        frame.with_columns(squared=(percentile - 50.0) ** 2,
                           group=pl.col("attribute").replace_strict(group, return_dtype=pl.Int64))
        .group_by("run", "group", *_LINE, "sample").agg(pl.col("squared").mean())
        .group_by("run", "group", "sample").agg(distance=pl.col("squared").mean())
        .with_columns(weight=pl.col("group").replace_strict(weight, return_dtype=pl.Float64))
        .group_by("run", "sample").agg(score=(pl.col("distance") * pl.col("weight")).sum() / pl.col("weight").sum())
        # rounded, so samples as far from the median (33.3 and 66.7) tie despite floating point
        .with_columns(pl.col("score").round(9))
        .sort("run", "score", "sample")
    )
    best = dict(scores.group_by("run", maintain_order=True).first().select("run", "sample").iter_rows())

    chosen = {}
    for run_id in run_ids:
        label = runs[run_id]
        if label not in chosen:
            chosen[label] = best.get(label, run_id)

    for label in chosen.keys() - best.keys():
        logger.warning("No weighted series in the results of '%s'; its first sample represents it", label)

    return chosen


def summaries(files: list[Path], label: str, layout: pa.Schema) -> list[pa.Table]:
    """The median and band of a scenario combination over its samples, per row and date, each as a run.

    Every distinct row gets a number, and the samples' values fill a matrix of rows by samples, read one sample at a
    time: a row only some samples have is summarised over those. Sorting each row of the matrix gives the median
    and band with linear interpolation, as numpy's and polars' quantile do.
    """
    rows, positions, values = None, [], []
    for file in files:
        sample = pl.read_parquet(file, columns=[*_ROW, "value"])
        if rows is None:
            rows = sample.select(_ROW).unique(maintain_order=True).with_row_index("position")

        located = sample.join(rows, on=_ROW, how="left", nulls_equal=True, maintain_order="left")
        unseen = located.filter(pl.col("position").is_null()).select(_ROW).unique(maintain_order=True)
        if unseen.height:
            rows = pl.concat([rows, unseen.with_row_index("position", offset=rows.height)])
            located = sample.join(rows, on=_ROW, how="left", nulls_equal=True, maintain_order="left")

        positions.append(located.get_column("position").to_numpy())
        values.append(located.get_column("value").to_numpy())

    dtype = np.float32 if layout.field("value").type == pa.float32() else np.float64
    matrix = np.full((rows.height, len(files)), np.nan, dtype=dtype)
    for column, (position, value) in enumerate(zip(positions, values)):
        matrix[position, column] = value

    del positions, values
    # missing values sort last, so each row's own samples come first
    matrix.sort(axis=1)
    counts = np.count_nonzero(~np.isnan(matrix), axis=1)
    keys = rows.drop("position").to_arrow()
    return [as_written(keys.append_column("value", pa.array(_quantile(matrix, counts, q))), layout, run=run)
            for run, q in zip(summary_runs(label).values(), (0.5, *BAND))]


def summary_runs(label: str) -> dict[str, str]:
    """The runs that carry a scenario combination's summary, by column: median, low and high."""
    return {column: f"{label}_{suffix}" for column, suffix in SUMMARY_RUNS.items()}


def _quantile(matrix: np.ndarray, counts: np.ndarray, q: float) -> np.ndarray:
    """The q quantile of each row's first counts values, which are sorted, interpolated linearly."""
    position = q * (counts - 1)
    lower = np.floor(position).astype(np.int64)
    upper = np.minimum(lower + 1, counts - 1)
    index = np.arange(matrix.shape[0])
    low, high = matrix[index, lower], matrix[index, upper]
    return low + (high - low) * (position - lower)
