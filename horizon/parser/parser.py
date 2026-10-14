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
    if isinstance(s, (int, float, bool)):
        return s
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


def _parse_kv_block(body_text):
    """
    Parse key=value pairs inside an override block body.

    Supports:
      - key = value
      - key = "quoted string"
      - key = [v1, v2, ...]  # list of literals

    Values are coerced via _coerce_literal().
    """
    kv = {}
    # Match key = value where value may be:
    #   - bracketed list: [ ... ]
    #   - quoted string
    #   - bare token/number/TRUE/FALSE
    kv_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\[[^]]*]|".*?"|\'.*?\'|[^\s,}]+)', flags=re.S)
    for m in kv_pattern.finditer(body_text):
        key = m.group(1)
        raw = m.group(2).strip()
        # list
        if raw.startswith('[') and raw.endswith(']'):
            inner = raw[1:-1].strip()
            if inner == '':
                kv[key] = []
            else:
                items = [item.strip() for item in inner.split(',')]
                kv[key] = [_coerce_literal(item) for item in items]
        else:
            kv[key] = _coerce_literal(raw)
    return kv


def _find_override_spans(block_content):
    """
    Locate all `if ... { ... }` override blocks in block_content.

    Returns a list of (start_if, brace_idx, end) tuples: the index of the `if` keyword,
    the index of its opening '{', and the index just past the matching '}'.
    """
    spans = []
    i = 0
    L = len(block_content)
    while True:
        m = re.search(r'\bif\b', block_content[i:], flags=re.I)
        if not m:
            break
        start_if = i + m.start()
        # find the opening brace
        brace_idx = block_content.find('{', start_if)
        if brace_idx == -1:
            raise ValueError("Malformed 'if' override: missing '{'")
        # find matching closing brace using depth counting
        j = brace_idx + 1
        depth = 1
        while j < L and depth > 0:
            if block_content[j] == '{':
                depth += 1
            elif block_content[j] == '}':
                depth -= 1
            j += 1
        if depth != 0:
            raise ValueError("Malformed 'if' override: unmatched '{'")
        spans.append((start_if, brace_idx, j))
        i = j
    return spans


