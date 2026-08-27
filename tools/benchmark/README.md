<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Benchmark and equivalence harness

Tooling for measuring Horizon's file-generation and queue-submission
performance, and for proving that a performance change did not alter what
Horizon produces. The verification protocol that uses these tools is
described in [`docs/performance-plan.md`](../../docs/performance-plan.md).

All scripts run inside the normal dev environment (`pip install -e ".[dev]"`).

| Script | Purpose |
|--------|---------|
| `make_study.py` | Generate a synthetic study (`.hor` + `.unc` + includes) at small/medium/large scale. |
| `bench_generation.py` | Run `create_files()` against a study and report per-phase wall time (optionally cProfile). |
| `bench_queue.py` | Measure `run_commands()` submission throughput through the `pueue` on PATH. |
| `manifest.py` | Snapshot SHA-256 hashes of all generated files; compare two snapshots (`compare`), or two queue logs order-insensitively (`compare --unordered`). |
| `pueue-stub` | Recording no-op `pueue` for benchmarks without a daemon (POSIX). |
| `make_e2e_study.py` | Build a four-run real-Navigate smoke study from a navigate-zcs checkout. |

## Measuring

```bash
# 1. build a study (2000 realizations)
python tools/benchmark/make_study.py /tmp/study --preset medium --fresh

# 2. put the recording stub on PATH as `pueue` (or use a real paused daemon)
mkdir -p /tmp/stubbin && cp tools/benchmark/pueue-stub /tmp/stubbin/pueue
export PATH="/tmp/stubbin:$PATH"
export PUEUE_STUB_LOG=/tmp/queue.log

# 3. run and time it (relative paths in the .hor resolve from the cwd)
cd /tmp/study
python <repo>/tools/benchmark/bench_generation.py study.hor --output-dir /tmp/study_out

# queue throughput separately, at higher task counts
python <repo>/tools/benchmark/bench_queue.py 2000
```

For true submission throughput, run against a real daemon instead of the
stub — `pueued -d`, then `pueue pause` so the tasks don't execute, and
`pueue reset --force` between runs (the daemon rewrites its full state on
every add, so leftover tasks slow every later submission).

## Verifying a change produces identical output

```bash
# on the baseline commit
cd /tmp/study && rm -rf /tmp/study_out output /tmp/queue.log
python <repo>/tools/benchmark/bench_generation.py study.hor --output-dir /tmp/study_out
python <repo>/tools/benchmark/manifest.py snapshot /tmp/study_out output -o /tmp/golden.json
mv /tmp/queue.log /tmp/queue_golden.log

# on the candidate commit (same study, same paths)
cd /tmp/study && rm -rf /tmp/study_out output /tmp/queue.log
python <repo>/tools/benchmark/bench_generation.py study.hor --output-dir /tmp/study_out
python <repo>/tools/benchmark/manifest.py snapshot /tmp/study_out output -o /tmp/candidate.json

python <repo>/tools/benchmark/manifest.py compare /tmp/golden.json /tmp/candidate.json
python <repo>/tools/benchmark/manifest.py compare /tmp/queue_golden.log /tmp/queue.log --unordered
```

File contents and paths must match byte for byte; queue submissions are
compared as an unordered set because the generation thread pool does not
submit in a deterministic order.

> **Reproducibility note:** golden comparison relies on `RandomSeed` making
> sampling deterministic. LHS reproducibility requires the seed to reach
> `pyDOE3.lhs(seed=...)` (fixed in Phase 0.1 of the performance plan;
> `pyproject.toml` pins `pyDOE3 >= 1.5` accordingly). If a golden check
> shows every sample_4+ realization changed, suspect the sampler's seeding
> before suspecting the change under test.

## Real-Navigate smoke test

With Navigate installed in the same environment and a pueue daemon running:

```bash
python tools/benchmark/make_e2e_study.py <navigate-zcs checkout> /tmp/smoke --fresh
cd /tmp/smoke
horizon study.hor --solver highs \
    --navigate-flags "-d <navigate-zcs checkout>/assumptions -s" \
    --output-dir /tmp/smoke/out
pueue status                    # four runs, ~10 s each with HiGHS
horizon --status /tmp/smoke/out # must report no failures
horizon -c /tmp/smoke/out collected.xlsx
```

Pass criteria: four pueue tasks end in `Success`, each realization folder
contains a `report/*_output.xlsx`, `horizon --status` reports no failures,
and `horizon -c` collects all four reports into one workbook.
