<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Horizon

[![CI](https://github.com/zerocarbonshipping/horizon-zcs/actions/workflows/ci.yml/badge.svg)](https://github.com/zerocarbonshipping/horizon-zcs/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSES/Apache-2.0.txt)

Horizon runs uncertainty studies of
[Navigate](https://github.com/zerocarbonshipping/navigate-zcs), the
open-source maritime transition model. It samples uncertain input parameters
(Latin hypercube or Monte Carlo) across scenario combinations, starts one
Navigate run per sample on this machine or through any queue (pueue, slurm,
...), and compacts the results into one file per study, ready for
[Lantern](https://github.com/zerocarbonshipping/lantern-zcs).

Horizon writes no simulation files: each run is the study's `.unc` deck,
which Navigate fills in itself from `--set NAME=VALUE`.

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
  in the same environment, in a version that takes `--set`, `--name` and
  `--output-dir`. Horizon runs the `navigate` command on
  `PATH`.
- Optionally a queue such as [pueue](https://github.com/Nukesor/pueue) or
  slurm; without one, Horizon runs the study on this machine.

## Installation

Install Horizon into the same environment as Navigate:

```bash
conda activate nav       # or whatever environment Navigate lives in
pip install .
```

For development with the lint tools:

```bash
pip install -e ".[dev]"
```

## Quick start

The bundled example checks and samples without Navigate: `--dry-run` prints
the study as `study.json` would hold it, every run with its sampled values,
and logs how the first run would start, but writes and runs nothing.

```bash
cd examples/uncertainty
horizon check fuel_cost_study.hor
horizon run fuel_cost_study.hor --dry-run
```

See [`examples/uncertainty/README.md`](examples/uncertainty/README.md) for a
walkthrough, and [`docs/configuration.md`](docs/configuration.md) for the
full configuration reference.

## Workflow

```bash
horizon check my_study.hor              # validate in seconds, and cross-check the deck
horizon run my_study.hor                # sample and run, here or through a queue
horizon collect my_study.hor            # once every run is ok: compact the results
```

The `.hor` file defines the study: which parameters vary, over what ranges
and distributions, across which scenario combinations. Its `UncFilePath`
names an ordinary Navigate deck with `%NAME%` placeholders where the sampled
values go, in the deck and in the files it includes. A parameter fits on one
line, e.g. `ContinuousParameter "WACC" = LOG-TRIANGULAR(0.6, 1.0, 1.8)`; the
block form is equally valid (see [`docs/configuration.md`](docs/configuration.md)).

`OutputPath` names the study folder, e.g. `output`, which holds:

| Path | What |
|------|------|
| `output/study.json` | the deck, the navigate command and its arguments, where the study came from, and every run, one per line: its run id, scenario combination, sample number and the value of every token |
| `output/logs/` | Navigate's log of each run and Horizon's own `horizon.log`, which has the command of each run; cleared when the study is run again |
| `output/job_ids.txt` | with `--write-ids`: what the scheduler printed for each run, its job id when asked for just that; never deleted, only overwritten |
| `output/parquets/<run_id>.parquet` | the results of one run; the folder is deleted once the study is compacted |
| `output/<study>_archive.parquet` | the archive: every row of every run, with the columns `run` and `sample` |
| `output/<study>.parquet` | for Lantern: per scenario combination the runs `<label>` (the representative sample), `<label>_median`, `<label>_p05` and `<label>_p95` |

Runs are named `<label>_sample<NNN>`. The label is the Include rule's
`_name`, else the active scenario values joined by `_`, else the study name;
the study name is the `.hor` file name.

Once every run is ok, `horizon collect` compacts the study into two files.
The archive, `<study>_archive.parquet`, holds every row of every run. The
Lantern file, `<study>.parquet`, holds the summary Lantern once computed
itself from an ensemble, as four runs per scenario combination that Lantern
opens and compares:

- `<label>`: the representative sample, the one that stays closest to the
  median of WTW emissions, installed power, fuel consumed by fuel type and
  fuel-related expenses (weighted 0.4, 0.3, 0.2 and 0.1). It is one real run,
  so its stacked plots add up.
- `<label>_median`, `<label>_p05`, `<label>_p95`: for each result row and
  date, the median and the 5th and 95th percentiles over the samples.

Percentiles do not add up, so where a plot sums rows (all fleets of a fuel,
say), the band of the sum is wider than the true band. `collect` deletes
`parquets/` only once both files are written and read back. With
`--command pueue`, the collect step is queued after the runs; otherwise run
`horizon collect` once every run is done. It refuses while a run has no
results, and `horizon collect --partial` compacts what there is and keeps
the per-run results.

Horizon keeps no state about the runs. A run that fails shows where it ran:
in the scheduler's status and log, or, without `--command`, in the summary
`horizon run` prints, grouped by the last line of each failed run's output.
Navigate's log of the run is in `logs/`. To run one again, restart it in the
scheduler (e.g. `pueue restart <task>`), or take its command from
`horizon.log`, which has the command of every run.

### Running through a queue

Without `--command`, `horizon run` runs the study here, one run per CPU at a
time (`-j N` to change that), and waits for it. With `--command`, it hands
each run to a scheduler and returns.

`--command pueue` queues each run as a pueue task labelled with its run id,
and after them the task `<study>_collect`, which runs `horizon collect` once
every run has succeeded. Words after `pueue` are options of `pueue add`, for
both:

```bash
horizon run my_study.hor --command pueue
horizon run my_study.hor --command "pueue -g horizon"
```

pueue runs as many tasks at a time as the group allows (`pueue parallel 8`,
or `-g` with a group of its own). If a run fails, pueue fails the collect
task too; start the run again (`pueue restart <task>`) and, once it is done,
run `horizon collect` yourself.

Any other prefix hands over the runs only. Each run is one Navigate command,
which the prefix gets with `{run}`, `{study}` and `{cmd}` filled in:

```bash
horizon run my_study.hor --command "sbatch --parsable --job-name {run} --wrap {cmd}" --write-ids
```

`{cmd}` is the Navigate command as one quoted argument, for schedulers that
take a command string (`sbatch --wrap`); without it, the command's words are
appended to the prefix. `--write-ids` writes what each submission prints to
`job_ids.txt` in the study folder, one line per run; asked for just the job
id (`sbatch --parsable`), that is the list a collect job waits on:

```bash
sbatch --dependency=afterok:$(paste -sd: output/job_ids.txt) --wrap "horizon collect my_study.hor"
```

`horizon.log` pairs each run id with what its submission printed, with or
without `--write-ids`. Either way, the command names the `navigate` that
`horizon run` found, so the queue's machines need the same environment, and
the study folder on a disk they share.

### Commands

See `horizon <command> --help` for the full list.

| Command | Description |
|---------|-------------|
| `run STUDY.hor` | Sample the study, write its study folder, and start its runs. `--command PREFIX` hands them to a queue (`pueue` also queues the collect step; `--write-ids` keeps the job ids of any other prefix in `job_ids.txt`); `-j N` runs N at a time here (default: one per CPU); `--samples N` replaces `NumberOfSamples` without editing the file; `--navigate "ARGS"` adds arguments to every Navigate run (e.g. `"-s -d ./assumptions"`; a single flag as `--navigate=-s`); `--dry-run` prints the study as `study.json` would hold it and logs how the first run would start, but writes and runs nothing. A study run again clears its logs and the results of its earlier runs first. |
| `check STUDY.hor` | Validate the study and cross-check the deck and the files it includes: every `%NAME%` has a parameter, unused parameters are warned about, and every include path resolves, for each scenario combination, to an existing file. Exits 0 (ok or warnings) or 1 (errors). |
| `collect STUDY` | Compact the study once every run is ok; `--partial` compacts the runs that have results and keeps the per-run files. `STUDY` is the `.hor` file or its study folder. |

A broken `.hor` file prints one line (`my_study.hor:41: error: ...`) and
exits 1.

## Documentation

- [`docs/configuration.md`](docs/configuration.md) — `.hor` format
  reference
- [Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/)
  — the simulation model itself, including the `.nav` file format

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines and
[`CODESTYLE.md`](CODESTYLE.md) for coding conventions.

## License

Copyright 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping.

Horizon is licensed under two licenses, depending on the type of content:

- The software — the `horizon` package and all build, tooling, and
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
