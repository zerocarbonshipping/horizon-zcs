# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for `horizon --check` and `--explain`: parse validation plus .unc/.inc cross-file checks."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from horizon.check.check import run_check

REPO_ROOT = Path(__file__).resolve().parents[2]

HOR = """
ScenarioParameter "policy" {
    values = [bau, nzf]
}

ContinuousParameter "price" {
    distribution = TRIANGULAR
    low_val = 1.0
    mid_val = 2.0
    high_val = 4.0
    if policy = nzf {
        low_val = 2.0
        mid_val = 3.0
        high_val = 5.0
    }
}

ContinuousParameter "growth" {
    distribution = UNIFORM
    low_val = 0.0
    high_val = 1.0
    default = 0.5
}

Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 10
}
"""

UNC = """Title "study"
    Include "inc/policy_%policy%.inc"
    #Include "inc/not_there.inc"
    Include "inc/fixed.inc"   # trailing comment
    # a comment naming %ghost%
    Load x(%price%)
"""


@pytest.fixture
def study(tmp_path):
    """A valid study: run.hor, run.unc and the include files."""
    (tmp_path / "run.hor").write_text(HOR)
    (tmp_path / "run.unc").write_text(UNC)
    (tmp_path / "inc").mkdir()
    for name in ("policy_bau.inc", "policy_nzf.inc"):
        (tmp_path / "inc" / name).write_text("x = 1\n")
    (tmp_path / "inc" / "fixed.inc").write_text("g = %growth%\n")
    return tmp_path


def check(study, **kwargs):
    old = os.getcwd()
    os.chdir(study)
    try:
        return run_check("run.hor", **kwargs)
    finally:
        os.chdir(old)


def test_valid_study_is_ok(study):
    report, code = check(study)

    assert code == 0
    assert report == "run.hor: ok: 2 sample parameters, 1 scenario parameters, 0 errors, 0 warnings\n"


def test_token_without_parameter_is_an_error_in_unc_and_in_includes(study):
    (study / "run.unc").write_text(UNC + "    Load y(%nope%)\n")
    (study / "inc" / "fixed.inc").write_text("g = %growth% %also_nope%\n")

    report, code = check(study)

    assert code == 1
    assert "run.unc:7: error: token %nope% has no matching parameter in run.hor" in report
    assert "inc/fixed.inc:1: error: token %also_nope% has no matching parameter" in report
    assert "%ghost%" not in report


def test_unused_parameter_is_a_warning(study):
    (study / "inc" / "fixed.inc").write_text("g = 1\n")

    report, code = check(study)

    assert code == 0
    assert "run.hor: warning: parameter 'growth' is not used in run.unc or the files it includes" in report
    assert report.endswith("0 errors, 1 warnings\n")


def test_scenario_parameter_used_only_in_if_conditions_is_not_a_warning(study):
    (study / "run.unc").write_text(UNC.replace('    Include "inc/policy_%policy%.inc"\n', ''))

    report, code = check(study)

    assert code == 0
    assert report.endswith("0 errors, 0 warnings\n")


def test_tokens_in_commented_out_include_are_not_followed(study):
    (study / "inc" / "not_there.inc").write_text("%unknown_in_unused_file%\n")
    (study / "run.unc").write_text(UNC.replace("#Include", "# Include"))

    report, code = check(study)

    assert code == 0, report


def test_scenario_value_without_include_file_is_an_error(study):
    (study / "inc" / "policy_nzf.inc").unlink()

    report, code = check(study)

    assert code == 1
    assert "run.unc:2: error: include 'inc/policy_nzf.inc' for policy = 'nzf': not found" in report


def test_include_file_with_wrong_case_is_an_error(study):
    (study / "inc" / "policy_nzf.inc").rename(study / "inc" / "policy_NZF.inc")

    report, code = check(study)

    assert code == 1
    assert "for policy = 'nzf': case differs from 'policy_NZF.inc'" in report


def test_static_include_that_does_not_exist_is_an_error(study):
    (study / "inc" / "fixed.inc").unlink()

    report, code = check(study)

    assert code == 1
    assert "run.unc:4: error: include 'inc/fixed.inc': not found" in report


def test_missing_unc_is_an_error(study):
    (study / "run.unc").unlink()

    report, code = check(study)

    assert code == 1
    assert "run.hor: error: UncFilePath 'run.unc' not found" in report


def test_parse_errors_are_reported_and_fail(study):
    (study / "run.hor").write_text(HOR.replace("high_val = 1.0", "hgh_val = 1.0"))

    report, code = check(study)

    assert code == 1
    assert report == ("run.hor:21: error: unknown key 'hgh_val' in ContinuousParameter \"growth\". "
                      "Did you mean 'high_val'?\n")


def test_parser_warnings_are_listed(study):
    (study / "run.hor").write_text(HOR.replace("NumberOfSamples = 10", "NumberOfSamples = 10\n    Exclude(policy = typo)"))

    report, code = check(study)

    assert code == 0
    assert "run.hor: warning: Exclude rule references value 'typo' for token 'policy'" in report


def test_explain_prints_resolved_rows_per_scenario_value(study):
    report, code = check(study, check=False, explain=True)

    assert code == 0
    lines = report.splitlines()
    assert lines[0] == "scenario policy: bau, nzf"
    assert lines[1].split() == ["parameter", "scenario", "dist", "low", "mode", "high", "default", "dec", "from"]
    bau, nzf, growth = lines[2:5]
    assert bau.split()[:8] == ["price", "policy=bau", "triangular", "1", "2", "4", "2.0", "4"]
    assert "base" in bau and "default from mid_val" in bau
    assert nzf.split()[:8] == ["price", "policy=nzf", "triangular", "2", "3", "5", "3.0", "4"]
    assert "if policy = nzf (low_val, mid_val, high_val)" in nzf
    assert growth.split()[:8] == ["growth", "*", "uniform", "0", "-", "1", "0.5", "4"]
    assert "run.hor: ok" not in report  # explain alone prints no check summary


def test_explain_with_check_adds_the_summary(study):
    report, code = check(study, explain=True)

    assert code == 0
    assert "triangular" in report and report.endswith("0 errors, 0 warnings\n")


def run_cli(directory, *arguments):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run([sys.executable, "-m", "horizon", *arguments], cwd=directory, env=env,
                          capture_output=True, text=True)


def test_cli_check_writes_nothing_and_sets_exit_code(study):
    before = sorted(p.name for p in study.iterdir())

    ok = run_cli(study, "run.hor", "--check")
    (study / "inc" / "fixed.inc").unlink()
    failed = run_cli(study, "run.hor", "--check")

    assert (ok.returncode, failed.returncode) == (0, 1)
    assert ok.stdout.endswith("0 errors, 0 warnings\n") and "run.unc:4: error" in failed.stdout
    assert sorted(p.name for p in study.iterdir()) == before  # no output.log, no samples


def test_cli_explain_alone_with_parse_error_exits_1(study):
    (study / "run.hor").write_text(HOR.replace("high_val = 1.0", "hgh_val = 1.0"))

    result = run_cli(study, "run.hor", "--explain")

    assert result.returncode == 1
    assert result.stdout.startswith("run.hor:21: error: unknown key 'hgh_val'")
