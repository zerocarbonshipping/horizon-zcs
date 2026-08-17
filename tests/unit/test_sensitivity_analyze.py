# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for PRCC sensitivity analysis orchestration."""

import csv
import os

import pytest

from horizon.exceptions import ValidationError
from horizon.sensitivity.analyze import (
    MetricSpec,
    _check_any_report_exists,
    _extract_output_path_from_hor,
    _find_csv,
    _load_metrics_from_collected_report,
    _load_parameter_matrix,
    _match_labels_to_folders,
    _metric_display_name,
    _parse_metric_spec,
    _parse_year_from_date_str,
    _read_collected_report,
    _resolve_collected_report,
    _resolve_metric_specs,
    _scenario_groups,
)


class TestParseMetricSpec:
    """Tests for metric specification parsing."""

    def test_metric_with_year(self):
        spec = _parse_metric_spec("TotalEquivalentWTW@2050")
        assert spec.metric_key == "TotalEquivalentWTW"
        assert spec.year == 2050
        assert spec.display_name == "TotalEquivalentWTW@2050"

    def test_metric_without_year(self):
        spec = _parse_metric_spec("Expenses")
        assert spec.metric_key == "Expenses"
        assert spec.year is None

    def test_metric_with_whitespace(self):
        spec = _parse_metric_spec("  TotalEquivalentWTW @ 2040  ")
        assert spec.metric_key == "TotalEquivalentWTW"
        assert spec.year == 2040

    def test_invalid_year_raises(self):
        with pytest.raises(ValidationError, match="not an integer"):
            _parse_metric_spec("TotalEquivalentWTW@abc")

    def test_empty_metric_key_raises(self):
        with pytest.raises(ValidationError, match="Empty metric key"):
            _parse_metric_spec("@2050")


class TestLoadParameterMatrix:
    """Tests for loading sampled_parameters.csv."""

    def test_basic_loading(self, tmp_path):
        """Load a simple CSV with mixed parameter types."""
        csv_path = tmp_path / "sampled_parameters.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sample", "FUEL", "TEMP", "SPEED"])
            writer.writerow(["parameter_type", "ScenarioParameter", "Continuous", "Continuous"])
            writer.writerow(["sample_1", "oil", "10.5", "25.0"])
            writer.writerow(["sample_2", "oil", "12.3", "30.0"])
            writer.writerow(["sample_3", "gas", "11.0", "27.5"])

        X_df, scenario_df = _load_parameter_matrix(str(tmp_path))

        # Scenario column excluded from X
        assert "FUEL" not in X_df.columns
        assert "TEMP" in X_df.columns
        assert "SPEED" in X_df.columns

        # Scenario column in scenario_df
        assert "FUEL" in scenario_df.columns
        assert len(X_df) == 3

    def test_missing_csv_raises(self, tmp_path):
        """Missing CSV should raise FileOperationError."""
        from horizon.exceptions import FileOperationError
        isolated = tmp_path / "deep" / "nested"
        isolated.mkdir(parents=True)
        with pytest.raises(FileOperationError, match="Cannot find"):
            _load_parameter_matrix(str(isolated))

    def test_explicit_samples_csv(self, tmp_path):
        """Explicit samples_csv path should be used directly."""
        csv_path = tmp_path / "my_custom_samples.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sample", "A", "B"])
            writer.writerow(["parameter_type", "Continuous", "Continuous"])
            writer.writerow(["s1", "1.0", "2.0"])
        X_df, _ = _load_parameter_matrix(str(tmp_path), samples_csv=str(csv_path))
        assert len(X_df) == 1
        assert "A" in X_df.columns

    def test_explicit_samples_csv_not_found(self, tmp_path):
        """Non-existent explicit CSV should raise FileOperationError."""
        from horizon.exceptions import FileOperationError
        with pytest.raises(FileOperationError, match="Specified samples CSV not found"):
            _load_parameter_matrix(str(tmp_path), samples_csv="/nonexistent/file.csv")

    def test_no_scenario_columns(self, tmp_path):
        """CSV with no scenario parameters should return empty scenario_df."""
        csv_path = tmp_path / "sampled_parameters.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sample", "ALPHA", "BETA"])
            writer.writerow(["parameter_type", "Continuous", "Discrete"])
            writer.writerow(["s1", "1.0", "0.5"])
            writer.writerow(["s2", "2.0", "0.8"])

        X_df, scenario_df = _load_parameter_matrix(str(tmp_path))
        assert scenario_df.columns.empty or len(scenario_df.columns) == 0
        assert len(X_df.columns) == 2


