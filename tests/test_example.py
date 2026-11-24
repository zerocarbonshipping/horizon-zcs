"""The example study through the installed horizon command: check and sample it, without Navigate or a queue."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

EXAMPLE = Path(__file__).parent.parent / "examples" / "uncertainty"
STUDY = "fuel_cost_study.hor"


def _horizon(*args: str, cwd: Path = EXAMPLE) -> subprocess.CompletedProcess[str]:
    """Run the horizon command in a folder."""
    return subprocess.run([sys.executable, "-m", "horizon", *args], cwd=cwd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def study() -> dict[str, Any]:
    """Return study.json of the example as a dry run prints it."""
    result = _horizon("run", STUDY, "--dry-run")
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def test_check_passes() -> None:
    """Check reads the study and its deck and finds nothing wrong."""
    result = _horizon("check", STUDY)
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip().endswith("50 runs, 0 errors, 0 warnings")


def test_dry_run_writes_nothing(study: dict[str, Any]) -> None:
    """A dry run leaves no study folder behind."""
    assert not (EXAMPLE / "output").exists()


def test_every_combination_gets_its_samples(study: dict[str, Any]) -> None:
    """Each scenario combination gets NumberOfSamples runs, named <label>_sample<NNN>."""
    runs = study["runs"]
    assert len(runs) == 50
    assert runs[0]["run_id"] == "baseline_sample001"
    assert runs[-1]["run_id"] == "carbon_levy_sample025"


def test_first_sample_holds_the_defaults(study: dict[str, Any]) -> None:
    """Sample 1 holds every default; DEMAND_GROWTH has none, so it takes mid_val."""
    assert study["runs"][0]["values"] == {
        "POLICY": "baseline",
        "FUEL_PRICE": "1.0",
        "DEMAND_GROWTH": "0.025",
        "RENEWAL": "0.05",
    }


def test_if_block_moves_the_range(study: dict[str, Any]) -> None:
    """Under carbon_levy, the `if` block samples FUEL_PRICE from its own range."""
    prices: dict[str, list[float]] = {"baseline": [], "carbon_levy": []}
    for run in study["runs"]:
        prices[run["run"]].append(float(run["values"]["FUEL_PRICE"]))

    assert min(prices["baseline"]) >= 0.8
    assert max(prices["baseline"]) <= 1.5
    assert min(prices["carbon_levy"]) >= 1.0
    assert max(prices["carbon_levy"]) <= 2.0


def test_the_seed_reproduces_the_samples(study: dict[str, Any]) -> None:
    """RandomSeed makes a second dry run sample the same values."""
    again = json.loads(_horizon("run", STUDY, "--dry-run").stdout)
    assert again["runs"] == study["runs"]


def test_a_broken_study_is_one_line(tmp_path: Path) -> None:
    """A study that does not validate prints one `file:line: error:` line and exits 1."""
    (tmp_path / "broken.hor").write_text('ContinuousParameter "X" { low_val = 2 high_val = 1 }\n', encoding="utf-8")
    result = _horizon("check", "broken.hor", cwd=tmp_path)
    assert result.returncode == 1
    assert result.stdout.strip().count("\n") == 0
    assert result.stdout.startswith("broken.hor:")
