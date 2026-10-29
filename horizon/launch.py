# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Launch the runs of a study: each is one Navigate command, run here in a pool of processes, or handed to a
scheduler through a command prefix.

The prefix hands each run to its scheduler, e.g. `sbatch --parsable --wrap {cmd}`: {cmd} is the Navigate command
as one shell-quoted argument, {run} the run id and {study} the study folder; without {cmd} the command's words
are appended to the prefix. An --after prefix then hands over `horizon collect` the same way, with {ids} the job
ids the runs' submissions printed: one argument per id where it stands alone (`pueue add --after {ids}`), joined
by `:` inside a word (`sbatch --dependency=afterok:{ids}`). Every submission passes its scheduler only the
environment a run needs, as pueue, for one, keeps it with each task.
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

# The variables a submission passes its scheduler, which hands them to the run. pueue keeps them with each task and
# rewrites its whole state on every add, so a shell's full environment, kilobytes a task, slows every add as the
# queue grows.
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
    # the scheduler's client finding its daemon
    "PUEUE_CONFIG_PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "SLURM_CONF",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR", "OS", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
)

# Comma-separated names of variables for the submissions to pass on as well.
TASK_ENV_EXTRA = "HORIZON_TASK_ENV"


def start(study: Study, prefix: str | None, jobs: int | None, write_ids: bool, after: str | None,
          full_env: bool) -> int:
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
    after : str or None
        The --after prefix that hands over `horizon collect` after the runs, waiting on their job ids.
    full_env : bool
        Give the submissions the whole environment, not only _TASK_ENV.
    """
    run_ids = study.run_ids
    if prefix is None:
        jobs = min(jobs or os.cpu_count() or 1, len(run_ids))

        logger.info("Running %d run(s), %d at a time", len(run_ids), jobs)

        failed = _run_local(study, run_ids, jobs)
        _report_failures(study, failed)
        return 1 if failed else 0

    env = _task_env(full_env)
    logger.debug("The submissions get %s", _describe_env(env))

    refused, printed = _submit(prefix, study, run_ids, write_ids, env)
    logger.info("Submitted %d of %d run(s)%s", len(run_ids) - len(refused), len(run_ids),
                f", their job ids in {study.folder / JOB_IDS}" if write_ids else "")

    if after and not refused:
        ids = _job_ids(printed)
        job = _submit_collect(study, after, ids, env)

        logger.info("Submitted `horizon collect` after the %d run(s): %s", len(ids), job)
        return 0

    if after:
        logger.error("The collect step is not submitted, as %d run(s) were not submitted", len(refused))

    if len(refused) < len(run_ids):
        logger.info("Once they have ended, `horizon collect %s` compacts them", study.config["hor"])

    return 1 if refused else 0


def dry_run(study: Study, prefix: str | None, after: str | None, full_env: bool) -> None:
    """Log how the first run would start, how the collect step would be handed over after the runs, and the
    environment the submissions would get."""
    run_id = study.run_ids[0]
    command = prefixed(prefix, study, run_id) if prefix else navigate_command(study, run_id)
    logger.info("%s would start as: %s", run_id, shlex.join(command))

    if after:
        logger.info("After the runs: %s", shlex.join(collect_command(study, after, ["ID1", "ID2"])))

    if prefix:
        logger.info("The submissions would get %s", _describe_env(_task_env(full_env)))


def navigate_command(study: Study, run_id: str) -> list[str]:
    """The Navigate command of a run: its tokens as --set, its name, and where its log and results go."""
    row = study.row(run_id)
    values = [f"{token}={value}" for token, value in row["values"].items()]
    return [study.config["navigate"], study.config["unc"], *[part for value in values for part in ("--set", value)],
            "--name", run_id, "--output-dir", str(study.folder), *study.config["navigate_args"]]


def _task_env(full_env: bool) -> dict[str, str] | None:
    """The environment a submission passes its scheduler: the variables of _TASK_ENV and HORIZON_TASK_ENV that are
    set, or None, the whole environment, with full_env."""
    if full_env:
        return None

    names = [*_TASK_ENV, *(name.strip() for name in os.environ.get(TASK_ENV_EXTRA, "").split(","))]
    return {name: os.environ[name] for name in names if name in os.environ}


def _describe_env(env: dict[str, str] | None) -> str:
    """Which variables the submissions get, for the log."""
    if env is None:
        return "the whole environment (--full-task-env)"

    return f"only {', '.join(sorted(env))} ({TASK_ENV_EXTRA}=NAME,... adds variables, --full-task-env gives all)"


def prefixed(prefix: str, study: Study, run_id: str) -> list[str]:
    """The command that hands one run to a scheduler through prefix."""
    return _fill(prefix, navigate_command(study, run_id), {"{run}": run_id, "{study}": str(study.folder)})


def collect_command(study: Study, after: str, ids: list[str]) -> list[str]:
    """The command that hands `horizon collect` of the study to a scheduler through the --after prefix, waiting on
    the job ids of its runs."""
    collect = [sys.executable, "-m", "horizon", "collect", str(study.folder)]
    return _fill(after, collect, {"{study}": str(study.folder)}, ids)


def _fill(template: str, command: list[str], values: dict[str, str], ids: list[str] | None = None) -> list[str]:
    """The words of template with values and ids filled in, and command as {cmd}, one shell-quoted argument, or
    appended without it. {ids} standing alone is one word per id (`pueue add --after {ids}`), and inside a word
    the ids joined by `:` (`sbatch --dependency=afterok:{ids}`)."""
    words = []
    for word in shlex.split(template):
        if ids is not None and word == "{ids}":
            words.extend(ids)
            continue

        for name, value in values.items():
            word = word.replace(name, value)

        words.append(word if ids is None else word.replace("{ids}", ":".join(ids)))

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


def _job_ids(printed: list[str]) -> list[str]:
    """The job ids of the runs from what their submissions printed, each up to a `;` (`sbatch --parsable` adds the
    cluster after one)."""
    ids = [text.split(";")[0].strip() for text in printed]
    for text, job in zip(printed, ids):
        if len(job.split()) != 1:
            raise StudyError(f"the collect step is not submitted, as a submission printed {text!r}, not a job id; "
                             "ask the scheduler for just the id (pueue add -p, sbatch --parsable), and run "
                             "`horizon collect` once the runs have ended")

    return ids


def _submit_collect(study: Study, after: str, ids: list[str], env: dict[str, str] | None) -> str:
    """Hand `horizon collect` to a scheduler through the --after prefix with env, waiting on ids; returns what the
    submission printed."""
    command = collect_command(study, after, ids)
    logger.debug("Submitting the collect step: %s", shlex.join(command))
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", env=env)
    except OSError as error:
        raise StudyError(f"cannot submit the collect step: {error}") from error

    if result.returncode:
        raise StudyError(f"submitting the collect step failed: {result.stderr.strip() or f'exit {result.returncode}'}")

    return result.stdout.strip()