class TestScenarioGroups:
    """Tests for scenario grouping logic."""

    def test_no_scenarios_returns_all_only(self):
        import pandas as pd
        scenario_df = pd.DataFrame(index=["s1", "s2", "s3"])
        common = pd.Index(["s1", "s2", "s3"])
        groups = _scenario_groups(scenario_df, common)
        assert "ALL" in groups
        assert len(groups) == 1

    def test_single_scenario_column(self):
        import pandas as pd
        scenario_df = pd.DataFrame(
            {"FUEL": ["oil"] * 5 + ["gas"] * 5},
            index=[f"s{i}" for i in range(10)],
        )
        common = pd.Index([f"s{i}" for i in range(10)])
        groups = _scenario_groups(scenario_df, common)

        assert "ALL" in groups
        assert len(groups["ALL"]) == 10
        assert "gas" in groups
        assert "oil" in groups
        assert len(groups["gas"]) == 5
        assert len(groups["oil"]) == 5

    def test_multi_scenario_columns(self):
        import pandas as pd
        scenario_df = pd.DataFrame(
            {
                "FUEL": ["oil"] * 5 + ["gas"] * 5,
                "POLICY": ["strict"] * 5 + ["lenient"] * 5,
            },
            index=[f"s{i}" for i in range(10)],
        )
        common = pd.Index([f"s{i}" for i in range(10)])
        groups = _scenario_groups(scenario_df, common)

        assert "ALL" in groups
        assert len(groups["ALL"]) == 10
        # Each combo has 5 samples (>= 4), so both should appear
        assert "oil_strict" in groups
        assert "gas_lenient" in groups


class TestCSVDiscovery:
    """Tests for CSV file discovery logic."""

    def test_finds_sampled_parameters_csv(self, tmp_path):
        """Should find sampled_parameters.csv in the directory."""
        (tmp_path / "sampled_parameters.csv").write_text("header\n")
        assert _find_csv(str(tmp_path)).endswith("sampled_parameters.csv")

    def test_finds_samples_csv(self, tmp_path):
        """Should find samples.csv when sampled_parameters.csv is absent."""
        (tmp_path / "samples.csv").write_text("header\n")
        assert _find_csv(str(tmp_path)).endswith("samples.csv")

    def test_finds_csv_in_subdirectory(self, tmp_path):
        """Should find the CSV in a subdirectory like 1_samples/."""
        sub = tmp_path / "1_samples"
        sub.mkdir()
        (sub / "samples.csv").write_text("header\n")
        assert _find_csv(str(tmp_path)).endswith("samples.csv")

    def test_finds_csv_in_parent_directory(self, tmp_path):
        """Should find the CSV in the parent directory."""
        child = tmp_path / "3_run"
        child.mkdir()
        (tmp_path / "samples.csv").write_text("header\n")
        assert _find_csv(str(child)).endswith("samples.csv")

    def test_finds_csv_via_hor_output_path(self, tmp_path):
        """Should extract OutputPath from .hor file and find the CSV there."""
        sub = tmp_path / "custom_output"
        sub.mkdir()
        csv_file = sub / "my_samples.csv"
        csv_file.write_text("header\n")
        hor_file = tmp_path / "run.hor"
        hor_file.write_text(f'OutputPath = "{csv_file}"\n')
        assert _find_csv(str(tmp_path)) == str(csv_file)

    def test_missing_csv_raises(self, tmp_path):
        """Should raise FileOperationError when no CSV is found."""
        from horizon.exceptions import FileOperationError
        isolated = tmp_path / "deep" / "nested"
        isolated.mkdir(parents=True)
        with pytest.raises(FileOperationError, match="Cannot find"):
            _find_csv(str(isolated))


