# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Launch the runs of a study: each is one Navigate command, run here in a pool of processes, or handed to a
scheduler through a command prefix.

The prefix `pueue`, optionally with options of `pueue add` (`pueue -g horizon`), queues each run in pueue and,
after them, `horizon collect`. Any other prefix hands each run to its scheduler, e.g. `sbatch --wrap {cmd}`:
{cmd} is the Navigate command as one shell-quoted argument, {run} the run id and {study} the study folder;
without {cmd} the command's words are appended to the prefix. pueue keeps the environment of `pueue add` as
each task's, so the alias passes it only what a run needs; other prefixes pass on the whole environment.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from tqdm import tqdm

from horizon.exceptions import StudyError
from horizon.study import Study

logger = logging.getLogger(__name__)

_SUBMITTERS = 8

JOB_IDS = "job_ids.txt"

# The variables the pueue alias passes to `pueue add`. pueue keeps them with each task and rewrites its whole state
# on every add, so a shell's full environment, kilobytes a task, slows every add as the queue grows.
_TASK_ENV = (
    # process basics
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "TZ",
    # locale
    "LANG", "LC_ALL", "LC_CTYPE",
    # libraries and Python
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV", "CONDA_EXE",
    # Navigate, its solvers and their threads
    "ASSUMPTIONS_DATA_DIR", "GRB_LICENSE_FILE", "GUROBI_HOME", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "POLARS_MAX_THREADS",
    # the pueue client finding its daemon
    "PUEUE_CONFIG_PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR", "OS", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
)

# Comma-separated names of variables for the pueue alias to pass on as well.
TASK_ENV_EXTRA = "HORIZON_TASK_ENV"


def start(study: Study, prefix: str | None, jobs: int | None, write_ids: bool, full_env: bool) -> int:
    """Run the runs here until they end, or hand them to a scheduler; returns the exit code.

    Parameters
    ----------
    study : Study
        The study, prepared.
    prefix : str or None
        The --command prefix; None runs the runs here.
    jobs : int or None
        Runs at a time here; one per CPU by default.
    write_ids : bool
        Write what each submission prints to job_ids.txt.
    full_env : bool
        Give the tasks of the pueue alias the whole environment, not only _TASK_ENV.
    """
    run_ids = study.run_ids
    if prefix is None:
        jobs = min(jobs or os.cpu_count() or 1, len(run_ids))

        logger.info("Running %d run(s), %d at a time", len(run_ids), jobs)

        failed = _run_local(study, run_ids, jobs)
        _report_failures(study, failed)
        return 1 if failed else 0

    options = pueue_options(prefix)
    env = _task_env(full_env) if options is not None else None
    if options is not None:
        logger.debug("The pueue tasks get %s", _describe_env(env))

    refused, tasks = _submit(prefix, study, run_ids, write_ids, env)
    logger.info("Submitted %d of %d run(s)%s", len(run_ids) - len(refused), len(run_ids),
                f", their job ids in {study.folder / JOB_IDS}" if write_ids else "")

    if options is not None and not refused:
        task = _queue_collect(study, options, tasks, env)

        logger.info("Queued `horizon collect` as pueue task %s; it starts once every run has succeeded, and fails "
                    "if one fails", task)
        return 0

    if options is not None:
        logger.error("The collect step is not queued, as %d run(s) were not submitted", len(refused))

    if len(refused) < len(run_ids):
        logger.info("Once they have ended, `horizon collect %s` compacts them", study.config["hor"])

    return 1 if refused else 0


def dry_run(study: Study, prefix: str | None, full_env: bool) -> None:
    """Log how the first run would start, and the collect step the pueue alias would queue after the runs with
    the environment its tasks would get."""
    run_id = study.run_ids[0]
    command = prefixed(prefix, study, run_id) if prefix else navigate_command(study, run_id)
    logger.info("%s would start as: %s", run_id, shlex.join(command))

    options = pueue_options(prefix) if prefix else None
    if options is not None:
        logger.info("After the runs: %s", shlex.join(_pueue_collect(study, options, ["TASK_IDS"])))
        logger.info("The pueue tasks would get %s", _describe_env(_task_env(full_env)))


def navigate_command(study: Study, run_id: str) -> list[str]:
    """The Navigate command of a run: its tokens as --set, its name, and where its log and results go."""
    row = study.row(run_id)
    values = [f"{token}={value}" for token, value in row["values"].items()]
    return [study.config["navigate"], study.config["unc"], *[part for value in values for part in ("--set", value)],
            "--name", run_id, "--output-dir", str(study.folder), *study.config["navigate_args"]]


def pueue_options(prefix: str) -> list[str] | None:
    """The options of `pueue add` when prefix is the pueue alias (`pueue`, `pueue -g horizon`), else None.

    A prefix that names the subcommand (`pueue add ...`) is a prefix like any other.
    """
    words = shlex.split(prefix)
    return words[1:] if words[:1] == ["pueue"] and "add" not in words else None


def _task_env(full_env: bool) -> dict[str, str] | None:
    """The environment the pueue alias passes to `pueue add`: the variables of _TASK_ENV and HORIZON_TASK_ENV that
    are set, or None, the whole environment, with full_env."""
    if full_env:
        return None

    names = [*_TASK_ENV, *(name.strip() for name in os.environ.get(TASK_ENV_EXTRA, "").split(","))]
    return {name: os.environ[name] for name in names if name in os.environ}


