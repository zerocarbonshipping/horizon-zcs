# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for .sen sensitivity analysis config file parser."""

import os

import pytest

from horizon.exceptions import ParseError, ValidationError
from horizon.sensitivity.config_parser import parse_sensitivity_config


class TestParseSensitivityConfig:
    """Tests for parse_sensitivity_config()."""

    @staticmethod
    def _write_sen(tmp_path, content, filename="config.sen"):
        p = tmp_path / filename
        p.write_text(content)
        return str(p)

    def test_full_config(self, tmp_path):
        path = self._write_sen(tmp_path, """
        # Full config
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            ReportCSV = "report.csv"
            SourceDir = "."
            OutputDir = "output"

            Metric "Emissions reduction" {
                key = "TotalEquivalentWTW"
                aggregation = difference
            }

            Metric "Lifetime emissions" {
                key = "TotalEquivalentWTW"
                aggregation = cumulative
            }

            Metric "Final year" {
                key = "Expenses"
                year = 2050
            }
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.samples_csv.endswith("samples.csv")
        assert config.report_csv.endswith("report.csv")
        assert config.source_dir == str(tmp_path)
        assert config.output_dir.endswith("output")
        assert len(config.metrics) == 3

        diff = config.metrics[0]
        assert diff.metric_key == "TotalEquivalentWTW"
        assert diff.aggregation == "difference"
        assert diff.year is None
        assert diff.display_name == "Emissions reduction"

        cum = config.metrics[1]
        assert cum.aggregation == "cumulative"

        point = config.metrics[2]
        assert point.aggregation == "point"
        assert point.year == 2050

    def test_minimal_config(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            SourceDir = "."
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.samples_csv.endswith("samples.csv")
        assert config.report_csv is None
        assert config.output_dir is None
        assert config.metrics == []

    def test_missing_samples_csv_raises(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SourceDir = "."
        }
        """)
        with pytest.raises(ParseError, match="SamplesCSV"):
            parse_sensitivity_config(path)

    def test_missing_source_dir_defaults_to_sen_dir(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.source_dir == str(tmp_path)

    def test_missing_block_raises(self, tmp_path):
        path = self._write_sen(tmp_path, "SamplesCSV = samples.csv\n")
        with pytest.raises(ParseError, match="SensitivityAnalysis"):
            parse_sensitivity_config(path)

    def test_missing_file_raises(self):
        with pytest.raises(ParseError, match="not found"):
            parse_sensitivity_config("/nonexistent/config.sen")

    def test_invalid_aggregation_raises(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            SourceDir = "."
            Metric "bad" {
                key = "X"
                aggregation = nonsense
            }
        }
        """)
        with pytest.raises(ValidationError, match="nonsense"):
            parse_sensitivity_config(path)

    def test_metric_missing_key_raises(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            SourceDir = "."
            Metric "bad" {
                aggregation = difference
            }
        }
        """)
        with pytest.raises(ParseError, match="key"):
            parse_sensitivity_config(path)

    def test_invalid_year_raises(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            SourceDir = "."
            Metric "bad" {
                key = "X"
                year = abc
            }
        }
        """)
        with pytest.raises(ValidationError, match="integer"):
            parse_sensitivity_config(path)

    def test_comments_stripped(self, tmp_path):
        path = self._write_sen(tmp_path, """
        # This is a comment
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"  # inline comment
            SourceDir = "."
            # Another comment
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.samples_csv.endswith("samples.csv")

    def test_relative_paths_resolved(self, tmp_path):
        sub = tmp_path / "configs"
        sub.mkdir()
        path = self._write_sen(sub, """
        SensitivityAnalysis {
            SamplesCSV = "../data/samples.csv"
            SourceDir = "../runs"
        }
        """)
        config = parse_sensitivity_config(path)
        assert os.path.isabs(config.samples_csv)
        assert "data" in config.samples_csv
        assert "runs" in config.source_dir

    def test_absolute_paths_unchanged(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "/abs/path/samples.csv"
            SourceDir = "/abs/runs"
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.samples_csv == "/abs/path/samples.csv"
        assert config.source_dir == "/abs/runs"

    def test_default_aggregation_is_point(self, tmp_path):
        path = self._write_sen(tmp_path, """
        SensitivityAnalysis {
            SamplesCSV = "samples.csv"
            SourceDir = "."
            Metric "just key" {
                key = "TotalEquivalentWTW"
            }
        }
        """)
        config = parse_sensitivity_config(path)
        assert config.metrics[0].aggregation == "point"
