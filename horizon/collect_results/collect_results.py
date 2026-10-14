# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Merge the Navigate results files below a folder into one file that Lantern shows as ensembles.

Each Navigate run writes '<realization>.parquet' next to its .nav. The merged file keeps their
rows and layout and adds two columns: sample, the file's path below the folder, and run, the
ensemble it belongs to. A realization named '<scenario>_sampleNNN' joins the ensemble
'<scenario>'; any other file is a run of its own. The layout matches Lantern's
scripts/compile_results.py. Files are streamed one at a time, so memory stays at one run.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from horizon.exceptions import ResultsCollectionError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
METADATA_KEY = b"navigate"

_SAMPLE_SUFFIX = re.compile(r"_sample\d+$")
_PROVENANCE = ("deck", "navigate_version", "created")


def collect_results(source_dir: str, output_file: str | None = None) -> Path | None:
    """Merge the results files below a folder into one file.

    Parameters
    ----------
    source_dir : str
        Folder searched recursively for .parquet files.
    output_file : str or None
        File to write; default '<folder>.parquet' next to the folder.

    Returns
    -------
    Path or None
        The written file, or None when the folder holds no results files.
    """
    folder = Path(source_dir).resolve()
    output = Path(output_file).resolve() if output_file else folder.with_name(folder.name + ".parquet")
    files = [file for file in sorted(folder.rglob("*.parquet")) if file.resolve() != output]

    if not files:
        logger.info("No results files below %s; nothing to collect.", folder)
        return None

    layout = None
    units = {}
    provenance = {}
    labels = {}

    for file in files:
        sample = file.relative_to(folder).with_suffix("").as_posix()
        schema = _read_schema(file, sample)
        meta = _navigate_metadata(schema, sample)

        if layout is None:
            layout = schema.remove_metadata()
        elif not schema.remove_metadata().equals(layout):
            raise ResultsCollectionError(f"'{sample}' has other columns than the files before it")

        clash = [attribute for attribute, unit in meta.get("units", {}).items()
                 if units.setdefault(attribute, unit) != unit]
        if clash:
            raise ResultsCollectionError(f"'{sample}' has other units for {', '.join(clash)}")

        provenance[sample] = {key: meta.get(key) for key in _PROVENANCE}
        labels[file] = (_SAMPLE_SUFFIX.sub("", file.stem) if _SAMPLE_SUFFIX.search(file.stem) else sample, sample)

    metadata = {"schema_version": SCHEMA_VERSION, "units": units, "samples": provenance}
    schema = layout.append(pa.field("run", pa.string())).append(pa.field("sample", pa.string()))
    _write(output, schema.with_metadata({METADATA_KEY: json.dumps(metadata).encode()}), labels)

    runs = {run for run, _ in labels.values()}
    logger.info("Collected %d results file(s) in %d run(s) into %s", len(files), len(runs), output)

    return output


def _read_schema(file: Path, sample: str) -> pa.Schema:
    """Read a results file's schema, metadata included, from its footer.

    Parameters
    ----------
    file : Path
        Results file to read.
    sample : str
        Name of the file in error messages.
    """
    try:
        return pq.read_schema(file)
    except (OSError, pa.ArrowException) as error:
        raise ResultsCollectionError(f"'{sample}' is not a readable Parquet file: {error}") from error


def _navigate_metadata(schema: pa.Schema, sample: str) -> dict:
    """Return the Navigate metadata of a single run's results file, or raise.

    Parameters
    ----------
    schema : pa.Schema
        Schema of the results file, metadata included.
    sample : str
        Name of the file in error messages.
    """
    try:
        meta = json.loads((schema.metadata or {})[METADATA_KEY])
    except (KeyError, ValueError) as error:
        raise ResultsCollectionError(f"'{sample}' is not a Navigate results file") from error

    if meta.get("schema_version") != SCHEMA_VERSION or "sample" in schema.names:
        raise ResultsCollectionError(f"'{sample}' is not a single run with results schema {SCHEMA_VERSION}")

    return meta


def _write(output: Path, schema: pa.Schema, labels: dict) -> None:
    """Stream the results files into ``output``, replacing it only once complete.

    Parameters
    ----------
    output : Path
        File to write.
    schema : pa.Schema
        Schema of the merged file, metadata included.
    labels : dict
        Run and sample name of each results file.
    """
    # a per-process name, so collectors that overlap never write into one file
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            for file, (run, sample) in labels.items():
                table = pq.read_table(file).select(schema.names[:-2])
                table = table.append_column("run", pa.repeat(run, table.num_rows))
                writer.write_table(table.append_column("sample", pa.repeat(sample, table.num_rows)))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    os.replace(temporary, output)
