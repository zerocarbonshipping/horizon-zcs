<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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

## [1.0.0] - 2026-08-17

Initial public release of Horizon, an open-source uncertainty analysis tool
for the [Navigate](https://github.com/zerocarbonshipping/navigate-zcs)
maritime transition model.
