"""
Horizon: uncertainty studies of the Navigate maritime transition model, run through any queue.

- cli.py: the run, check and collect commands.
- parser/: the .hor format: its grammar (grammar.lark) and syntax (syntax.py), entry values (values.py), parameter
  blocks (blocks.py), scenario combinations and labels (scenarios.py), and the file as a whole (hor_file.py).
- parameters.py and sampling.py: the parameters with their `if` overrides, and their Latin hypercube or Monte Carlo
  sampling; the math of the study.
- study.py: the runs of a study and its folder.
- launch.py: each run's Navigate command, run here or handed to a scheduler.
- check.py: horizon check.
- collect.py: horizon collect, writing the archive and the Lantern file with results.py (reading and writing Navigate
  results files) and summary.py (the representative sample, the median and the band).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("horizon-zcs")
except PackageNotFoundError:
    __version__ = "unknown"
