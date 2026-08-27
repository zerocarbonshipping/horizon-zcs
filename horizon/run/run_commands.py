# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Queuing and execution of NavigaTE simulation commands via pueue."""

import concurrent.futures
import json
import logging
import os
import queue
import subprocess
import threading
from collections import Counter
from typing import List

from horizon.run import pueue_client
from horizon.run.pueue_client import PueueDirectError

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "low": -5,
    "normal": 0,
    "high": 5,
}

_PROGRESS_HEARTBEAT_INTERVAL = 50

# Environment variables forwarded to pueue tasks by default. pueue stores the
# submitting client's *entire* environment inside every task and rewrites the
# whole task list to disk on every add, so on typical shells each task drags
# multiple kilobytes of unrelated variables into the daemon state - the state
# file grows with it, and every subsequent add, status call, and horizon
# --status slows down. This whitelist keeps what a navigate run (and the pueue
# client itself) actually needs; extend per setup with HORIZON_TASK_ENV, or
# disable trimming entirely with `horizon --full-task-env`.
_TASK_ENV_WHITELIST = (
    # process basics and shells
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "TZ",
    # locale
    "LANG", "LC_ALL", "LC_CTYPE",
    # dynamic linking and Python resolution
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME",
    # Python environments
    "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_EXE",
    # Navigate and solver licensing
    "ASSUMPTIONS_DATA_DIR", "GRB_LICENSE_FILE", "GUROBI_HOME",
    # pueue client and config discovery
    "PUEUE_CONFIG_PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
    # Windows process and client basics
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR", "OS",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
)

# Comma-separated variable names in this env var are forwarded in addition to
# the whitelist (e.g. HPC module systems, proxies, extra license servers).
_TASK_ENV_EXTRA_VAR = "HORIZON_TASK_ENV"


