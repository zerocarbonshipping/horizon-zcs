# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
Scenario combination filtering rules for .hor files.

Provides parsing and matching for Exclude() and Include() directives that
allow users to filter scenario combinations from the cartesian product.

Syntax in .hor files (inside the Horizon { } block):

    Exclude(BIO = "high", ELEC = "high")
    Exclude(BIO = low, POLICY = strict)
    Include(BIO = "high")
    Include(BIO = "low", ELEC = "low")

Both directives use partial matching: specifying a subset of tokens matches
all combinations containing that subset regardless of other tokens.

Filtering semantics:
- No Include, no Exclude → run all combinations
- Include only → run only matching combinations (whitelist)
- Exclude only → run all except matching (blacklist)
- Include + Exclude → Include narrows first, then Exclude removes from that set
"""

import logging
import re

from horizon.exceptions import ParseError

logger = logging.getLogger(__name__)

# Regex to find Exclude(...) and Include(...) directives in Horizon block content
_EXCLUDE_DIRECTIVE_RE = re.compile(r'Exclude\s*\(([^)]+)\)', re.IGNORECASE)
_INCLUDE_DIRECTIVE_RE = re.compile(r'Include\s*\(([^)]+)\)', re.IGNORECASE)

# Regex for key=value pairs inside Exclude(...)
_KV_PAIR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(".*?"|\'.*?\'|[^\s,)]+)')


def parse_exclude_directive(pairs_text):
    """
    Parse the content inside a single Exclude(...) into a dict.

    Parameters
    ----------
    pairs_text : str
        The text between parentheses, e.g. 'BIO = "high", ELEC = "high"'.

    Returns
    -------
    dict
        Mapping of token name to value, e.g. {"BIO": "high", "ELEC": "high"}.

    Raises
    ------
    ValueError
        If no valid key=value pairs are found.
    """
    pairs = {}
    for m in _KV_PAIR_RE.finditer(pairs_text):
        key = m.group(1)
        raw_value = m.group(2).strip()
        # Strip surrounding quotes if present
        if (raw_value.startswith('"') and raw_value.endswith('"')) or \
           (raw_value.startswith("'") and raw_value.endswith("'")):
            raw_value = raw_value[1:-1]
        pairs[key] = raw_value

    if not pairs:
        raise ValueError(f"No valid key=value pairs found in Exclude directive: '{pairs_text}'")

    return pairs


def parse_all_exclusions(horizon_content):
    """
    Find and parse all Exclude(...) directives in Horizon block content.

    Parameters
    ----------
    horizon_content : str
        The text content of the Horizon { } block.

    Returns
    -------
    list[dict]
        List of exclusion rules, each a dict mapping token names to values.
        Empty list if no Exclude() directives found.
    """
    exclusion_rules = []
    for m in _EXCLUDE_DIRECTIVE_RE.finditer(horizon_content):
        pairs_text = m.group(1).strip()
        if not pairs_text:
            continue
        try:
            rule = parse_exclude_directive(pairs_text)
            exclusion_rules.append(rule)
        except ValueError as e:
            logger.warning("Skipping invalid Exclude directive: %s", e)

    return exclusion_rules


def parse_all_inclusions(horizon_content):
    """
    Find and parse all Include(...) directives in Horizon block content.

    Parameters
    ----------
    horizon_content : str
        The text content of the Horizon { } block.

    Returns
    -------
    list[dict]
        List of inclusion rules, each a dict mapping token names to values.
        Empty list if no Include() directives found.
    """
    inclusion_rules = []
    for m in _INCLUDE_DIRECTIVE_RE.finditer(horizon_content):
        pairs_text = m.group(1).strip()
        if not pairs_text:
            continue
        try:
            # Reuse parse_exclude_directive — format is identical
            rule = parse_exclude_directive(pairs_text)
            inclusion_rules.append(rule)
        except ValueError as e:
            logger.warning("Skipping invalid Include directive: %s", e)

    # Validate _name consistency: either all Include rules have _name or none do
    if inclusion_rules:
        has_name = [("_name" in rule) for rule in inclusion_rules]
        if any(has_name) and not all(has_name):
            raise ParseError(
                "Mixed _name usage in Include directives: if any Include rule "
                "has _name, all must have it."
            )

    return inclusion_rules


def is_combination_excluded(scenario_map, exclusion_rules):
    """
    Check whether a scenario combination should be excluded.

    Uses partial matching: a rule matches if ALL of its token-value pairs
    are present in the scenario_map with matching values.

    Parameters
    ----------
    scenario_map : dict
        The full scenario combination, e.g. {"BIO": "high", "ELEC": "low", "POLICY": "strict"}.
    exclusion_rules : list[dict] or None
        List of exclusion rules from parse_all_exclusions(). None or empty means no exclusions.

    Returns
    -------
    bool
        True if the combination matches any exclusion rule (should be excluded).
    """
    if not exclusion_rules:
        return False

    for rule in exclusion_rules:
        if all(scenario_map.get(token) == value for token, value in rule.items()):
            return True

    return False


def _matches_any_rule(scenario_map, rules):
    """Check if scenario_map matches any rule in the list (partial matching).

    The reserved ``_name`` key is skipped during matching — it is used only
    for labeling, not filtering.
    """
    for rule in rules:
        if all(scenario_map.get(token) == value
               for token, value in rule.items() if token != "_name"):
            return True
    return False


def get_scenario_label(scenario_map, inclusion_rules):
    """Return the ``_name`` of the first matching Include rule, or ``None``.

    Parameters
    ----------
    scenario_map : dict
        The full scenario combination.
    inclusion_rules : list[dict] or None
        Inclusion rules from ``parse_all_inclusions()``.

    Returns
    -------
    str or None
        The ``_name`` value if the matching rule has one, otherwise ``None``.
    """
    if not inclusion_rules:
        return None
    for rule in inclusion_rules:
        name = rule.get("_name")
        if name is None:
            return None  # no labels defined
        filter_items = {k: v for k, v in rule.items() if k != "_name"}
        if all(scenario_map.get(token) == value for token, value in filter_items.items()):
            return name
    return None


def should_skip_combination(scenario_map, exclusion_rules, inclusion_rules):
    """
    Check whether a scenario combination should be skipped.

    Applies inclusion and exclusion rules with the following semantics:
    - No Include, no Exclude → don't skip (run all)
    - Include only → skip if combo doesn't match any inclusion rule
    - Exclude only → skip if combo matches any exclusion rule
    - Include + Exclude → Include narrows first, then Exclude removes from that set

    Parameters
    ----------
    scenario_map : dict
        The full scenario combination, e.g. {"BIO": "high", "ELEC": "low"}.
    exclusion_rules : list[dict] or None
        Exclusion rules from parse_all_exclusions(). None or empty means no exclusions.
    inclusion_rules : list[dict] or None
        Inclusion rules from parse_all_inclusions(). None or empty means no inclusions.

    Returns
    -------
    bool
        True if the combination should be skipped.
    """
    # If inclusion rules exist, combo must match at least one to be kept
    if inclusion_rules:
        if not _matches_any_rule(scenario_map, inclusion_rules):
            return True

    # If exclusion rules exist, combo must not match any
    if exclusion_rules:
        if _matches_any_rule(scenario_map, exclusion_rules):
            return True

    return False
