# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Benchmark Horizon's file-creation pipeline end to end.

Runs ``create_files()`` in-process against a study (typically produced by
``make_study.py``) and reports wall time per phase: parse, sampling, CSV
export, NAV generation, and queuing. Optionally wraps the run in cProfile.

The queue phase defaults to ``--via cli``: it talks to whatever ``pueue``
binary is first on PATH — put ``tools/benchmark/pueue-stub`` there (and name
its variables in ``HORIZON_TASK_ENV=PUEUE_STUB_LOG,PUEUE_STUB_DELAY``, since
the minimal task environment would otherwise strip them) to measure Horizon's
own submission overhead without a daemon. ``--via auto`` allows the direct
daemon connection instead; use it only when you intend to submit the
synthetic tasks to a real (paused) daemon.

Relative paths inside the .hor are resolved from the current working
directory, so run this from the study directory:

    cd /tmp/study
    python /path/to/tools/benchmark/bench_generation.py study.hor \
        --output-dir /tmp/study_out --no-queue --profile
"""

import argparse
import cProfile
import io
import logging
import pstats
import time

import horizon.test_manager.create_files as cf
from horizon.file_handler.file_handler import FileHandler
from horizon.run import run_commands as rc

PHASES = {}


def _timed(name, fn):
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            PHASES[name] = PHASES.get(name, 0.0) + (time.perf_counter() - t0)
    return wrapper


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hor_file")
    ap.add_argument("--output-dir", required=True,
                    help="directory for generated realization folders")
    ap.add_argument("--no-queue", action="store_true", help="skip queuing entirely")
    ap.add_argument("--via", choices=["cli", "auto"], default="cli",
                    help="submission path. Default 'cli' keeps the benchmark on the pueue "
                         "executable found on PATH (so the recording stub actually intercepts "
                         "it); 'auto' allows the direct daemon connection - only use it when "
                         "you intend to submit to a real daemon.")
    ap.add_argument("--profile", action="store_true", help="wrap the run in cProfile")
    ap.add_argument("--profile-out", default=None, help="also dump pstats data to this file")
    ap.add_argument("--top", type=int, default=30, help="profile rows to print")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)

    # Instrument the seams create_files already has. This is a dev tool; the
    # monkeypatching mirrors the call graph in create_files() and needs
    # updating if those seams move.
    #
    # Queuing is streamed: submissions happen during the generate phase, so
    # "generate" includes overlapped submission work and "queue(drain)" is
    # only the wait for outstanding submissions at the end. TOTAL is the
    # honest cross-commit comparison number.
    cf.parse_hor_file = _timed("parse", cf.parse_hor_file)
    cf.sample_parameters = _timed("sample", cf.sample_parameters)
    cf.output_sampled_parameters_to_csv = _timed("csv", cf.output_sampled_parameters_to_csv)
    FileHandler.generate_scenarios_and_nav_files = _timed(
        "generate", FileHandler.generate_scenarios_and_nav_files)

    if args.no_queue:
        class _NullQueuer:
            def __init__(self, *a, **kw):
                PHASES.setdefault("queue(skipped)", 0.0)

            def start(self, expected_total):
                pass

            def submit(self, command):
                pass

            def finish(self):
                return (0, 0)

        cf.StreamingQueuer = _NullQueuer
    else:
        force_cli = args.via == "cli"

        class _TimedQueuer(rc.StreamingQueuer):
            def __init__(self, **kwargs):
                if force_cli:
                    kwargs["pueue_cli"] = True
                super().__init__(**kwargs)

            def finish(self):
                t0 = time.perf_counter()
                try:
                    return super().finish()
                finally:
                    PHASES["queue(drain)"] = PHASES.get("queue(drain)", 0.0) + (
                        time.perf_counter() - t0)

        cf.StreamingQueuer = _TimedQueuer

    profiler = cProfile.Profile() if args.profile else None
    t0 = time.perf_counter()
    if profiler:
        profiler.enable()
    cf.create_files(args.hor_file, output_dir=args.output_dir)
    if profiler:
        profiler.disable()
    total = time.perf_counter() - t0

    print("\n=== phase timings ===")
    for name, dt in sorted(PHASES.items(), key=lambda kv: -kv[1]):
        print(f"  {name:16s} {dt:8.2f} s")
    print(f"  {'(other)':16s} {total - sum(PHASES.values()):8.2f} s")
    print(f"  {'TOTAL':16s} {total:8.2f} s")

    if profiler:
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(args.top)
        print(stream.getvalue())
        if args.profile_out:
            profiler.dump_stats(args.profile_out)
            print(f"profile data -> {args.profile_out}")


if __name__ == "__main__":
    main()