def parse_overrides(block_content):
    """
    Extract all `if ... { ... }` override blocks from block_content.

    Returns list of override dicts:
      [{'conditions': [...], 'attrs': {...}, 'start': int, 'end': int}, ...]
    """
    overrides = []
    for start_if, brace_idx, j in _find_override_spans(block_content):
        header = block_content[start_if:brace_idx].strip()
        cond_text = header[len('if'):].strip()
        body_text = block_content[brace_idx + 1: j - 1]
        conditions = _parse_conditions(cond_text)
        attrs = _parse_kv_block(body_text)
        overrides.append({
            "conditions": conditions,
            "attrs": attrs,
            "start": start_if,
            "end": j
        })
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
        # Find the matching closing brace for the parameter block.
        # The regex includes the opening '{', so the opening brace index is block_start.end() - 1.
        start_brace_idx = block_start.end() - 1
        if start_brace_idx < 0 or content[start_brace_idx] != '{':
            raise ValueError(f"Malformed parameter block at position {block_start.start()}: missing '{{'")

        # Walk the content to find the matching '}' taking nesting into account.
        j = start_brace_idx + 1
        depth = 1
        L = len(content)
        while j < L and depth > 0:
            ch = content[j]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            j += 1

        if depth != 0:
            raise ValueError("Malformed parameter block: unmatched '{'")

        # block_content is the text inside the outermost braces of this parameter block
        block_content = content[block_start.end(): j - 1]
        param_type = block_start.group(1)
        # Base attributes are read with the `if ... { ... }` override blocks removed
        # (ScenarioParameter has no overrides).
        if param_type == "ScenarioParameter":
            base_content = block_content
        else:
            try:
                base_content = _base_content(block_content)
            except ValueError as e:
                raise ValueError(f"Error parsing overrides in parameter block '{block_start.group(2)}': {e}")

        # Extract basic attributes shared by all parameter types.
        # token and name are optional and default to the block header name.
        header_name = block_start.group(2)
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
                f"Parameter '{token}' is defined twice ({seen_tokens[token]} and {block_start.group(1)}). "
                "Parameter names must be unique."
            )
        seen_tokens[token] = block_start.group(1)

        name_match = name_pattern.search(base_content)
        name = name_match.group(1) if name_match else header_name
        active_str = active_pattern.search(base_content)
        active = (not active_str or active_str.group(1) == "TRUE")
        default_val = default_pattern.search(base_content)
        default = None if not default_val else default_val.group(1)

        # Create parameter objects dynamically
        parameter_object = create_parameter(param_type, name, token, active, default, block_content)
        if param_type == "ScenarioParameter":
            scenario_parameters.append(parameter_object)

        elif param_type in ["ContinuousParameter", "DiscreteParameter"]:
            parameters.append(parameter_object)

        else:
            raise ValueError(f"Parameter type not accepted: {param_type}")

    # Extract and process the Horizon block
    horizon_match = re.search(r'Horizon\s*{([\s\S]*?)}', content)
    if not horizon_match:
        raise ValueError("Horizon block not defined.")
    horizon_content = horizon_match.group(1)

    # Extract unc file path and raise an error if it is not defined
    unc_file_path_match = re.search(r'UncFilePath\s*=\s*"([^"]+)"', horizon_content)
    unc_file_path = unc_file_path_match.group(1) if unc_file_path_match else None
    if not unc_file_path:
        raise ValueError("UncFilePath not defined.")

    # Extract OutputPath
    output_path_match = re.search(r'OutputPath\s*=\s*"([^"]+)"', horizon_content)
    output_path = output_path_match.group(1) if output_path_match else None
    if not output_path:
        raise ValueError("OutputPath not defined in the Horizon block.")

    # Extract number of samples and raise an error if it is not defined
    number_of_samples_match = re.search(r'NumberOfSamples\s*=\s*(\d+)', horizon_content)
    if not number_of_samples_match:
        raise ValueError("NumberOfSamples not defined.")
    number_of_samples = int(number_of_samples_match.group(1))

    # Extract parameter tokens to filter applicable parameters
    scenario_param_tokens = re.findall(r'ScenarioParameter\("([^"]+)"\)', horizon_content)
    parameter_tokens = re.findall(r'ContinuousParameter\("([^"]+)"\)|DiscreteParameter\("([^"]+)"\)', horizon_content)
    parameter_tokens = [token for token_group in parameter_tokens for token in token_group if token]

    # Extract optional sampling method. If not specified, it defaults to latin hypercube sampling (LHS)
    sampling_method_match = re.search(r'SamplingMethod\s*=\s*(\w+)', horizon_content)
    sampling_method = sampling_method_match.group(1) if sampling_method_match else "LHS"

    # Extract MaxParallelWorkers (None if not specified, so we can detect explicit use)
    max_parallel_workers_match = max_parallel_workers_pattern.search(horizon_content)
    max_parallel_workers = int(max_parallel_workers_match.group(1)) if max_parallel_workers_match else None

    # Extract RandomSeed with a default value of 69 if not defined
    random_seed_match = random_seed_pattern.search(horizon_content)
    random_seed = int(random_seed_match.group(1)) if random_seed_match else 69

    # Extracting SampleOnly and defaulting to False if not defined
    sample_only_match = sample_only_pattern.search(horizon_content)
    sample_only = sample_only_match.group(1) == "TRUE" if sample_only_match else False

    # Extracting Plot and defaulting to False if not defined
    plot_match = plot_pattern.search(horizon_content)
    plot = plot_match.group(1) == "TRUE" if plot_match else False

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
    known_scenario_tokens = [sp.token for sp in filtered_scenario_parameters]

    # Build scenario value lookup for validation
    all_scenario_values = {}
    if exclusion_rules or inclusion_rules:
        for sp in filtered_scenario_parameters:
            all_scenario_values[sp.token] = sp.values

    # Validate exclusion rules against known scenario tokens and values
    if exclusion_rules:
        for rule in exclusion_rules:
            for token, value in rule.items():
                if token not in known_scenario_tokens:
                    logger.warning(
                        "Exclude rule references unknown scenario token '%s'. "
                        "Known tokens: %s. This rule will never match.",
                        token, known_scenario_tokens
                    )
                elif token in all_scenario_values and value not in all_scenario_values[token]:
                    logger.warning(
                        "Exclude rule references value '%s' for token '%s', "
                        "but known values are: %s. This rule may never match.",
                        value, token, all_scenario_values[token]
                    )

    # Validate inclusion rules against known scenario tokens and values
    if inclusion_rules:
        for rule in inclusion_rules:
            for token, value in rule.items():
                if token == "_name":
                    continue  # reserved label key, not a scenario token
                if token not in known_scenario_tokens:
                    logger.warning(
                        "Include rule references unknown scenario token '%s'. "
                        "Known tokens: %s. This rule will never match.",
                        token, known_scenario_tokens
                    )
                elif token in all_scenario_values and value not in all_scenario_values[token]:
                    logger.warning(
                        "Include rule references value '%s' for token '%s', "
                        "but known values are: %s. This rule may never match.",
                        value, token, all_scenario_values[token]
                    )

    return (unc_file_path, output_path, number_of_samples, filtered_scenario_parameters, filtered_parameters,
            sampling_method, plot, max_parallel_workers, random_seed, sample_only, exclusion_rules, inclusion_rules)


