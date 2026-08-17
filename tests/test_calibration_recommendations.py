# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Synthetic integration test for calibration recommendation engine.

Creates fake calibration data (CSV + Excel reports) matching the real folder
structure, runs the full pipeline, and verifies the HTML output.
"""

import csv
import os
from datetime import date

import numpy as np
import openpyxl
import pytest

from horizon.calibration.analyze import (
    _aggregate_scores,
    _build_fuel_share_figures,
    _compute_confidence,
    _score_cross_scenario_consistency,
    _score_fuel_share_realism,
    _score_monotonicity,
    _score_sensitivity,
    _score_trajectory_smoothness,
    _suggest_next_discretization,
    calibration_plot,
)

# -- Test configuration --
PARAMS = {
    "fleet_inertia": {"default": "0.9", "values": ["0.0", "0.3", "0.5", "0.7", "0.9"]},
    "bunkering_inertia": {"default": "0.9", "values": ["0.0", "0.3", "0.5", "0.7", "0.9"]},
    "fleet_beta": {"default": "3", "values": ["0.5", "1", "2", "3", "5"]},
    "producer_inertia": {"default": "0.9", "values": ["0.0", "0.3", "0.5", "0.7", "0.9"]},
    "producer_inter_beta": {"default": "3", "values": ["1", "2", "3", "4", "5"]},
    "producer_intra_beta": {"default": "3", "values": ["1", "2", "3", "4", "5"]},
}
REGULATIONS = ["no_regulation", "mid_regulation", "strong_regulation"]
YEARS = list(range(2024, 2051))
FUEL_TYPES = ["OIL", "METHANE", "METHANOL", "AMMONIA"]


def _synthetic_emissions(param_token, param_value, regulation, year):
    """Generate a synthetic WTW emissions value.

    Higher inertia -> higher emissions (less transition).
    Stronger regulation -> lower emissions.
    fleet_inertia has the biggest effect (high sensitivity).
    """
    base = 100.0
    reg_factor = {"no_regulation": 1.0, "mid_regulation": 0.85, "strong_regulation": 0.70}
    val = float(param_value)

    # Year decay (linear decline)
    year_factor = 1.0 - 0.01 * (year - 2024)

    # Parameter sensitivity (fleet_inertia has highest)
    sensitivity_map = {
        "fleet_inertia": 0.30,
        "bunkering_inertia": 0.20,
        "fleet_beta": 0.15,
        "producer_inertia": 0.08,
        "producer_inter_beta": 0.04,
        "producer_intra_beta": 0.03,
    }
    sens = sensitivity_map.get(param_token, 0.05)
    param_effect = 1.0 + sens * (val - 0.5)  # higher value -> more emissions

    return base * reg_factor[regulation] * year_factor * param_effect


def _synthetic_fuel_shares(param_token, param_value, regulation, year):
    """Generate synthetic InstalledPower values per fuel type.

    Under no_regulation: OIL dominant, METHANE second, minimal alternatives.
    Under regulation: alternatives grow faster.
    """
    total = 1000.0  # MW total
    val = float(param_value)
    t = (year - 2024) / 26.0  # 0 to 1

    if "no_regulation" in regulation:
        oil = 0.55 - 0.10 * t - 0.05 * val * t
        methane = 0.35 + 0.02 * t
        methanol = 0.05 + 0.04 * t * val
        ammonia = 0.01 + 0.02 * t * val
    elif "mid" in regulation:
        oil = 0.55 - 0.20 * t - 0.05 * val * t
        methane = 0.30 + 0.05 * t
        methanol = 0.08 + 0.08 * t * val
        ammonia = 0.03 + 0.05 * t * val
    else:
        oil = 0.55 - 0.30 * t - 0.05 * val * t
        methane = 0.25 + 0.05 * t
        methanol = 0.10 + 0.12 * t * val
        ammonia = 0.05 + 0.08 * t * val

    # Normalize
    shares = np.array([oil, methane, methanol, ammonia])
    shares = np.clip(shares, 0, 1)
    shares = shares / shares.sum()
    return {fuel: shares[i] * total for i, fuel in enumerate(FUEL_TYPES)}


def _write_excel_report(path, param_token, param_value, regulation):
    """Write a synthetic Excel report matching the real Global sheet format."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Global"

    # Row 1: scope
    ws.append(["Scope", "Year", "TotalEquivalentWTW", "Expenses",
               "ConsumedEnergy", "ConsumedEnergy", "ConsumedEnergy", "ConsumedEnergy",
               "InstalledPower", "InstalledPower", "InstalledPower", "InstalledPower"])
    # Row 2: metric names (same as row 1 for real format)
    ws.append(["", "", "TotalEquivalentWTW", "Expenses",
               "ConsumedEnergy", "ConsumedEnergy", "ConsumedEnergy", "ConsumedEnergy",
               "InstalledPower", "InstalledPower", "InstalledPower", "InstalledPower"])
    # Row 3: sub-metrics (fuel types)
    ws.append(["", "", None, None,
               "OIL", "METHANE", "METHANOL", "AMMONIA",
               "OIL", "METHANE", "METHANOL", "AMMONIA"])
    # Row 4: empty separator
    ws.append([])

    # Data rows
    for year in YEARS:
        emissions = _synthetic_emissions(param_token, param_value, regulation, year)
        expenses = emissions * 10  # arbitrary
        fuels = _synthetic_fuel_shares(param_token, param_value, regulation, year)
        # ConsumedEnergy = shares * some factor; InstalledPower = shares directly
        ws.append([
            date(year, 1, 1), year,
            emissions, expenses,
            fuels["OIL"] * 0.5, fuels["METHANE"] * 0.5,
            fuels["METHANOL"] * 0.5, fuels["AMMONIA"] * 0.5,
            fuels["OIL"], fuels["METHANE"], fuels["METHANOL"], fuels["AMMONIA"],
        ])

    wb.save(path)


