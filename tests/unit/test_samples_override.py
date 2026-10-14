# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""--samples replaces NumberOfSamples for one run and is logged; the file is not edited.

The CLI flag itself is covered in test_cli_errors.py."""

import logging

from horizon.test_manager.create_files import create_files

HOR = """ContinuousParameter "a" {
    distribution = UNIFORM
    low_val = 0.0
    high_val = 1.0
    default = 0.5
}

Horizon {
    UncFilePath = "run.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 300
}
"""


def test_samples_overrides_number_of_samples_and_logs_it(tmp_path, caplog):
    hor = tmp_path / "run.hor"
    hor.write_text(HOR)

    with caplog.at_level(logging.INFO):
        create_files(str(hor), dry_run=True, samples=3)

    assert "NumberOfSamples overridden by --samples: 3 (the file says 300)" in caplog.text
    assert "Total realizations to generate: 3" in caplog.text
    assert hor.read_text() == HOR


def test_without_samples_the_file_value_is_used(tmp_path, caplog):
    hor = tmp_path / "run.hor"
    hor.write_text(HOR.replace("300", "2"))

    with caplog.at_level(logging.INFO):
        create_files(str(hor), dry_run=True)

    assert "overridden" not in caplog.text
    assert "Total realizations to generate: 2" in caplog.text
