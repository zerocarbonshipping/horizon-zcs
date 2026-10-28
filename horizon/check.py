# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
Static checks of a study for `horizon check`.

The check reads the .hor file (all its validation) and cross-checks it against the .unc file and the .inc files
it includes: every %token% must have a parameter, every parameter should be used, and every include path must
resolve, for each scenario combination, to an existing file. Nothing is sampled, written or run.
"""

import os
import re

from horizon.exceptions import HorizonError, error_line
from horizon.parser import read_hor

_TOKEN_RE = re.compile(r'%([A-Za-z0-9_]+)%')
_INCLUDE_RE = re.compile(r'^\s*Include\s*"([^"]*)"')
_BUILTIN_TOKENS = {"SCENARIO"}


def run_check(hor_file):
    """
    Check a .hor file and the simulation files it names.

    Parameters
    ----------
    hor_file : str
        Path to the .hor file.

    Returns
    -------
    tuple[str, int]
        The text to print and the exit code (1 if there are errors).
    """
    try:
        hor = read_hor(hor_file)
    except HorizonError as error:
        return error_line(hor_file, error) + "\n", 1

    errors, deck_warnings = _check_deck(hor_file, hor)
    warnings = [f"{location}: warning: {message}" for location, message in hor.warnings] + deck_warnings

    verdict = "failed" if errors else "ok"
    runs = len(hor.combinations) * hor.number_of_samples
    summary = (f"{hor_file}: {verdict}: {len(hor.parameters)} sample parameters, {len(hor.scenario_parameters)} "
               f"scenario parameters, {len(hor.combinations)} scenario combinations, {runs} runs, {len(errors)} "
               f"errors, {len(warnings)} warnings")

    return "\n".join(errors + warnings + [summary]) + "\n", 1 if errors else 0


def _strip_comment(line):
    """Remove a `#` comment running to end of line, unless the `#` is inside a quoted string."""
    quote = None
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character == '#':
            return line[:index].rstrip() + '\n'
    return line


def _template_lines(path):
    """Lines of a .unc or .inc file with comments removed (line numbers are kept)."""
    with open(path, 'r', encoding='utf-8') as file:
        return [_strip_comment(line) for line in file]


def _expand_include(raw_path, combinations):
    """
    Resolve the tokens of an include path for every scenario combination the study runs.

    Returns a list of (path, combination label) and the tokens that are not scenario tokens (their value is
    only known at sampling time, so those paths cannot be checked).
    """
    tokens = set(_TOKEN_RE.findall(raw_path))
    unknown = sorted(tokens - set(combinations[0].scenario_map))
    if unknown:
        return [], unknown

    resolved = {}
    for combination in combinations:
        path = _TOKEN_RE.sub(lambda match: combination.scenario_map[match.group(1)], raw_path)
        resolved.setdefault(path, combination.label if tokens else "")
    return list(resolved.items()), []


def _check_deck(hor_file, hor):
    """Cross-check the .unc file and its includes against the parameters; returns (errors, warnings)."""
    errors = []
    warnings = []
    unc_file = os.path.relpath(hor.unc_path)
    if not hor.unc_path.is_file():
        return [f"{hor_file}: error: UncFilePath '{unc_file}' not found"], warnings

    used = {}  # token -> "file:line" of its first use
    checked_paths = set()

    def note_tokens(path, lines):
        for line_number, line in enumerate(lines, 1):
            for token in _TOKEN_RE.findall(line):
                used.setdefault(token, f"{path}:{line_number}")

    unc_lines = _template_lines(hor.unc_path)
    note_tokens(unc_file, unc_lines)
    for line_number, line in enumerate(unc_lines, 1):
        include = _INCLUDE_RE.match(line)
        if not include:
            continue

        where = f"{unc_file}:{line_number}"
        resolved, unknown = _expand_include(include.group(1), hor.combinations)
        if unknown:
            warnings.append(f"{where}: warning: include path '{include.group(1)}' uses "
                            f"{', '.join('%' + t + '%' for t in unknown)}, which is not a scenario "
                            "parameter; not checked")

        for raw_path, label in resolved:
            path = os.path.normpath(os.path.join(hor.unc_path.parent, raw_path))
            if path in checked_paths:
                continue

            checked_paths.add(path)
            if not os.path.isfile(path):
                errors.append(f"{where}: error: include '{raw_path}'{' for ' + label if label else ''}: not found")
                continue

            note_tokens(os.path.relpath(path), _template_lines(path))

    defined = {p.token for p in hor.scenario_parameters} | {p.token for p in hor.parameters}
    for token in sorted(set(used) - defined - _BUILTIN_TOKENS):
        errors.append(f"{used[token]}: error: token %{token}% has no matching parameter in {hor_file}")

    # a scenario parameter that only drives `if` overrides needs no token in the simulation files
    conditioned = {c["token"] for p in hor.parameters for ov in p.overrides for c in ov["conditions"]}
    for token in sorted(defined - set(used) - conditioned):
        warnings.append(f"{hor_file}: warning: parameter '{token}' is not used in {unc_file} "
                        "or the files it includes")

    return errors, warnings
