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
import logging
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


def _find_override_spans(block_content):
    """
    Locate all `if ... { ... }` override blocks in block_content.

    Returns a list of (start_if, brace_idx, end) tuples: the index of the `if` keyword,
    the index of its opening '{', and the index just past the matching '}'.
    """
    spans = []
    for m in re.finditer(r'\bif\b', block_content, flags=re.I):
        if spans and m.start() < spans[-1][2]:
            continue  # an `if` inside the previous override's body

        brace_idx = block_content.find('{', m.start())
        if brace_idx == -1:
            raise ValueError("Malformed 'if' override: missing '{'")

        spans.append((m.start(), brace_idx, _matching_brace(block_content, brace_idx, "'if' override")))
    return spans


def parse_overrides(block_content):
    """
    Extract all `if ... { ... }` override blocks from block_content.

    Returns list of override dicts:
      [{'conditions': [...], 'attrs': {...}}, ...]
    """
    overrides = []
    for start_if, brace_idx, j in _find_override_spans(block_content):
        header = block_content[start_if:brace_idx].strip()
        cond_text = header[len('if'):].strip()
        body_text = block_content[brace_idx + 1: j - 1]
        conditions = _parse_conditions(cond_text)
        attrs = _parse_kv_block(body_text)
        overrides.append({"conditions": conditions, "attrs": attrs})
    return overrides


def _base_content(block_content):
    """
    Return block_content with every `if ... { ... }` override block removed.

    Base attributes are searched only in this view, so a field set only inside an
    override is never taken as the base value.
    """
    parts = []
    i = 0
    for start_if, _, end in _find_override_spans(block_content):
        parts.append(block_content[i:start_if])
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


