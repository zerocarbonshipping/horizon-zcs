# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry point for Horizon: dispatches to sampling, report collection, and replotting."""

import argparse
import logging
import os
import sys

from horizon.collect_reports.collect_reports import collect_reports
from horizon.logging.logging import setup_logging
from horizon.test_manager.create_files import create_files

logger = logging.getLogger(__name__)

_PKL_FILENAME = "plot_data.pkl"


def main():
    """
    Entry point for the command-line interface of the Horizon application.
    Parses command-line arguments and invokes the appropriate functions.
    """
    cli_parser = argparse.ArgumentParser(prog="horizon")

    # Add arguments
    cli_parser.add_argument("arguments", type=str, nargs="*", help="List of arguments.")

    cli_parser.add_argument("-c", "--collect", action="store_true", help="Collect reports from specified directory.")
    cli_parser.add_argument("--priority", choices=["low", "normal", "high"], default="normal",
                            help="Pueue task scheduling priority (default: normal).")
    cli_parser.add_argument("--solver", default=None, choices=["auto", "gurobi", "highs"],
                            help="Solver backend for NavigaTE: 'auto' tries Gurobi then falls back to HiGHS, "
                                 "'gurobi' prefers Gurobi (falls back to HiGHS if unlicensed), "
                                 "'highs' skips Gurobi and uses HiGHS directly. Default: auto.")
    cli_parser.add_argument("--navigate-flags", type=str, default=None,
                            help='Extra flags to pass to each navigate command (e.g. "-d ./assumptions -s")')
    cli_parser.add_argument("--output-dir", type=str, default=None,
                            help="Directory for generated scenario folders (default: next to .unc file)")
    cli_parser.add_argument("--report-name", type=str, default=None,
                            help="Only collect reports matching this filename pattern (supports * wildcards, e.g. 'summary*')")
    cli_parser.add_argument("--calibration-plot", action="store_true",
                            help="Generate calibration comparison dashboard from completed simulation results.")
    cli_parser.add_argument("--dry-run", action="store_true",
                            help="Validate configuration and preview what would be generated without running simulations.")
    cli_parser.add_argument("--status", type=str, default=None, metavar="OUTPUT_DIR",
                            help="Check pueue task status and report failures for the given output directory.")
    cli_parser.add_argument("--replot", action="store_true",
                            help="Queue replot jobs via pueue for directories containing plot_data.pkl.")
    cli_parser.add_argument("--sensitivity-analysis", nargs="?", const=True, default=None,
                            help="Run PRCC sensitivity analysis.  Optionally provide a .sen "
                                 "config file path (e.g. --sensitivity-analysis config.sen).")
    cli_parser.add_argument("--metric", type=str, action="append", default=None,
                            help="Metric for PRCC analysis (e.g., 'TotalEquivalentWTW@2050', "
                                 "'Expenses:difference', 'TotalEquivalentWTW:cumulative'). Repeatable.")
    cli_parser.add_argument("--samples-csv", type=str, default=None,
                            help="Path to the parameter samples CSV file (e.g. samples.csv).")
    cli_parser.add_argument("--report-csv", type=str, default=None,
                            help="Path to a collected report CSV (from 'horizon -c'). "
                                 "Avoids reading individual Excel reports.")

    # Parse the arguments
    args = cli_parser.parse_args()

    if args.replot:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        _replot_batch(args)
        return

    if args.status:
        # Status check mode — non-blocking check of pueue tasks with failure
        # log tailing. Dispatched before the positional-argument check since
        # the output directory arrives via --status, not as a positional.
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        from horizon.run.run_commands import check_status
        check_status(args.status)
        return

    if args.sensitivity_analysis is not None:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        from horizon.sensitivity.analyze import prcc_analysis

        # Config-file mode: --sensitivity-analysis path/to/config.sen
        if args.sensitivity_analysis is not True:
            from horizon.sensitivity.config_parser import parse_sensitivity_config
            config = parse_sensitivity_config(args.sensitivity_analysis)
            # CLI args override config-file values where provided
            samples_csv = args.samples_csv or config.samples_csv
            report_csv = args.report_csv or config.report_csv
            source_dir = (args.arguments[0] if args.arguments
                          else config.source_dir)
            output_dir = (args.arguments[1] if len(args.arguments) > 1
                          else config.output_dir)
            # CLI --metric flags override config metrics entirely
            metrics = args.metric if args.metric else (
                config.metrics if config.metrics else None
            )
        else:
            # Pure-CLI mode (backward compatible)
            if not args.samples_csv:
                logger.error(
                    "--samples-csv is required for --sensitivity-analysis "
                    "when no .sen config file is provided."
                )
                sys.exit(1)
            samples_csv = args.samples_csv
            report_csv = args.report_csv
            source_dir = args.arguments[0]
            output_dir = (args.arguments[1]
                          if len(args.arguments) > 1 else None)
            metrics = args.metric

        prcc_analysis(source_dir, metrics=metrics, output_dir=output_dir,
                      samples_csv=samples_csv, report_csv=report_csv)
        return

    if not args.arguments:
        logger.error("No .hor file paths provided.")
        sys.exit(1)

    # Set up logging to file in the directory of the .hor file
    hor_file_path = os.path.abspath(args.arguments[0])
    log_directory = os.path.dirname(hor_file_path)
    log_file_path = os.path.join(log_directory, "output.log")
    setup_logging(log_file_path)

    if args.calibration_plot:
        # Calibration dashboard mode
        from horizon.calibration.analyze import calibration_plot
        source_dir = args.arguments[0]
        output_html = args.arguments[1] if len(args.arguments) > 1 else None
        calibration_plot(source_dir, output_html)
        return

    if args.collect:
        # Collect reports mode
        if len(args.arguments) == 1:
            source_dir = os.getcwd()
            target_file = args.arguments[0]
        elif len(args.arguments) == 2:
            source_dir = args.arguments[0]
            target_file = args.arguments[1]
        else:
            logger.error("Too many arguments for collect mode.")
            sys.exit(1)

        if not os.path.isabs(target_file):
            target_file = os.path.join(source_dir, target_file)

        logger.info(f"Collecting reports from {source_dir} to {target_file}")
        collect_reports([source_dir], target_file, sort_flag=True, name_pattern=args.report_name)

    else:
        # Normal mode
        if len(args.arguments) > 1:
            logger.error("Too many arguments provided.")
            sys.exit(1)

        file_name = args.arguments[0]
        create_files(file_name, priority=args.priority, solver=args.solver,
                     navigate_flags=args.navigate_flags, output_dir=args.output_dir,
                     dry_run=args.dry_run)


def _replot_batch(args):
    """Build and queue navigate replot commands for the given directories."""
    if not args.arguments:
        logger.error("No directories provided for --replot.")
        sys.exit(1)

    commands = []
    for directory in args.arguments:
        abs_dir = os.path.abspath(directory)
        pkl_path = os.path.join(abs_dir, _PKL_FILENAME)

        if not os.path.isdir(abs_dir):
            logger.error("Directory does not exist: %s", abs_dir)
            continue

        if not os.path.isfile(pkl_path):
            logger.error("No %s found in: %s", _PKL_FILENAME, abs_dir)
            continue

        commands.append(f'navigate -r "{abs_dir}"')

    if not commands:
        logger.error("No valid directories found. Nothing to queue.")
        sys.exit(1)

    from horizon.run.run_commands import run_commands
    logger.info("Queuing %d replot command(s).", len(commands))
    run_commands(commands, priority=args.priority)


if __name__ == "__main__":
    main()
