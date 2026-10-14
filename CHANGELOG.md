<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
  in `simulation_includes/`; `--no-shared-includes` restores full
  per-realization copies.
- Rewritten include filenames now carry the source file's stem (e.g.
  `policy_POLICY_sample_1.inc` instead of `POLICY_sample_1.inc`), with a
  short hash suffix when two sources share a stem or the include path is
  tokenized.
- The generation thread pool sizes itself to the output filesystem: a
  startup latency probe detects network storage and raises the pool from
  `min(8, CPU)` to `min(64, 4×CPU)` there, where generation is bound by
  file-creation round trips and blocked threads are free. `--gen-workers`
  still overrides.
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

### Fixed
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
- Templates still using `INCLUDE` (or lowercase `include`) are normalized to
  `Include` on generation, and the generated include lines now keep the
  template's own indentation.
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
