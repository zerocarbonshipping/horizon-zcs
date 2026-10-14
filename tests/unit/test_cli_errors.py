# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""CLI behaviour: a broken .hor file gives one clean error line, exit code 1 and the traceback only in
output.log; --samples overrides NumberOfSamples."""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BROKEN_HOR = """ContinuousParameter "a" {
    low_val = 0.1
    hgh_val = 0.5
}

Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 10
}
"""


def run_horizon(directory, *arguments):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run([sys.executable, "-m", "horizon", *arguments], cwd=directory, env=env,
                          capture_output=True, text=True)


def error_lines(result):
    """stderr without the INFO log lines (the log setup announces output.log)."""
    return [line for line in result.stderr.splitlines() if " - INFO - " not in line]


def test_parse_error_prints_one_line_and_exits_1(tmp_path):
    (tmp_path / "run.hor").write_text(BROKEN_HOR)

    result = run_horizon(tmp_path, "run.hor")

    assert result.returncode == 1
    assert result.stdout == ""
    assert error_lines(result) == [
        "run.hor:3: error: unknown key 'hgh_val' in ContinuousParameter \"a\". Did you mean 'high_val'?"]
    assert "Traceback" in (tmp_path / "output.log").read_text()


def test_error_without_location_gets_the_file_name(tmp_path):
    (tmp_path / "run.hor").write_text("ContinuousParameter \"a\" {\n}\n")

    result = run_horizon(tmp_path, "run.hor")

    assert result.returncode == 1
    assert error_lines(result) == ["run.hor: error: Horizon block not defined."]


def test_missing_file_is_a_clean_error(tmp_path):
    result = run_horizon(tmp_path, "nope.hor")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_samples_flag(tmp_path):
    (tmp_path / "run.hor").write_text(BROKEN_HOR.replace("hgh_val = 0.5", "high_val = 0.5\n    default = 0.2"))

    result = run_horizon(tmp_path, "run.hor", "--dry-run", "--samples", "1")

    assert result.returncode == 0, result.stderr
    assert "overridden by --samples: 1" in result.stderr
    assert "Total realizations to generate: 1" in result.stderr
