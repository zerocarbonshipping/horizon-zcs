<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Horizon

[![CI](https://github.com/zerocarbonshipping/horizon-zcs/actions/workflows/ci.yml/badge.svg)](https://github.com/zerocarbonshipping/horizon-zcs/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSES/Apache-2.0.txt)

Horizon is an uncertainty analysis tool for
[Navigate](https://github.com/zerocarbonshipping/navigate-zcs), the
open-source maritime transition model. It samples uncertain input parameters
(Monte Carlo or Latin Hypercube), generates one Navigate simulation per
sample across scenario combinations, queues the runs, and aggregates the
results — turning a single Navigate scenario into an uncertainty study.

Horizon is a companion to Navigate, not a standalone tool: it prepares,
schedules, and post-processes Navigate simulations, and a working Navigate
installation is required for any actual simulation run.

## Disclaimer

Horizon is an open-source analytical tool intended for research and scenario
analysis of maritime decarbonisation pathways. Its outputs depend on the
model, assumptions, and parameter ranges selected by the user and are
provided for illustrative and analytical purposes only. They should not be
interpreted as forecasts, benchmarks, recommendations or commercially
optimal outcomes, nor as legal, financial or investment advice. Users are
responsible for selecting appropriate assumptions and for exercising their
own independent judgement when interpreting any outputs.

## Requirements

- Python >= 3.12
- [Navigate](https://github.com/zerocarbonshipping/navigate-zcs) installed
  in the same environment (Horizon invokes the `navigate` command)
- [pueue](https://github.com/Nukesor/pueue) for queuing simulation runs —
  the daemon must be running (`pueued -d`)

## Installation

Install Horizon into the same environment as Navigate:

```bash
conda activate nav       # or whatever environment Navigate lives in
pip install .
```

For development with lint and test tools:

```bash
pip install -e ".[dev]"
```

## Quick start

The bundled example runs without Navigate or pueue — it stops after
sampling (`SampleOnly = TRUE`) and writes sampled values plus sampling
diagnostics:

```bash
cd examples/uncertainty
horizon fuel_cost_study.hor
```

See [`examples/uncertainty/README.md`](examples/uncertainty/README.md) for a
walkthrough, and [`docs/configuration.md`](docs/configuration.md) for the
full configuration reference.

## Workflow

A full uncertainty study runs in three steps:

```bash
# 1. Sample parameters, generate .nav files, queue Navigate runs via pueue
horizon my_study.hor

# 2. Watch progress (pueue) or check for failures
pueue status
horizon --status path/to/output_dir

# 3. Collect the Excel reports of completed runs into one file
horizon -c path/to/output_dir results.xlsx
```

The `.hor` file defines the study: which parameters vary, over what ranges
and distributions, across which scenario combinations. The `.unc` file it
points at is an ordinary Navigate simulation file with `%TOKEN%`
placeholders where sampled values are substituted.

### Sensitivity analysis

After a study completes, Horizon can compute PRCC (partial rank correlation
coefficient) sensitivities linking sampled parameters to output metrics:

```bash
horizon --sensitivity-analysis my_study.sen
```

### Command-line options

See `horizon --help` for the full list. The most used flags:

| Flag | Description |
|------|-------------|
| `-c`, `--collect` | Collect reports from completed runs into a single Excel file. |
| `--report-name PATTERN` | Only collect reports matching a filename pattern (supports `*`). |
| `--priority {low,normal,high}` | Pueue scheduling priority for queued runs. |
| `--solver {auto,gurobi,highs}` | Solver backend passed through to Navigate. |
| `--navigate-flags "..."` | Extra flags appended to each `navigate` command (e.g. `"-d ./assumptions -s"`). |
| `--full-task-env` | Forward your entire environment to each queued task. By default only the variables a Navigate run needs are forwarded (plus any named in `HORIZON_TASK_ENV`, comma-separated), which keeps pueue's state small and submission fast. |
| `--pueue-cli` | Submit through the pueue CLI instead of the direct daemon connection. |
| `--gen-workers N` | Thread-pool size for `.nav` generation (default: `min(8, CPU count)`). |
| `--output-dir DIR` | Directory for generated scenario folders (default: next to the `.unc` file). |
| `--dry-run` | Validate the configuration and preview what would be generated. |
| `--status DIR` | Check pueue task status and report failures for an output directory. |
| `--replot DIR ...` | Queue Navigate replot jobs for directories containing `plot_data.pkl`. |
| `--sensitivity-analysis [FILE]` | Run PRCC sensitivity analysis, optionally from a `.sen` config. |
| `--calibration-plot` | Generate a calibration comparison dashboard from completed results. |

## Performance and sizing

Horizon runs the same on a many-core production server and on a laptop; the
defaults adapt to the machine, and two knobs cover the extremes:

- **File generation** uses a thread pool of `min(8, CPU count)` workers —
  measured optimal at core count on a 4-core machine (serial is ~2.4x
  slower, twice the cores ~1.75x slower). For scale: 10 000 realizations
  generate in ~15 s and ~160 MB of RAM on a 4-core machine — memory is not
  the constraint, disk is (that study writes ~0.7 GB of `.nav`/`.inc` files
  before Navigate produces any results).

- **Network storage** (an output directory on NFS/SMB) changes the game:
  every file creation is a metadata round trip, so include-heavy decks are
  bound by files-per-realization, not CPU. Two things address this. First,
  rewritten includes whose content only varies per scenario are written once
  per scenario combination into `shared_includes/` instead of into every
  realization folder (on by default; `--no-shared-includes` restores full
  copies). Second, Horizon probes the output filesystem's latency at
  generation start and automatically sizes the pool at `min(64, 4×CPU)`
  when it looks network-backed — threads blocked on round trips cost
  nothing, so more in-flight operations directly raise throughput. The log
  says when this triggers; `--gen-workers` overrides either way (measure
  your optimum with [`tools/benchmark/`](tools/benchmark/README.md)).

- **Queue submission** uses a direct connection to the pueue daemon when a
  pueue >= 4 unix socket is found, and the pueue CLI otherwise (or with
  `--pueue-cli`). pueue rewrites its entire state file on every add, so
  submission slows as the queue grows — measured with the direct
  connection: the first 2 000 tasks submit at ~160/s, tasks 8 000–10 000
  at ~17/s. Two habits keep it fast:
  - run `pueue clean` between studies (finished tasks keep bloating the
    state and slow every later submission and `pueue status`);
  - for studies beyond ~10 000 runs, expect submission to take minutes and
    consider splitting the study.

- **Simulation parallelism** is pueue's job: set `pueue parallel N` to how
  many Navigate runs the machine should execute at once (a handful on a
  laptop, many on the production server). Horizon sizes each task's
  thread-limit variables (`OMP_NUM_THREADS`, ...) from the study size
  automatically.

- **Task environment**: queued tasks get a minimal environment by default.
  If your setup needs extra variables (license servers, module systems),
  pass them with `HORIZON_TASK_ENV=VAR1,VAR2` or fall back to
  `--full-task-env`.

## Editor support

The [`syntax/`](syntax/) folder contains Notepad++ user-defined-language
files with syntax highlighting for `.hor`, `.sen`, and Navigate files.

## Documentation

- [`docs/configuration.md`](docs/configuration.md) — `.hor` and `.sen`
  format reference
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the code is
  structured, for contributors
- [Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/)
  — the simulation model itself, including the `.nav` file format

## Testing

```bash
pip install -e ".[dev]"
pytest
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines and
[`CODESTYLE.md`](CODESTYLE.md) for coding conventions.

## License

Copyright 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping.

Horizon is licensed under two licenses, depending on the type of content:

- The software — the `horizon` package, tests, and all build, tooling, and
  editor-support files — is licensed under the
  [Apache License 2.0](LICENSES/Apache-2.0.txt) (see also the root
  [LICENSE](LICENSE) file).
- The documentation, examples, and figures are licensed under
  [Creative Commons Attribution 4.0 International](LICENSES/CC-BY-4.0.txt)
  (CC-BY-4.0).

Every file declares its license through an `SPDX-License-Identifier` header
or through the metadata in [REUSE.toml](REUSE.toml), following the
[REUSE specification](https://reuse.software/). The full license texts are
in the [LICENSES](LICENSES/) directory.
