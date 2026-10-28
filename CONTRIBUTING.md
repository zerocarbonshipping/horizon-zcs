<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Contributing to Horizon

Thank you for your interest in improving Horizon. In principle anything can
be contributed: bug fixes, features, documentation, and examples.
Contributions are made by forking the repository and opening a pull request
against the `dev` branch.

Horizon is a companion tool for
[Navigate](https://github.com/zerocarbonshipping/navigate-zcs). Changes to
the simulation model itself belong in the Navigate repository; Horizon only
covers the uncertainty analysis layer on top of it.

## Getting started

1. Fork the repository and clone your fork.
2. Install it into Navigate's environment with the lint tools:
   `pip install -e ".[dev]"`.
3. Create a branch from `dev` for your change.
4. Verify your setup with `flake8 horizon` and `isort horizon --check-only`.

## Code layout

In `horizon/`:

- `cli.py`: the `run`, `check` and `collect` commands.
- `parser/`: the `.hor` format: its grammar (`grammar.lark`) and syntax
  (`syntax.py`), entry values (`values.py`), parameter blocks
  (`blocks.py`), scenario combinations and labels (`scenarios.py`), and the
  file as a whole (`hor_file.py`).
- `parameters.py` and `sampling.py`: the parameters with their `if`
  overrides, and their Latin hypercube or Monte Carlo sampling; the math of
  the study.
- `study.py`: the runs of a study and its folder.
- `launch.py`: each run's Navigate command, run here or handed to a
  scheduler.
- `check.py`: `horizon check`.
- `collect.py`: `horizon collect`, writing the archive and the Lantern file
  with `results.py` (reading and writing Navigate results files) and
  `summary.py` (the representative sample, the median and the band).

## What to contribute

Any valuable contribution is welcome.

**Bug fixes** can be submitted directly as a pull request. Describe the bug
and how the fix addresses it. If you have found a bug but do not plan to fix
it yourself, please open an issue instead.

**Large features**, such as new sampling methods, new distribution types, and
major refactors, should start as a feature request issue. This lets us align
on scope and design before you invest significant effort. Smaller features
can be submitted directly as a pull request.

## Pull request expectations

- Target the `dev` branch. Maintainers merge `dev` into `main` when a
  release is cut.
- Keep each pull request focused on a single change. Unrelated fixes and
  refactors belong in separate pull requests.
- Write a clear description: what the change does, why it is needed, and how
  it was verified.
- Follow the existing code style (`flake8` configuration and
  [`CODESTYLE.md`](CODESTYLE.md)). Match the conventions of the file you are
  editing.
- Update documentation when behavior changes: the reference in `docs/` for
  user-facing changes and docstrings for code changes.
- Add an entry to `CHANGELOG.md` for user-visible changes. See the
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Checking changes

- Changes that alter sampled values should explain the difference in the
  pull request description.

## Questions

Issues are the preferred way to ask. Whether you are unsure if a change needs
a feature request first, want to discuss a design, or have a question about
the tool, open an issue and we will get back to you.

### Note on AI Tools

The use of Generative AI and related tools is neither encouraged nor
discouraged. However, you are responsible for the quality of your own
contributions, and we kindly ask that you do not clutter the repository with
code or inputs you do not fully understand.
