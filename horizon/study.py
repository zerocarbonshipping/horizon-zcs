"""
A study: its settings and runs, and the folder they write into.

OutputPath names the study folder. It holds study.json (the settings and every run with its values, all a run
needs to start), logs/ (Navigate's log of each run and horizon.log), parquets/ (each run's results until they are
compacted), and once compacted the archive <name>_archive.parquet and the Lantern file <name>.parquet.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import numpy as np

from horizon import __version__
from horizon.exceptions import StudyError
from horizon.parameters import resolve_parameters_for_scenario
from horizon.sampling import ParameterSampler

if TYPE_CHECKING:
    from horizon.parameters import Value
    from horizon.parser import HorFile

CONFIG = "study.json"

# the per-run results, where Navigate writes them for a run with --set; deleted with the folder once the study is compacted
RESULTS = "parquets"


class Run(TypedDict):
    """A run of study.json: its id, its scenario combination, its sample number and the value of every token."""

    run_id: str
    run: str
    sample: int
    values: dict[str, str]


class StudyConfig(TypedDict):
    """What study.json holds: everything a run needs to start, where the study came from, and its runs."""

    study: str
    hor: str
    hor_sha256: str
    horizon_version: str
    unc: str
    navigate: str
    navigate_args: list[str]
    sampling_method: str
    random_seed: int
    runs: list[Run]


def format_value(value: Value) -> str:
    """Write a sampled value as Navigate reads it: floats without exponent, as short as they round-trip."""
    if isinstance(value, float):
        return np.format_float_positional(value, trim="0")

    return str(value)


def sample_runs(hor: HorFile, number_of_samples: int) -> list[Run]:
    """
    Sample every scenario combination: one entry per run, its id, label and sample number, and its values.

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
    rows: list[Run] = []
    for combination in hor.combinations:
        parameters = resolve_parameters_for_scenario(hor.parameters, combination.scenario_map)
        for index, values in enumerate(sample(parameters, number_of_samples, seed=hor.random_seed), 1):
            rows.append(
                {
                    "run_id": f"{combination.label}_sample{index:0{width}d}",
                    "run": combination.label,
                    "sample": index,
                    "values": {**combination.scenario_map, **{p.token: format_value(values[p.token]) for p in parameters}},
                }
            )

    return rows


@dataclass
class Study:
    """A study folder as written by `horizon run`: study.json, its settings and its runs."""

    folder: Path
    config: StudyConfig

    @property
    def rows(self) -> list[Run]:
        """The runs, in the order they start."""
        return self.config["runs"]

    @property
    def name(self) -> str:
        """The study name, the stem of the .hor file."""
        return self.config["study"]

    @property
    def run_ids(self) -> list[str]:
        """The id of every run, in the order they start."""
        return [row["run_id"] for row in self.rows]

    @property
    def logs(self) -> Path:
        """The folder of Navigate's log of each run and of horizon.log."""
        return self.folder / "logs"

    @property
    def horizon_log(self) -> Path:
        """Horizon's own log, with the command of every run; run and collect append to it."""
        return self.logs / "horizon.log"

    @property
    def archive(self) -> Path:
        """The compacted file with every row of every run."""
        return self.folder / f"{self.name}_archive.parquet"

    @property
    def lantern(self) -> Path:
        """The compacted file for Lantern: per scenario combination the representative run and the summary."""
        return self.folder / f"{self.name}.parquet"

    @property
    def results_folder(self) -> Path:
        """The folder of the per-run results, until the study is compacted."""
        return self.folder / RESULTS

    def results(self, run_id: str) -> Path:
        """Return the results file of a run."""
        return self.results_folder / f"{run_id}.parquet"

    def row(self, run_id: str) -> Run:
        """Return a run by its id."""
        return self._rows_by_id[run_id]

    @cached_property
    def _rows_by_id(self) -> dict[str, Run]:
        return {row["run_id"]: row for row in self.rows}


def load_study(folder: str | Path) -> Study:
    """
    Read the study written to a folder by `horizon run`.

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


def config_text(config: StudyConfig) -> str:
    """Write the text of study.json: indented, with each run on one line, so that a run is one line to find."""
    settings = json.dumps({key: value for key, value in config.items() if key != "runs"}, indent=2)
    runs = ",\n".join(f"    {json.dumps(run)}" for run in config["runs"])
    return f'{settings[:-2]},\n  "runs": [\n{runs}\n  ]\n}}\n'


def study_config(hor: HorFile, rows: list[Run], navigate: str, navigate_args: list[str]) -> StudyConfig:
    """Gather what study.json holds."""
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


def prepare_study(hor: HorFile, rows: list[Run], navigate: str, navigate_args: list[str]) -> Study:
    """
    Write study.json, after clearing what an earlier run of the study left behind.

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
