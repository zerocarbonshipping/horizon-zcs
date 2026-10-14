# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# File: horizon/parser/parser.py
"""
Parser utilities for .hor files.

This file contains:
 - parse_hor_file() and create_parameter() (existing functionality)
 - Added helpers for parsing `if ... { ... }` override blocks.
 - The parser attaches parsed overrides (list) to ContinuousParameter and DiscreteParameter
   via the 'overrides' keyword argument when constructing parameter objects.
"""
import difflib
import logging
import math
import re

import numpy as np

from horizon.exceptions import ParameterError, ParseError, ValidationError
from horizon.parameters.parameter import (
    DEFAULT_DISTRIBUTION,
    ContinuousParameter,
    DiscreteParameter,
    ScenarioParameter,
    auto_decimals,
    normalize_distribution,
    round_value,
)
from horizon.parser.exclusions import parse_all_exclusions, parse_all_inclusions
from horizon.parser.parser_patterns import (
    active_pattern,
    decimals_pattern,
    default_pattern,
    distribution_pattern,
    emission_high_pattern,
    emission_low_pattern,
    high_val_pattern,
    low_val_pattern,
    max_parallel_workers_pattern,
    mid_val_pattern,
    name_pattern,
    param_block_pattern,
    plot_pattern,
    probabilities_pattern,
    random_seed_pattern,
    sample_only_pattern,
    token_pattern,
    values_pattern,
)
from horizon.validation import validate_bounds_order, validate_file_exists

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Helpers for override parsing (if ... { ... } blocks)
# -------------------------------------------------------------------------
_num_float_re = re.compile(r'^-?\d+\.\d+$')
_num_int_re = re.compile(r'^-?\d+$')


_valid_token_re = re.compile(r'^[A-Za-z0-9_]+$')


def _coerce_literal(s):
    """Coerce a literal string into int/float/bool/string as appropriate."""
    s = s.strip()
    if s.upper() == "TRUE":
        return True
    if s.upper() == "FALSE":
        return False
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if _num_float_re.match(s):
        return float(s)
    if _num_int_re.match(s):
        return int(s)
    return s  # bare identifier -> string


def _parse_conditions(cond_text):
    """
    Parse condition text like:
      bio = low_bio and electrification in (low_el, high_el)
    Returns a list of condition dicts:
      [{"token": "bio", "op": "eq", "value": "low_bio"}, ...]
    """
    conditions = []
    for part in re.split(r'\band\b', cond_text, flags=re.I):
        p = part.strip()
        if not p:
            continue
        # membership: token in (a, b)
        m_in = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s+in\s*\(\s*([^)]+)\s*\)\s*$', p, flags=re.I)
        if m_in:
            token = m_in.group(1)
            vals = [v.strip().strip('"').strip("'") for v in m_in.group(2).split(',')]
            conditions.append({"token": token, "op": "in", "values": vals})
            continue
        # equality: token = value
        m_eq = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(".*?"|\'.*?\'|[^\s,]+)\s*$', p)
        if m_eq:
            token = m_eq.group(1)
            raw = m_eq.group(2).strip()
            val = raw.strip('"').strip("'") if (raw.startswith('"') or raw.startswith("'")) else raw
            conditions.append({"token": token, "op": "eq", "value": val})
            continue
        raise ValueError(f"Cannot parse condition: '{p}'")
    return conditions


# key = value, where value is a bracketed list, a quoted string, or a bare token/number/TRUE/FALSE
_kv_re = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\[[^]]*]|".*?"|\'.*?\'|[^\s,}]+)', flags=re.S)


def _parse_kv_block(body_text):
    """
    Parse key=value pairs inside an override block body; values (or the items of a
    `[v1, v2, ...]` list) are coerced via _coerce_literal().
    """
    kv = {}
    for m in _kv_re.finditer(body_text):
        key, raw = m.group(1), m.group(2).strip()
        if raw.startswith('[') and raw.endswith(']'):
            inner = raw[1:-1].strip()
            kv[key] = [_coerce_literal(item) for item in inner.split(',')] if inner else []
        else:
            kv[key] = _coerce_literal(raw)
    return kv


