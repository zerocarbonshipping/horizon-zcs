# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""A .hor file as a whole: its parameters, the settings of its Horizon block and its scenario combinations.

Every error names the file and line of the text it is about.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from horizon.exceptions import ParseError
from horizon.parser.blocks import parameters_of
from horizon.parser.scenarios import Combination, combinations
from horizon.parser.syntax import Block, HorizonBlock, parse_text
from horizon.parser.values import as_count, as_text, error_at, keyed

_HORIZON_KEYS = ("UncFilePath", "OutputPath", "NumberOfSamples", "SamplingMethod", "RandomSeed")

SAMPLING_METHODS = ("LHS", "MC")
DEFAULT_SEED = 69


@dataclass
class HorFile:
    """A validated .hor file."""

    path: Path
    name: str
    unc_path: Path
    study_folder: Path
    number_of_samples: int
    sampling_method: str
    random_seed: int
    scenario_parameters: list
    parameters: list
    combinations: list[Combination]
    # each as its `file:line` and message
    warnings: list[tuple[str, str]]


def read_hor(path) -> HorFile:
    """Read and validate a .hor file.

    Parameters
    ----------
    path : str or Path
        The .hor file; its paths resolve from its folder.
    """
    path = Path(path)
    where = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ParseError(f"cannot read the study file: {error.strerror or error}") from error

    blocks = parse_text(text, where)
    horizons = [block for block in blocks if isinstance(block, HorizonBlock)]
    if not horizons:
        raise ParseError("no Horizon block", f"{where}:1")

    if len(horizons) > 1:
        raise error_at(where, horizons[1].line, "a second Horizon block; a study has one")

    scenario_parameters, parameters = parameters_of([block for block in blocks if isinstance(block, Block)], where)
    settings = _settings(horizons[0], path, where)
    found, warnings = combinations(horizons[0], scenario_parameters, path.stem, where)

    return HorFile(path=path, name=path.stem, scenario_parameters=scenario_parameters, parameters=parameters,
                   combinations=found, warnings=warnings, **settings)


def _settings(horizon: HorizonBlock, path: Path, where: str) -> dict:
    """The settings of the Horizon block; paths resolve from the folder of the .hor file."""
    entries = keyed(horizon.entries, _HORIZON_KEYS, "the Horizon block", where)
    for key in ("UncFilePath", "OutputPath", "NumberOfSamples"):
        if key not in entries:
            raise error_at(where, horizon.line, f"the Horizon block needs {key}")

    output_path = Path(as_text(entries["OutputPath"], where))
    line = entries["OutputPath"].line
    if output_path.suffix.lower() == ".csv":
        folder = output_path.parent if len(output_path.parts) > 1 else "output"
        raise error_at(where, line, f'OutputPath names the study folder now, e.g. "{folder}"; the run list is in '
                                    "study.json")

    study_folder = (path.parent / output_path).resolve()
    if study_folder == path.parent.resolve():
        raise error_at(where, line, 'OutputPath needs a folder of its own, e.g. "output": it holds the runs of '
                                    "the study")

    method = as_text(entries["SamplingMethod"], where).upper() if "SamplingMethod" in entries else "LHS"
    if method not in SAMPLING_METHODS:
        raise error_at(where, entries["SamplingMethod"].line, f"SamplingMethod must be one of "
                                                              f"{', '.join(SAMPLING_METHODS)}, got {method}")

    folder = path.parent
    return {
        "unc_path": (folder / as_text(entries["UncFilePath"], where)).resolve(),
        "study_folder": study_folder,
        "number_of_samples": as_count(entries["NumberOfSamples"], where, 1),
        "sampling_method": method,
        "random_seed": as_count(entries["RandomSeed"], where, 0) if "RandomSeed" in entries else DEFAULT_SEED,
    }