class TestExtractOutputPathFromHor:
    """Tests for .hor OutputPath extraction."""

    def test_extracts_absolute_path(self, tmp_path):
        hor = tmp_path / "test.hor"
        hor.write_text('OutputPath = "/data/output/samples.csv"\n')
        assert _extract_output_path_from_hor(str(hor)) == "/data/output/samples.csv"

    def test_extracts_relative_path(self, tmp_path):
        hor = tmp_path / "test.hor"
        hor.write_text('OutputPath = "1_samples/samples.csv"\n')
        result = _extract_output_path_from_hor(str(hor))
        assert result == os.path.join(str(tmp_path), "1_samples/samples.csv")

    def test_returns_none_for_missing_output_path(self, tmp_path):
        hor = tmp_path / "test.hor"
        hor.write_text("SomeOtherKey = value\n")
        assert _extract_output_path_from_hor(str(hor)) is None


class TestCollectedReport:
    """Tests for collected report (horizon -c output) parsing."""

    @staticmethod
    def _make_collected_csv(path, folder_names, metrics, years, values):
        """Create a minimal collected report CSV.

        Parameters
        ----------
        folder_names : list[str]
            e.g. ["s1_sample001", "s1_sample002"]
        metrics : list[str]
            e.g. ["TotalEquivalentWTW", "Expenses"]
        years : list[int]
            e.g. [2030, 2050]
        values : dict[(folder, metric, year), float]
        """
        n_metrics = len(metrics)
        lines = []

        # Row 0: scenario labels (folder_name_report repeated per metric)
        row0 = ["", ""]
        for fn in folder_names:
            row0.extend([f"{fn}_mtc_metrics"] * n_metrics)
        lines.append(",".join(row0))

        # Row 1: Date, Time (days), global...
        row1 = ["Date", "Time (days)"]
        row1.extend(["global"] * n_metrics * len(folder_names))
        lines.append(",".join(row1))

        # Row 2: metric names
        row2 = ["", ""]
        for _ in folder_names:
            row2.extend(metrics)
        lines.append(",".join(row2))

        # Row 3: empty (sub-metric)
        lines.append(",".join([""] * (2 + n_metrics * len(folder_names))))

        # Row 4: empty spacer
        lines.append(",".join([""] * (2 + n_metrics * len(folder_names))))

        # Row 5+: data rows
        for yi, year in enumerate(years):
            row = [f"01/01/{year} 00.00", str(yi * 365)]
            for fn in folder_names:
                for m in metrics:
                    row.append(str(values.get((fn, m, year), "")))
            lines.append(",".join(row))

        path.write_text("\n".join(lines))

    def test_load_metrics_basic(self, tmp_path):
        """Extract metrics from a small collected report."""
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001", "s1_sample002"]
        metrics = ["TotalEquivalentWTW", "Expenses"]
        years = [2030, 2050]
        vals = {
            ("s1_sample001", "TotalEquivalentWTW", 2050): 100.0,
            ("s1_sample001", "Expenses", 2050): 200.0,
            ("s1_sample002", "TotalEquivalentWTW", 2050): 150.0,
            ("s1_sample002", "Expenses", 2050): 250.0,
        }
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        specs = [
            MetricSpec("TotalEquivalentWTW", 2050, "TotalEquivalentWTW@2050"),
            MetricSpec("Expenses", 2050, "Expenses@2050"),
        ]
        df = _read_collected_report(str(csv_path))
        result = _load_metrics_from_collected_report(df, folders, specs)

        assert len(result) == 2
        assert result["s1_sample001"][specs[0]] == 100.0
        assert result["s1_sample001"][specs[1]] == 200.0
        assert result["s1_sample002"][specs[0]] == 150.0

    def test_folder_name_prefix_matching(self, tmp_path):
        """Scenario labels with suffix should match folder names by prefix."""
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001"]
        metrics = ["TotalEquivalentWTW"]
        years = [2050]
        vals = {("s1_sample001", "TotalEquivalentWTW", 2050): 42.0}
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        specs = [MetricSpec("TotalEquivalentWTW", 2050, "TotalEquivalentWTW@2050")]
        df = _read_collected_report(str(csv_path))
        result = _load_metrics_from_collected_report(df, folders, specs)

        assert "s1_sample001" in result
        assert result["s1_sample001"][specs[0]] == 42.0

    def test_unmatched_labels_return_empty(self, tmp_path):
        """Labels that don't match any folder names produce empty results."""
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001"]
        metrics = ["TotalEquivalentWTW"]
        years = [2050]
        vals = {("s1_sample001", "TotalEquivalentWTW", 2050): 42.0}
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        specs = [MetricSpec("TotalEquivalentWTW", 2050, "TotalEquivalentWTW@2050")]
        df = _read_collected_report(str(csv_path))
        # Pass folder names that don't match the labels
        result = _load_metrics_from_collected_report(
            df, ["totally_different"], specs
        )
        assert len(result) == 0