def _describe_env(env: dict[str, str] | None) -> str:
    """Which variables the pueue tasks get, for the log."""
    if env is None:
        return "the whole environment (--full-task-env)"

    return f"only {', '.join(sorted(env))} ({TASK_ENV_EXTRA}=NAME,... adds variables, --full-task-env gives all)"


def prefixed(prefix: str, study: Study, run_id: str) -> list[str]:
    """The command that hands one run to a scheduler through prefix."""
    command = navigate_command(study, run_id)
    options = pueue_options(prefix)
    if options is not None:
        return ["pueue", "add", "-p", "--label", run_id, *options, "--", shlex.join(command)]

    words = [word.replace("{run}", run_id).replace("{study}", str(study.folder)) for word in shlex.split(prefix)]
    if not any("{cmd}" in word for word in words):
        return words + command

    return [word.replace("{cmd}", shlex.join(command)) for word in words]


def _run_local(study: Study, run_ids: list[str], jobs: int) -> dict[str, str]:
    """Run the runs on this machine, jobs at a time, until all have ended.

    Returns
    -------
    dict
        The last line of output of each run that failed, by run id, so runs that fail alike group together.
    """
    def run(run_id):
        command = navigate_command(study, run_id)
        logger.debug("Running %s: %s", run_id, shlex.join(command))
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", cwd=study.folder)
        except OSError as error:
            return run_id, f"cannot start {command[0]}: {error}"

        if result.returncode == 0:
            return run_id, None

        lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        return run_id, lines[-1] if lines else f"exit {result.returncode}"

    failed = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        progress = tqdm(pool.map(run, run_ids), total=len(run_ids), desc="Running", unit="run", colour="green",
                        dynamic_ncols=True, disable=None)
        for run_id, ending in progress:
            if ending is not None:
                failed[run_id] = ending
                progress.set_postfix(failed=len(failed))

    return failed


def _report_failures(study: Study, failed: dict[str, str]) -> None:
    """Log the runs that failed, grouped by the last line of their output."""
    if not failed:
        logger.info("All %d run(s) ok; `horizon collect %s` compacts them", len(study.run_ids), study.config["hor"])
        return

    groups = {}
    for run_id, ending in failed.items():
        groups.setdefault(ending, []).append(run_id)

    for ending, run_ids in sorted(groups.items(), key=lambda item: -len(item[1])):
        shown = ", ".join(run_ids[:3]) + (f" and {len(run_ids) - 3} more" if len(run_ids) > 3 else "")

        logger.error("%d run(s) failed (%s), ending with: %s", len(run_ids), shown, ending)

    logger.error("%d of %d run(s) failed; their logs are in %s, and `horizon run %s --dry-run` prints the "
                 "command of each run, to start one again", len(failed), len(study.run_ids), study.logs,
                 study.config["hor"])


def _submit(prefix: str, study: Study, run_ids: list[str], write_ids: bool,
            env: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """Hand every run to a scheduler through prefix, each submission with env as its environment (None: the whole
    one).

    What each submission prints is its job id when the scheduler is asked for just that (`pueue add -p`,
    `sbatch --parsable`). With write_ids, job_ids.txt in the study folder keeps those, one line per submitted
    run in run order, for a post-processing job that waits on them.

    Returns
    -------
    tuple of list
        The run ids whose submission failed, and what each submitted run's submission printed, in run order.
    """
    def hand_over(run_id):
        command = prefixed(prefix, study, run_id)
        logger.debug("Submitting %s: %s", run_id, shlex.join(command))
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", env=env)
        except OSError as error:
            return run_id, None, str(error)

        if result.returncode:
            return run_id, None, result.stderr.strip() or f"exit {result.returncode}"

        return run_id, result.stdout.strip(), None

    refused, jobs = [], []
    with ThreadPoolExecutor(max_workers=_SUBMITTERS) as pool:
        for run_id, job, error in tqdm(pool.map(hand_over, run_ids), total=len(run_ids), desc="Submitting",
                                       unit="run", colour="green", dynamic_ncols=True, disable=None):
            if error is None:
                jobs.append(job)
                logger.debug("%s submitted as %s", run_id, job)
                continue

            if not refused:
                logger.error("Submitting %s failed: %s", run_id, error)

            refused.append(run_id)

    if write_ids:
        (study.folder / JOB_IDS).write_text("".join(f"{job}\n" for job in jobs), encoding="utf-8")
        if any(not job or len(job.split()) > 1 for job in jobs):
            logger.warning("The scheduler printed more than a job id for some runs, so %s holds that text; ask it "
                           "for just the id (e.g. sbatch --parsable)", study.folder / JOB_IDS)

    return refused, jobs


def _pueue_collect(study: Study, options: list[str], tasks: list[str]) -> list[str]:
    """The pueue command that queues `horizon collect` of the study after the tasks of its runs."""
    collect = [sys.executable, "-m", "horizon", "collect", str(study.folder)]
    return ["pueue", "add", "-p", "--label", f"{study.name}_collect", *options, "--after", *tasks, "--",
            shlex.join(collect)]


def _queue_collect(study: Study, options: list[str], tasks: list[str], env: dict[str, str] | None) -> str:
    """Queue `horizon collect` in pueue with env, to start once every task of the runs has succeeded; returns its
    task id."""
    command = _pueue_collect(study, options, tasks)
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", env=env)
    except OSError as error:
        raise StudyError(f"cannot queue the collect step: {error}") from error

    if result.returncode:
        raise StudyError(f"queuing the collect step failed: {result.stderr.strip() or f'exit {result.returncode}'}")

    return result.stdout.strip()
