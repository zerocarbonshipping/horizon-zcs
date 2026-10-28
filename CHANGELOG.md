<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Horizon 2: no generated simulation files, any queue, one compacted file per study. The 1.x entries
after this block are superseded where they conflict.

### Added
- Subcommands `horizon run | check | collect`. `run --command PREFIX` hands each run
  to any scheduler (`sbatch --wrap {cmd}`); without it the runs run in a local pool (`-j N`, one per
  CPU by default). Each run is one Navigate command; `run --dry-run` prints the study as `study.json`
  would hold it, but writes and runs nothing. `horizon.log` has the command of every run.
- `run --command pueue` queues the runs in pueue and, after them, `horizon collect`, which starts once
  every run has succeeded. With any other prefix, `--write-ids` writes what each submission printed
  (the job id with `sbatch --parsable`) to `job_ids.txt`, for a collect job that waits on the runs.
- Navigate fills in the `%NAME%` placeholders from `--set NAME=VALUE` (needs a Navigate with `--set`,
  `--name` and `--output-dir`); Horizon writes no `.nav` or `.inc` files.
- The study folder, `OutputPath`, holds `study.json` (the settings and every run with its values), `logs/`
  (Navigate's log of each run and Horizon's `horizon.log`) and `parquets/` (each run's results,
  until the study is compacted). Horizon keeps no run state: failures show
  in the scheduler, or in the summary of `horizon run` without one.