class TestResolveCollectedReport:
    """Tests for collected report resolution."""

    def test_explicit_path(self, tmp_path):
        report = tmp_path / "my_report.csv"
        report.write_text("data\n")
        assert _resolve_collected_report(str(report), str(tmp_path)) == str(report)

    def test_explicit_path_missing_raises(self, tmp_path):
        from horizon.exceptions import FileOperationError
        with pytest.raises(FileOperationError, match="Specified report CSV not found"):
            _resolve_collected_report("/nonexistent/report.csv", str(tmp_path))

    def test_auto_detect_report_csv(self, tmp_path):
        (tmp_path / "report.csv").write_text("data\n")
        result = _resolve_collected_report(None, str(tmp_path))
        assert result is not None
        assert result.endswith("report.csv")

    def test_auto_detect_report_xlsx(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"fake xlsx")
        result = _resolve_collected_report(None, str(tmp_path))
        assert result is not None
        assert result.endswith("report.xlsx")

    def test_no_collected_report_returns_none(self, tmp_path):
        assert _resolve_collected_report(None, str(tmp_path)) is None


class TestParseYearFromDateStr:
    """Tests for date string year parsing."""

    def test_standard_format(self):
        assert _parse_year_from_date_str("01/01/2050 00.00") == 2050

    def test_year_only(self):
        assert _parse_year_from_date_str("01/06/2030 00.00") == 2030

    def test_none_returns_none(self):
        assert _parse_year_from_date_str(None) is None

    def test_garbage_returns_none(self):
        assert _parse_year_from_date_str("not a date") is None

    def test_dash_separated_format(self):
        assert _parse_year_from_date_str("01-01-2050") == 2050

    def test_dash_separated_with_time(self):
        assert _parse_year_from_date_str("15-06-2030 12.00") == 2030


class TestMatchLabelsToFolders:
    """Tests for label-to-folder matching strategies."""

    def test_direct_match(self):
        labels = ["sample_1", "sample_2"]
        folders = ["sample_1", "sample_2"]
        result = _match_labels_to_folders(labels, folders)
        assert result == {"sample_1": "sample_1", "sample_2": "sample_2"}

    def test_prefix_match(self):
        labels = ["s1_sample001_mtc_metrics"]
        folders = ["s1_sample001"]
        result = _match_labels_to_folders(labels, folders)
        assert result == {"s1_sample001_mtc_metrics": "s1_sample001"}

    def test_sample_number_match(self):
        """Labels like s1_sample001 match folders like sample_1 by number."""
        labels = ["s1_sample001_mtc_metrics", "s1_sample002_mtc_metrics"]
        folders = ["sample_1", "sample_2"]
        result = _match_labels_to_folders(labels, folders)
        assert result["s1_sample001_mtc_metrics"] == "sample_1"
        assert result["s1_sample002_mtc_metrics"] == "sample_2"

    def test_no_match_returns_empty(self):
        labels = ["totally_different"]
        folders = ["sample_1"]
        result = _match_labels_to_folders(labels, folders)
        assert result == {}


