# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Collect the results of a study's runs into two files in the study folder, then delete the per-run files.

<name>_archive.parquet is the archive: every row of every run, with the columns run (the scenario combination)
and sample (the run id). <name>.parquet is for Lantern, which opens a file with a run column and one value per
row and date in each run. It holds four runs per scenario combination (lantern.py): <label>, the representative
sample, and <label>_median, <label>_p05 and <label>_p95. The folder of the per-run files goes only once both
files are written and read back.
"""

from __future__ import annotations

import logging
import shutil

import pyarrow as pa

from horizon import __version__
from horizon.exceptions import StudyError
from horizon.lantern import BAND, SUMMARY_RUNS, representatives, summaries, weighted_rows
from horizon.results import read_ahead, read_footers, read_results, verify_rows, write_results
from horizon.study import Study

logger = logging.getLogger(__name__)


def collect(study: Study, partial: bool = False) -> dict[str, str]:
    """Write the archive and the Lantern file of a study, then delete the per-run results.

    Parameters
    ----------
    study : Study
        The study.
    partial : bool
        Compact the runs that have results so far and keep the per-run files.

    Returns
    -------
    dict
        The representative run id of each scenario combination.
    """
    runs = {row["run_id"]: row["run"] for row in study.rows}
    available = [run_id for run_id in runs if study.results(run_id).exists()]
    missing = len(runs) - len(available)
    if missing and not partial:
        raise StudyError(f"{missing} of {len(runs)} runs have no results yet; start them again, or "
                         "compact what there is with `horizon collect --partial`")

    if not available:
        raise StudyError(f"no run of {study.folder} has results yet")

    layout, metadata, provenance, rows = read_footers(study, available)
    base = {**metadata, "study": study.name, "horizon_version": __version__}

    weighted = []
    archive_schema = layout.append(pa.field("run", pa.string())).append(pa.field("sample", pa.string()))
    archive_metadata = {**base, "samples": provenance}

    def read_sample(run_id):
        table = read_results(study.results(run_id), layout, run=runs[run_id], sample=run_id)
        weighted.append(weighted_rows(table))
        return table

    tables = read_ahead(read_sample, available)
    written = write_results(study.archive, archive_schema, archive_metadata, tables, len(available), "Compacting")
    verify_rows(study.archive, written, sum(rows.values()))

    chosen = representatives(pa.concat_tables(weighted), available, runs)
    lantern_schema = layout.append(pa.field("run", pa.string()))
    lantern_metadata = {**base, "representatives": chosen, "band": list(BAND),
                        "samples": {run_id: provenance[run_id] for run_id in chosen.values()}}
    expected = [sum(rows[run_id] for run_id in chosen.values())]

    def lantern_tables():
        for label, run_id in chosen.items():
            yield read_results(study.results(run_id), layout, run=label)
            files = [study.results(other) for other in available if runs[other] == label]
            for table in summaries(files, label, layout):
                expected.append(table.num_rows)
                yield table

    written = write_results(study.lantern, lantern_schema, lantern_metadata, lantern_tables(),
                            len(chosen) * (1 + len(SUMMARY_RUNS)), "Summarising")
    verify_rows(study.lantern, written, sum(expected))

    logger.info("Compacted %d run(s) into %s and their summary (representative, median, %g%% and %g%%) into %s",
                len(available), study.archive, BAND[0] * 100, BAND[1] * 100, study.lantern)

    if not partial:
        shutil.rmtree(study.results_folder)

    return chosen
