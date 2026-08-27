# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Benchmark queue submission throughput of horizon.run.run_commands.

Submits N synthetic navigate commands through whatever ``pueue`` binary is
first on PATH and reports tasks/second.

Two useful setups:

* ``tools/benchmark/pueue-stub`` on PATH — measures Horizon's own submission
  overhead (subprocess spawning, threading) with a near-zero-cost fake pueue.
  Set PUEUE_STUB_DELAY (seconds, e.g. 0.02) to emulate daemon round-trip
  latency, and PUEUE_STUB_LOG to record every invocation for equivalence
  checks with ``manifest.py compare --unordered``.

* a real ``pueued`` with its default group paused (``pueue pause``) —
  measures true end-to-end submission throughput including the daemon's
  per-add state save. Pause first, or the tasks will actually execute.
  ``pueue reset --force`` between runs keeps the state comparable: the
  daemon rewrites its full state on every add, so a large existing task
  list slows every subsequent submission.

Usage:
    python tools/benchmark/bench_queue.py 2000
"""

import argparse
import logging
import shutil
import time

from horizon.run.run_commands import run_commands


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("count", type=int, help="number of commands to submit")
    ap.add_argument("--priority", choices=["low", "normal", "high"], default="normal")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    pueue = shutil.which("pueue")
    print(f"pueue on PATH: {pueue}")

    commands = [
        f'navigate "/bench/study/scen_sample{i:05d}/scen_sample{i:05d}.nav" --solver highs'
        for i in range(args.count)
    ]

    t0 = time.perf_counter()
    run_commands(commands, priority=args.priority)
    dt = time.perf_counter() - t0
    print(f"\nsubmitted {args.count} tasks in {dt:.2f} s  ({args.count / dt:.0f} tasks/s)")


if __name__ == "__main__":
    main()
