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

```
study.hor ─▶ horizon run ─▶ navigate × N ─▶ horizon collect ─▶ study.parquet ─▶ lantern
```

## Install

Horizon runs the `navigate` command on `PATH`, so it goes into the same
environment as Navigate, in a version that takes `--set`, `--name` and
`--output-dir`. Python 3.12 or newer.

```bash
conda activate nav          # the environment Navigate lives in
pip install .               # from a clone of this repository
pip install -e ".[dev]"     # for development, with the lint tools
```

A queue such as [pueue](https://github.com/Nukesor/pueue) or slurm is
optional; without one, Horizon runs the study on this machine.

## Use

```bash
horizon check my_study.hor              # validate in seconds, and cross-check the deck
horizon run my_study.hor --dry-run      # print every run with its sampled values; write and run nothing
horizon run my_study.hor                # sample and run here, one run per CPU (-j N to change)
horizon run my_study.hor --samples 1    # one sample per scenario combination, the file unchanged
horizon collect my_study.hor            # once every run is ok: compact the results for Lantern
lantern output/my_study.parquet         # look at them
```

Through a queue, `--command` hands each run to the scheduler and `--after`
queues the collect step behind them:

```bash
horizon run my_study.hor --command "pueue add -p -g horizon --label {run} -- {cmd}" \
                         --after "pueue add -p -g horizon --after {ids} -- {cmd}"
horizon run my_study.hor --command "sbatch --parsable --job-name {run} --wrap {cmd}" \
                         --after "sbatch --parsable --kill-on-invalid-dep=yes --dependency=afterok:{ids} --wrap {cmd}"
```

| Command | What |
|---|---|
| `check STUDY.hor` | validate the study and cross-check the deck and the files it includes; exit 0 or 1 |
| `run STUDY.hor` | sample, write the study folder and start the runs, here or through a queue |
| `collect STUDY` | compact the results once every run is ok; `--partial` compacts what there is |

`horizon <command> --help` lists every option. A broken `.hor` file prints
one line (`my_study.hor:41: error: ...`) and exits 1. Horizon keeps no state
about the runs: a failed run shows in the scheduler, or in the summary
`horizon run` prints, and `logs/horizon.log` has the command of every run.

To try it without Navigate, the bundled example checks and samples:

```bash
cd examples/uncertainty
horizon check fuel_cost_study.hor
horizon run fuel_cost_study.hor --dry-run
```

## Files

The `.hor` file defines the study: which parameters vary, over what ranges
and distributions, across which scenario combinations. Its `UncFilePath`
names an ordinary Navigate deck with `%NAME%` placeholders where the sampled
values go; Navigate fills them in from `--set NAME=VALUE`, so Horizon writes
no simulation files.

```
ContinuousParameter "WACC" = LOG-TRIANGULAR(0.6, 1.0, 1.8)

Horizon {
    UncFilePath = "deck.unc"
    OutputPath = "output"
    NumberOfSamples = 100
    SamplingMethod = LHS
}
```

`OutputPath` names the study folder:

| Path | What |
|---|---|
| `study.json` | the study and every run with its sampled values |
| `logs/` | Navigate's log of each run, and `horizon.log` with the command of each run |
| `parquets/` | each run's results, until `collect` |
| `<study>_archive.parquet` | every row of every run |
| `<study>.parquet` | for Lantern: per scenario combination a representative run and the median, 5th and 95th percentile |

## Documentation

- [`docs/configuration.md`](docs/configuration.md): the `.hor` format
- [`docs/queues.md`](docs/queues.md): pueue, slurm and other schedulers, and the environment a run gets
- [`docs/output.md`](docs/output.md): the study folder, how the representative run is picked, failed runs
- [`examples/uncertainty/`](examples/uncertainty/README.md): a small study to start from
- [Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/): the model and its deck format
- [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODESTYLE.md`](CODESTYLE.md): how a change gets in

## Disclaimer

Horizon is an open-source analytical tool intended for research and scenario
analysis of maritime decarbonisation pathways. Its outputs depend on the
model, assumptions, and parameter ranges selected by the user and are
provided for illustrative and analytical purposes only. They should not be
interpreted as forecasts, benchmarks, recommendations or commercially
optimal outcomes, nor as legal, financial or investment advice. Users are
responsible for selecting appropriate assumptions and for exercising their
own independent judgement when interpreting any outputs.

## License

Copyright 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping.

The `horizon` package and the build, tooling and editor-support files are
licensed under the [Apache License 2.0](LICENSES/Apache-2.0.txt) (see also
[LICENSE](LICENSE)); the documentation, examples and figures under
[CC-BY-4.0](LICENSES/CC-BY-4.0.txt). Every file declares its license
following the [REUSE specification](https://reuse.software/), see
[REUSE.toml](REUSE.toml).