# -------------------------------------------------------------------------
# Existing parser implementation (unchanged except where noted)
# -------------------------------------------------------------------------
def parse_hor_file(file_path):
    """
    Parses a horizon file to extract parameters and configuration for simulations.
    """
    # Validate file exists before attempting to open
    validate_file_exists(file_path, "configuration file")

    processed_content = []

    # Read file and strip comments (full-line and trailing; '#' inside quotes is kept)
    with open(file_path, 'r') as file:
        for line in file:
            processed_content.append(_strip_comment(line))

    content = ''.join(processed_content)

    scenario_parameters = []
    parameters = []
    seen_tokens = {}  # token -> block type, to reject duplicate definitions

    # Parse parameter blocks using predefined patterns
    for block_start in param_block_pattern.finditer(content):
        param_type, header_name = block_start.groups()

        # block_content is the text inside the outermost braces of this parameter block
        block_end = _matching_brace(content, block_start.end() - 1, "parameter block")
        block_content = content[block_start.end(): block_end - 1]

        # Base attributes are read with the `if ... { ... }` override blocks removed
        # (ScenarioParameter has no overrides).
        base_content = block_content
        if param_type != "ScenarioParameter":
            try:
                base_content = _base_content(block_content)
            except ValueError as e:
                raise ValueError(f"Error parsing overrides in parameter block '{header_name}': {e}")

        # token and name are optional and default to the block header name.
        token_match = token_pattern.search(base_content)
        if token_match:
            token = token_match.group(1)
        elif _valid_token_re.match(header_name):
            token = header_name
        else:
            raise ParseError(
                f"Parameter block '{header_name}' has no 'token' field, and its name cannot be used as one. "
                "Use only letters, digits and underscores in the block name, or set token explicitly."
            )
        if token in seen_tokens:
            raise ParseError(
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

        parameter = create_parameter(param_type, name, token, active, default, block_content, base_content)
        (scenario_parameters if param_type == "ScenarioParameter" else parameters).append(parameter)

    # Extract and process the Horizon block
    horizon_match = re.search(r'Horizon\s*{([\s\S]*?)}', content)
    if not horizon_match:
        raise ValueError("Horizon block not defined.")
    horizon_content = horizon_match.group(1)

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

    # Legacy parameter lists, used to filter the parameter blocks (see below)
    scenario_param_tokens = re.findall(r'ScenarioParameter\("([^"]+)"\)', horizon_content)
    parameter_tokens = [a or b for a, b in re.findall(
        r'ContinuousParameter\("([^"]+)"\)|DiscreteParameter\("([^"]+)"\)', horizon_content)]

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

    # Every defined parameter block is part of the run; active = FALSE fixes it at its default.
    # Legacy files list parameters in the Horizon block (ScenarioParameters = [...],
    # Parameters = [...]). If either list is present the file is treated as legacy: both
    # kinds are filtered as before (a missing list means none of that kind), with a warning.
    if scenario_param_tokens or parameter_tokens:
        logger.warning(
            "Parameter lists in the Horizon block are deprecated: in the new format every defined "
            "parameter block is included. Remove the ScenarioParameters/Parameters lists, and delete or "
            "comment out blocks you do not want."
        )
        filtered_scenario_parameters = [p for p in scenario_parameters if p.token in scenario_param_tokens]
        filtered_parameters = [p for p in parameters if p.token in parameter_tokens]
    else:
        filtered_scenario_parameters = scenario_parameters
        filtered_parameters = parameters

    # Validate exclusion and inclusion rules against known scenario tokens and values
    known_values = {sp.token: sp.values for sp in filtered_scenario_parameters}
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

    return (unc_file_path, output_path, number_of_samples, filtered_scenario_parameters, filtered_parameters,
            sampling_method, plot, max_parallel_workers, random_seed, sample_only, exclusion_rules, inclusion_rules)


def _validate_continuous_variants(parameter, distribution_defaulted):
    """Check the base parameter and each `if` override variant at parse time, so problems
    show up before sampling: unknown distributions, and log distributions with bounds <= 0."""
    variants = [("", parameter)]
    for ov in parameter.overrides:
        try:
            variants.append((" in an 'if' block", parameter.shallow_copy_with_overrides(ov["attrs"])))
        except ParameterError as e:
            raise ParseError(f"Parameter '{parameter.token}' in an 'if' block: {e}")
    for where, v in variants:
        if not v.distribution.startswith("log"):
            continue
        if v.low_val <= 0 or v.high_val <= 0 or (v.mid_val is not None and v.mid_val <= 0):
            hint = (" (no distribution set, so it defaults to LOG-TRIANGULAR)"
                    if distribution_defaulted and v.distribution == DEFAULT_DISTRIBUTION else "")
            raise ParseError(
                f"Parameter '{parameter.token}'{where}: {v.distribution.upper()} needs bounds above 0"
                f"{hint}. Use positive bounds or set distribution = TRIANGULAR or UNIFORM."
            )


def create_parameter(param_type, name, token, active, default, block_content, base_content):
    """
    Creates a parameter object of the given type. Base attributes are read from base_content
    (override blocks removed); the overrides themselves are parsed from block_content.
    """
    if param_type == "ScenarioParameter":
        return ScenarioParameter(name, token, process_values(values_pattern.search(block_content).group(1)),
                                 active, default)

    attributes = {"name": name, "token": token, "active": active, "default": default}

    # optional per-parameter emissions patterns
    for key, pattern in (("emissions_low", emission_low_pattern), ("emissions_high", emission_high_pattern)):
        match = pattern.search(base_content)
        attributes[key] = match.group(1) if match else None

    try:
        attributes["overrides"] = parse_overrides(block_content)
    except Exception as e:
        raise ValueError(f"Error parsing overrides for parameter '{token}': {e}")

    if param_type == "DiscreteParameter":
        if default is None:
            raise ParseError(f"Parameter '{token}' has no default. Set default explicitly.")

        # Values are kept as strings; probabilities are floats, scaled to sum to 1.0, or uniform
        values = [value.strip().strip('"') for value in values_pattern.search(base_content).group(1).split(',')]
        probabilities_str = probabilities_pattern.search(base_content).group(1)
        probabilities = [float(prob) for prob in probabilities_str.split(',')]
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

    parameter = ContinuousParameter(
        low_val=low_val, mid_val=mid_val, high_val=high_val, distribution=distribution,
        decimals=decimals if dec_m else None, default_from_mid=default_from_mid, **attributes)
    _validate_continuous_variants(parameter, distribution_defaulted=distribution_search is None)
    return parameter


def process_values(values_string):
    """
    Processes a string of values, removing quotes and trimming whitespace.
    """
    return [value.strip().replace('"', '') for value in values_string.split(',')]
