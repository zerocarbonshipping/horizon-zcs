"""
Study scaffolding shared by the tests that go through the parser.

`write_study` writes `study.hor` into tmp_path: the parameter blocks given, then a Horizon block with three
samples and the Include and Exclude `rules` given, or the `horizon` text in its place. `read_study` writes that
study and reads it with read_hor. The deck the Horizon block names does not exist: reading a study never opens it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from horizon.parser import read_hor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from horizon.parser import HorFile

HORIZON = """
Horizon {{
    UncFilePath = "deck.unc"
    OutputPath = "output"
    NumberOfSamples = 3
    {rules}
}}
"""


@pytest.fixture
def write_study(tmp_path: Path) -> Callable[..., Path]:
    """Write a study into tmp_path and return its path."""

    def write(parameters: str, *, rules: str = "", horizon: str | None = None) -> Path:
        study = tmp_path / "study.hor"
        study.write_text(parameters + (HORIZON.format(rules=rules) if horizon is None else horizon), encoding="utf-8")
        return study

    return write


@pytest.fixture
def read_study(write_study: Callable[..., Path]) -> Callable[..., HorFile]:
    """Write a study into tmp_path and return it as read_hor reads it."""

    def read(parameters: str, *, rules: str = "", horizon: str | None = None) -> HorFile:
        return read_hor(write_study(parameters, rules=rules, horizon=horizon))

    return read
