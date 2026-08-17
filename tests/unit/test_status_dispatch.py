# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for the --status CLI dispatch in horizon.__main__."""

from unittest.mock import patch

import pytest


class TestStatusDispatch:
    """The documented 'horizon --status DIR' invocation must reach check_status."""

    def test_status_without_positionals_reaches_check_status(self, tmp_path):
        """--status is dispatched before the positional-argument guard."""
        from horizon.__main__ import main

        argv = ["horizon", "--status", str(tmp_path)]
        with patch("sys.argv", argv), \
                patch("horizon.run.run_commands.check_status") as mock_status:
            main()

        mock_status.assert_called_once_with(str(tmp_path))

    def test_status_does_not_exit(self, tmp_path):
        """--status alone must not trigger the 'No .hor file paths' exit."""
        from horizon.__main__ import main

        argv = ["horizon", "--status", str(tmp_path)]
        with patch("sys.argv", argv), \
                patch("horizon.run.run_commands.check_status"):
            try:
                main()
            except SystemExit as exc:  # pragma: no cover - regression guard
                pytest.fail(f"--status exited with {exc.code} before dispatch")
