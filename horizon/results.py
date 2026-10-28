# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Navigate results files: the per-run files a study's runs write, and the compacted files written from them.

A results file is Parquet with a table of kind, node, attribute, key1, key2, date and value, and Navigate's
metadata (schema version, units, constants, provenance) as JSON under the key navigate.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from horizon.exceptions import StudyError
from horizon.study import Study

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
METADATA_KEY = b"navigate"

_PROVENANCE = ("deck", "navigate_version", "created", "params")


def read_footers(study: Study, run_ids: list[str]) -> tuple[pa.Schema, dict, dict, dict]:
    """Read the footers of the per-run files: their common layout, merged metadata, provenance and row counts."""
    layout = None
    metadata = {"schema_version": SCHEMA_VERSION, "units": {}, "constants": {}, "plant_fuel": {}}
    provenance, rows = {}, {}
    for run_id in run_ids:
        try:
            footer = pq.read_metadata(study.results(run_id))
        except (OSError, pa.ArrowException) as error:
            raise StudyError(f"'{run_id}' is not a readable Parquet file: {error}") from error

        schema = footer.schema.to_arrow_schema()
        meta = _navigate_metadata(footer.metadata or {}, schema, run_id)
        if layout is None:
            layout = schema.remove_metadata()
        elif not schema.remove_metadata().equals(layout):
            raise StudyError(f"'{run_id}' has other columns than the runs before it")

        clash = _merge(metadata["units"], meta.get("units", {}))
        if clash:
            raise StudyError(f"'{run_id}' has other units for {', '.join(clash)}")

        for key in ("constants", "plant_fuel"):
            clash = _merge(metadata[key], meta.get(key, {}))
            if clash:
                logger.warning("'%s' has other %s for %s than the runs before it; the compacted files keep the "
                               "first", run_id, key, ", ".join(clash))

        provenance[run_id] = {key: meta[key] for key in _PROVENANCE if key in meta}
        rows[run_id] = footer.num_rows

    return layout, metadata, provenance, rows


def _navigate_metadata(footer_metadata: dict, schema: pa.Schema, run_id: str) -> dict:
    """The Navigate metadata of a single run's results file, or raise."""
    try:
        meta = json.loads(footer_metadata[METADATA_KEY])
    except (KeyError, ValueError) as error:
        raise StudyError(f"'{run_id}' is not a Navigate results file") from error

    if meta.get("schema_version") != SCHEMA_VERSION or "sample" in schema.names:
        raise StudyError(f"'{run_id}' is not a single run with results schema {SCHEMA_VERSION}")

    return meta


def _merge(into: dict, more: dict, prefix: str = "") -> list[str]:
    """Merge more into into, nested dicts key by key; returns the keys whose values disagree (into keeps its)."""
    clash = []
    for key, value in more.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            clash += _merge(into[key], value, f"{prefix}{key}.")
        elif into.setdefault(key, value) != value:
            clash.append(f"{prefix}{key}")

    return clash


def read_results(file: Path, layout: pa.Schema, **constants: str) -> pa.Table:
    """Read a per-run file with its string columns as dictionaries, adding a constant column per keyword."""
    strings = [field.name for field in layout if pa.types.is_string(field.type)]
    table = pq.read_table(file, columns=layout.names, read_dictionary=strings)
    for name, value in constants.items():
        table = table.append_column(name, constant_column(value, table.num_rows))

    return table


def as_written(table: pa.Table, layout: pa.Schema, **constants: str) -> pa.Table:
    """A table in the types of the per-run files, string columns as dictionaries, with a constant column per
    keyword."""
    columns = [table[field.name].cast(field.type, safe=False) for field in layout]
    columns = [column.dictionary_encode() if pa.types.is_string(field.type) else column
               for column, field in zip(columns, layout)]
    table = pa.table(columns, names=layout.names)
    for name, value in constants.items():
        table = table.append_column(name, constant_column(value, table.num_rows))

    return table


def constant_column(value: str, length: int) -> pa.DictionaryArray:
    """A column of length times value, as a dictionary of that one value."""
    return pa.DictionaryArray.from_arrays(pa.repeat(pa.scalar(0, pa.int32()), length), [value])


def write_results(output: Path, schema: pa.Schema, metadata: dict, tables: Iterable[pa.Table], total: int,
                  description: str) -> int:
    """Stream tables into output, replacing it only once complete; returns the rows written.

    Parameters
    ----------
    output : Path
        File to write.
    schema : pa.Schema
        Columns of the file.
    metadata : dict
        Navigate metadata of the file.
    tables : iterable of pa.Table
        Rows to write, string columns as dictionaries.
    total : int
        Number of tables, for the progress bar.
    description : str
        Label of the progress bar.
    """
    # a per-process name, so compactions that overlap never write into one file
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    output.parent.mkdir(parents=True, exist_ok=True)

    dictionary_schema = pa.schema([field.with_type(pa.dictionary(pa.int32(), field.type))
                                   if pa.types.is_string(field.type) else field for field in schema])
    keyed = [name for name in ("run", "sample") if name in schema.names]

    # Encoded as Navigate writes a run: the labels, dates, run and sample repeat a few values; the floats are
    # stored byte plane by byte plane, which zstd packs far tighter. Statistics only where a reader filtering by
    # run or sample skips the other row groups with them. Without the stored Arrow schema the dictionaries read
    # back as strings, and the metadata is added to the file by hand.
    written = 0
    try:
        with pq.ParquetWriter(temporary, dictionary_schema, compression="zstd",
                              use_dictionary=[name for name in schema.names if name != "value"],
                              column_encoding={"value": "BYTE_STREAM_SPLIT"},
                              write_statistics=keyed, store_schema=False) as writer:
            # shown only on a terminal, so the log of a queued run stays clean
            for table in tqdm(tables, total=total, desc=description, unit="run", colour="green",
                              dynamic_ncols=True, disable=None):
                writer.write_table(table)
                written += table.num_rows

            writer.add_key_value_metadata({METADATA_KEY: json.dumps(metadata).encode()})
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    os.replace(temporary, output)
    return written


def verify_rows(output: Path, written: int, expected: int) -> None:
    """Read back the footer of a written file; its rows must be all the rows of its inputs."""
    rows = pq.read_metadata(output).num_rows
    if rows != expected or written != expected:
        raise StudyError(f"{output} holds {rows} rows, but its runs have {expected}; the per-run files "
                         "are kept")


def read_ahead(read: Callable, items: Iterable) -> Iterator[pa.Table]:
    """Yield read(item) for each item in order, reading the next while the caller uses this one.

    Reading and writing both release the GIL, so they overlap; one read is in flight at a time, so memory
    stays at two runs.
    """
    with ThreadPoolExecutor(max_workers=1) as reader:
        pending = None
        for item in items:
            upcoming = reader.submit(read, item)
            if pending is not None:
                yield pending.result()
            pending = upcoming
        if pending is not None:
            yield pending.result()
