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
- File generation is substantially faster: `.unc` templates and `.inc`
  include files are tokenized once and rendered per realization instead of
  being regex-scanned line by line for every realization. Generated files
  are byte-identical to before.
- Realization folders are created with one directory round trip each, and
  `simulation_includes/` is only created when an include is actually
  rewritten for that realization — realizations without tokenized includes
  no longer contain an empty `simulation_includes/` folder.

### Fixed
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
