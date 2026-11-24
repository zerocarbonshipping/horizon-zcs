"""
The values of `key = value` entries as the study needs them, and the keys a block allows.

The syntax gives every value as text or a list; each function here takes an entry and returns its value as one
type, or raises a ParseError naming the entry's line.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

from horizon.exceptions import ParseError
from horizon.parameters import DISTRIBUTIONS

if TYPE_CHECKING:
    from horizon.parameters import Number, Value
    from horizon.parser.syntax import Entry

_INT_RE = re.compile(r"[+-]?\d+")
_EMISSIONS = ("MINIMUM", "MAXIMUM")


def error_at(where: str, line: int, message: str) -> ParseError:
    """Make a ParseError at line of the file where."""
    return ParseError(message, f"{where}:{line}")


def as_number(entry: Entry, where: str) -> Number:
    """Read an int from whole-number text, else a finite float."""
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
    """Read a whole number of at least minimum."""
    value = as_number(entry, where)
    if not isinstance(value, int) or value < minimum:
        raise error_at(where, entry.line, f"{entry.key} must be a whole number of at least {minimum}, got {value}")

    return value


def as_text(entry: Entry, where: str) -> str:
    """Read one value, not a list."""
    if not isinstance(entry.value, str):
        raise error_at(where, entry.line, f"{entry.key} takes one value, not a list")

    return entry.value


def as_texts(entry: Entry, where: str) -> list[str]:
    """Read a list of single values."""
    texts = [value for value in entry.value if isinstance(value, str)] if isinstance(entry.value, list) else []
    if not texts or len(texts) != len(entry.value):
        raise error_at(where, entry.line, f"{entry.key} takes a list of values, e.g. [a, b]")

    return texts


def as_flag(entry: Entry, where: str) -> bool:
    """Read TRUE or FALSE, in any case."""
    value = as_text(entry, where).upper()
    if value not in ("TRUE", "FALSE"):
        raise error_at(where, entry.line, f"{entry.key} must be TRUE or FALSE, got {entry.value!r}")

    return value == "TRUE"


def normalize_distribution(name: str) -> str:
    """
    Return the canonical name of a distribution as a .hor file writes it.

    In any case and with _ for - (LOG-TRIANGULAR, log-triangular, LOG_TRIANGULAR); raises ValueError for an
    unknown one.
    """
    normalized = name.strip().lower().replace("_", "-")
    if normalized not in DISTRIBUTIONS:
        raise ValueError(f"Unknown distribution '{name}'. Use one of: {', '.join(d.upper() for d in DISTRIBUTIONS)}")

    return normalized


def as_distribution(entry: Entry, where: str) -> str:
    """Read a distribution name, canonical."""
    try:
        return normalize_distribution(as_text(entry, where))
    except ValueError as error:
        raise error_at(where, entry.line, str(error)) from None


def as_emissions(entry: Entry, where: str) -> Value:
    """Read MINIMUM, MAXIMUM or a number."""
    if isinstance(entry.value, str) and entry.value.upper() in _EMISSIONS:
        return entry.value.upper()

    return as_number(entry, where)


def keyed(entries: list[Entry], allowed: tuple[str, ...], what: str, where: str) -> dict[str, Entry]:
    """Key the entries by key; an unknown or repeated key is an error, listing the keys allowed."""
    by_key: dict[str, Entry] = {}
    for entry in entries:
        if entry.key in by_key:
            raise error_at(where, entry.line, f"{entry.key} is set twice in {what} (also on line {by_key[entry.key].line})")

        if entry.key not in allowed:
            raise error_at(where, entry.line, f"unknown key '{entry.key}' in {what}. Valid keys: {', '.join(allowed)}.")

        by_key[entry.key] = entry

    return by_key