def _matching_brace(text, brace_idx, what):
    """Return the index just past the '}' matching the '{' at brace_idx."""
    depth = 0
    for idx in range(brace_idx, len(text)):
        if text[idx] == '{':
            depth += 1
        elif text[idx] == '}':
            depth -= 1
            if depth == 0:
                return idx + 1
    raise ValueError(f"Malformed {what}: unmatched '{{'")


# Quoted strings and bracketed lists are matched (and skipped) so that an `if` inside them is
# never seen; a real override header is `if` at the start of a line.
_override_start_re = re.compile(r'"[^"]*"|\'[^\']*\'|\[[^\]]*]|^[ \t]*(if)\b', flags=re.I | re.M)


def _find_override_spans(block_content):
    """
    Locate all `if ... { ... }` override blocks in block_content.

    Returns a list of (start_if, brace_idx, end) tuples: the index of the `if` keyword,
    the index of its opening '{', and the index just past the matching '}'.
    """
    spans = []
    for m in _override_start_re.finditer(block_content):
        if m.group(1) is None:
            continue  # a quoted string or list, skipped

        if spans and m.start(1) < spans[-1][2]:
            continue  # an `if` inside the previous override's body

        brace_idx = block_content.find('{', m.start(1))
        if brace_idx == -1:
            raise ValueError("Malformed 'if' override: missing '{'")

        spans.append((m.start(1), brace_idx, _matching_brace(block_content, brace_idx, "'if' override")))
    return spans


def parse_overrides(block_content, first_line=1):
    """
    Extract all `if ... { ... }` override blocks from block_content.

    first_line is the file line on which block_content starts; it gives each override its "line".

    Returns list of override dicts:
      [{'conditions': [...], 'attrs': {...}, 'line': n}, ...]
    """
    overrides = []
    for start_if, brace_idx, j in _find_override_spans(block_content):
        header = block_content[start_if:brace_idx].strip()
        cond_text = header[len('if'):].strip()
        body_text = block_content[brace_idx + 1: j - 1]
        conditions = _parse_conditions(cond_text)
        attrs = _parse_kv_block(body_text)
        overrides.append({"conditions": conditions, "attrs": attrs,
                          "line": first_line + block_content.count('\n', 0, start_if)})
    return overrides


def override_label(conditions):
    """Condition list as written in a .hor file, e.g. `policy in (a, b) and bio = low`."""
    return " and ".join(f"{c['token']} in ({', '.join(c['values'])})" if c["op"] == "in"
                        else f"{c['token']} = {c['value']}" for c in conditions)


def _base_content(block_content):
    """
    Return block_content with every `if ... { ... }` override block blanked out.

    Base attributes are searched only in this view, so a field set only inside an
    override is never taken as the base value. The line breaks of the blocks are kept,
    so line numbers still match the file.
    """
    parts = []
    i = 0
    for start_if, _, end in _find_override_spans(block_content):
        parts.append(block_content[i:start_if])
        parts.append('\n' * block_content.count('\n', start_if, end))
        i = end
    parts.append(block_content[i:])
    return ''.join(parts)


def _strip_comment(line):
    """Remove a `#` comment running to end of line, unless the `#` is inside a quoted string."""
    quote = None
    for idx, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '#':
            return line[:idx].rstrip() + '\n'
    return line


# Keys read per block kind. Overrides take the parameter keys except the identity ones.
_COMMON_KEYS = ("token", "name", "active", "default")
_BLOCK_KEYS = {
    "ScenarioParameter": _COMMON_KEYS + ("values",),
    "ContinuousParameter": _COMMON_KEYS + ("low_val", "mid_val", "high_val", "distribution", "decimals",
                                           "emissions_low", "emissions_high"),
    "DiscreteParameter": _COMMON_KEYS + ("values", "probabilities", "emissions_low", "emissions_high"),
}
_OVERRIDE_KEYS = {kind: tuple(key for key in keys if key != "token") for kind, keys in _BLOCK_KEYS.items()}
_HORIZON_KEYS = ("UncFilePath", "OutputPath", "NumberOfSamples", "SamplingMethod", "RandomSeed", "SampleOnly",
                 "MaxParallelWorkers", "Plot")

