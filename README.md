# Horizon

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
environment as Navigate 1.1.0 or later. `horizon run` checks that Navigate
before it starts a run, and stops with a message naming what to upgrade;
`horizon --version` names the Navigate and the schemas this Horizon works with.
Python 3.13 or newer, as Navigate.

```bash
conda activate nav          # the environment Navigate lives in
pip install horizon-zcs
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

To try it without Navigate, the [example study](https://github.com/zerocarbonshipping/horizon-zcs/tree/main/examples/uncertainty)
in the repository checks and samples:

```bash
git clone https://github.com/zerocarbonshipping/horizon-zcs
cd horizon-zcs/examples/uncertainty
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

- [`docs/configuration.md`](https://github.com/zerocarbonshipping/horizon-zcs/blob/main/docs/configuration.md): the `.hor` format
- [`docs/queues.md`](https://github.com/zerocarbonshipping/horizon-zcs/blob/main/docs/queues.md): pueue, slurm and other schedulers, and the environment a run gets
- [`docs/output.md`](https://github.com/zerocarbonshipping/horizon-zcs/blob/main/docs/output.md): the study folder, how the representative run is picked, failed runs
- [`examples/uncertainty/`](https://github.com/zerocarbonshipping/horizon-zcs/blob/main/examples/uncertainty/README.md): a small study to start from
- [Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/): the model and its deck format

## Contributing

Horizon follows Navigate's
[contributing guide](https://github.com/zerocarbonshipping/navigate-zcs/blob/dev/CONTRIBUTING.md#horizon-and-lantern) and
[code style](https://github.com/zerocarbonshipping/navigate-zcs/blob/dev/CODESTYLE.md): pull requests target `dev`, and
`CHANGELOG.md` records what a user would notice. Set up and check a change with:

```bash
pip install -e ".[dev]"
ruff check horizon tests && ruff format --check horizon tests && mypy && pytest
```

The suite is kept small on purpose, as Navigate's is: a test earns its place only when a plausible change could
silently break the decision it pins. It covers the example study end to end, checked and sampled without Navigate
(`tests/test_example.py`); the sampling math: each distribution's inverse CDF, the reserved samples 2 and 3 and the
automatic decimals (`tests/test_sampling.py`); and the parser: the scenario combinations a study runs, and the line
an error names (`tests/test_parser.py`). Queues and launching, what the libraries guarantee, and collecting results
are not tested here; the weekly Navigate workflow runs and collects a study on Navigate itself. Expected values are
worked out by hand or taken from an independent source, never from current output, so no test pins sampled values:
a change that alters them explains the difference in its pull request. As in Navigate
([`tests/AGENTS.md`](https://github.com/zerocarbonshipping/navigate-zcs/blob/dev/tests/AGENTS.md)), a coding agent
proposes a test, naming what it pins and where its expected value comes from, rather than writing it, and a failing
test is never changed to make it pass.

The package docstring, `horizon/__init__.py`, maps the modules.

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

Horizon is licensed under the [Apache License 2.0](https://github.com/zerocarbonshipping/horizon-zcs/blob/main/LICENSE).
