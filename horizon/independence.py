# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Do the sampled inputs vary independently of each other?

This module looks at the uniform draw matrix of a study (one column per parameter, one row per random sample) and
finds inputs that move together: the largest rank correlation of any pair, copied columns and repeated rows. A
sampler bug or a copied block shows here before any run starts. `horizon run` writes the record to
sampling_check.json in the study folder.
"""

from __future__ import annotations

import itertools
import json
import math

import numpy as np

from horizon.parameters import resolve_parameters_for_scenario
from horizon.parser import HorFile
from horizon.sampling import ParameterSampler

SAMPLING_CHECK = "sampling_check.json"

# The limit is LIMIT / sqrt(n). For 300 LHS seeds at n = 997 and 16 inputs, max |rho| * sqrt(n)
# had p99 3.91 and max 4.22; a true correlation of 0.3 is far above it at any study size.
LIMIT = 4.5

# Below this many draws a rank correlation says little; the record has no verdict on it.
MIN_DRAWS = 30

# Bins per input for the combination table: 10 x 10 cells for pairs, 5^3 for triples, 4^4 for 4 at once.
_BINS = {2: 10, 3: 5, 4: 4}
_MAX_SUBSETS = 20000  # above this many input subsets of one size, a seeded random choice of them


def _varies(parameter) -> bool:
    """True if the parameter is sampled in some scenario: active, or made active by an override."""
    return parameter.active or any(override["attrs"].get("active") is True for override in parameter.overrides)


def _varying(draws, parameters):
    """The columns of the parameters that vary, and their tokens."""
    keep = [index for index, parameter in enumerate(parameters) if _varies(parameter)]
    return np.asarray(draws)[:, keep], [parameters[index].token for index in keep]


def _ranks(columns):
    return np.argsort(np.argsort(columns, axis=0, kind="stable"), axis=0).astype(float)


def independence(draws, parameters) -> dict:
    """The largest rank (Spearman) correlation of any pair, copied inputs and repeated draws.

    Parameters
    ----------
    draws : array (n, d)
        The uniform matrix the sampler mapped to parameter values; it serves every scenario combination.
    parameters : list
        The parameters of the columns; those that never vary are left out.
    """
    varying, tokens = _varying(draws, parameters)
    count, inputs = varying.shape
    limit = LIMIT / math.sqrt(count) if count else math.inf
    largest = {"pair": [], "rho": 0.0}
    if inputs >= 2 and count >= 2:
        rho = np.nan_to_num(np.corrcoef(_ranks(varying), rowvar=False))  # a constant column has no correlation
        np.fill_diagonal(rho, 0.0)
        first, second = sorted(np.unravel_index(np.abs(rho).argmax(), rho.shape))
        largest = {"pair": [tokens[first], tokens[second]], "rho": round(float(rho[first, second]), 4)}

    # one draw (LHS of a single sample is 0.5 everywhere) makes every column look copied
    pairs = itertools.combinations(range(inputs), 2) if count >= 2 else []
    same_input = [[tokens[first], tokens[second]] for first, second in pairs
                  if np.array_equal(varying[:, first], varying[:, second])]
    same_draw = int(count - np.unique(varying, axis=0).shape[0]) if count and inputs else 0
    return {
        "draws": int(count),
        "inputs": int(inputs),
        "largest": largest,
        "limit": round(limit, 4),
        "same_input": same_input,
        "same_draw": same_draw,
        "independent": (count < MIN_DRAWS or abs(largest["rho"]) <= limit) and not same_input and not same_draw,
    }


def combination_coverage(draws, parameters, seed: int = 0) -> list[dict]:
    """Share of filled grid cells for every 2, 3 and 4 inputs at once: the worst and the mean.

    It shows holes in the joint space. It is not a test: random sampling fills the cells as well as LHS does, so a
    correlated sampler can still score high here.

    Parameters
    ----------
    draws : array (n, d)
        The uniform draw matrix.
    parameters : list
        The parameters of the columns; those that never vary are left out.
    seed : int
        Seed for the choice of subsets when there are too many to count.
    """
    varying, _ = _varying(draws, parameters)
    count, inputs = varying.shape
    rows = []
    rng = np.random.default_rng(seed)
    for size, bins in _BINS.items():
        if inputs < size or not count:
            continue

        cells = np.minimum((varying * bins).astype(int), bins - 1)
        subsets = list(itertools.combinations(range(inputs), size))
        if len(subsets) > _MAX_SUBSETS:
            subsets = [subsets[index] for index in rng.choice(len(subsets), _MAX_SUBSETS, replace=False)]

        weights = bins ** np.arange(size)
        shares = [np.unique(cells[:, list(subset)] @ weights).size / bins ** size for subset in subsets]
        rows.append({"size": size, "cells": bins ** size, "worst": round(float(min(shares)), 4),
                     "mean": round(float(np.mean(shares)), 4)})

    return rows


def sampling_record(hor: HorFile, number_of_samples: int | None = None, sampler: ParameterSampler | None = None) -> dict:
    """The record of sampling_check.json: independence() plus the combination table.

    Parameters
    ----------
    hor : HorFile
        The study.
    number_of_samples : int, optional
        Samples per scenario combination; NumberOfSamples by default.
    sampler : ParameterSampler, optional
        A sampler that sampled the study already (sample_runs); without it the draws are made here.
    """
    if sampler is None or sampler.draws is None:
        sampler = ParameterSampler()
        sample = sampler.sample_latin_hypercube if hor.sampling_method == "LHS" else sampler.sample_monte_carlo
        sample(resolve_parameters_for_scenario(hor.parameters, {}), number_of_samples or hor.number_of_samples,
               seed=hor.random_seed, first_sample=hor.first_sample)

    draws = sampler.draws if sampler.draws is not None else np.empty((0, len(hor.parameters)))
    return {**independence(draws, hor.parameters), "first_sample": hor.first_sample, "seed": hor.random_seed,
            "coverage": combination_coverage(draws, hor.parameters)}


def describe(record: dict) -> str:
    """One line on the record, for the log."""
    pair = " and ".join(record["largest"]["pair"]) or "no pair"
    line = (f"Sampling check: {record['draws']} draws of {record['inputs']} inputs, largest rank correlation "
            f"{record['largest']['rho']:+.3f} ({pair}), limit {record['limit']:.3f}")
    if record["same_input"]:
        line += f"; inputs drawn the same: {', '.join(' = '.join(pair) for pair in record['same_input'])}"

    if record["same_draw"]:
        line += f"; {record['same_draw']} repeated draws"

    return line + ("" if record["independent"] else "; the inputs do NOT vary independently")


def write_record(folder, record: dict) -> None:
    """Write the record to sampling_check.json in the study folder."""
    (folder / SAMPLING_CHECK).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
