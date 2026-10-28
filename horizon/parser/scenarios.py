# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The scenario combinations a study runs: the active scenario parameters span them, Include and Exclude rules
filter them, and each gets the label that names its runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from horizon.exceptions import ParseError
from horizon.parameters import ScenarioParameter
from horizon.parser.syntax import HorizonBlock
from horizon.parser.values import as_text, error_at

# a run label is part of file names
_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Combination:
    """A scenario combination of the study: its label and the value of every scenario token."""

    label: str
    scenario_map: dict


def combinations(horizon: HorizonBlock, scenario_parameters: list[ScenarioParameter], study: str,
                 where: str) -> tuple[list[Combination], list[tuple[str, str]]]:
    """The scenario combinations of the study, and the warnings about rules that never match.

    The active scenario parameters span the combinations; inactive ones keep their default. Include rules,
    if any, keep only the combinations they match, and Exclude rules remove those they match. A label is the
    _name of the first matching Include rule, else the active values joined by _, else the study name. With
    _name labels, the label is also the SCENARIO token.

    Parameters
    ----------
    horizon : HorizonBlock
        The Horizon block, with the rules.
    scenario_parameters : list of ScenarioParameter
        The scenario parameters of the study.
    study : str
        The study name, the label without active scenario parameters.
    where : str
        The file, for error messages.

    Returns
    -------
    tuple
        The combinations, and each warning as its `file:line` and message.
    """
    includes, excludes, warnings = _rules(horizon, scenario_parameters, where)
    if not _LABEL_RE.fullmatch(study):
        raise ParseError(f"the study file name '{study}' names its results files; use letters, digits, _, . and -",
                         f"{where}:1")

    active = [sp for sp in scenario_parameters if sp.active]
    named = bool(includes) and "_name" in includes[0]
    found, origin = [], {}
    for values in product(*(sp.values for sp in active)):
        chosen = dict(zip((sp.token for sp in active), values))
        scenario_map = {sp.token: chosen.get(sp.token, sp.default) for sp in scenario_parameters}
        include = next((rule for rule in includes if _matches(rule, scenario_map)), None)
        if (includes and include is None) or any(_matches(rule, scenario_map) for rule in excludes):
            continue

        label = include["_name"] if named else "_".join(values) if values else study
        source = include["line"] if named else horizon.line
        if not _LABEL_RE.fullmatch(label):
            raise error_at(where, source, f"'{label}' cannot name runs: the label of a scenario combination is part "
                                          "of file names, so use letters, digits, _, . and -")

        if label in origin:
            raise error_at(where, source, f"two scenario combinations get the label '{label}' "
                                          f"({origin[label]} and {', '.join(values)}); labels must be unique")

        origin[label] = ", ".join(values) or study
        found.append(Combination(label, {**scenario_map, "SCENARIO": label} if named else scenario_map))

    if not found:
        raise error_at(where, horizon.line, "the Include and Exclude rules leave no scenario combination to run")

    return found, warnings


def _rules(horizon: HorizonBlock, scenario_parameters: list[ScenarioParameter],
           where: str) -> tuple[list[dict], list[dict], list[tuple[str, str]]]:
    """Include and Exclude rules as {token: value}, an Include label under the reserved key _name, and the
    warnings about tokens and values that are not the study's."""
    known = {sp.token: sp.values for sp in scenario_parameters}
    rules, warnings = {"Include": [], "Exclude": []}, []
    for rule in horizon.rules:
        pairs = {}
        for pair in rule.pairs:
            if pair.key in pairs:
                raise error_at(where, pair.line, f"{pair.key} appears twice in one {rule.kind} rule")

            if pair.key == "_name" and rule.kind == "Exclude":
                raise error_at(where, pair.line, "_name labels Include rules only")

            pairs[pair.key] = as_text(pair, where)
            if pair.key == "_name":
                continue

            if pair.key not in known:
                warnings.append((f"{where}:{rule.line}", f"{rule.kind} rule names '{pair.key}', which is not a "
                                 f"ScenarioParameter (known: {', '.join(known) or 'none'}); it never matches"))
            elif pairs[pair.key] not in known[pair.key]:
                warnings.append((f"{where}:{rule.line}", f"{rule.kind} rule names '{pairs[pair.key]}' for "
                                 f"'{pair.key}', which is not one of its values ({', '.join(known[pair.key])}); "
                                 "it never matches"))

        rules[rule.kind].append({**pairs, "line": rule.line})

    named = [("_name" in rule) for rule in rules["Include"]]
    if any(named) and not all(named):
        line = rules["Include"][named.index(False)]["line"]
        raise error_at(where, line, "if any Include rule has _name, all must have it")

    return rules["Include"], rules["Exclude"], warnings


def _matches(rule: dict, scenario_map: dict) -> bool:
    return all(scenario_map.get(token) == value for token, value in rule.items() if token not in ("_name", "line"))
