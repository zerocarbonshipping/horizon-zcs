# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The values of `key = value` entries as the study needs them, and the keys a block allows.

The syntax gives every value as text or a list; each function here takes an entry and returns its value as one
type, or raises a ParseError naming the entry's line.
"""

from __future__ import annotations

import math
import re

from horizon.exceptions import ParseError
from horizon.parameters import DISTRIBUTIONS
from horizon.parser.syntax import Entry

_INT_RE = re.compile(r"[+-]?\d+")
_EMISSIONS = ("MINIMUM", "MAXIMUM")


def error_at(where: str, line: int, message: str) -> ParseError:
    """A ParseError at line of the file where."""
    return ParseError(message, f"{where}:{line}")


def as_number(entry: Entry, where: str):
    """int for whole-number text, else a finite float."""
    if isinstance(entry.value, str):
        if _INT_RE.fullmatch(entry.value):
            return int(entry.value)

        try:
            value = float(entry.value)
        except ValueError:
            value = math.nan

        if math.isfinite(value):
            return value

    raise error_at(where, entry.line, f"{entry.key} must be a number, got {entry.value!r}")


def as_count(entry: Entry, where: str, minimum: int) -> int:
    """A whole number of at least minimum."""
    value = as_number(entry, where)
    if not isinstance(value, int) or value < minimum:
        raise error_at(where, entry.line, f"{entry.key} must be a whole number of at least {minimum}, got {value}")

    return value


def as_text(entry: Entry, where: str) -> str:
    """One value, not a list."""
    if not isinstance(entry.value, str):
        raise error_at(where, entry.line, f"{entry.key} takes one value, not a list")

    return entry.value


def as_texts(entry: Entry, where: str) -> list[str]:
    """A list of single values."""
    if not isinstance(entry.value, list) or not entry.value or not all(isinstance(v, str) for v in entry.value):
        raise error_at(where, entry.line, f"{entry.key} takes a list of values, e.g. [a, b]")

    return entry.value


def as_flag(entry: Entry, where: str) -> bool:
    """TRUE or FALSE, in any case."""
    value = as_text(entry, where).upper()
    if value not in ("TRUE", "FALSE"):
        raise error_at(where, entry.line, f"{entry.key} must be TRUE or FALSE, got {entry.value!r}")

    return value == "TRUE"


def normalize_distribution(name: str) -> str:
    """The canonical name of a distribution as a .hor file writes it, in any case and with _ for -
    (LOG-TRIANGULAR, log-triangular, LOG_TRIANGULAR); raises ValueError for an unknown one."""
    normalized = name.strip().lower().replace("_", "-")
    if normalized not in DISTRIBUTIONS:
        raise ValueError(f"Unknown distribution '{name}'. Use one of: {', '.join(d.upper() for d in DISTRIBUTIONS)}")

    return normalized


def as_distribution(entry: Entry, where: str) -> str:
    """A distribution name, canonical."""
    try:
        return normalize_distribution(as_text(entry, where))
    except ValueError as error:
        raise error_at(where, entry.line, str(error)) from None


def as_emissions(entry: Entry, where: str):
    """MINIMUM, MAXIMUM or a number."""
    if isinstance(entry.value, str) and entry.value.upper() in _EMISSIONS:
        return entry.value.upper()

    return as_number(entry, where)


def keyed(entries: list[Entry], allowed: tuple, what: str, where: str) -> dict[str, Entry]:
    """The entries by key; an unknown or repeated key is an error, listing the keys allowed."""
    by_key = {}
    for entry in entries:
        if entry.key in by_key:
            raise error_at(where, entry.line, f"{entry.key} is set twice in {what} (also on line "
                                              f"{by_key[entry.key].line})")

        if entry.key not in allowed:
            raise error_at(where, entry.line, f"unknown key '{entry.key}' in {what}. Valid keys: "
                                              f"{', '.join(allowed)}.")

        by_key[entry.key] = entry

    return by_key
