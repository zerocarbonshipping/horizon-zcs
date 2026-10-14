# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
Static checks of a study for `horizon --check` and `--explain`.

--check parses the .hor file (all parser validation) and cross-checks it against the .unc template
and the .inc files the template includes: every %token% must have a parameter, every parameter should
be used, and every scenario value in an include path such as `policy_%policy%.inc` must resolve to an
existing file with exactly that spelling. --explain prints each parameter as it resolves per scenario
value. Nothing is sampled, written or queued.
"""

import itertools
import logging
import os
import re

from horizon.exceptions import HorizonError, error_line
from horizon.parameters.parameter import ContinuousParameter
from horizon.parameters.sampler import best_override, ensure_mid_val
from horizon.parser.parser import _strip_comment, override_label, parse_hor_file

_TOKEN_RE = re.compile(r'%([A-Za-z0-9_]+)%')
_INCLUDE_RE = re.compile(r'^\s*Include\s*"([^"]*)"')
_BUILTIN_TOKENS = {"SCENARIO"}


class _WarningCollector(logging.Handler):
    """Collects the warnings the parser logs, so they can be listed in the summary."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def run_check(hor_file, check=True, explain=False):
    """
    Check and/or explain a .hor file.

    Parameters
    ----------
    hor_file : str
        Path to the .hor file.
    check : bool
        Run the cross-file checks and print the summary.
    explain : bool
        Append the resolved parameter table.

    Returns
    -------
    tuple[str, int]
        The text to print and the exit code (1 if there are errors).
    """
    horizon_logger = logging.getLogger("horizon")
    collector = _WarningCollector()
    saved = (horizon_logger.level, horizon_logger.propagate)
    horizon_logger.addHandler(collector)
    horizon_logger.setLevel(logging.WARNING)
    horizon_logger.propagate = False
    try:
        model = parse_hor_file(hor_file)
    except (HorizonError, ValueError) as error:
        return error_line(hor_file, error) + "\n", 1
    finally:
        horizon_logger.removeHandler(collector)
        horizon_logger.setLevel(saved[0])
        horizon_logger.propagate = saved[1]

    unc_file_path, scenario_parameters, parameters = model[0], model[3], model[4]
    errors = []
    warnings = [f"{hor_file}: warning: {message}" for message in collector.messages]
    if check:
        errors, cross_warnings = _check_template(hor_file, unc_file_path, scenario_parameters, parameters)
        warnings += cross_warnings

    lines = errors + warnings
    if explain:
        lines.append(_explain(scenario_parameters, parameters))

    if check:
        verdict = "failed" if errors else "ok"
        lines.append(f"{hor_file}: {verdict}: {len(parameters)} sample parameters, {len(scenario_parameters)} "
                     f"scenario parameters, {len(errors)} errors, {len(warnings)} warnings")

    return "\n".join(lines) + "\n", 1 if errors else 0


def _scenario_values(scenario_parameters):
    """Scenario token -> the values it takes in the study (an inactive parameter keeps its default)."""
    return {sp.token: sp.values if sp.active else [sp.default.strip('"').strip("'")] if sp.default else []
            for sp in scenario_parameters}


def _template_lines(path):
    """Lines of a .unc or .inc file with comments removed (line numbers are kept)."""
    with open(path, 'r', encoding='utf-8') as file:
        return [_strip_comment(line) for line in file]


def _spelling_problem(path):
    """None if path exists spelled exactly like this on disk, else why not (also on case-insensitive disks)."""
    parent, name = os.path.split(os.path.abspath(path))
    if not name:
        return None  # the root

    problem = _spelling_problem(parent)
    if problem:
        return problem

    entries = os.listdir(parent) if os.path.isdir(parent) else []
    if name in entries:
        return None

    near = [entry for entry in entries if entry.lower() == name.lower()]
    return f"case differs from '{near[0]}'" if near else "not found"


def _expand_include(raw_path, scenario_values):
    """
    Resolve the tokens of an include path with every combination of scenario values.

    Returns a list of (path, scenario combination label) and the tokens that are not scenario tokens
    (their value is only known at sampling time, so those paths cannot be checked).
    """
    tokens = list(dict.fromkeys(_TOKEN_RE.findall(raw_path)))
    unknown = [token for token in tokens if token not in scenario_values]
    if unknown:
        return [], unknown

    resolved = []
    for values in itertools.product(*(scenario_values[token] for token in tokens)):
        chosen = dict(zip(tokens, values))
        label = ", ".join(f"{token} = '{value}'" for token, value in chosen.items())
        resolved.append((_TOKEN_RE.sub(lambda match: str(chosen[match.group(1)]), raw_path), label))
    return resolved, []