# `key =` outside quoted strings, bracketed lists and parenthesised calls such as Include(...)
_key_re = re.compile(r'"[^"]*"|\'[^\']*\'|\[[^\]]*]|\([^)]*\)|([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)')

# parameter lists of the old format, in the Horizon block
_legacy_list_re = re.compile(r'\b(?:Scenario)?Parameters\s*=|\b(?:Scenario|Continuous|Discrete)Parameter\s*\(')


def _error(file_path, line, message):
    """ParseError located at file_path:line (no location when file_path is empty)."""
    return ParseError(message, f"{file_path}:{line}" if file_path else None)


def _number(text):
    """int for whole-number text, else float; None if the text is not a finite number."""
    text = str(text).strip().strip('"').strip("'")
    try:
        return int(text)
    except ValueError:
        pass

    try:
        value = float(text)
    except ValueError:
        return None

    return value if math.isfinite(value) else None


def _check_keys(text, allowed, what, file_path, first_line):
    """Raise a ParseError for the first `key =` in text that is not in allowed, with a suggestion."""
    by_lower = {key.lower(): key for key in allowed}
    for match in _key_re.finditer(text):
        key = match.group(1)
        if key is None or key in allowed:
            continue

        line = first_line + text.count('\n', 0, match.start(1))
        close = difflib.get_close_matches(key.lower(), list(by_lower), n=1)
        hint = f"Did you mean '{by_lower[close[0]]}'?" if close else f"Valid keys: {', '.join(allowed)}."
        raise _error(file_path, line, f"unknown key '{key}' in {what}. {hint}")


def _check_block_keys(param_type, header_name, block_content, base_content, file_path, first_line):
    """Check the keys of a parameter block and of each of its `if` bodies against the whitelist."""
    _check_keys(base_content, _BLOCK_KEYS[param_type], f'{param_type} "{header_name}"', file_path, first_line)
    if param_type == "ScenarioParameter":
        return

    for _, brace_idx, end in _find_override_spans(block_content):
        _check_keys(block_content[brace_idx + 1: end - 1], _OVERRIDE_KEYS[param_type],
                    f'an \'if\' block of {param_type} "{header_name}"', file_path,
                    first_line + block_content.count('\n', 0, brace_idx + 1))


def _validate_conditions(parameters, scenario_parameters, file_path):
    """Every `if` condition must name a defined ScenarioParameter and only values it has."""
    values_by_token = {sp.token: sp.values for sp in scenario_parameters}
    for parameter in parameters:
        for override in parameter.overrides:
            for condition in override["conditions"]:
                token = condition["token"]
                if token == "SCENARIO":
                    continue  # label of an Include rule, not a ScenarioParameter

                wanted = condition["values"] if condition["op"] == "in" else [condition["value"]]
                where = f"Parameter '{parameter.token}', 'if {override_label(override['conditions'])}'"
                if token not in values_by_token:
                    close = difflib.get_close_matches(token, list(values_by_token), n=1)
                    hint = (f"Did you mean '{close[0]}'?" if close
                            else f"Defined: {', '.join(values_by_token) or 'none'}.")
                    raise _error(file_path, override["line"],
                                 f"{where}: '{token}' is not a ScenarioParameter. {hint}")

                for value in wanted:
                    if value not in values_by_token[token]:
                        raise _error(file_path, override["line"],
                                     f"{where}: '{value}' is not a value of ScenarioParameter '{token}'. "
                                     f"Valid values: {', '.join(values_by_token[token])}.")