class TestCheckAnyReportExists:
    """Tests for early report existence check."""

    def test_no_reports_logs_warning(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _check_any_report_exists(str(tmp_path))
        assert "No Excel reports found" in caplog.text

    def test_with_reports_no_warning(self, tmp_path, caplog):
        import logging
        sub = tmp_path / "s1_sample001" / "reports"
        sub.mkdir(parents=True)
        (sub / "report.xlsx").write_bytes(b"fake")
        with caplog.at_level(logging.WARNING):
            _check_any_report_exists(str(tmp_path))
        assert "No Excel reports found" not in caplog.text


class TestAggregationParsing:
    """Tests for metric aggregation mode parsing."""

    def test_difference_spec(self):
        spec = _parse_metric_spec("TotalEquivalentWTW:difference")
        assert spec.metric_key == "TotalEquivalentWTW"
        assert spec.aggregation == "difference"
        assert spec.year is None
        assert "difference" in spec.display_name

    def test_cumulative_spec(self):
        spec = _parse_metric_spec("Expenses:cumulative")
        assert spec.metric_key == "Expenses"
        assert spec.aggregation == "cumulative"
        assert spec.year is None

    def test_explicit_point_spec(self):
        spec = _parse_metric_spec("TotalEquivalentWTW@2050:point")
        assert spec.metric_key == "TotalEquivalentWTW"
        assert spec.year == 2050
        assert spec.aggregation == "point"

    def test_year_ignored_for_difference(self):
        spec = _parse_metric_spec("TotalEquivalentWTW@2050:difference")
        assert spec.aggregation == "difference"
        assert spec.year is None  # year not applicable

    def test_backward_compat_no_aggregation(self):
        spec = _parse_metric_spec("TotalEquivalentWTW@2050")
        assert spec.aggregation == "point"
        assert spec.year == 2050

    def test_backward_compat_bare_key(self):
        spec = _parse_metric_spec("Expenses")
        assert spec.aggregation == "point"
        assert spec.year is None

    def test_metric_spec_hashable_with_aggregation(self):
        """MetricSpec with aggregation field works as dict key."""
        s1 = MetricSpec("X", 2050, "X@2050", "point")
        s2 = MetricSpec("X", None, "X (difference)", "difference")
        d = {s1: 1.0, s2: 2.0}
        assert d[s1] == 1.0
        assert d[s2] == 2.0

    def test_metric_spec_default_aggregation(self):
        """Old 3-arg construction defaults to 'point'."""
        spec = MetricSpec("X", 2050, "X@2050")
        assert spec.aggregation == "point"

    def test_display_name_difference(self):
        assert _metric_display_name("X", None, "difference") == "X (difference)"

    def test_display_name_cumulative(self):
        assert _metric_display_name("X", None, "cumulative") == "X (cumulative)"

    def test_display_name_point(self):
        assert _metric_display_name("X", 2050, "point") == "X@2050"


class TestResolveMetricSpecsAggregation:
    """Tests for _resolve_metric_specs with aggregation modes."""

    def test_default_metrics_are_tuples(self):
        """Defaults produce difference and cumulative specs without needing year."""
        specs = _resolve_metric_specs(None, lambda: 2050)
        aggs = {s.aggregation for s in specs}
        assert "difference" in aggs
        assert "cumulative" in aggs

    def test_difference_specs_skip_year_probe(self):
        """Difference/cumulative specs don't need year, so probe isn't called."""
        called = []

        def bad_probe():
            called.append(True)
            return None

        specs = _resolve_metric_specs(
            ["TotalEquivalentWTW:difference", "Expenses:cumulative"],
            bad_probe,
        )
        assert not called
        assert all(s.year is None for s in specs)

    def test_mixed_point_and_difference(self):
        """Point spec without year triggers probe; difference does not."""
        specs = _resolve_metric_specs(
            ["TotalEquivalentWTW", "Expenses:difference"],
            lambda: 2050,
        )
        point = [s for s in specs if s.aggregation == "point"]
        diff = [s for s in specs if s.aggregation == "difference"]
        assert len(point) == 1
        assert point[0].year == 2050
        assert len(diff) == 1
        assert diff[0].year is None

    def test_pre_built_metric_specs_passed_through(self):
        """MetricSpec objects are passed through without re-parsing."""
        pre = MetricSpec("X", None, "X (cumulative)", "cumulative")
        specs = _resolve_metric_specs([pre], lambda: 2050)
        assert specs[0] is pre


class TestCollectedReportAggregation:
    """Tests for difference/cumulative extraction from collected reports."""

    @staticmethod
    def _make_collected_csv(path, folder_names, metrics, years, values):
        """Create a minimal collected report CSV."""
        n_metrics = len(metrics)
        lines = []
        row0 = ["", ""]
        for fn in folder_names:
            row0.extend([f"{fn}_mtc_metrics"] * n_metrics)
        lines.append(",".join(row0))
        row1 = ["Date", "Time (days)"]
        row1.extend(["global"] * n_metrics * len(folder_names))
        lines.append(",".join(row1))
        row2 = ["", ""]
        for _ in folder_names:
            row2.extend(metrics)
        lines.append(",".join(row2))
        lines.append(",".join([""] * (2 + n_metrics * len(folder_names))))
        lines.append(",".join([""] * (2 + n_metrics * len(folder_names))))
        for yi, year in enumerate(years):
            row = [f"01/01/{year} 00.00", str(yi * 365)]
            for fn in folder_names:
                for m in metrics:
                    row.append(str(values.get((fn, m, year), "")))
            lines.append(",".join(row))
        path.write_text("\n".join(lines))

    def test_difference_metric(self, tmp_path):
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001"]
        metrics = ["TotalEquivalentWTW"]
        years = [2026, 2030, 2050]
        vals = {
            ("s1_sample001", "TotalEquivalentWTW", 2026): 1000.0,
            ("s1_sample001", "TotalEquivalentWTW", 2030): 800.0,
            ("s1_sample001", "TotalEquivalentWTW", 2050): 500.0,
        }
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        spec = MetricSpec("TotalEquivalentWTW", None,
                          "TotalEquivalentWTW (difference)", "difference")
        df = _read_collected_report(str(csv_path))
        result = _load_metrics_from_collected_report(df, folders, [spec])

        # difference = last_year - first_year = 500 - 1000 = -500
        assert result["s1_sample001"][spec] == pytest.approx(-500.0)

    def test_cumulative_metric(self, tmp_path):
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001"]
        metrics = ["Expenses"]
        years = [2026, 2030, 2050]
        vals = {
            ("s1_sample001", "Expenses", 2026): 100.0,
            ("s1_sample001", "Expenses", 2030): 200.0,
            ("s1_sample001", "Expenses", 2050): 300.0,
        }
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        spec = MetricSpec("Expenses", None,
                          "Expenses (cumulative)", "cumulative")
        df = _read_collected_report(str(csv_path))
        result = _load_metrics_from_collected_report(df, folders, [spec])

        # cumulative = sum of all years = 100 + 200 + 300 = 600
        assert result["s1_sample001"][spec] == pytest.approx(600.0)

    def test_mixed_point_and_difference(self, tmp_path):
        csv_path = tmp_path / "report.csv"
        folders = ["s1_sample001"]
        metrics = ["TotalEquivalentWTW"]
        years = [2026, 2050]
        vals = {
            ("s1_sample001", "TotalEquivalentWTW", 2026): 1000.0,
            ("s1_sample001", "TotalEquivalentWTW", 2050): 500.0,
        }
        self._make_collected_csv(csv_path, folders, metrics, years, vals)

        specs = [
            MetricSpec("TotalEquivalentWTW", 2050,
                       "TotalEquivalentWTW@2050", "point"),
            MetricSpec("TotalEquivalentWTW", None,
                       "TotalEquivalentWTW (difference)", "difference"),
        ]
        df = _read_collected_report(str(csv_path))
        result = _load_metrics_from_collected_report(df, folders, specs)

        assert result["s1_sample001"][specs[0]] == pytest.approx(500.0)
        assert result["s1_sample001"][specs[1]] == pytest.approx(-500.0)