- `horizon collect` compacts the study into the archive `<study>_archive.parquet` with every sample,
  and the Lantern file `<study>.parquet`, both in the study folder, with four runs per scenario
  combination: the representative sample (Lantern's former rule) and the median, 5th and 95th
  percentile of each row and date over the samples, which its metadata names per combination under
  `summaries` (`median`, `low`, `high`). A 300-sample combination of the basecase takes
  about 30 s and 2 GB. `parquets/` is deleted once both files are verified.
- Runs are named `<label>_sample<NNN>`, padded to the sample count; labels must be unique and fit in
  file names.

### Changed
- The `.hor` parser is a Lark grammar. Newlines are not significant, commas between entries are
  optional and values may be quoted or bare everywhere. `token` and `name` are gone (the block name is
  the token), as are `MaxParallelWorkers`, `Plot`, `SampleOnly` (use `horizon run --dry-run`), the
  `MIN`/`MAX` aliases and lowercase `include(`/`exclude(`. `SamplingMethod` is `LHS` or `MC`. Paths are taken from the folder of the
  `.hor` file. A key set twice in a block and a scenario `default` outside its values are errors.
  `docs/configuration.md` lists what to change in a 1.x file.
- One sampling path: every scenario combination resolves its overrides and samples on the shared draw
  matrix, which gives the values of both 1.x paths.

### Removed
- The test suite, and the `syntax/` editor files, which highlighted the 1.x format.
- `.nav`/`.inc` generation, the direct pueue client, `--priority`, `--threads`, `--full-task-env`, `--pueue-cli`,
  `--gen-workers`, `--output-dir`, `--replot`, `-c` report collection, PRCC sensitivity analysis and
  `.sen` files, the calibration dashboard, the sampling diagnostics plots, `--explain`, the check
  for include names that differ only in case, and the "did you mean" suggestions in errors. Drops the dependencies
  pandas, matplotlib, scipy, openpyxl, python-calamine, seaborn, psutil and plotly; adds lark and
  polars.

### Fixed
- The minimum and maximum of numeric discrete values compared as text (`10` < `5`).
- Log distributions crashed on an `if` block that fixes the value (`low_val = high_val`).
- Sampled values with 0 decimals were floats (`300.0`); an explicit continuous `default` stayed text.
- An Include `_name` that matched several combinations gave their runs the same name silently.

### Added (1.x)
- `--threads N`: pin `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and `NUMEXPR_NUM_THREADS` to
  exactly N for every queued task (direct daemon and pueue CLI paths). Without it the
  automatic `max(2, cpu_count // tasks)` split is unchanged.
- One-line distribution call in `.hor` files, an alternative to the block form
  (both can be mixed in one file): `ContinuousParameter "x" = LOG-TRIANGULAR(0.6, 1.0, 1.8)`,
  `... = UNIFORM(0, 1) { emissions_low = MAXIMUM }`,
  `DiscreteParameter "y" = DISCRETE([3, 4, 5], [0.2, 0.5, 0.3]) { default = 4 }`.
  Wrong argument counts and mixing the call with `low_val`/`distribution`/`values`
  are errors with a line number. Overrides keep the `if ... { }` block form.
- `horizon run.hor --check`: parses with all validation and cross-checks the
  `.unc` and the `.inc` files it includes (tokens without a parameter,
  unused parameters, scenario values in include paths that resolve to no
  file or one with different case). Prints a summary, exits 0 or 1, writes
  nothing. `--explain` prints the resolved parameters per scenario value and
  where each value came from. `--samples N` overrides `NumberOfSamples` for
  one run without editing the file.
- VS Code extension `syntax/horizon-language-1.1.0.vsix` with syntax
  highlighting for `.hor` and `.sen` files. It and the Notepad++ UDL now
  highlight the distribution keywords `LOG-TRIANGULAR`, `TRIANGULAR`,
  `LOG-UNIFORM` and `UNIFORM` (and the `LOG_` spellings).
- A study now queues a results collector behind its Navigate runs. Once
  every run has succeeded, it merges their results files (`.parquet`) into
  `<output_dir>.parquet` next to the output directory, which Lantern shows
  as ensembles: a realization `<scenario>_sampleNNN` joins the ensemble
  `<scenario>`. It succeeds without writing anything when the runs wrote no
  results files. `horizon --collect-results DIR` runs it by hand, with a
  progress bar. Reads Navigate results schema 1 and merges 1000 basecase
  runs in about 20 s. Adds `pyarrow` and `tqdm` as dependencies.

### Changed
- Stricter `.hor` parser. Unknown keys in parameter blocks, `if` bodies and
  the `Horizon` block are errors with a "Did you mean" suggestion. `if` is
  only recognised at the start of a line, so `name = "Cost if any"` works.
  After applying every override, `low_val <= high_val`, the triangular mode
  and the default must lie within the bounds, and `if` conditions must name
  a `ScenarioParameter` and one of its values. A `DiscreteParameter` without
  `probabilities` is uniform (it crashed before) and needs one probability
  per value. `.hor` files are read as UTF-8. Errors carry file and line, and
  the command line prints one `file:line: error: ...` line and exits 1; the
  traceback goes to `output.log`.
- The legacy `ScenarioParameters` / `Parameters` lists in the `Horizon` block
  (and bare `ContinuousParameter("X")` lines) are an error instead of a
  deprecation warning. Delete the list; use `active = FALSE` to pin a
  parameter; set `distribution = UNIFORM` where the old default was relied on.
- Shorter `.hor` format. `token` and `name` are optional and default to the
  block name. A `ContinuousParameter` without `default` uses `mid_val` (also
  inside `if` overrides); without either, parsing fails. Every defined block
  takes part in the study, so the `ScenarioParameters` / `Parameters` lists in
  the `Horizon` block are no longer needed (and now an error, see below). Two blocks with the same name now raise an error. A
  `DiscreteParameter` without `default` now raises an error instead of writing
  an empty value.
- `distribution` is a keyword (`LOG-TRIANGULAR`, `TRIANGULAR`, `LOG-UNIFORM`,
  `UNIFORM`) and defaults to `LOG-TRIANGULAR` when omitted (was `uniform`).
  Quoted lowercase names still work; unknown names raise an error.
- `decimals` is optional. Without it the rounding step is at most 1/10,000 of
  the range, and log distributions keep 4 significant digits at `low_val`.
  `if` blocks that change bounds or distribution recompute it.
- Rewritten includes whose content depends only on the scenario (all replaced
  tokens are scenario tokens) are written once per scenario combination under
  `shared_includes/<combination>/` and referenced from there, instead of
  being copied into every realization folder. Include-heavy studies save one
  file creation per such include per realization — the dominant generation
  cost on network filesystems. Sample-dependent rewrites stay per-realization
  in `simulation_includes/`.
- Rewritten include filenames now carry the source file's stem (e.g.
  `policy_POLICY_sample_1.inc` instead of `POLICY_sample_1.inc`), with a
  short hash suffix when two sources share a stem or the include path is
  tokenized.
- **Breaking**: the default emissions metric key is `TotalEquivalentWtw`
  (formerly `TotalEquivalentWTW`), following navigate-zcs renaming its
  report properties — the key must match the report column navigate writes.
  Configs and `--metric` arguments using the old spelling find no column.
- File generation is substantially faster: `.unc` templates and `.inc`
  include files are tokenized once and rendered per realization instead of
  being regex-scanned line by line for every realization. Generated files
  are byte-identical to before.
- Realization folders are created with one directory round trip each, and
  `simulation_includes/` is only created when an include is actually
  rewritten for that realization — realizations without tokenized includes
  no longer contain an empty `simulation_includes/` folder.
- Per-scenario sampling computes the seeded LHS/MC draw matrix once and
  reuses it across scenario combinations (it is identical by design) instead
  of recomputing it per combination — maximin LHS is quadratic in the sample
  count, so large studies save most of their sampling time. Scenario
  include/exclude rules are likewise evaluated once per combination instead
  of once per sample.
- Queue submission is streamed: each realization is submitted to pueue the
  moment its `.nav` is written, instead of after all files are generated.
  The first simulations start seconds after `horizon` is invoked, and total
  submission wall time becomes max(generation, queuing) instead of their
  sum.
- Queued tasks now carry a minimal environment instead of the submitting
  shell's entire environment. pueue stores the full client environment in
  every task and rewrites its whole state file on every add, so the
  environment payload was the main driver of daemon state size (7x smaller
  in our benchmark) and submission slowdown on large studies. The default
  whitelist covers what a Navigate run needs (PATH, HOME, locale, Python
  env, `ASSUMPTIONS_DATA_DIR`, Gurobi licensing, thread limits, ...);
  extend it with `HORIZON_TASK_ENV=VAR1,VAR2` or disable trimming with
  `horizon --full-task-env`.
- New `--gen-workers N` flag to size the generation thread pool per machine
  (default unchanged: `min(8, CPU count)`, measured optimal at core count),
  and a "Performance and sizing" README section with measured guidance for
  laptops and many-core production hosts, including pueue queue hygiene
  (`pueue clean`) for large studies.
- Tasks are submitted over a single direct connection to the pueue daemon
  when a pueue >= 4 unix socket is found (POSIX only), instead of spawning
  one `pueue add` client process per task — 2000 tasks queue in ~13 s where
  the CLI path took ~129 s in our benchmark. Any problem with the direct
  connection (older pueue, TCP-configured daemon, protocol error mid-run)
  falls back to the pueue CLI automatically without losing tasks;
  `horizon --pueue-cli` forces the CLI path outright.

### Removed
- Include directives not spelled `Include` (`INCLUDE`, `include`, ...) in `.unc` templates are no longer normalized;
  generation and token extraction now fail with an error naming the file and
  line. Write `Include`.
- Network-filesystem latency probe and adaptive generation pool size: the
  default is `min(8, CPU count)`; `--gen-workers N` sets it explicitly.
- `--no-shared-includes`: the shared include store is the only behaviour.
- The study-specific "Suggested Round 2 Discretization" table in the calibration
  dashboard.
- Calibration analysis of legacy output without a `regulation` column (folders
  named `{regulation}_sampleNNN`): `sampled_parameters.csv` must now have a
  `regulation` column, otherwise a clear error is raised.
- Unused internals: `plot_sampled_parameters_histograms`,
  `FileHandler.generate_commands_list` and `FileHandler.commands`.

### Fixed
- Sensitivity analysis from a collected report (`--report-csv`) skipped a `.sen` point
  metric that has a custom label and an explicit year with "No data found".
- Calibration recommendation cards no longer show the literal text `# noqa: E122`.
- Two different `.inc` files replacing the same token set were rewritten to
  the same `simulation_includes/` filename, silently overwriting each other:
  both nav `Include` lines then pointed at whichever copy was written last,
  so the simulation ran with one include's content missing and the other's
  duplicated. Any study with two or more includes tokenized by the same
  scenario parameter was affected.
- The direct pueue daemon connection now engages with pueued-generated
  configs: unset options written as YAML `null` (e.g.
  `unix_socket_path: null`) were read as literal paths, so discovery failed
  and submission silently used the slower CLI path.
- `RandomSeed` now actually reproduces LHS studies: the seed is passed to
  pyDOE3's `lhs()` directly (pyDOE3 >= 1.5 ignores the legacy global NumPy
  seed, so sampled values differed between runs of the same study). This
  also restores the documented guarantee that per-scenario sampling reuses
  one draw matrix, so `sample_i` aligns across scenario combinations.
  Requires `pyDOE3 >= 1.5` (now pinned).