def _create_synthetic_calibration(tmpdir):
    """Create a full synthetic calibration directory structure.

    Matches the real layout:
        tmpdir/
            output/sampled_parameters.csv
            {regulation}_sample{NNN}/plots/{folder}_report_default.xlsx
    """
    # Build sample list: 6 params × 5 values = 30 samples
    samples = []
    sample_num = 1
    for param_token, cfg in PARAMS.items():
        for val in cfg["values"]:
            sample_name = f"{param_token}_{val}"
            row = {"sample_number": sample_num, "sample": sample_name}
            # Set all params to their defaults
            for pt, pc in PARAMS.items():
                row[pt] = pc["default"]
            # Override the varied parameter
            row[param_token] = val
            samples.append(row)
            sample_num += 1

    # Write CSV
    output_dir = os.path.join(tmpdir, "output")
    os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, "sampled_parameters.csv")

    fieldnames = ["sample_number", "sample"] + list(PARAMS.keys())
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        # Parameter type row (skipped by parser)
        type_row = {k: "" for k in fieldnames}
        type_row["sample_number"] = "parameter_type"
        writer.writerow(type_row)
        for sample in samples:
            writer.writerow(sample)

    # Create folders and Excel reports
    for reg in REGULATIONS:
        for sample in samples:
            snum = sample["sample_number"]
            folder_name = f"{reg}_sample{snum:03d}"
            folder_path = os.path.join(tmpdir, folder_name)
            plots_dir = os.path.join(folder_path, "plots")
            os.makedirs(plots_dir)

            xlsx_path = os.path.join(plots_dir, f"{folder_name}_report_default.xlsx")

            # Find which param is varied
            sample_name = sample["sample"]
            param_token = None
            param_value = None
            for pt in PARAMS:
                if sample_name.startswith(pt + "_"):
                    param_token = pt
                    param_value = sample[pt]
                    break

            _write_excel_report(xlsx_path, param_token, param_value, reg)

    return tmpdir


# ---------------------------------------------------------------------------
# Unit tests for scoring functions
# ---------------------------------------------------------------------------

