# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for the --solver CLI flag threading through to NavigaTE commands."""

from unittest.mock import MagicMock, patch

import pytest

from horizon.file_handler.file_handler import FileHandler

# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class TestSolverCLIParsing:
    """Test that --solver is correctly parsed by argparse."""

    @staticmethod
    def _parse(args_list):
        """Parse args through the real argparse parser."""
        import argparse
        cli_parser = argparse.ArgumentParser(prog="horizon")
        cli_parser.add_argument("arguments", type=str, nargs="*")
        cli_parser.add_argument("-c", "--collect", action="store_true")
        cli_parser.add_argument("--priority", choices=["low", "normal", "high"], default="normal")
        cli_parser.add_argument("--solver", default=None, choices=["auto", "gurobi", "highs"])
        return cli_parser.parse_args(args_list)

    def test_solver_default_is_none(self):
        args = self._parse(["test.hor"])
        assert args.solver is None

    def test_solver_gurobi(self):
        args = self._parse(["--solver", "gurobi", "test.hor"])
        assert args.solver == "gurobi"

    def test_solver_highs(self):
        args = self._parse(["--solver", "highs", "test.hor"])
        assert args.solver == "highs"

    def test_solver_auto(self):
        args = self._parse(["--solver", "auto", "test.hor"])
        assert args.solver == "auto"

    def test_solver_invalid_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["--solver", "cplex", "test.hor"])


# ---------------------------------------------------------------------------
# Command generation
# ---------------------------------------------------------------------------


class TestSolverCommandGeneration:
    """Test that generate_commands_list appends --solver correctly."""

    def test_no_solver_no_flag(self, tmp_path):
        fh = FileHandler()
        nav_file = tmp_path / "test.nav"
        nav_file.touch()
        fh.nav_filepaths = [str(nav_file)]

        fh.generate_commands_list(solver=None)

        assert len(fh.commands) == 1
        assert "--solver" not in fh.commands[0]

    def test_solver_highs_appended(self, tmp_path):
        fh = FileHandler()
        nav_file = tmp_path / "test.nav"
        nav_file.touch()
        fh.nav_filepaths = [str(nav_file)]

        fh.generate_commands_list(solver="highs")

        assert len(fh.commands) == 1
        assert fh.commands[0].endswith("--solver highs")

    def test_solver_gurobi_appended(self, tmp_path):
        fh = FileHandler()
        nav_file = tmp_path / "test.nav"
        nav_file.touch()
        fh.nav_filepaths = [str(nav_file)]

        fh.generate_commands_list(solver="gurobi")

        assert fh.commands[0].endswith("--solver gurobi")

    def test_solver_auto_appended(self, tmp_path):
        fh = FileHandler()
        nav_file = tmp_path / "test.nav"
        nav_file.touch()
        fh.nav_filepaths = [str(nav_file)]

        fh.generate_commands_list(solver="auto")

        assert fh.commands[0].endswith("--solver auto")

    def test_solver_with_multiple_nav_files(self, tmp_path):
        fh = FileHandler()
        nav_files = []
        for i in range(3):
            f = tmp_path / f"test_{i}.nav"
            f.touch()
            nav_files.append(str(f))
        fh.nav_filepaths = nav_files

        fh.generate_commands_list(solver="highs")

        assert len(fh.commands) == 3
        for cmd in fh.commands:
            assert cmd.endswith("--solver highs")
            assert 'navigate "' in cmd


# ---------------------------------------------------------------------------
# Integration: create_files passes solver through
# ---------------------------------------------------------------------------


class TestSolverIntegration:
    """Test that create_files threads solver through to the file handler."""

    @patch("horizon.test_manager.create_files.run_commands")
    @patch("horizon.test_manager.create_files.FileHandler")
    @patch("horizon.test_manager.create_files.parse_hor_file")
    def test_solver_passed_to_generate(self, mock_parse, mock_fh_cls, mock_run):
        """create_files passes solver kwarg to generate_scenarios_and_nav_files."""
        from horizon.test_manager.create_files import create_files

        # Set up parse mock to return valid config
        mock_parse.return_value = (
            "/tmp/test.unc",    # unc_file_path
            "/tmp/output",      # output_path
            5,                  # number_of_samples
            [],                 # scenario_parameters
            [],                 # parameters (empty -> no overrides path)
            "MC",               # sampling_method
            False,              # plot
            None,               # max_parallel_workers
            42,                 # random_seed
            False,              # sample_only
            None,               # exclusion_rules
            None,               # inclusion_rules
        )

        # Set up FileHandler mock
        mock_fh = MagicMock()
        mock_fh.nav_filepaths = ["/tmp/output/test.nav"]
        mock_fh.commands = ['navigate "/tmp/output/test.nav" --solver highs']
        mock_fh.skipped_count = 0
        mock_fh_cls.return_value = mock_fh

        # Mock sample_parameters to return something
        with patch("horizon.test_manager.create_files.sample_parameters") as mock_sample:
            mock_sample.return_value = [{"token_a": 1.0}]
            with patch("horizon.test_manager.create_files.output_sampled_parameters_to_csv"):
                create_files("/tmp/test.hor", solver="highs")

        # Verify solver was passed through
        mock_fh.generate_scenarios_and_nav_files.assert_called_once()
        call_kwargs = mock_fh.generate_scenarios_and_nav_files.call_args
        assert call_kwargs.kwargs.get("solver") == "highs"

    @patch("horizon.test_manager.create_files.run_commands")
    @patch("horizon.test_manager.create_files.FileHandler")
    @patch("horizon.test_manager.create_files.parse_hor_file")
    def test_solver_none_by_default(self, mock_parse, mock_fh_cls, mock_run):
        """create_files passes solver=None when not specified."""
        from horizon.test_manager.create_files import create_files

        mock_parse.return_value = (
            "/tmp/test.unc", "/tmp/output", 5, [], [],
            "MC", False, None, 42, False, None, None,
        )

        mock_fh = MagicMock()
        mock_fh.nav_filepaths = ["/tmp/output/test.nav"]
        mock_fh.commands = ['navigate "/tmp/output/test.nav"']
        mock_fh.skipped_count = 0
        mock_fh_cls.return_value = mock_fh

        with patch("horizon.test_manager.create_files.sample_parameters") as mock_sample:
            mock_sample.return_value = [{"token_a": 1.0}]
            with patch("horizon.test_manager.create_files.output_sampled_parameters_to_csv"):
                create_files("/tmp/test.hor")

        call_kwargs = mock_fh.generate_scenarios_and_nav_files.call_args
        assert call_kwargs.kwargs.get("solver") is None
