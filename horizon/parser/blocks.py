# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The parameter blocks of a .hor file as parameters: their keys, the one-line call, the `if` overrides, and the
checks of every variant a scenario can give."""

from __future__ import annotations

import re

from horizon.parameters import (
    DEFAULT_DISTRIBUTION,
    ContinuousParameter,
    DiscreteParameter,
    ScenarioParameter,
    override_label,
    round_value,
)
from horizon.parser.syntax import Block, Entry
from horizon.parser.values import (
    as_count,
    as_distribution,
    as_emissions,
    as_flag,
    as_number,
    as_text,
    as_texts,
    error_at,
    keyed,
    normalize_distribution,
)

_KEYS = {
    "ScenarioParameter": ("active", "default", "values"),
    "ContinuousParameter": ("active", "default", "low_val", "mid_val", "high_val", "distribution", "decimals",
                            "emissions_low", "emissions_high"),
    "DiscreteParameter": ("active", "default", "values", "probabilities", "emissions_low", "emissions_high"),
}
_CALL_KEYS = ("low_val", "mid_val", "high_val", "distribution", "values", "probabilities")

# a block name is the %token% in the simulation files
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def parameters_of(blocks: list[Block], where: str) -> tuple[list[ScenarioParameter], list]:
    """The scenario parameters and the sampled parameters of the parameter blocks, in file order.

    Parameters
    ----------
    blocks : list of Block
        The parameter blocks of the file.
    where : str
        The file, for error messages.
    """
    scenario_parameters, parameters, lines = [], [], {}
    for block in blocks:
        if block.name in lines:
            raise error_at(where, block.line, f"parameter '{block.name}' is defined twice (also on line "
                                              f"{lines[block.name]}); parameter names must be unique")

        if not _TOKEN_RE.fullmatch(block.name) or block.name == "SCENARIO":
            raise error_at(where, block.line, f"'{block.name}' cannot be a parameter name: it is the %token% in "
                                              "the simulation files, so use letters, digits and _ (SCENARIO is "
                                              "reserved)")

        lines[block.name] = block.line
        if block.kind == "ScenarioParameter":
            scenario_parameters.append(_scenario_parameter(block, where))
        elif block.kind == "ContinuousParameter":
            parameters.append(_continuous_parameter(block, where))
        else:
            parameters.append(_discrete_parameter(block, where))

    _check_conditions(parameters, scenario_parameters, where)
    return scenario_parameters, parameters


def _check_probabilities(probabilities: list, count: int, where: str, line: int) -> list:
    if len(probabilities) != count:
        raise error_at(where, line, f"{len(probabilities)} probabilities for {count} values; give one per value, "
                                    "or leave probabilities out for a uniform choice")

    if any(p < 0 for p in probabilities) or sum(probabilities) <= 0:
        raise error_at(where, line, "probabilities must be 0 or more and not all 0")

    return probabilities


_COERCE = {"active": as_flag, "low_val": as_number, "mid_val": as_number, "high_val": as_number,
           "distribution": as_distribution, "emissions_low": as_emissions, "emissions_high": as_emissions,
           "values": as_texts}


def _coerce(kind: str, entry: Entry, where: str):
    """The value of an entry of a parameter block as the parameter keeps it."""
    if entry.key == "decimals":
        return as_count(entry, where, 0)

    if entry.key == "default":
        return as_number(entry, where) if kind == "ContinuousParameter" else as_text(entry, where)

    if entry.key == "probabilities":
        return [as_number(Entry(entry.key, value, entry.line), where) for value in as_texts(entry, where)]

    return _COERCE[entry.key](entry, where)


def _scenario_parameter(block: Block, where: str) -> ScenarioParameter:
    what = f'ScenarioParameter "{block.name}"'
    if block.call or block.overrides:
        line = block.call.line if block.call else block.overrides[0].line
        raise error_at(where, line, f"{what} takes neither a distribution call nor `if` blocks")

    entries = keyed(block.entries, _KEYS[block.kind], what, where)
    if "values" not in entries:
        raise error_at(where, block.line, f"{what} has no values")

    values = as_texts(entries["values"], where)
    default = as_text(entries["default"], where) if "default" in entries else values[0]
    if default not in values:
        raise error_at(where, entries["default"].line, f"{what}: default '{default}' is not one of its values "
                                                       f"({', '.join(values)})")

    active = as_flag(entries["active"], where) if "active" in entries else True
    return ScenarioParameter(block.name, values, active, default)


def _call_entries(block: Block, where: str) -> list[Entry]:
    """The entries the one-line call stands for, e.g. `= UNIFORM(0, 1)` for distribution, low_val and high_val."""
    call, what = block.call, f'{block.kind} "{block.name}"'
    for entry in block.entries:
        if entry.key in _CALL_KEYS:
            raise error_at(where, entry.line, f"{what}: '{entry.key}' cannot be set next to the call; remove it, "
                                              "or write the block in the long form")

    if block.kind == "DiscreteParameter":
        if call.name != "DISCRETE" or not 1 <= len(call.args) <= 2:
            raise error_at(where, call.line, f"{what}: expected DISCRETE([values], [probabilities]); "
                                             "probabilities are optional (uniform)")

        return [Entry(key, args, call.line) for key, args in zip(("values", "probabilities"), call.args)]

    try:
        distribution = normalize_distribution(call.name)
    except ValueError as error:
        raise error_at(where, call.line, f"{what}: {error}") from None

    keys = ("low_val", "mid_val", "high_val") if "triangular" in distribution else ("low_val", "high_val")
    if len(call.args) != len(keys):
        expected = "low, mode, high" if len(keys) == 3 else "low, high"
        raise error_at(where, call.line, f"{what}: {distribution.upper()} takes {len(keys)} numbers ({expected}), "
                                         f"got {len(call.args)}")

    return [Entry("distribution", distribution, call.line)] + [
        Entry(key, arg, call.line) for key, arg in zip(keys, call.args)]


def _overrides(block: Block, where: str) -> list[dict]:
    """The `if` blocks as the sampler applies them: conditions, coerced attributes and line."""
    what = f"an 'if' block of {block.kind} \"{block.name}\""
    overrides = []
    for override in block.overrides:
        for condition in override.conditions:
            wanted = condition["values"] if condition["op"] == "in" else [condition["value"]]
            if not all(isinstance(value, str) for value in wanted):
                raise error_at(where, override.line, f"{what}: a condition of '{condition['token']}' compares with "
                                                     "single values, not lists")

        entries = keyed(override.entries, _KEYS[block.kind], what, where)
        attrs = {key: _coerce(block.kind, entry, where) for key, entry in entries.items()}
        overrides.append({"conditions": override.conditions, "attrs": attrs, "line": override.line})

    return overrides


def _continuous_parameter(block: Block, where: str) -> ContinuousParameter:
    what = f'ContinuousParameter "{block.name}"'
    entries = keyed(block.entries + (_call_entries(block, where) if block.call else []), _KEYS[block.kind],
                    what, where)
    attrs = {key: _coerce(block.kind, entry, where) for key, entry in entries.items()}
    if "low_val" not in attrs or "high_val" not in attrs:
        raise error_at(where, block.line, f"{what} needs low_val and high_val")

    if attrs["low_val"] >= attrs["high_val"]:
        raise error_at(where, entries["low_val"].line, f"{what}: low_val ({attrs['low_val']}) must be below "
                                                       f"high_val ({attrs['high_val']})")

    distribution = attrs.get("distribution", DEFAULT_DISTRIBUTION)
    parameter = ContinuousParameter(
        block.name, attrs.get("active", True), attrs.get("default"), attrs["low_val"], attrs.get("mid_val"),
        attrs["high_val"], distribution=distribution, decimals=attrs.get("decimals"),
        emissions_low=attrs.get("emissions_low"), emissions_high=attrs.get("emissions_high"),
        overrides=_overrides(block, where), default_from_mid="default" not in attrs)

    if parameter.default_from_mid:
        if parameter.mid_val is None:
            raise error_at(where, block.line, f"{what} has no mid_val and no default. Set default explicitly.")

        parameter.default = round_value(parameter.mid_val, parameter.decimals)

    _check_variants(parameter, "distribution" not in attrs, where, block.line)
    return parameter


def _check_variants(parameter: ContinuousParameter, distribution_defaulted: bool, where: str, line: int) -> None:
    """Check the base parameter and each `if` variant: log bounds above 0, bounds in order, the mode and the
    default within the bounds. An `if` may set low_val = high_val to fix a value."""
    token = parameter.token
    variants = [("", parameter, line, False)]
    for override in parameter.overrides:
        context = f" in 'if {override_label(override['conditions'])}'"
        variant = parameter.shallow_copy_with_overrides(override["attrs"])
        variants.append((context, variant, override["line"], "default" in override["attrs"]))

    for context, variant, line, default_in_override in variants:
        low, mid, high, default = variant.low_val, variant.mid_val, variant.high_val, variant.default
        if variant.distribution.startswith("log") and (low <= 0 or high <= 0 or (mid is not None and mid <= 0)):
            hint = (" (no distribution set, so it defaults to LOG-TRIANGULAR)"
                    if distribution_defaulted and variant.distribution == DEFAULT_DISTRIBUTION else "")
            raise error_at(where, line, f"Parameter '{token}'{context}: {variant.distribution.upper()} needs bounds "
                                        f"above 0{hint}. Use positive bounds or set distribution = TRIANGULAR or "
                                        "UNIFORM.")

        if low > high:
            raise error_at(where, line, f"Parameter '{token}'{context}: low_val ({low}) must be <= high_val "
                                        f"({high}).")

        if "triangular" in variant.distribution and mid is not None and not low <= mid <= high:
            raise error_at(where, line, f"Parameter '{token}'{context}: mid_val ({mid}) must lie within "
                                        f"[{low}, {high}].")

        if not low <= default <= high:
            hint = (" The base default still applies here; set default inside this 'if' block."
                    if context and not default_in_override and not variant.default_from_mid else "")
            raise error_at(where, line, f"Parameter '{token}'{context}: default ({default}) must lie within "
                                        f"[{low}, {high}].{hint}")


def _discrete_parameter(block: Block, where: str) -> DiscreteParameter:
    what = f'DiscreteParameter "{block.name}"'
    entries = keyed(block.entries + (_call_entries(block, where) if block.call else []), _KEYS[block.kind],
                    what, where)
    if "values" not in entries:
        raise error_at(where, block.line, f"{what} has no values")

    if "default" not in entries:
        raise error_at(where, block.line, f"{what} has no default. Set default explicitly.")

    values = as_texts(entries["values"], where)
    uniform = "probabilities" not in entries
    probabilities = [1.0 / len(values)] * len(values)
    if not uniform:
        probabilities = _check_probabilities(_coerce(block.kind, entries["probabilities"], where), len(values),
                                             where, entries["probabilities"].line)

    overrides = _overrides(block, where)
    for override, parsed in zip(overrides, block.overrides):
        attrs = override["attrs"]
        if "probabilities" in attrs:
            line = next(entry.line for entry in parsed.entries if entry.key == "probabilities")
            _check_probabilities(attrs["probabilities"], len(attrs.get("values", values)), where, line)
        elif "values" in attrs and uniform:
            attrs["probabilities"] = [1 / len(attrs["values"])] * len(attrs["values"])
        elif "values" in attrs and len(attrs["values"]) != len(values):
            raise error_at(where, parsed.line, f"{what}: this 'if' block changes the number of values, so it needs "
                                               "its own probabilities")

    return DiscreteParameter(
        block.name, as_flag(entries["active"], where) if "active" in entries else True,
        as_text(entries["default"], where), values, probabilities,
        emissions_low=as_emissions(entries["emissions_low"], where) if "emissions_low" in entries else None,
        emissions_high=as_emissions(entries["emissions_high"], where) if "emissions_high" in entries else None,
        overrides=overrides)


def _check_conditions(parameters: list, scenario_parameters: list, where: str) -> None:
    """Every `if` condition must name a ScenarioParameter (or SCENARIO) and only values it has."""
    values_by_token = {sp.token: sp.values for sp in scenario_parameters}
    for parameter in parameters:
        for override in parameter.overrides:
            context = f"Parameter '{parameter.token}', 'if {override_label(override['conditions'])}'"
            for condition in override["conditions"]:
                token = condition["token"]
                wanted = condition["values"] if condition["op"] == "in" else [condition["value"]]
                if token == "SCENARIO":
                    continue

                if token not in values_by_token:
                    raise error_at(where, override["line"], f"{context}: '{token}' is not a ScenarioParameter. "
                                                            f"Defined: {', '.join(values_by_token) or 'none'}.")

                for value in wanted:
                    if value not in values_by_token[token]:
                        raise error_at(where, override["line"],
                                       f"{context}: '{value}' is not a value of ScenarioParameter '{token}'. "
                                       f"Valid values: {', '.join(values_by_token[token])}.")