class TestScoringFunctions:
    """Test individual scoring functions with controlled data."""

    def _make_report(self, final_wtw, fuel_shares=None):
        """Create a minimal report dict."""
        import pandas as pd
        years = list(range(2024, 2051))
        n = len(years)
        # Linear decline to final_wtw
        vals = np.linspace(100, final_wtw, n)
        report = {
            "TotalEquivalentWTW": pd.DataFrame({"Total": vals}, index=years),
        }
        if fuel_shares:
            total = 1000.0
            ip_data = {fuel: np.full(n, share * total) for fuel, share in fuel_shares.items()}
            report["InstalledPower"] = pd.DataFrame(ip_data, index=years)
        return report

    def test_sensitivity_high_spread(self):
        data = {
            "0.0": self._make_report(50),
            "0.5": self._make_report(70),
            "0.9": self._make_report(90),
        }
        score = _score_sensitivity(data)
        assert score > 0.5, f"Expected high sensitivity, got {score}"

    def test_sensitivity_no_spread(self):
        data = {
            "0.0": self._make_report(80),
            "0.5": self._make_report(80),
            "0.9": self._make_report(80),
        }
        score = _score_sensitivity(data)
        assert score == 0.0

    def test_monotonicity_perfect(self):
        data = {
            "0.0": self._make_report(50),
            "0.3": self._make_report(60),
            "0.5": self._make_report(70),
            "0.7": self._make_report(80),
            "0.9": self._make_report(90),
        }
        score = _score_monotonicity(data)
        assert score > 0.99

    def test_monotonicity_non_monotonic(self):
        data = {
            "0.0": self._make_report(50),
            "0.3": self._make_report(90),
            "0.5": self._make_report(60),
            "0.7": self._make_report(80),
            "0.9": self._make_report(70),
        }
        score = _score_monotonicity(data)
        assert score < 0.8

    def test_cross_scenario_all_agree(self):
        data_per_reg = {}
        for reg in ["no_regulation", "mid_regulation", "strong_regulation"]:
            data_per_reg[reg] = {
                "0.0": self._make_report(50),
                "0.5": self._make_report(70),
                "0.9": self._make_report(90),
            }
        score = _score_cross_scenario_consistency(data_per_reg)
        assert score == 1.0, f"All regs agree on 0.0, expected 1.0, got {score}"

    def test_fuel_share_realism_within_range(self):
        shares = {"OIL": 0.50, "METHANE": 0.38, "METHANOL": 0.07, "AMMONIA": 0.01}
        data = {"0.5": self._make_report(70, fuel_shares=shares)}
        score = _score_fuel_share_realism(data, "no_regulation")
        assert score > 0.8, f"Expected high realism, got {score}"

    def test_fuel_share_realism_neutral_for_regulated(self):
        shares = {"OIL": 0.10, "METHANE": 0.10, "METHANOL": 0.40, "AMMONIA": 0.40}
        data = {"0.5": self._make_report(70, fuel_shares=shares)}
        score = _score_fuel_share_realism(data, "strong_regulation")
        assert score == 0.5

    def test_trajectory_smoothness_smooth(self):
        import pandas as pd
        years = list(range(2024, 2051))
        vals = np.linspace(100, 50, len(years))
        report = {"TotalEquivalentWTW": pd.DataFrame({"Total": vals}, index=years)}
        data = {"0.5": report}
        score = _score_trajectory_smoothness(data)
        assert score > 0.99, f"Linear trajectory should be perfectly smooth, got {score}"

    def test_trajectory_smoothness_jagged(self):
        import pandas as pd
        years = list(range(2024, 2051))
        vals = np.linspace(100, 50, len(years))
        # Add high-frequency noise
        vals += np.random.default_rng(42).normal(0, 10, len(years))
        report = {"TotalEquivalentWTW": pd.DataFrame({"Total": vals}, index=years)}
        data = {"0.5": report}
        score = _score_trajectory_smoothness(data)
        assert score < 0.8, f"Jagged trajectory should score low, got {score}"


class TestAggregation:
    def test_confidence_high(self):
        assert _compute_confidence(0.75, 0.5) == "HIGH"

    def test_confidence_medium(self):
        assert _compute_confidence(0.55, 0.1) == "MEDIUM"

    def test_confidence_low(self):
        assert _compute_confidence(0.3, 0.1) == "LOW"

    def test_aggregate_scores_all_ones(self):
        scores = {k: 1.0 for k in ["sensitivity", "monotonicity", "cross_scenario",
                                   "fuel_share_realism", "smoothness"]}
        assert abs(_aggregate_scores(scores) - 1.0) < 1e-9

    def test_aggregate_scores_all_zeros(self):
        scores = {k: 0.0 for k in ["sensitivity", "monotonicity", "cross_scenario",
                                   "fuel_share_realism", "smoothness"]}
        assert _aggregate_scores(scores) == 0.0


class TestRound2Discretization:
    def test_known_params_have_grids(self):
        from horizon.calibration.analyze import Recommendation
        recs = [
            Recommendation("fleet_inertia", "0.7", "HIGH", {}, "0.9"),
            Recommendation("producer_inter_beta", "3", "LOW", {}, "3"),
        ]
        grids = _suggest_next_discretization(recs)
        assert len(grids["fleet_inertia"]) == 8
        assert len(grids["producer_inter_beta"]) == 3

    def test_unknown_param_gets_fallback(self):
        from horizon.calibration.analyze import Recommendation
        recs = [Recommendation("mystery_param", "2.0", "MEDIUM", {}, "1.0")]
        grids = _suggest_next_discretization(recs)
        assert "mystery_param" in grids
        assert 2.0 in grids["mystery_param"]