def _check_template(hor_file, unc_file_path, scenario_parameters, parameters):
    """Cross-check the .unc template and its includes against the parameters; returns (errors, warnings)."""
    errors = []
    warnings = []
    if not os.path.isfile(unc_file_path):
        return [f"{hor_file}: error: UncFilePath '{unc_file_path}' not found"], warnings

    scenario_values = _scenario_values(scenario_parameters)
    used = {}  # token -> "file:line" of its first use
    unc_dir = os.path.dirname(os.path.abspath(unc_file_path))
    checked_paths = set()

    def note_tokens(path, lines):
        for line_number, line in enumerate(lines, 1):
            for token in _TOKEN_RE.findall(line):
                used.setdefault(token, f"{path}:{line_number}")

    unc_lines = _template_lines(unc_file_path)
    note_tokens(unc_file_path, unc_lines)
    for line_number, line in enumerate(unc_lines, 1):
        include = _INCLUDE_RE.match(line)
        if not include:
            continue

        where = f"{unc_file_path}:{line_number}"
        resolved, unknown = _expand_include(include.group(1), scenario_values)
        if unknown:
            warnings.append(f"{where}: warning: include path '{include.group(1)}' uses "
                            f"{', '.join('%' + t + '%' for t in unknown)}, which is not a scenario "
                            "parameter; not checked")

        for raw_path, label in resolved:
            path = os.path.normpath(os.path.join(unc_dir, raw_path))
            if path in checked_paths:
                continue

            checked_paths.add(path)
            problem = _spelling_problem(path)
            if problem:
                errors.append(f"{where}: error: include '{raw_path}'"
                              f"{' for ' + label if label else ''}: {problem}")
                continue

            note_tokens(os.path.relpath(path), _template_lines(path))

    defined = {p.token for p in scenario_parameters} | {p.token for p in parameters}
    for token in sorted(set(used) - defined - _BUILTIN_TOKENS):
        errors.append(f"{used[token]}: error: token %{token}% has no matching parameter in {hor_file}")

    for token in sorted(defined - set(used)):
        warnings.append(f"{hor_file}: warning: parameter '{token}' is not used in {unc_file_path} "
                        "or the files it includes")

    return errors, warnings


def _number(value):
    """Compact text of a number."""
    return f"{value:.10g}" if isinstance(value, float) else str(value)


def _table(rows):
    """Left-aligned plain text table; the first row is the header."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows)


def _explain_row(parameter, scenario_map):
    """One table row for the parameter as it resolves for scenario_map."""
    override = best_override(parameter, scenario_map)
    attrs = override["attrs"] if override else {}
    resolved = parameter.shallow_copy_with_overrides(attrs)
    origin = "base"
    if override:
        origin = f"if {override_label(override['conditions'])} ({', '.join(attrs) or 'no change'})"

    notes = [] if parameter.active else ["inactive, fixed at default"]
    scenario = ", ".join(f"{token}={value}" for token, value in scenario_map.items()) or "*"

    if not isinstance(resolved, ContinuousParameter):
        probabilities = "uniform" if len(set(resolved.probabilities)) == 1 else "p=" + ", ".join(
            _number(p) for p in resolved.probabilities)
        notes.append(f"values {', '.join(str(v) for v in resolved.values)}; {probabilities}")
        return [parameter.token, scenario, "discrete", "-", "-", "-", str(resolved.default), "-",
                "; ".join([origin] + notes)]

    mode = "-"
    if "triangular" in resolved.distribution:
        if resolved.mid_val is None:
            ensure_mid_val(resolved)
            notes.append("mode inferred")
        mode = _number(resolved.mid_val)

    if resolved.default_from_mid and "default" not in attrs:
        notes.append("default from mid_val")

    if resolved.decimals_auto and "decimals" not in attrs:
        notes.append("decimals auto")

    return [parameter.token, scenario, resolved.distribution, _number(resolved.low_val), mode,
            _number(resolved.high_val), str(resolved.default), str(resolved.decimals),
            "; ".join([origin] + notes)]


def _explain(scenario_parameters, parameters):
    """Resolved view: the scenario parameters, then one row per sample parameter and scenario value."""
    values = _scenario_values(scenario_parameters)
    lines = [f"scenario {sp.token}: {', '.join(values[sp.token])}"
             f"{'' if sp.active else ' (inactive, fixed at default)'}" for sp in scenario_parameters]

    rows = [["parameter", "scenario", "dist", "low", "mode", "high", "default", "dec", "from"]]
    for parameter in parameters:
        tokens = sorted({c["token"] for ov in parameter.overrides for c in ov["conditions"]} & set(values))
        for combination in itertools.product(*(values[token] for token in tokens)):
            rows.append(_explain_row(parameter, dict(zip(tokens, combination))))

    return "\n".join(lines + [_table(rows)])
