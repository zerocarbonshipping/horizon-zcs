<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Tests

Run the suite from the repository root:

```bash
pytest
```

No external tools are needed — the tests run without Navigate or pueue
installed.

## Layout

- `unit/` — unit tests per module: sampling math (`test_sampler.py`,
  including the triangular PPF and per-scenario override resolution), PRCC
  statistics (`test_prcc.py`), Include/Exclude semantics
  (`test_exclusions.py`), parsers (`test_parser.py`,
  `test_sensitivity_config.py`), report/CSV handling
  (`test_sensitivity_analyze.py`), CLI flag plumbing (`test_solver_flag.py`,
  `test_replot.py`), and validation utilities.
- `test_calibration_recommendations.py` — tests for the calibration scoring
  and recommendation logic, kept at the top level because they exercise the
  calibration workflow end to end rather than a single module.
- `conftest.py` — shared fixtures: parameter objects, temporary `.hor`
  files, edge-case numeric values, and a fixed random seed for
  reproducibility.

## Coverage

Coverage reporting is available via pytest-cov (installed with the `dev`
extra):

```bash
pytest --cov=horizon --cov-report=term-missing
```
