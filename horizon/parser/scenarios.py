"""
The scenario combinations a study runs.

The active scenario parameters span them, Include and Exclude rules filter them, and each gets the label that
names its runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING

from horizon.exceptions import ParseError
from horizon.parser.values import as_text, error_at

if TYPE_CHECKING:
    from horizon.parameters import ScenarioParameter
    from horizon.parser.syntax import HorizonBlock

# a run label is part of file names
_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Combination:
    """A scenario combination of the study: its label and the value of every scenario token."""

    label: str
    scenario_map: dict[str, str]


def combinations(
    horizon: HorizonBlock, scenario_parameters: list[ScenarioParameter], study: str, where: str
) -> tuple[list[Combination], list[tuple[str, str]]]:
    """
    Find the scenario combinations of the study, and the warnings about rules that never match.

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
        raise ParseError(
            f"the study file name '{study}' names its results files; use letters, digits, _, . and -", f"{where}:1"
        )

    active = [sp for sp in scenario_parameters if sp.active]
    named = bool(includes) and includes[0].name is not None
    found: list[Combination] = []
    origin: dict[str, str] = {}
    for values in product(*(sp.values for sp in active)):
        chosen = dict(zip((sp.token for sp in active), values, strict=True))
        scenario_map = {sp.token: chosen.get(sp.token, sp.default) for sp in scenario_parameters}
        include = next((rule for rule in includes if rule.matches(scenario_map)), None)
        if (includes and include is None) or any(rule.matches(scenario_map) for rule in excludes):
            continue

        label, source = "_".join(values) if values else study, horizon.line
        if include is not None and include.name is not None:
            label, source = include.name, include.line

        if not _LABEL_RE.fullmatch(label):
            raise error_at(
                where,
                source,
                f"'{label}' cannot name runs: the label of a scenario combination is part "
                "of file names, so use letters, digits, _, . and -",
            )

        if label in origin:
            raise error_at(
                where,
                source,
                f"two scenario combinations get the label '{label}' "
                f"({origin[label]} and {', '.join(values)}); labels must be unique",
            )

        origin[label] = ", ".join(values) or study
        found.append(Combination(label, {**scenario_map, "SCENARIO": label} if named else scenario_map))

    if not found:
        raise error_at(where, horizon.line, "the Include and Exclude rules leave no scenario combination to run")

    return found, warnings


@dataclass(frozen=True)
class _Rule:
    """An Include or Exclude rule: the value of each token it names, an Include's label (_name), and its line."""

    values: dict[str, str]
    name: str | None
    line: int

    def matches(self, scenario_map: dict[str, str]) -> bool:
        """Return whether the scenario combination gives every token of the rule its value."""
        return all(scenario_map.get(token) == value for token, value in self.values.items())


def _rules(
    horizon: HorizonBlock, scenario_parameters: list[ScenarioParameter], where: str
) -> tuple[list[_Rule], list[_Rule], list[tuple[str, str]]]:
    """Read the Include and Exclude rules, and the warnings about tokens and values that are not the study's."""
    known = {sp.token: sp.values for sp in scenario_parameters}
    rules: dict[str, list[_Rule]] = {"Include": [], "Exclude": []}
    warnings: list[tuple[str, str]] = []
    for rule in horizon.rules:
        pairs: dict[str, str] = {}
        for pair in rule.pairs:
            if pair.key in pairs:
                raise error_at(where, pair.line, f"{pair.key} appears twice in one {rule.kind} rule")

            if pair.key == "_name" and rule.kind == "Exclude":
                raise error_at(where, pair.line, "_name labels Include rules only")

            pairs[pair.key] = as_text(pair, where)
            if pair.key == "_name":
                continue

            if pair.key not in known:
                warnings.append(
                    (
                        f"{where}:{rule.line}",
                        f"{rule.kind} rule names '{pair.key}', which is not a "
                        f"ScenarioParameter (known: {', '.join(known) or 'none'}); it never matches",
                    )
                )
            elif pairs[pair.key] not in known[pair.key]:
                warnings.append(
                    (
                        f"{where}:{rule.line}",
                        f"{rule.kind} rule names '{pairs[pair.key]}' for "
                        f"'{pair.key}', which is not one of its values ({', '.join(known[pair.key])}); "
                        "it never matches",
                    )
                )

        name = pairs.pop("_name", None)
        rules[rule.kind].append(_Rule(pairs, name, rule.line))

    named = [rule.name is not None for rule in rules["Include"]]
    if any(named) and not all(named):
        line = rules["Include"][named.index(False)].line
        raise error_at(where, line, "if any Include rule has _name, all must have it")

    return rules["Include"], rules["Exclude"], warnings
