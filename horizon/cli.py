# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The horizon command: run, check and collect."""

import argparse
import logging
import shlex
import shutil
import sys
from pathlib import Path

from horizon.exceptions import HorizonError, StudyError, error_line

logger = logging.getLogger(__name__)

_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def _positive_int(value):
    """argparse type: an integer >= 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {number}")
    return number


def _parser():
    parser = argparse.ArgumentParser(prog="horizon", description="Uncertainty studies of Navigate: sample a .hor "
                                                                 "study, run it through any queue, compact it.")
    commands = parser.add_subparsers(dest="command_name", required=True, metavar="COMMAND")

    run = commands.add_parser("run", help="sample a study and start its runs")
    run.add_argument("hor", help="the .hor file")
    run.add_argument("--command", metavar="PREFIX",
                     help="hand each run to a scheduler through this prefix: 'pueue' (with options of pueue add, "
                          "e.g. 'pueue -g horizon') queues the runs and, after them, `horizon collect`; any other "
                          "prefix only hands over the runs, e.g. 'sbatch --parsable --wrap {cmd}' ({cmd}: the "
                          "Navigate command as one argument, {run}: the run id, {study}: the study folder); without "
                          "it the runs run here")
    run.add_argument("--write-ids", action="store_true",
                     help="with a --command other than pueue: write what each submission prints, the job id when "
                          "asked for just that, to job_ids.txt in the study folder, for a job that waits on the runs")
    run.add_argument("--full-task-env", action="store_true",
                     help="with --command pueue: give each task the whole environment, not only what a run needs "
                          "(PATH, HOME, Python, licensing, thread limits, ...; HORIZON_TASK_ENV=NAME,... adds "
                          "variables)")
    run.add_argument("-j", "--jobs", type=_positive_int, metavar="N",
                     help="runs at a time without --command (default: one per CPU, as a run uses one)")
    run.add_argument("--samples", type=_positive_int, metavar="N",
                     help="N samples instead of NumberOfSamples, without editing the file")
    run.add_argument("--navigate", default="", metavar="ARGS",
                     help="extra arguments for every Navigate run, e.g. '-s -d ./assumptions --solver highs'")
    run.add_argument("--dry-run", action="store_true",
                     help="run and write nothing: print study.json, the settings and every run with its "
                          "values, and log how the first run would start")

    check = commands.add_parser("check", help="validate a study and cross-check its simulation files")
    check.add_argument("hor", help="the .hor file")

    collect = commands.add_parser("collect", help="compact a study's results once its runs have ended")
    collect.add_argument("study", help="the .hor file, or its study folder")
    collect.add_argument("--partial", action="store_true",
                         help="compact the runs that have results so far and keep the per-run files")

    return parser


def main():
    """Entry point of the horizon command."""
    parser = _parser()
    args = parser.parse_args()
    if getattr(args, "write_ids", False):
        from horizon.launch import pueue_options

        if args.command is None:
            parser.error("--write-ids needs --command")

        if pueue_options(args.command) is not None:
            parser.error("--write-ids is for prefixes other than pueue, which queues the collect step itself")

    if getattr(args, "full_task_env", False):
        from horizon.launch import pueue_options

        if args.command is None or pueue_options(args.command) is None:
            parser.error("--full-task-env is for --command pueue; other prefixes pass on the whole environment")

    target = getattr(args, "hor", None) or getattr(args, "study", "horizon")
    logging.basicConfig(level=logging.INFO, format=_FORMAT)

    try:
        code = _COMMANDS[args.command_name](args)
    except HorizonError as error:
        sys.stderr.write(error_line(target, error) + "\n")
        code = 1

    sys.exit(code)


def _run(args) -> int:
    from horizon.launch import dry_run, start
    from horizon.parser import read_hor
    from horizon.study import Study, config_text, prepare_study, sample_runs, study_config

    hor = read_hor(args.hor)
    for location, message in hor.warnings:
        logger.warning("%s: %s", location, message)

    number_of_samples = args.samples or hor.number_of_samples
    if args.samples:
        logger.info("NumberOfSamples overridden by --samples: %d (the file says %d)", args.samples,
                    hor.number_of_samples)

    rows = sample_runs(hor, number_of_samples)
    navigate = shutil.which("navigate")
    navigate_args = shlex.split(args.navigate)

    if args.dry_run:
        config = study_config(hor, rows, navigate or "navigate", navigate_args)
        sys.stdout.write(config_text(config))

        logger.info("Dry run of %s: %d scenario combination(s) x %d samples = %d runs; nothing is written or run",
                    hor.name, len(hor.combinations), number_of_samples, len(rows))

        dry_run(Study(hor.study_folder, config), args.command, args.full_task_env)
        return 0

    if navigate is None:
        raise StudyError("navigate is not on PATH; activate Navigate's environment")

    study = prepare_study(hor, rows, navigate, navigate_args)
    _log_to(study.logs / "horizon.log")

    logger.info("Study %s: %d scenario combination(s) x %d samples = %d runs in %s", hor.name,
                len(hor.combinations), number_of_samples, len(rows), study.folder)

    return start(study, args.command, args.jobs, args.write_ids, args.full_task_env)


def _check(args) -> int:
    from horizon.check import run_check

    text, code = run_check(args.hor)
    sys.stdout.write(text)
    return code


def _collect(args) -> int:
    from horizon.collect import collect

    study = _study(args.study)
    _log_to(study.logs / "horizon.log")
    if study.archive.exists() and not study.results_folder.exists():
        logger.info("%s is compacted already, into %s and %s", study.folder, study.archive, study.lantern)
        return 0

    representatives = collect(study, partial=args.partial)
    for label, run_id in representatives.items():
        logger.info("%s is represented by %s", label, run_id)

    return 0


def _study(argument):
    """The study of a collect argument: the .hor file, or the study folder it names."""
    from horizon.parser import read_hor
    from horizon.study import load_study

    path = Path(argument)
    return load_study(read_hor(path).study_folder if path.suffix == ".hor" else path)


def _log_to(file):
    """Log to the console (INFO) and to file (DEBUG), appending, as run and collect share it."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    for handler, level in ((logging.FileHandler(file, encoding="utf-8"), logging.DEBUG),
                           (logging.StreamHandler(), logging.INFO)):
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)

    logger.info("Logging to %s (DEBUG), console (INFO)", file)


_COMMANDS = {"run": _run, "check": _check, "collect": _collect}
