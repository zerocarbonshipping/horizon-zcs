<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Architecture

Horizon wraps the [Navigate](https://github.com/zerocarbonshipping/navigate-zcs)
simulation model with an uncertainty analysis layer: it samples parameter
values, generates one Navigate simulation file per sample, queues the runs,
and aggregates the results. This document describes how the pieces fit
together for anyone modifying the code.

## Execution flow

```
.hor config ──> parser ──> sampler ──> file handler ──> pueue queue ──> navigate runs
                                │                                            │
                                └──> samples.csv + diagnostics      reports ─┴─> horizon -c
                                                                              └─> sensitivity / calibration analysis
```

1. **CLI entry** (`horizon/__main__.py`) parses arguments, sets up logging,
   and dispatches to one of the modes: uncertainty analysis (default),
   report collection (`-c`), batch replotting (`--replot`), sensitivity
   analysis (`--sensitivity-analysis`), calibration dashboard
   (`--calibration-plot`), or queue status (`--status`).

2. **File creation pipeline** (`horizon/test_manager/create_files.py`) runs
   the main mode: parse the `.hor` file, sample parameters, write the
   samples CSV, then (unless `SampleOnly = TRUE`) generate `.nav` files from
   the `.unc` template and queue one `navigate` command per realization.

## Components

### Parameters (`horizon/parameters/`)

Three parameter types: `ScenarioParameter` (categorical, defines scenario
branches), `ContinuousParameter` (numeric with a distribution), and
`DiscreteParameter` (fixed values with probabilities). Parameters support
conditional overrides (`if ... { ... }` blocks) that modify their definition
per scenario combination.

### Sampling (`horizon/parameters/sampler.py`)

`ParameterSampler` implements Monte Carlo, Latin Hypercube, one-at-a-time
sensitivity, and calibration sampling. The first three samples are always
deterministic (defaults, emissions-low, emissions-high); random draws start
at `sample_4`.

When parameters have overrides and active scenario parameters exist,
sampling runs separately per scenario combination. The same underlying draw
matrix is reused across scenarios so `sample_i` indices align — important
when comparing scenarios sample-by-sample. Overrides are resolved before
sampling via `resolve_parameters_for_scenario()`; touch this code with care
and run the sampler tests.

For triangular distributions without an explicit `mid_val`, the mode is
inferred: arithmetic mean for `triangular`, geometric mean for
`log-triangular`.

### Parser (`horizon/parser/`)

Parses `.hor` files: parameter blocks, the `Horizon` block, override blocks,
and `Include`/`Exclude` directives. Validation happens at parse time where
possible (bounds, missing fields, probability sums) so users get errors
before any files are generated. See
[`configuration.md`](configuration.md) for the format itself, including the
Include/Exclude matching semantics and the `_name` / `%SCENARIO%` labeling
rules.

### File handler (`horizon/file_handler/`)

Generates `.nav` files from the `.unc` template: processes `Include`
directives (tokenizing `.inc` files as well), replaces `%TOKEN%`
placeholders with sampled values, creates one directory per realization, and
builds the corresponding `navigate` command list. Scenario and sample tokens
are replaced uniformly.

### Command execution (`horizon/run/`)

Commands are queued through [pueue](https://github.com/Nukesor/pueue). The
`--priority` flag maps to pueue priorities (low = -5, normal = 0,
high = 5). Thread-limiting environment variables (`OMP_NUM_THREADS`,
`MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS`) are set per task adaptively:
`max(2, cpu_count // num_tasks)`, capped at `cpu_count`. Each task is
labelled with its realization folder name so `pueue status` stays readable.
Horizon never imports Navigate as a library — the seam between the two tools
is the `navigate` CLI.

### Report collection (`horizon/collect_reports/`)

`collect_reports()` aggregates the Excel reports of completed runs into a
single file for analysis (`horizon -c`), optionally filtered by report
filename pattern (`--report-name`).

### Sensitivity analysis (`horizon/sensitivity/`)

PRCC (partial rank correlation coefficient) analysis linking sampled
parameters to output metrics, configured via a `.sen` file or CLI flags.
Metrics support point, difference, and cumulative aggregation over the
simulation years.

### Calibration (`horizon/calibration/`)

Systematic variation of discrete parameters plus a comparison dashboard
(`--calibration-plot`) for choosing parameter values that best match
reference data.

### Plotting (`horizon/plot/`)

`analyze_sampled_parameters()` generates sampling diagnostics
(distributions, correlations, coverage) so a study's sampling quality can be
checked before burning compute on simulations.

## Conventions

- **Logging, never `print()`**: output goes through the logging module to
  `output.log` next to the `.hor` file.
- **Structured exceptions** (`horizon/exceptions.py`): `HorizonError` is the
  base; `ParseError`, `ValidationError`, `FileOperationError`,
  `ParameterError`, and `SamplingError` carry context about what failed and
  which value caused it.
- **Two-phase execution**: sampling/file generation is separate from
  simulation execution, so `SampleOnly = TRUE` gives a full dry run of the
  sampling design without touching Navigate or pueue.

## File extensions

| Extension | Description |
|-----------|-------------|
| `.hor` | Horizon configuration (defines the uncertainty study) |
| `.unc` | Navigate simulation file with `%TOKEN%` placeholders |
| `.nav` | Generated Navigate simulation file (after token replacement) |
| `.inc` | Include file referenced from `.unc` templates |
| `.sen` | Sensitivity analysis configuration |
