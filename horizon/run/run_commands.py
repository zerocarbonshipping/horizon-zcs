# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Queuing and execution of NavigaTE simulation commands via pueue."""

import concurrent.futures
import json
import logging
import os
import subprocess
from collections import Counter
from typing import List

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "low": -5,
    "normal": 0,
    "high": 5,
}

_PROGRESS_HEARTBEAT_INTERVAL = 50


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


def _queue_single_command(command: str, pueue_priority: int, env_vars: str) -> tuple:
    """Queue a single command via pueue.

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

        result = subprocess.run(pueue_cmd, capture_output=True, text=True)

        if result.returncode == 0:
            return (True, "")
        else:
            return (False, result.stderr)
    except Exception as e:
        return (False, str(e))


def run_commands(commands: List[str], priority: str = "normal") -> None:
    """
    Run multiple navigation commands using pueue with adaptive thread allocation.

    Thread count per task is computed as ``max(2, cpu_count // num_tasks)``,
    capped at ``cpu_count``. This gives small runs more threads per task while
    preventing over-subscription on large runs.

    Each task is labelled with its realization folder name for readable
    ``pueue status`` output.

    Parameters
    ----------
    commands : List[str]
        List of navigation commands to execute.
    priority : str
        Pueue scheduling priority ('low', 'normal', or 'high').
    """
    pueue_priority = PRIORITY_MAP[priority]

    # Adaptive thread count based on job count vs available cores
    cpu_count = os.cpu_count() or 4
    num_tasks = len(commands)
    threads_per_task = max(2, cpu_count // max(num_tasks, 1))
    threads_per_task = min(threads_per_task, cpu_count)

    env_vars = (
        f"OMP_NUM_THREADS={threads_per_task} "
        f"MKL_NUM_THREADS={threads_per_task} "
        f"NUMEXPR_NUM_THREADS={threads_per_task}"
    )
    logger.info(
        "Queuing %d tasks with %d threads each (%d CPUs available)",
        num_tasks, threads_per_task, cpu_count,
    )

    queued_ok = 0
    queued_fail = 0

    max_workers = min(16, cpu_count)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cmd = {
            executor.submit(_queue_single_command, command, pueue_priority, env_vars): command
            for command in commands
        }

        completed = 0
        for future in concurrent.futures.as_completed(future_to_cmd):
            command = future_to_cmd[future]
            success, error_msg = future.result()

            if success:
                logger.debug("Successfully queued command (priority=%s): %s", priority, command)
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

    if queued_fail:
        logger.warning("Queued %d/%d task(s) (priority=%s), %d failed", queued_ok, num_tasks, priority, queued_fail)
    else:
        logger.info("Successfully queued %d/%d task(s) (priority=%s)", queued_ok, num_tasks, priority)


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


def _task_is_queued(task):
    """Check if a pueue task is in the Queued state."""
    return task.get("status") == "Queued"


def _task_is_running(task):
    """Check if a pueue task is in the Running state."""
    return task.get("status") == "Running"


def _task_succeeded(task):
    """Check if a pueue task completed successfully."""
    status = task.get("status")
    if isinstance(status, dict) and "Done" in status:
        result = status["Done"]
        if isinstance(result, dict):
            return "Success" in result
        return result == "Success"
    return False


def _task_failed(task):
    """Check if a pueue task failed."""
    status = task.get("status")
    if isinstance(status, dict) and "Done" in status:
        result = status["Done"]
        if isinstance(result, dict):
            return "Success" not in result
        return result != "Success"
    return False


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