def _utf8_encodable(text):
    """True when text encodes as UTF-8 (no surrogates from surrogateescape)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _build_task_env(full_task_env=False):
    """Build the environment for `pueue add` subprocesses.

    Returns None with ``full_task_env=True`` (inherit everything, pueue's
    stock behavior), otherwise the whitelist plus any HORIZON_TASK_ENV
    additions. pueue captures the client environment as the task
    environment, so trimming the client env is what trims the per-task
    payload stored in the daemon state.
    """
    if full_task_env:
        return None
    env = {key: os.environ[key] for key in _TASK_ENV_WHITELIST if key in os.environ}
    extra = os.environ.get(_TASK_ENV_EXTRA_VAR, "")
    for name in (part.strip() for part in extra.split(",")):
        if name and name in os.environ:
            env[name] = os.environ[name]
    return env


def _extract_label(command: str) -> str:
    """Extract realization folder name from a navigate command for pueue label.

    Parameters
    ----------
    command : str
        A navigate command, e.g. ``navigate "/abs/path/scenario_sample001/scenario_sample001.nav" ...``
        or a replot command, e.g. ``navigate -r "/abs/path/scenario_sample001"``

    Returns
    -------
    str
        The folder name (e.g. ``scenario_sample001``), or empty string on failure.
    """
    try:
        path_part = command.split('"')[1]
        if os.path.splitext(path_part)[1]:
            # Has file extension (e.g. .nav) → use parent directory name
            return os.path.basename(os.path.dirname(path_part))
        else:
            # No extension (directory path, e.g. replot) → use directory name
            return os.path.basename(path_part)
    except (IndexError, OSError):
        return ""


def _queue_single_command(command: str, pueue_priority: int, env_vars: str, task_env=None) -> tuple:
    """Queue a single command via pueue.

    ``task_env`` is the environment for the pueue client subprocess - pueue
    stores the client's environment as the task's environment, so this is
    what the navigate run will see. None inherits the full parent env.

    Returns
    -------
    tuple[bool, str]
        (success, error_message)
    """
    try:
        label = _extract_label(command)
        pueue_cmd = ['pueue', 'add', '--priority', str(pueue_priority)]
        if label:
            pueue_cmd += ['--label', label]
        pueue_cmd += ['--', f'{env_vars} {command}']

        result = subprocess.run(pueue_cmd, capture_output=True, text=True, env=task_env)

        if result.returncode == 0:
            return (True, "")
        else:
            return (False, result.stderr)
    except Exception as e:
        return (False, str(e))


class _DirectSubmitter:
    """Streams Add requests over one direct daemon connection.

    A single background thread owns the connection (the protocol is strict
    request/response). Any failure marks the submitter broken and the
    commands not yet sent are handed back for CLI resubmission — the fast
    path never loses a task. The one command *in flight* when the
    connection breaks is special: the daemon persists a task before
    acknowledging it (pueue's add handler saves state, then responds), so
    that command may or may not already be queued and is returned
    separately for the caller to disambiguate rather than blindly
    resubmit — a duplicate would run the same realization twice.
    """

    _SENTINEL = object()

    def __init__(self, pueue_priority, envs, cwd, expected_total):
        # Raises PueueDirectError when no daemon is reachable.
        self._conn = pueue_client.connect()
        self._pueue_priority = pueue_priority
        self._envs = envs
        self._cwd = cwd
        self._expected = expected_total
        self._queue = queue.SimpleQueue()
        self._ok = 0
        self._failures = []
        self._leftover = []
        self._unknown = None
        self._error = None
        self._thread = threading.Thread(target=self._run, name="pueue-direct-submit", daemon=True)
        self._thread.start()

    def submit(self, command):
        self._queue.put(command)

    def finish(self):
        """Wait for the worker.

        Returns (ok_count, failures, leftover, unknown, error): ``leftover``
        was never sent and is safe to resubmit; ``unknown`` (a single
        command or None) was in flight when the connection broke and may
        already be queued.
        """
        self._queue.put(self._SENTINEL)
        self._thread.join()
        self._conn.close()
        return self._ok, self._failures, self._leftover, self._unknown, self._error

    def _run(self):
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                return
            if self._error is not None:
                self._leftover.append(item)
                continue
            try:
                success, error_msg = self._conn.add_task(
                    item, self._cwd, self._envs, self._pueue_priority, _extract_label(item))
            except PueueDirectError as exc:
                # Connection-level failure mid-request: the daemon may have
                # committed this task before the acknowledgement was lost.
                self._error = exc
                self._unknown = item
                continue
            except Exception as exc:
                # Anything unexpected (e.g. a command or environment value
                # that is not valid UTF-8 failing CBOR encoding, before any
                # bytes reach the daemon). The worker must never die
                # silently: mark the path broken and hand this and all
                # remaining commands back for CLI resubmission.
                self._error = PueueDirectError(f"unexpected error in direct submission: {exc!r}")
                self._leftover.append(item)
                continue
            if success:
                self._ok += 1
                if (self._expected > _PROGRESS_HEARTBEAT_INTERVAL
                        and self._ok % _PROGRESS_HEARTBEAT_INTERVAL == 0
                        and self._ok < self._expected):
                    logger.info("Queuing progress: %d/%d tasks submitted...", self._ok, self._expected)
            else:
                self._failures.append((item, error_msg))


class StreamingQueuer:
    """Submits navigate commands to pueue as they are produced.

    Lets queuing overlap file generation: the file handler calls ``submit()``
    the moment each .nav is written, so the first simulations start while the
    rest of the study is still being generated, and total wall time becomes
    max(generation, queuing) instead of their sum.

    Submission goes over a direct connection to the pueue daemon when one is
    reachable (one connection for the whole study - no per-task process
    spawn or client handshake); otherwise, and on any protocol failure, over
    the pueue CLI. ``pueue_cli=True`` forces the CLI path.

    Protocol: ``start(expected_total)`` once (the per-task thread-limit
    environment is derived from the expected task count, exactly like the
    batch path always did), then ``submit(command)`` per task, then
    ``finish()`` to wait for all submissions and log the summary.

    Thread count per task is computed as ``max(2, cpu_count // num_tasks)``,
    capped at ``cpu_count``. This gives small runs more threads per task
    while preventing over-subscription on large runs.
    """

    def __init__(self, priority: str = "normal", full_task_env: bool = False,
                 pueue_cli: bool = False):
        self._priority = priority
        self._pueue_priority = PRIORITY_MAP[priority]
        self._executor = None
        self._future_to_cmd = {}
        self._env_vars = ""
        self._expected = 0
        self._full_task_env = full_task_env
        self._task_env = _build_task_env(full_task_env)
        self._force_cli = pueue_cli
        self._direct = None

    def start(self, expected_total: int) -> None:
        """Size the per-task thread env for ``expected_total`` tasks and
        start accepting submissions."""
        cpu_count = os.cpu_count() or 4
        threads_per_task = max(2, cpu_count // max(expected_total, 1))
        threads_per_task = min(threads_per_task, cpu_count)

        self._env_vars = (
            f"OMP_NUM_THREADS={threads_per_task} "
            f"MKL_NUM_THREADS={threads_per_task} "
            f"NUMEXPR_NUM_THREADS={threads_per_task}"
        )
        self._expected = expected_total
        logger.info(
            "Queuing %d tasks with %d threads each (%d CPUs available)",
            expected_total, threads_per_task, cpu_count,
        )

        if not self._force_cli:
            # The direct path sets the task environment explicitly, so the
            # thread limits travel as environment entries instead of a
            # POSIX-shell prefix in the command string.
            direct_envs = dict(os.environ) if self._full_task_env else dict(self._task_env)
            direct_envs.update(
                OMP_NUM_THREADS=str(threads_per_task),
                MKL_NUM_THREADS=str(threads_per_task),
                NUMEXPR_NUM_THREADS=str(threads_per_task),
            )
            # The protocol is CBOR, which requires valid UTF-8. Environment
            # values read through surrogateescape (invalid bytes in the
            # shell env) cannot be encoded - drop those entries instead of
            # letting the first Add break the whole direct path.
            direct_envs = {key: value for key, value in direct_envs.items()
                           if _utf8_encodable(key) and _utf8_encodable(value)}
            try:
                self._direct = _DirectSubmitter(
                    self._pueue_priority, direct_envs, os.getcwd(), expected_total)
                logger.info("Submitting over a direct pueue daemon connection "
                            "(protocol %s).", self._direct._conn.daemon_version)
            except PueueDirectError as exc:
                logger.debug("Direct pueue submission unavailable (%s); using the pueue CLI.", exc)

        if self._direct is None:
            self._start_cli_executor()

    def _start_cli_executor(self):
        if self._executor is None:
            cpu_count = os.cpu_count() or 4
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(16, cpu_count))

    def submit(self, command: str) -> None:
        """Queue one command; ``start()`` must have been called."""
        if self._direct is not None:
            self._direct.submit(command)
            return
        self._submit_cli(command)

    def _submit_cli(self, command: str) -> None:
        future = self._executor.submit(
            _queue_single_command, command, self._pueue_priority, self._env_vars, self._task_env)
        self._future_to_cmd[future] = command

    def finish(self) -> tuple:
        """Wait for all submissions, log the outcome, and return
        ``(queued_ok, queued_fail)``. Safe to call when ``start()`` never
        ran (returns ``(0, 0)``)."""
        queued_ok = 0
        queued_fail = 0

        if self._direct is not None:
            direct_ok, failures, leftover, unknown, error = self._direct.finish()
            self._direct = None
            queued_ok += direct_ok
            for command, error_msg in failures:
                logger.error("Failed to queue command: %s", command)
                if error_msg:
                    logger.error("Pueue error: %s", error_msg)
                queued_fail += 1
            if error is not None:
                if leftover or unknown:
                    logger.warning(
                        "Direct pueue submission failed mid-stream (%s); "
                        "resubmitting %d undelivered task(s) via the pueue CLI.",
                        error, len(leftover))
                else:
                    logger.warning("Direct pueue submission failed (%s).", error)
            if unknown is not None:
                # The daemon persists a task before acknowledging it, so a
                # command whose acknowledgement was lost may already be
                # queued: resubmitting it blindly could run the same
                # realization twice. Ask the daemon whether the label exists
                # and only resubmit when it provably is not queued.
                label = _extract_label(unknown)
                verdict = _task_with_label_exists(label)
                if verdict is True:
                    logger.warning(
                        "Task %r was in flight when the daemon connection broke; pueue "
                        "reports it queued, so it is not resubmitted.", label)
                    queued_ok += 1
                elif verdict is False:
                    logger.warning(
                        "Task %r was in flight when the daemon connection broke and pueue "
                        "does not report it; resubmitting it via the CLI.", label)
                    leftover = [unknown] + leftover
                else:
                    logger.error(
                        "Task %r was in flight when the daemon connection broke and its "
                        "state could not be determined. It is NOT resubmitted to avoid a "
                        "duplicate run - check `pueue status` and requeue it manually if "
                        "missing: %s", label, unknown)
                    queued_fail += 1
            if leftover:
                self._start_cli_executor()
                for command in leftover:
                    self._submit_cli(command)

        if self._executor is None:
            if queued_ok or queued_fail:
                self._log_summary(queued_ok, queued_fail)
            return (queued_ok, queued_fail)

        completed = 0
        num_tasks = len(self._future_to_cmd)

        for future in concurrent.futures.as_completed(self._future_to_cmd):
            command = self._future_to_cmd[future]
            success, error_msg = future.result()

            if success:
                logger.debug("Successfully queued command (priority=%s): %s", self._priority, command)
                queued_ok += 1
            else:
                logger.error("Failed to queue command: %s", command)
                if error_msg:
                    logger.error("Pueue error: %s", error_msg)
                queued_fail += 1

            completed += 1
            # Progress heartbeat for large batches
            if (num_tasks > _PROGRESS_HEARTBEAT_INTERVAL
                    and completed % _PROGRESS_HEARTBEAT_INTERVAL == 0
                    and completed < num_tasks):
                logger.info("Queuing progress: %d/%d tasks submitted...", completed, num_tasks)

        self._executor.shutdown(wait=True)
        self._executor = None
        self._future_to_cmd = {}

        self._log_summary(queued_ok, queued_fail)
        return (queued_ok, queued_fail)

    def _log_summary(self, queued_ok, queued_fail):
        num_tasks = queued_ok + queued_fail
        if queued_fail:
            logger.warning("Queued %d/%d task(s) (priority=%s), %d failed",
                           queued_ok, num_tasks, self._priority, queued_fail)
        else:
            logger.info("Successfully queued %d/%d task(s) (priority=%s)",
                        queued_ok, num_tasks, self._priority)


def run_commands(commands: List[str], priority: str = "normal", full_task_env: bool = False,
                 pueue_cli: bool = False) -> None:
    """
    Run multiple navigation commands using pueue with adaptive thread allocation.

    Batch form of :class:`StreamingQueuer` (same submission engine, same
    thread-limit environment, same logging): submit everything, then wait.

    Each task is labelled with its realization folder name for readable
    ``pueue status`` output.

    Parameters
    ----------
    commands : List[str]
        List of navigation commands to execute.
    priority : str
        Pueue scheduling priority ('low', 'normal', or 'high').
    full_task_env : bool
        Forward the entire environment to each task instead of the
        whitelist (see _TASK_ENV_WHITELIST / HORIZON_TASK_ENV).
    pueue_cli : bool
        Force submission through the pueue CLI instead of the direct
        daemon connection.
    """
    queuer = StreamingQueuer(priority=priority, full_task_env=full_task_env, pueue_cli=pueue_cli)
    queuer.start(len(commands))
    for command in commands:
        queuer.submit(command)
    queuer.finish()


def check_status(output_folder):
    """Check current pueue task status and report failures (non-blocking).

    Queries ``pueue status --json`` once, prints a summary of task states,
    and for any failed tasks, tails the last 30 lines of the NavigaTE log
    from each realization folder, grouping similar errors.

    Parameters
    ----------
    output_folder : str
        The output directory where realization folders are located.

    Usage::

        horizon --status /path/to/output_dir
    """
    tasks = _get_pueue_tasks()
    if tasks is None:
        logger.error("Could not retrieve pueue status. Is pueue running?")
        return

    if not tasks:
        logger.info("No pueue tasks found.")
        return

    # Categorize tasks
    queued = [t for t in tasks if _task_is_queued(t)]
    running = [t for t in tasks if _task_is_running(t)]
    succeeded = [t for t in tasks if _task_succeeded(t)]
    failed_tasks = [t for t in tasks if _task_failed(t)]
    other = len(tasks) - len(queued) - len(running) - len(succeeded) - len(failed_tasks)

    logger.info("--- Pueue Task Status ---")
    logger.info("Total: %d | Queued: %d | Running: %d | Succeeded: %d | Failed: %d",
                len(tasks), len(queued), len(running), len(succeeded), len(failed_tasks))
    if other > 0:
        logger.info("Other (stashed/locked/etc): %d", other)

    if failed_tasks:
        _report_failures(failed_tasks, output_folder)
    elif not queued and not running:
        logger.info("All tasks completed successfully.")
    else:
        logger.info("Tasks still in progress.")


def _task_with_label_exists(label):
    """Ask the daemon (via the pueue CLI) whether a task with this label exists.

    Returns True/False, or None when the answer cannot be determined (no
    label to match, or the daemon state could not be read).
    """
    if not label:
        return None
    tasks = _get_pueue_tasks()
    if tasks is None:
        return None
    return any(task.get("label") == label for task in tasks)


def _get_pueue_tasks():
    """Retrieve current pueue tasks as a list of dicts."""
    try:
        result = subprocess.run(
            ["pueue", "status", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        # pueue status --json returns {"tasks": {id: task, ...}}
        tasks_dict = data.get("tasks", {})
        return list(tasks_dict.values())
    except Exception:
        return None


def _status_is(task, state):
    """Check a pueue task's lifecycle state across pueue JSON dialects.

    pueue 3.x reports plain strings (``"status": "Running"``); pueue 4.x
    wraps every state in a dict carrying its metadata
    (``"status": {"Running": {"enqueued_at": ...}}``).
    """
    status = task.get("status")
    if status == state:
        return True
    return isinstance(status, dict) and state in status


def _task_is_queued(task):
    """Check if a pueue task is in the Queued state."""
    return _status_is(task, "Queued")


def _task_is_running(task):
    """Check if a pueue task is in the Running state."""
    return _status_is(task, "Running")


def _done_result(task):
    """Return a finished pueue task's result payload, or None if not Done.

    pueue 3.x: ``{"Done": "Success"}`` or ``{"Done": {"Failed": 1}}``.
    pueue 4.x: ``{"Done": {..., "result": "Success"}}`` or
    ``{"Done": {..., "result": {"Failed": 1}}}``.
    """
    status = task.get("status")
    if not (isinstance(status, dict) and "Done" in status):
        return None
    done = status["Done"]
    if isinstance(done, dict) and "result" in done:
        return done["result"]
    return done


def _task_succeeded(task):
    """Check if a pueue task completed successfully."""
    result = _done_result(task)
    if result is None:
        return False
    if isinstance(result, dict):
        return "Success" in result
    return result == "Success"


def _task_failed(task):
    """Check if a pueue task failed."""
    result = _done_result(task)
    if result is None:
        return False
    if isinstance(result, dict):
        return "Success" not in result
    return result != "Success"


def _report_failures(failed_tasks, output_folder):
    """Report failed tasks, grouping by similar error output and tailing logs."""
    logger.warning("--- Failed Task Details ---")

    error_groups = Counter()
    label_by_error = {}

    for task in failed_tasks:
        label = task.get("label", "unknown")
        # Try to read the NavigaTE log from the realization folder
        log_tail = _tail_realization_log(label, output_folder)
        if log_tail:
            # Use last 5 lines as a grouping key to detect duplicate errors
            group_key = "\n".join(log_tail[-5:])
            error_groups[group_key] += 1
            if group_key not in label_by_error:
                label_by_error[group_key] = (label, log_tail)
        else:
            logger.warning("  FAILED: %s (no log file found)", label)

    for group_key, count in error_groups.items():
        label, log_tail = label_by_error[group_key]
        if count > 1:
            logger.warning("  FAILED: %s (and %d other(s) with similar error):", label, count - 1)
        else:
            logger.warning("  FAILED: %s:", label)
        for line in log_tail:
            logger.warning("    %s", line)

    logger.warning("--- End Failed Task Details ---")


def _tail_realization_log(label, output_folder, num_lines=30):
    """Read the last N lines of a realization's NavigaTE log file.

    Looks for ``<output_folder>/<label>/<label>.log``.

    Returns
    -------
    list[str] or None
        The last ``num_lines`` lines, or None if the log file doesn't exist.
    """
    if not label:
        return None
    log_path = os.path.join(output_folder, label, f"{label}.log")
    if not os.path.isfile(log_path):
        return None
    try:
        with open(log_path, 'r') as fh:
            lines = fh.readlines()
        return [line.rstrip() for line in lines[-num_lines:]]
    except Exception:
        return None
