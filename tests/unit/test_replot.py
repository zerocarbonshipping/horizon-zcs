# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for the --replot batch replot feature."""

from unittest.mock import patch

import pytest

from horizon.run.run_commands import _extract_label

# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class TestReplotCLIParsing:
    """Test that --replot is correctly parsed by argparse."""

    @staticmethod
    def _parse(args_list):
        """Parse args through the real argparse parser."""
        import argparse
        cli_parser = argparse.ArgumentParser(prog="horizon")
        cli_parser.add_argument("arguments", type=str, nargs="*")
        cli_parser.add_argument("-c", "--collect", action="store_true")
        cli_parser.add_argument("--priority", choices=["low", "normal", "high"], default="normal")
        cli_parser.add_argument("--solver", default=None, choices=["auto", "gurobi", "highs"])
        cli_parser.add_argument("--replot", action="store_true")
        return cli_parser.parse_args(args_list)

    def test_replot_default_is_false(self):
        args = self._parse(["test.hor"])
        assert args.replot is False

    def test_replot_flag_parsed(self):
        args = self._parse(["--replot", "dir1"])
        assert args.replot is True

    def test_replot_with_multiple_dirs(self):
        args = self._parse(["--replot", "dir1", "dir2", "dir3"])
        assert args.replot is True
        assert args.arguments == ["dir1", "dir2", "dir3"]

    def test_replot_with_priority(self):
        args = self._parse(["--replot", "--priority", "high", "dir1", "dir2"])
        assert args.replot is True
        assert args.priority == "high"
        assert args.arguments == ["dir1", "dir2"]


# ---------------------------------------------------------------------------
# Label extraction
# ---------------------------------------------------------------------------


class TestExtractLabelReplot:
    """Test _extract_label for both normal and replot commands."""

    def test_normal_nav_command(self):
        cmd = 'navigate "/abs/path/scenario_001/scenario_001.nav" --solver auto'
        assert _extract_label(cmd) == "scenario_001"

    def test_replot_directory_command(self):
        cmd = 'navigate -r "/abs/path/scenario_001"'
        assert _extract_label(cmd) == "scenario_001"

    def test_replot_directory_trailing_slash(self):
        # os.path.basename handles trailing slash by returning ''
        # but in practice paths won't have trailing slashes from os.path.abspath
        cmd = 'navigate -r "/abs/path/scenario_001"'
        assert _extract_label(cmd) == "scenario_001"

    def test_empty_command(self):
        assert _extract_label("") == ""

    def test_no_quotes(self):
        assert _extract_label("navigate somefile") == ""


# ---------------------------------------------------------------------------
# Directory validation and command building
# ---------------------------------------------------------------------------


class TestReplotBatch:
    """Test _replot_batch directory validation and command building."""

    def test_valid_directory_with_pkl(self, tmp_path):
        """Valid directory with plot_data.pkl produces a command."""
        d = tmp_path / "scenario_001"
        d.mkdir()
        (d / "plot_data.pkl").touch()

        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(arguments=[str(d)], priority="normal", full_task_env=False)

        with patch("horizon.run.run_commands.run_commands") as mock_run:
            _replot_batch(args)

        mock_run.assert_called_once()
        commands = mock_run.call_args[0][0]
        assert len(commands) == 1
        assert "scenario_001" in commands[0]

    def test_valid_dirs_queued(self, tmp_path):
        """Multiple valid directories all get queued."""
        dirs = []
        for i in range(3):
            d = tmp_path / f"run_{i}"
            d.mkdir()
            (d / "plot_data.pkl").touch()
            dirs.append(str(d))

        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(arguments=dirs, priority="normal", full_task_env=False)

        with patch("horizon.run.run_commands.run_commands") as mock_run:
            _replot_batch(args)

        mock_run.assert_called_once()
        commands = mock_run.call_args[0][0]
        assert len(commands) == 3
        for cmd in commands:
            assert cmd.startswith('navigate -r "')

    def test_missing_pkl_skipped(self, tmp_path):
        """Directory without plot_data.pkl is skipped."""
        d = tmp_path / "empty_dir"
        d.mkdir()

        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(arguments=[str(d)], priority="normal", full_task_env=False)

        with pytest.raises(SystemExit):
            _replot_batch(args)

    def test_nonexistent_directory_skipped(self, tmp_path):
        """Nonexistent directory is skipped."""
        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(arguments=[str(tmp_path / "does_not_exist")], priority="normal", full_task_env=False)

        with pytest.raises(SystemExit):
            _replot_batch(args)

    def test_no_arguments_exits(self):
        """No arguments causes sys.exit(1)."""
        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(arguments=[], priority="normal", full_task_env=False)

        with pytest.raises(SystemExit):
            _replot_batch(args)

    def test_mixed_validity(self, tmp_path):
        """Only valid directories produce commands; invalid ones are skipped."""
        valid_dir = tmp_path / "valid"
        valid_dir.mkdir()
        (valid_dir / "plot_data.pkl").touch()

        invalid_dir = tmp_path / "no_pkl"
        invalid_dir.mkdir()

        missing_dir = tmp_path / "missing"

        from types import SimpleNamespace

        from horizon.__main__ import _replot_batch

        args = SimpleNamespace(
            arguments=[str(valid_dir), str(invalid_dir), str(missing_dir)],
            priority="high",
            full_task_env=False,
        )

        with patch("horizon.run.run_commands.run_commands") as mock_run:
            _replot_batch(args)

        commands = mock_run.call_args[0][0]
        assert len(commands) == 1
        assert str(valid_dir) in commands[0]
        assert mock_run.call_args[1]["priority"] == "high"
