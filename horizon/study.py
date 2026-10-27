# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""A study: its settings and runs, and the folder they write into.

OutputPath names the study folder. It holds study.json (the settings and every run with its values, all a run
needs to start), logs/
(Navigate's log of each run and horizon.log), parquets/ (each run's results until they are compacted),
and once compacted the archive <name>_archive.parquet and the Lantern file <name>.parquet.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from horizon import __version__
from horizon.exceptions import StudyError
from horizon.parameters import resolve_parameters_for_scenario
from horizon.parser import HorFile
from horizon.sampling import ParameterSampler

CONFIG = "study.json"

# the per-run results, where Navigate writes them for a run with --set; deleted with the folder once the study is compacted
RESULTS = "parquets"


def format_value(value) -> str:
    """Text of a sampled value as Navigate reads it: floats without exponent, as short as they round-trip."""
    if isinstance(value, float):
        return np.format_float_positional(value, trim="0")

    return str(value)


def sample_runs(hor: HorFile, number_of_samples: int) -> list[dict]:
    """Sample every scenario combination: one entry per run, its id, label and sample number, and its values.

    Each combination resolves its `if` overrides and samples with one shared draw matrix, so sample i of
    every combination comes from the same draw. The values are the text each token gets through --set.

    Parameters
    ----------
    hor : HorFile
        The study.
    number_of_samples : int
        Samples per scenario combination.
    """
    sampler = ParameterSampler()
    sample = sampler.sample_latin_hypercube if hor.sampling_method == "LHS" else sampler.sample_monte_carlo
    width = max(3, len(str(number_of_samples)))
    rows = []
    for combination in hor.combinations:
        parameters = resolve_parameters_for_scenario(hor.parameters, combination.scenario_map)
        for index, values in enumerate(sample(parameters, number_of_samples, seed=hor.random_seed), 1):
            rows.append({"run_id": f"{combination.label}_sample{index:0{width}d}", "run": combination.label,
                         "sample": index, "values": {**combination.scenario_map,
                                                     **{p.token: format_value(values[p.token]) for p in parameters}}})

    return rows


@dataclass
class Study:
    """A study folder as written by `horizon run`: study.json, its settings and its runs."""

    folder: Path
    config: dict

    @property
    def rows(self) -> list[dict]:
        return self.config["runs"]

    @property
    def name(self) -> str:
        return self.config["study"]

    @property
    def run_ids(self) -> list[str]:
        return [row["run_id"] for row in self.rows]

    @property
    def logs(self) -> Path:
        return self.folder / "logs"

    @property
    def archive(self) -> Path:
        return self.folder / f"{self.name}_archive.parquet"

    @property
    def lantern(self) -> Path:
        return self.folder / f"{self.name}.parquet"

    @property
    def results_folder(self) -> Path:
        return self.folder / RESULTS

    def results(self, run_id: str) -> Path:
        return self.results_folder / f"{run_id}.parquet"

    def row(self, run_id: str) -> dict:
        return self._rows_by_id[run_id]

    @cached_property
    def _rows_by_id(self) -> dict[str, dict]:
        return {row["run_id"]: row for row in self.rows}


def load_study(folder) -> Study:
    """Read the study written to a folder by `horizon run`.

    Parameters
    ----------
    folder : str or Path
        The study folder, OutputPath.
    """
    folder = Path(folder).resolve()
    try:
        config = json.loads((folder / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StudyError(f"{folder} holds no study written by `horizon run` ({error})") from error

    if not isinstance(config.get("runs"), list):
        raise StudyError(f"{folder / CONFIG} has no runs; it is from an earlier Horizon, so run the study again")

    return Study(folder, config)


def config_text(config: dict) -> str:
    """The text of study.json: indented, with each run on one line, so that a run is one line to find."""
    settings = json.dumps({key: value for key, value in config.items() if key != "runs"}, indent=2)
    runs = ",\n".join(f"    {json.dumps(run)}" for run in config["runs"])
    return f'{settings[:-2]},\n  "runs": [\n{runs}\n  ]\n}}\n'


def study_config(hor: HorFile, rows: list[dict], navigate: str, navigate_args: list[str]) -> dict:
    """What study.json holds: everything a run needs to start, where the study came from, and its runs."""
    return {
        "study": hor.name,
        "hor": str(hor.path.resolve()),
        "hor_sha256": hashlib.sha256(hor.path.read_bytes()).hexdigest(),
        "horizon_version": __version__,
        "unc": str(hor.unc_path),
        "navigate": navigate,
        "navigate_args": navigate_args,
        "sampling_method": hor.sampling_method,
        "random_seed": hor.random_seed,
        "runs": rows,
    }


def prepare_study(hor: HorFile, rows: list[dict], navigate: str, navigate_args: list[str]) -> Study:
    """Write study.json, after clearing what an earlier run of the study left behind.

    Parameters
    ----------
    hor : HorFile
        The study.
    rows : list of dict
        The runs, from sample_runs.
    navigate : str
        The Navigate executable.
    navigate_args : list of str
        Extra arguments for every Navigate run.
    """
    folder = hor.study_folder
    _clear(folder)

    folder.mkdir(parents=True, exist_ok=True)
    config = study_config(hor, rows, navigate, navigate_args)
    (folder / CONFIG).write_text(config_text(config), encoding="utf-8")

    study = Study(folder, config)
    study.logs.mkdir()
    return study


def _clear(folder: Path) -> None:
    """Remove the logs and per-run results of an earlier run of the study in folder."""
    shutil.rmtree(folder / RESULTS, ignore_errors=True)
    shutil.rmtree(folder / "logs", ignore_errors=True)