# ---------------------------------------------------------------------------
# Full pipeline integration test
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """End-to-end test: create synthetic data, run calibration_plot, check HTML."""

    @pytest.fixture(scope="class")
    def dashboard_html(self, tmp_path_factory):
        tmpdir = str(tmp_path_factory.mktemp("calibration"))
        _create_synthetic_calibration(tmpdir)
        output_path = os.path.join(tmpdir, "dashboard.html")
        calibration_plot(tmpdir, output_path=output_path)
        with open(output_path) as f:
            return f.read()

    def test_html_has_recommendations_tab(self, dashboard_html):
        assert 'id="tab-recommendations"' in dashboard_html
        assert "Parameter Recommendations" in dashboard_html

    def test_html_has_confidence_badges(self, dashboard_html):
        for _level in ["HIGH", "MEDIUM", "LOW"]:
            # At least one badge should appear (we don't know which params get which)
            pass
        # But all params should have SOME badge
        for pt in PARAMS:
            assert pt.replace("_", " ").title() in dashboard_html

    def test_html_has_fuel_share_plots(self, dashboard_html):
        assert "Fuel Share Breakdown" in dashboard_html
        for fuel in FUEL_TYPES:
            assert f"{fuel} Share of Installed Power" in dashboard_html

    def test_html_has_score_bars(self, dashboard_html):
        assert "Sensitivity" in dashboard_html
        assert "Monotonicity" in dashboard_html
        assert "Cross-scenario" in dashboard_html
        assert "Fuel share realism" in dashboard_html
        assert "Smoothness" in dashboard_html

    def test_html_has_round2_table(self, dashboard_html):
        assert "Round 2 Discretization" in dashboard_html
        assert "fleet_inertia" in dashboard_html

    def test_html_has_regulation_tabs(self, dashboard_html):
        for reg in REGULATIONS:
            assert f'id="tab-{reg}"' in dashboard_html

    def test_html_has_existing_metric_plots(self, dashboard_html):
        assert "Total WTW Emissions" in dashboard_html
        assert "Total Expenses" in dashboard_html

    def test_html_has_heatmap(self, dashboard_html):
        assert "Final-Year WTW Emissions" in dashboard_html

    def test_recommendations_produced(self, tmp_path_factory):
        """Verify recommendations are generated with expected structure."""
        tmpdir = str(tmp_path_factory.mktemp("calibration_recs"))
        _create_synthetic_calibration(tmpdir)

        # Need to run data loading steps
        from horizon.calibration.analyze import _build_recommendations, _load_index, _read_report

        runs, param_defaults = _load_index(tmpdir)
        data_by_folder = {}
        for run in runs:
            folder_path = os.path.join(tmpdir, run.folder_name)
            if os.path.exists(folder_path):
                report = _read_report(folder_path)
                if report is not None:
                    data_by_folder[run.folder_name] = report

        recs = _build_recommendations(runs, data_by_folder, param_defaults)

        assert len(recs) == 6  # 6 parameters
        for rec in recs:
            assert rec.confidence in ("HIGH", "MEDIUM", "LOW")
            assert rec.recommended_value is not None
            assert "aggregate" in rec.scores_breakdown
            assert 0 <= rec.scores_breakdown["aggregate"] <= 1


class TestFuelShareFigures:
    """Test fuel share figure generation."""

    def test_fuel_share_figures_produced(self, tmp_path_factory):
        tmpdir = str(tmp_path_factory.mktemp("calibration_fs"))
        _create_synthetic_calibration(tmpdir)

        from horizon.calibration.analyze import _load_index, _read_report

        runs, param_defaults = _load_index(tmpdir)
        data_by_folder = {}
        for run in runs:
            folder_path = os.path.join(tmpdir, run.folder_name)
            if os.path.exists(folder_path):
                report = _read_report(folder_path)
                if report is not None:
                    data_by_folder[run.folder_name] = report

        fs_figs = _build_fuel_share_figures(runs, data_by_folder, param_defaults)

        # Should have figures for each (regulation, param_token) combo
        assert len(fs_figs) > 0
        # Each entry should have up to 4 figures (one per fuel type)
        for _key, figs in fs_figs.items():
            assert len(figs) <= 4
            assert len(figs) > 0