def parse_hor_file(file_path):
    """
    Parses a horizon file to extract parameters and configuration for simulations.
    """
    # Validate file exists before attempting to open
    validate_file_exists(file_path, "configuration file")

    # Read file and strip comments (full-line and trailing; '#' inside quotes is kept)
    with open(file_path, 'r', encoding='utf-8') as file:
        content = ''.join(_strip_comment(line) for line in file)

    # Extract and process the Horizon block
    horizon_match = re.search(r'Horizon\s*{([\s\S]*?)}', content)
    if not horizon_match:
        raise ValueError("Horizon block not defined.")
    horizon_content = horizon_match.group(1)
    horizon_line = content.count('\n', 0, horizon_match.start(1)) + 1

    legacy_match = _legacy_list_re.search(horizon_content)
    if legacy_match:
        raise _error(file_path, horizon_line + horizon_content.count('\n', 0, legacy_match.start()),
                     "parameter lists in the Horizon block are no longer supported. Delete the list: every "
                     "parameter block is part of the run. Set `active = FALSE` to pin a parameter at its "
                     "default, or remove its block. Where the old default distribution (UNIFORM) was relied "
                     "on, set `distribution = UNIFORM` explicitly.")

    _check_keys(horizon_content, _HORIZON_KEYS, "the Horizon block", file_path, horizon_line)

    def setting(pattern):
        match = re.search(pattern, horizon_content)
        return match.group(1) if match else None

    unc_file_path = setting(r'UncFilePath\s*=\s*"([^"]+)"')
    if not unc_file_path:
        raise ValueError("UncFilePath not defined.")

    output_path = setting(r'OutputPath\s*=\s*"([^"]+)"')
    if not output_path:
        raise ValueError("OutputPath not defined in the Horizon block.")

    number_of_samples = setting(r'NumberOfSamples\s*=\s*(\d+)')
    if number_of_samples is None:
        raise ValueError("NumberOfSamples not defined.")
    number_of_samples = int(number_of_samples)

    # Optional settings; sampling defaults to latin hypercube (LHS), the seed to 69. MaxParallelWorkers
    # stays None if not specified, so explicit use can be detected.
    sampling_method = setting(r'SamplingMethod\s*=\s*(\w+)') or "LHS"
    max_parallel_workers = setting(max_parallel_workers_pattern)
    max_parallel_workers = int(max_parallel_workers) if max_parallel_workers else None
    random_seed = int(setting(random_seed_pattern) or 69)
    sample_only = setting(sample_only_pattern) == "TRUE"
    plot = setting(plot_pattern) == "TRUE"

    # Parse Exclude() and Include() directives
    exclusion_rules = parse_all_exclusions(horizon_content)
    inclusion_rules = parse_all_inclusions(horizon_content)

    scenario_parameters = []
    parameters = []
    seen_tokens = {}  # token -> block type, to reject duplicate definitions

    # Parse parameter blocks using predefined patterns
    for block_start in param_block_pattern.finditer(content):
        param_type, header_name = block_start.groups()

        # block_content is the text inside the outermost braces of this parameter block
        block_end = _matching_brace(content, block_start.end() - 1, "parameter block")
        block_content = content[block_start.end(): block_end - 1]
        first_line = content.count('\n', 0, block_start.end() - 1) + 1

        # Base attributes are read with the `if ... { ... }` override blocks removed
        base_content = block_content
        if param_type == "ScenarioParameter":
            if _find_override_spans(block_content):
                raise _error(file_path, first_line, f'ScenarioParameter "{header_name}" cannot have `if` blocks.')
        else:
            try:
                base_content = _base_content(block_content)
            except ValueError as e:
                raise ValueError(f"Error parsing overrides in parameter block '{header_name}': {e}")

        _check_block_keys(param_type, header_name, block_content, base_content, file_path, first_line)

        # token and name are optional and default to the block header name.
        token_match = token_pattern.search(base_content)
        if token_match:
            token = token_match.group(1)
        elif _valid_token_re.match(header_name):
            token = header_name
        else:
            raise _error(
                file_path, first_line,
                f"Parameter block '{header_name}' has no 'token' field, and its name cannot be used as one. "
                "Use only letters, digits and underscores in the block name, or set token explicitly."
            )
        if token in seen_tokens:
            raise _error(
                file_path, first_line,
                f"Parameter '{token}' is defined twice ({seen_tokens[token]} and {param_type}). "
                "Parameter names must be unique."
            )
        seen_tokens[token] = param_type

        name_match = name_pattern.search(base_content)
        name = name_match.group(1) if name_match else header_name
        active_match = active_pattern.search(base_content)
        active = not active_match or active_match.group(1) == "TRUE"
        default_match = default_pattern.search(base_content)
        default = default_match.group(1) if default_match else None

        try:
            parameter = create_parameter(param_type, name, token, active, default, block_content, base_content,
                                         file_path=file_path, first_line=first_line)
        except ParseError as e:
            if e.location:
                raise
            raise _error(file_path, first_line, e.message) from e

        (scenario_parameters if param_type == "ScenarioParameter" else parameters).append(parameter)

    _validate_conditions(parameters, scenario_parameters, file_path)

    # Validate exclusion and inclusion rules against known scenario tokens and values
    known_values = {sp.token: sp.values for sp in scenario_parameters}
    for kind, rules in (("Exclude", exclusion_rules), ("Include", inclusion_rules)):
        for rule in rules:
            for token, value in rule.items():
                if token == "_name":
                    continue  # reserved label key of Include rules, not a scenario token

                if token not in known_values:
                    logger.warning(
                        "%s rule references unknown scenario token '%s'. "
                        "Known tokens: %s. This rule will never match.",
                        kind, token, list(known_values)
                    )
                elif value not in known_values[token]:
                    logger.warning(
                        "%s rule references value '%s' for token '%s', "
                        "but known values are: %s. This rule may never match.",
                        kind, value, token, known_values[token]
                    )

    return (unc_file_path, output_path, number_of_samples, scenario_parameters, parameters,
            sampling_method, plot, max_parallel_workers, random_seed, sample_only, exclusion_rules, inclusion_rules)


