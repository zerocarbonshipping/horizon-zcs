# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Merge the Navigate results files below a folder into one file that Lantern shows as ensembles.

Each Navigate run writes '<realization>.parquet' next to its .nav. The merged file keeps their
rows and layout and adds two columns: sample, the file's path below the folder, and run, the
ensemble it belongs to. A realization named '<scenario>_sampleNNN' joins the ensemble
'<scenario>'; any other file is a run of its own. The layout matches Lantern's
scripts/compile_results.py. Files are streamed in order, the next read while one is written, so
memory stays at two runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from horizon.exceptions import ResultsCollectionError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3
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

    # encoded as Navigate writes a run: the labels, dates, run and sample repeat a few values;
    # the floats are stored byte plane by byte plane, which zstd packs far tighter
    repeated = [name for name in schema.names if name != "value"]
    columns = schema.names[:-2]

    # the string columns pass through as dictionaries, so they are neither decoded on reading
    # nor hashed again on writing; without the stored Arrow schema they read back as strings
    strings = [field.name for field in schema if pa.types.is_string(field.type)]
    dictionary_schema = pa.schema([field.with_type(pa.dictionary(pa.int32(), field.type))
                                   if field.name in strings else field for field in schema])

    try:
        # statistics only where a reader filtering by run or sample skips the other row groups with
        # them; on the other columns they cost the writer a comparison of every value
        with pq.ParquetWriter(temporary, dictionary_schema, compression="zstd", use_dictionary=repeated,
                              column_encoding={"value": "BYTE_STREAM_SPLIT"}, store_schema=False,
                              write_statistics=["run", "sample"]) as writer:
            tables = _read_ahead(partial(_read, columns=columns, strings=strings), labels.items())
            # shown only on a terminal, so the log of a queued collector stays clean
            for table in tqdm(tables, total=len(labels), desc="Collecting results", unit="file",
                              colour="green", dynamic_ncols=True, disable=None):
                writer.write_table(table)

            # not stored with the schema, so added to the file's metadata by hand
            writer.add_key_value_metadata(schema.metadata)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    os.replace(temporary, output)


def _read_ahead(read, items):
    """Yield ``read(*item)`` for each item in order, reading the next while the caller uses this one.

    Reading and writing both release the GIL, so they overlap; one read is in flight at a time,
    so memory stays at two results.

    Parameters
    ----------
    read : callable
        Function to read one item.
    items : iterable of tuple
        Arguments of each read.
    """
    with ThreadPoolExecutor(max_workers=1) as reader:
        pending = None
        for item in items:
            upcoming = reader.submit(read, *item)
            if pending is not None:
                yield pending.result()
            pending = upcoming
        if pending is not None:
            yield pending.result()


def _read(file: Path, label: tuple, columns: list, strings: list) -> pa.Table:
    """Read a results file as written to the merged file, with its run and sample columns.

    Parameters
    ----------
    file : Path
        Results file to read.
    label : tuple
        Run and sample name of the file.
    columns : list
        Columns to read.
    strings : list
        Columns to read as dictionaries.
    """
    run, sample = label
    table = pq.read_table(file, columns=columns, read_dictionary=[name for name in columns if name in strings])
    table = table.append_column("run", _constant(run, table.num_rows))
    return table.append_column("sample", _constant(sample, table.num_rows))


def _constant(value: str, length: int) -> pa.DictionaryArray:
    """Return a column of ``length`` times ``value``, as a dictionary of that one value.

    Parameters
    ----------
    value : str
        Value of every row.
    length : int
        Number of rows.
    """
    return pa.DictionaryArray.from_arrays(pa.repeat(pa.scalar(0, pa.int32()), length), [value])
