# Fuel cost uncertainty study

A minimal, self-contained Horizon example. It samples three uncertain
parameters across two policy scenarios using Latin Hypercube Sampling and
stops after sampling (`SampleOnly = TRUE`), so it runs without Navigate or
pueue installed.

## Run it

From this directory:

```bash
horizon fuel_cost_study.hor
```

This writes to `output/`:

- `samples.csv` — the sampled parameter values, one row per sample, with a
  `POLICY` column for the scenario
- `plots/` — sampling diagnostics (histograms, ECDFs, correlation checks),
  generated per scenario where parameters have scenario overrides
- `sampling_analysis_summary.csv` — summary statistics per parameter

A log of the run is written to `output.log` next to the `.hor` file.

## What it demonstrates

- A **scenario parameter** (`POLICY`) that expands the study into a
  baseline and a carbon-levy branch
- A **conditional override**: under `POLICY = carbon_levy`, the fuel price
  multiplier is sampled from a higher range (`1.0–2.0` instead of `0.8–1.5`)
- A **triangular distribution** (`DEMAND_GROWTH`) and a **weighted discrete
  parameter** (`RENEWAL`)
- The `%TOKEN%` mechanism in `template.unc`, the tokenized Navigate file
  Horizon fills in per sample

## Going further

To turn this into a real study, replace `template.unc` with a tokenized copy
of a working `.nav` file from your own Navigate project, set
`SampleOnly = FALSE`, start the pueue daemon (`pueued -d`), and run the same
command. Horizon then generates one `.nav` file per sample/scenario
combination and queues a Navigate run for each. See
[`docs/configuration.md`](../../docs/configuration.md) for the full `.hor`
reference.
