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
        if raw_value[0] in "\"'" and raw_value[-1] == raw_value[0]:
            raw_value = raw_value[1:-1]
        pairs[key] = raw_value

    if not pairs:
        raise ValueError(f"No valid key=value pairs found in Exclude directive: '{pairs_text}'")

    return pairs


def _parse_directives(directive_re, horizon_content, kind):
    """Parse every directive matched by directive_re into a rule dict; invalid ones are skipped with a warning."""
    rules = []
    for m in directive_re.finditer(horizon_content):
        pairs_text = m.group(1).strip()
        if not pairs_text:
            continue

        try:
            rules.append(parse_exclude_directive(pairs_text))
        except ValueError as e:
            logger.warning("Skipping invalid %s directive: %s", kind, e)

    return rules


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
    return _parse_directives(_EXCLUDE_DIRECTIVE_RE, horizon_content, "Exclude")


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
    inclusion_rules = _parse_directives(_INCLUDE_DIRECTIVE_RE, horizon_content, "Include")

    # Validate _name consistency: either all Include rules have _name or none do
    has_name = [("_name" in rule) for rule in inclusion_rules]
    if any(has_name) and not all(has_name):
        raise ParseError(
            "Mixed _name usage in Include directives: if any Include rule "
            "has _name, all must have it."
        )

    return inclusion_rules


def _matches_any_rule(scenario_map, rules):
    """Check if scenario_map matches any rule in the list (partial matching: all of a rule's
    token-value pairs must be present).

    The reserved ``_name`` key is skipped during matching: it is used only for labeling.
    """
    return any(all(scenario_map.get(token) == value for token, value in rule.items() if token != "_name")
               for rule in rules)


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

        if _matches_any_rule(scenario_map, [rule]):
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
    if inclusion_rules and not _matches_any_rule(scenario_map, inclusion_rules):
        return True

    return _matches_any_rule(scenario_map, exclusion_rules or [])