def _validate_continuous_variants(parameter, distribution_defaulted, file_path="", first_line=1):
    """Check the base parameter and each `if` override variant at parse time, so problems
    show up before sampling: unknown distributions, log distributions with bounds <= 0, inverted
    bounds, a triangular mode or a default outside the bounds. Messages name the parameter and,
    for an override, its condition."""
    token = parameter.token
    variants = [("", parameter, first_line, False)]
    for ov in parameter.overrides:
        where = f" in an 'if' block ({override_label(ov['conditions'])})"
        attrs = ov["attrs"]
        for key in ("low_val", "mid_val", "high_val", "default"):
            if key in attrs and _number(attrs[key]) is None:
                raise _error(file_path, ov["line"], f"Parameter '{token}'{where}: {key} must be a number, "
                                                    f"got '{attrs[key]}'.")

        try:
            variants.append((where, parameter.shallow_copy_with_overrides(attrs), ov["line"], "default" in attrs))
        except ParameterError as e:
            raise _error(file_path, ov["line"], f"Parameter '{token}'{where}: {e}")

    for where, v, line, default_in_override in variants:
        if v.distribution.startswith("log"):
            if v.low_val <= 0 or v.high_val <= 0 or (v.mid_val is not None and v.mid_val <= 0):
                hint = (" (no distribution set, so it defaults to LOG-TRIANGULAR)"
                        if distribution_defaulted and v.distribution == DEFAULT_DISTRIBUTION else "")
                raise _error(
                    file_path, line,
                    f"Parameter '{token}'{where}: {v.distribution.upper()} needs bounds above 0"
                    f"{hint}. Use positive bounds or set distribution = TRIANGULAR or UNIFORM."
                )

        low, high, default = _number(v.low_val), _number(v.high_val), _number(v.default)
        mid = None if v.mid_val is None else _number(v.mid_val)
        if low > high:
            raise _error(file_path, line, f"Parameter '{token}'{where}: low_val ({low}) must be <= high_val ({high}).")

        if "triangular" in v.distribution and mid is not None and not low <= mid <= high:
            raise _error(file_path, line, f"Parameter '{token}'{where}: mid_val ({mid}) must lie "
                                          f"within [{low}, {high}].")

        if not low <= default <= high:
            hint = (" The base default still applies here; set default inside this 'if' block."
                    if where and not default_in_override and not v.default_from_mid else "")
            raise _error(file_path, line, f"Parameter '{token}'{where}: default ({default}) must lie "
                                          f"within [{low}, {high}].{hint}")