- `horizon --status` understands pueue 4.x task states. Under pueue 4,
  successful runs were reported as failed (with their warning logs tailed as
  "failure details") and queued/running counts were wrong.
- Include directives written into generated `.nav` files now use Navigate's
  current `Include` spelling instead of the legacy all-caps `INCLUDE`, which
  Navigate's grammar rejects. Every include line in a generated `.nav` was
  affected, so any template with includes failed to run.
- Generated include lines now keep the template's own indentation.
- Include-line detection is word-boundary aware, so an attribute such as
  `IncludeRate = 5` is no longer mistaken for an include directive.
- `.hor` parsing strips trailing `#` comments (a `#` inside a quoted string is
  kept). Before, only full-line comments were removed, so a trailing comment
  containing the word `if` (e.g. `default = 0.5  # if needed`) was parsed as
  an override header and broke the parameter.
- Base parameter fields (`low_val`, `mid_val`, `default`, `distribution`,
  `decimals`, `values`, ...) are read only outside `if ... { ... }` override
  blocks. Before, a field missing from the base but set in an override was
  silently taken as the base value; a missing required field now raises the
  missing-field error. Attribute patterns also match whole keys only
  (`xname =` no longer matches `name`).

## [1.0.1] - 2026-10-13

### Fixed
- Generated `.nav` files now always emit the `Include` keyword in Title case, as required by navigate-zcs; 1.0.0 emitted `INCLUDE`, which navigate rejects.

## [1.0.0] - 2026-08-17

Initial public release of Horizon, an open-source uncertainty analysis tool
for the [Navigate](https://github.com/zerocarbonshipping/navigate-zcs)
maritime transition model.
