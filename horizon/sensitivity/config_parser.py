# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Parse ``.sen`` configuration files for sensitivity analysis.

File format::

    # Sensitivity analysis configuration
    SensitivityAnalysis {
        SamplesCSV = "1_samples/samples.csv"
        ReportCSV = "report.csv"          # optional
        SourceDir = "."                    # ensemble directory
        OutputDir = "prcc_analysis"       # optional

        Metric "Emissions reduction" {
            key = "TotalEquivalentWTW"
            aggregation = difference
        }

        Metric "Lifetime emissions" {
            key = "TotalEquivalentWTW"
            aggregation = cumulative
        }

        Metric "Final year emissions" {
            key = "TotalEquivalentWTW"
            year = 2050
        }
    }
"""

import logging
import os
import re
from dataclasses import dataclass, field

from horizon.exceptions import ParseError, ValidationError
from horizon.sensitivity.analyze import (
    _AGGREGATIONS,
    MetricSpec,
    _metric_display_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public data structure
# ---------------------------------------------------------------------------


@dataclass
class SensitivityConfig:
    """Parsed contents of a ``.sen`` configuration file."""
    samples_csv: str
    source_dir: str
    report_csv: str | None = None
    output_dir: str | None = None
    metrics: list[MetricSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"#.*$", re.MULTILINE)
_TOP_BLOCK_RE = re.compile(
    r"SensitivityAnalysis\s*\{", re.IGNORECASE,
)
_METRIC_BLOCK_RE = re.compile(
    r'Metric\s+"([^"]+)"\s*\{', re.IGNORECASE,
)
_KV_RE = re.compile(
    r'(\w+)\s*=\s*(".*?"|\S+)',
)


# ---------------------------------------------------------------------------
# Brace-depth block extraction (mirrors horizon/parser/parser.py)
# ---------------------------------------------------------------------------

def _extract_block(text, start_of_brace):
    """Return the body between matched ``{`` … ``}`` starting at *start_of_brace*."""
    depth = 0
    for i in range(start_of_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start_of_brace + 1:i]
    raise ParseError("Unmatched '{' in .sen file.")


def _strip_quotes(val):
    """Remove surrounding quotes from a string value."""
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    return val


# ---------------------------------------------------------------------------
# Metric block parsing
# ---------------------------------------------------------------------------

def _parse_metric_block(label, body):
    """Parse a single ``Metric "label" { … }`` block into a MetricSpec."""
    attrs = {}
    for m in _KV_RE.finditer(body):
        attrs[m.group(1).lower()] = _strip_quotes(m.group(2))

    key = attrs.get("key")
    if not key:
        raise ParseError(
            f"Metric block '{label}' is missing required 'key' attribute."
        )

    aggregation = attrs.get("aggregation", "point").lower()
    if aggregation not in _AGGREGATIONS:
        raise ValidationError(
            f"Metric block '{label}': invalid aggregation '{aggregation}'. "
            f"Must be one of {sorted(_AGGREGATIONS)}."
        )

    year = None
    if "year" in attrs:
        try:
            year = int(attrs["year"])
        except ValueError:
            raise ValidationError(
                f"Metric block '{label}': 'year' must be an integer, "
                f"got '{attrs['year']}'."
            )

    # difference/cumulative don't use year
    if aggregation in ("difference", "cumulative"):
        year = None

    display = label or _metric_display_name(key, year, aggregation)
    return MetricSpec(
        metric_key=key, year=year,
        display_name=display, aggregation=aggregation,
    )


# ---------------------------------------------------------------------------
# Top-level parsing
# ---------------------------------------------------------------------------

def _parse_top_level_kv(body):
    """Extract key-value pairs from the SensitivityAnalysis block body.

    Skips over nested ``Metric`` blocks so their contents don't pollute
    the top-level values.
    """
    # Remove nested Metric blocks first
    cleaned = body
    for m in _METRIC_BLOCK_RE.finditer(body):
        brace_pos = body.index("{", m.start() + len(m.group(0)) - 1)
        inner = _extract_block(body, brace_pos)
        # Remove the full "Metric … { … }" span
        full_span = body[m.start():brace_pos + len(inner) + 2]
        cleaned = cleaned.replace(full_span, "", 1)

    result = {}
    for m in _KV_RE.finditer(cleaned):
        result[m.group(1)] = _strip_quotes(m.group(2))
    return result


def parse_sensitivity_config(file_path):
    """Parse a ``.sen`` configuration file.

    Parameters
    ----------
    file_path : str
        Path to the ``.sen`` file.

    Returns
    -------
    SensitivityConfig

    Raises
    ------
    ParseError
        If required fields are missing or the file is malformed.
    ValidationError
        If metric blocks contain invalid values.
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise ParseError(f"Configuration file not found: {file_path}")

    with open(file_path, encoding="utf-8") as fh:
        raw = fh.read()

    # Strip comments
    text = _COMMENT_RE.sub("", raw)

    # Find the SensitivityAnalysis block
    top_match = _TOP_BLOCK_RE.search(text)
    if top_match is None:
        raise ParseError(
            "No 'SensitivityAnalysis { … }' block found in " + file_path
        )
    brace_start = text.index("{", top_match.start())
    body = _extract_block(text, brace_start)

    # Top-level key-value pairs
    kv = _parse_top_level_kv(body)

    base_dir = os.path.dirname(file_path)

    def _resolve(path):
        if path and not os.path.isabs(path):
            return os.path.normpath(os.path.join(base_dir, path))
        return path

    samples_csv = kv.get("SamplesCSV")
    if not samples_csv:
        raise ParseError("Missing required 'SamplesCSV' in .sen file.")

    source_dir = kv.get("SourceDir")
    if not source_dir:
        # Default to the directory containing the .sen file
        source_dir = base_dir

    report_csv = kv.get("ReportCSV")
    output_dir = kv.get("OutputDir")

    # Parse Metric blocks
    metrics = []
    for m in _METRIC_BLOCK_RE.finditer(body):
        label = m.group(1)
        brace_pos = body.index("{", m.start() + len(m.group(0)) - 1)
        metric_body = _extract_block(body, brace_pos)
        metrics.append(_parse_metric_block(label, metric_body))

    config = SensitivityConfig(
        samples_csv=_resolve(samples_csv),
        source_dir=_resolve(source_dir),
        report_csv=_resolve(report_csv) if report_csv else None,
        output_dir=_resolve(output_dir) if output_dir else None,
        metrics=metrics,
    )

    logger.info(
        "Parsed .sen config: source_dir=%s, %d metric(s) defined.",
        config.source_dir, len(config.metrics),
    )
    return config