def create_parameter(param_type, name, token, active, default, block_content, base_content,
                     file_path="", first_line=1):
    """
    Creates a parameter object of the given type. Base attributes are read from base_content
    (override blocks removed); the overrides themselves are parsed from block_content, which starts
    on file line first_line (used with file_path to locate errors).
    """
    if param_type == "ScenarioParameter":
        values_match = values_pattern.search(block_content)
        if not values_match:
            raise ParseError(f"ScenarioParameter '{token}' has no values.")

        return ScenarioParameter(name, token, process_values(values_match.group(1)), active, default)

    attributes = {"name": name, "token": token, "active": active, "default": default}

    # optional per-parameter emissions patterns
    for key, pattern in (("emissions_low", emission_low_pattern), ("emissions_high", emission_high_pattern)):
        match = pattern.search(base_content)
        attributes[key] = match.group(1) if match else None

    try:
        attributes["overrides"] = parse_overrides(block_content, first_line)
    except Exception as e:
        raise ValueError(f"Error parsing overrides for parameter '{token}': {e}")

    if param_type == "DiscreteParameter":
        if default is None:
            raise ParseError(f"Parameter '{token}' has no default. Set default explicitly.")

        # Values are kept as strings; probabilities are floats, scaled to sum to 1.0, or uniform
        values_match = values_pattern.search(base_content)
        if not values_match:
            raise ParseError(f"DiscreteParameter '{token}' has no values.")

        values = [value.strip().strip('"') for value in values_match.group(1).split(',')]
        probabilities_match = probabilities_pattern.search(base_content)
        if not probabilities_match:
            probabilities = "uniform"
        else:
            probabilities = [float(prob) for prob in probabilities_match.group(1).split(',')]
            if len(probabilities) != len(values):
                raise ParseError(f"DiscreteParameter '{token}' has {len(values)} values but "
                                 f"{len(probabilities)} probabilities. Give one probability per value, "
                                 "or omit probabilities for a uniform choice.")

            total = sum(probabilities)
            if not np.isclose(total, 1.0):
                probabilities = [p / total for p in probabilities]

        return DiscreteParameter(values=values, probabilities=probabilities, **attributes)

    # distribution is a keyword (LOG-TRIANGULAR, TRIANGULAR, LOG-UNIFORM, UNIFORM);
    # quoted lowercase names still work. Default: log-triangular.
    distribution_search = distribution_pattern.search(base_content)
    try:
        distribution = normalize_distribution(
            distribution_search.group(1) if distribution_search else DEFAULT_DISTRIBUTION)
    except ParameterError as e:
        raise ParseError(f"Parameter '{token}': {e}")

    low_m = low_val_pattern.search(base_content)
    mid_m = mid_val_pattern.search(base_content)
    high_m = high_val_pattern.search(base_content)
    dec_m = decimals_pattern.search(base_content)

    if not low_m or not high_m:
        raise ValueError(f"Missing low_val or high_val for ContinuousParameter '{token}'.")

    try:
        low_val = float(low_m.group(1))
        mid_val = float(mid_m.group(1)) if mid_m else None
        high_val = float(high_m.group(1))
        # decimals is optional: without it, decimals follow the bounds (auto_decimals)
        decimals = int(dec_m.group(1)) if dec_m else auto_decimals(low_val, high_val, distribution)
    except ValueError as e:
        raise ParseError(f"Invalid numeric value in parameter '{token}': {e}")

    validate_bounds_order(low_val, high_val, token, allow_equal=False, context="parameter definition")

    if decimals < 0:
        raise ValidationError(f"Parameter '{token}': decimals must be non-negative, got {decimals}")

    # default is optional when mid_val is set: it then follows mid_val (also in overrides).
    default_from_mid = default is None
    if default_from_mid:
        if mid_val is None:
            raise ParseError(f"Parameter '{token}' has no mid_val and no default. Set default explicitly.")

        attributes["default"] = round_value(mid_val, decimals)
    elif _number(default) is None:
        raise ParseError(f"Parameter '{token}': default '{default}' is not a number.")

    parameter = ContinuousParameter(
        low_val=low_val, mid_val=mid_val, high_val=high_val, distribution=distribution,
        decimals=decimals if dec_m else None, default_from_mid=default_from_mid, **attributes)
    _validate_continuous_variants(parameter, distribution_search is None, file_path, first_line)
    return parameter


def process_values(values_string):
    """
    Processes a string of values, removing quotes and trimming whitespace.
    """
    return [value.strip().replace('"', '') for value in values_string.split(',')]
