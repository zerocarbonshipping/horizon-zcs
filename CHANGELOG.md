# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- What gets an entry: Navigate's CONTRIBUTING.md, Changelog. -->

## [Unreleased]

Horizon 2 is a rewrite. Navigate fills in each run's sampled values itself, so
Horizon writes no simulation files; the runs go to any scheduler or run on this
machine; and `horizon collect` compacts a study into one file for Lantern.
[Changes from Horizon 1](docs/configuration.md#changes-from-horizon-1) lists
what to change in a 1.x `.hor` file.

### Added
- The commands `horizon run`, `horizon check` and `horizon collect`;
  `horizon STUDY.hor` becomes `horizon run STUDY.hor`.
- `run` runs the study on this machine, one run per CPU at a time (`-j N`), or
  hands each run to a scheduler through `--command PREFIX`, such as
  `sbatch --parsable --wrap {cmd}`; `docs/queues.md` describes the prefix.
- `run --after PREFIX` hands `horizon collect` to the scheduler, to start once
  the runs have succeeded; `--write-ids` keeps what each submission printed in
  `job_ids.txt`.
- `run --samples N` runs N samples instead of `NumberOfSamples`, without
  editing the file.
- A submission passes its scheduler only the environment a run needs;
  `HORIZON_TASK_ENV=NAME,...` adds variables, `--full-task-env` passes them all.
- `check` validates a study and cross-checks its deck and the files it
  includes: every `%NAME%` needs a parameter, an unused parameter is a warning,
  and every include path must exist for each scenario combination.
- `collect` compacts a study into `<study>_archive.parquet`, every row of every
  run, and `<study>.parquet` for Lantern: per scenario combination the
  representative run and the median, 5th and 95th percentile (`docs/output.md`).
- `collect --partial` compacts the runs that have results so far.
- `collect` carries every key of the runs' metadata into both study files,
  not only `units`, `constants`, `plant_fuel` and the provenance; where runs
  differ on a key other than `units`, it warns and keeps the first run's
  value. (#26)
- A run of a results schema Horizon does not read stops `collect` with a
  message naming the version and whether to upgrade Horizon or run it again
  with a newer Navigate; a study archive passed as a run says so. (#26)
- The one-line form of a parameter, such as
  `ContinuousParameter "x" = LOG-TRIANGULAR(0.6, 1.0, 1.8)` or
  `DiscreteParameter "y" = DISCRETE([3, 4, 5], [0.2, 0.5, 0.3]) { default = 4 }`.

### Changed
- Horizon requires Python 3.13 or newer, and a Navigate that takes `--set`,
  `--name` and `--output-dir`, installed in the same environment.
- Each run is the study's deck with `--set NAME=VALUE` for every token; Horizon
  writes no `.nav` or `.inc` files.
- `OutputPath` names the study folder: `study.json` with every run and its
  values, `logs/` with Navigate's log of each run and `horizon.log`, and the
  results. Runs are named `<label>_sample<NNN>`.
- `--dry-run` prints the study as `study.json` would hold it and how the first
  run would start, and writes nothing.
- In a `.hor` file newlines do not matter, commas between entries are optional,
  and values may be quoted or bare.
- The block name is the token: write the block under its token and delete
  `token` and `name`.
- Every block takes part in the study: delete the `ScenarioParameters` and
  `Parameters` lists of the Horizon block, and set `active = FALSE` to fix a
  parameter at its default.
- `distribution` defaults to `LOG-TRIANGULAR`, not uniform; write
  `distribution = UNIFORM` where a study relied on the old default.
- `decimals` is optional: without it, the rounding step is at most 1/10,000 of
  the range, and log distributions keep 4 significant digits at `low_val`.
- A `ContinuousParameter` without `default` takes `mid_val`; a
  `DiscreteParameter` without `default` is an error, and one without
  `probabilities` is uniform.
- `MIN` and `MAX` are `MINIMUM` and `MAXIMUM`, `Include` and `Exclude` are
  written so, and `SamplingMethod` is `LHS` or `MC`.
- `UncFilePath` and `OutputPath` are taken from the folder of the `.hor` file,
  not from the folder `horizon` runs in.
- Stricter checks: an unknown key, a key set twice, two blocks of one name, and
  a scenario `default` outside its values are errors.
- Each `if` block is checked too: bounds in order, the mode and the default
  within them, and conditions on the values of a scenario parameter.
- An error prints one `file:line: error: ...` line and exits 1.

### Removed
- `-c`, `--report-name` and `--report-csv`: `horizon collect` compacts the
  results.
- `--priority`, `--status` and `--output-dir`: the scheduler's options go in
  the `--command` prefix, the scheduler shows the runs, and `OutputPath` names
  the study folder.
- `--solver`: pass it to Navigate, `--navigate-flags "--solver highs"`.
- The PRCC sensitivity analysis and `.sen` files, the calibration dashboard and
  the plots, with `--sensitivity-analysis`, `--metric`, `--samples-csv`,
  `--calibration-plot` and `--replot`: Lantern opens the compacted study.
- The Horizon block keys `MaxParallelWorkers`, `Plot` and `SampleOnly`: use
  `-j N` and `horizon run --dry-run`.
- The Notepad++ files in `syntax/`: Navigate's `syntax/` highlights `.hor`
  files.
- The dependencies pandas, matplotlib, scipy, openpyxl, python-calamine,
  seaborn, psutil and plotly; Horizon needs lark, polars, pyarrow and tqdm.

### Fixed
- `RandomSeed` reproduces a Latin hypercube study: pyDOE3 1.5 ignores NumPy's
  global seed, so each run of a study sampled other values. Requires pyDOE3 1.5
  or newer.
- The minimum and maximum of numeric discrete values compared as text, so
  `10` < `5`.
- Log distributions crashed on an `if` block that fixes the value
  (`low_val = high_val`).
- Sampled values with 0 decimals were written as floats (`300.0`), and an
  explicit continuous `default` as text.
- A trailing comment holding `if`, such as `default = 0.5  # if needed`, broke
  its parameter.
- A key set only inside an `if` block was taken as the parameter's own.
- An Include `_name` that matched several scenario combinations gave their runs
  one name; it is an error.

## [1.0.1] - 2026-10-13

### Fixed
- Generated `.nav` files now always emit the `Include` keyword in Title case, as required by navigate-zcs; 1.0.0 emitted `INCLUDE`, which navigate rejects.

## [1.0.0] - 2026-08-17

Initial public release of Horizon, an open-source uncertainty analysis tool
for the [Navigate](https://github.com/zerocarbonshipping/navigate-zcs)
maritime transition model.
