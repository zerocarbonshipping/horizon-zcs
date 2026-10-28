# Fuel cost uncertainty study

A minimal, self-contained Horizon example. It samples three uncertain
parameters across two policy scenarios using Latin Hypercube Sampling, and
checks and samples without Navigate or a queue installed.

## Run it

From this directory:

```bash
horizon check fuel_cost_study.hor
horizon run fuel_cost_study.hor --dry-run
```

`check` validates the study and cross-checks `template.unc`: every `%NAME%`
placeholder has a parameter and every parameter is used. `run --dry-run`
prints the study as `output/study.json` would hold it, every run with its
sampled values, and logs how the first run would start, but writes and runs
nothing.

## What it demonstrates

- A **scenario parameter** (`POLICY`) that expands the study into a
  baseline and a carbon-levy branch
- A **conditional override**: under `POLICY = carbon_levy`, the fuel price
  multiplier is sampled from a higher range (`1.0–2.0` instead of `0.8–1.5`)
- A **triangular distribution** (`DEMAND_GROWTH`) and a **weighted discrete
  parameter** (`RENEWAL`)
- The `%NAME%` placeholders in `template.unc`, which Navigate fills in from
  each run's `--set NAME=VALUE`

## Going further

To turn this into a real study, replace `template.unc` with a working
Navigate deck from your own project with the uncertain values replaced by
placeholders, and run `horizon run fuel_cost_study.hor`: here, one run per
CPU at a time, or through a queue with `--command pueue`. Once the runs are
done, `horizon collect fuel_cost_study.hor` compacts their results;
`--command pueue` queues that step for you. See
[`docs/configuration.md`](../../docs/configuration.md) for the full `.hor`
reference.