def create_parameter(param_type, name, token, active, default, block_content):
    """
    Creates a parameter object based on the specified parameter type and attributes.
    """
    # Initialize with generic attributes across all parameter types
    attributes = {
        "name": name,
        "token": token,
        "active": active,
        "default": default
    }

    # Base attributes are read with the `if ... { ... }` override blocks removed;
    # the overrides themselves are parsed from the full block_content.
    base_content = block_content
    if param_type in ("ContinuousParameter", "DiscreteParameter"):
        try:
            base_content = _base_content(block_content)
        except ValueError as e:
            raise ValueError(f"Error parsing overrides for parameter '{token}': {e}")

    # Extract common attributes and then specific ones for ContinuousParameter
    if param_type == "ContinuousParameter":
        # pick up any per-parameter overrides
        low_case = emission_low_pattern.search(base_content)
        high_case = emission_high_pattern.search(base_content)
        attributes["emissions_low"] = low_case.group(1) if low_case else None
        attributes["emissions_high"] = high_case.group(1) if high_case else None

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

        # Parse numeric values with error handling
        try:
            low_val = float(low_m.group(1))
            mid_val = float(mid_m.group(1)) if mid_m else None
            high_val = float(high_m.group(1))
            # decimals is optional: without it, decimals follow the bounds (auto_decimals)
            decimals = int(dec_m.group(1)) if dec_m else auto_decimals(low_val, high_val, distribution)
        except ValueError as e:
            raise ParseError(f"Invalid numeric value in parameter '{token}': {e}")

        # Validate parameter constraints
        validate_bounds_order(low_val, high_val, token, allow_equal=False, context="parameter definition")

        if decimals < 0:
            raise ValidationError(f"Parameter '{token}': decimals must be non-negative, got {decimals}")

        # default is optional when mid_val is set: it then follows mid_val (also in overrides).
        default_from_mid = attributes["default"] is None
        if default_from_mid:
            if mid_val is None:
                raise ParseError(
                    f"Parameter '{token}' has no mid_val and no default. Set default explicitly."
                )
            attributes["default"] = round_value(mid_val, decimals)

        attributes.update({
            "low_val": low_val,
            "mid_val": mid_val,
            "high_val": high_val,
            "decimals": decimals if dec_m else None,
            "distribution": distribution,
            "default_from_mid": default_from_mid,
        })

        # parse optional overrides (if ... { ... } blocks)
        try:
            attributes["overrides"] = parse_overrides(block_content)
        except Exception as e:
            raise ValueError(f"Error parsing overrides for parameter '{token}': {e}")

        return ContinuousParameter(**attributes)

    elif param_type == "DiscreteParameter":
        if attributes["default"] is None:
            raise ParseError(f"Parameter '{token}' has no default. Set default explicitly.")

        # pick up any per-parameter overrides
        low_case = emission_low_pattern.search(base_content)
        high_case = emission_high_pattern.search(base_content)
        attributes["emissions_low"] = low_case.group(1) if low_case else None
        attributes["emissions_high"] = high_case.group(1) if high_case else None

        # Extracting values and probabilities
        values_str = values_pattern.search(base_content).group(1)
        probabilities_str = probabilities_pattern.search(base_content).group(1)

        # Parsing values as they are, not converting to float
        values = [value.strip().strip('"') for value in values_str.split(',')]

        # Parsing probabilities (must be floats)
        probabilities = [float(prob.strip()) for prob in probabilities_str.split(',')] if probabilities_str else ['uniform']

        # If probabilities are not uniform, scale them so they sum to 1.0
        if probabilities != ['uniform']:
            sum_probabilities = sum(probabilities)
            if not np.isclose(sum_probabilities, 1.0):
                probabilities = [p / sum_probabilities for p in probabilities]

        attributes.update({
            "values": values,
            "probabilities": probabilities if probabilities != ['uniform'] else [1.0 / len(values)] * len(values)
        })

        # parse optional overrides (if ... { ... } blocks)
        try:
            attributes["overrides"] = parse_overrides(block_content)
        except Exception as e:
            raise ValueError(f"Error parsing overrides for parameter '{token}': {e}")

        return DiscreteParameter(**attributes)

    elif param_type == "ScenarioParameter":
        # Process values to remove quotes and whitespace in ScenarioParameters
        values_str = values_pattern.search(block_content).group(1)
        attributes.update({"values": process_values(values_str)})

        return ScenarioParameter(**attributes)

    else:
        raise ValueError(f"Unrecognized parameter type: {param_type}")


def process_values(values_string):
    """
    Processes a string of values, removing quotes and trimming whitespace.
    """
    return [value.strip().replace('"', '') for value in values_string.split(',')]
