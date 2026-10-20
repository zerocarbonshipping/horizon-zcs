# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``--threads N``: a fixed per-task OMP/MKL/NUMEXPR thread count."""

import os
import sys

import pytest

from horizon.run import run_commands
from horizon.run.run_commands import StreamingQueuer

THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
CMD = 'navigate "/d/a_sample001/a_sample001.nav"'


@pytest.mark.unit
class TestThreadsArgparse:

    @staticmethod
    def _parse(monkeypatch, argv):
        from horizon import __main__ as cli
        monkeypatch.setattr(sys, "argv", ["horizon"] + argv)
        captured = {}

        def fake_create_files(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(cli, "create_files", fake_create_files)
        monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)  # no output.log, no root handlers
        cli.main()
        return captured

    def test_default_is_none(self, monkeypatch):
        assert self._parse(monkeypatch, ["x.hor"])["threads"] is None

    def test_value_reaches_create_files(self, monkeypatch):
        assert self._parse(monkeypatch, ["--threads", "4", "x.hor"])["threads"] == 4

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
    def test_invalid_rejected(self, monkeypatch, bad):
        with pytest.raises(SystemExit) as exc:
            self._parse(monkeypatch, ["--threads", bad, "x.hor"])
        assert exc.value.code == 2

    def test_replot_passes_threads_to_run_commands(self, monkeypatch, tmp_path):
        from unittest.mock import patch

        from horizon import __main__ as cli
        d = tmp_path / "r1"
        d.mkdir()
        (d / "plot_data.pkl").write_bytes(b"")
        monkeypatch.setattr(sys, "argv", ["horizon", "--replot", "--threads", "3", str(d)])
        with patch("horizon.run.run_commands.run_commands") as mock_run:
            cli.main()
        assert mock_run.call_args[1]["threads"] == 3


@pytest.mark.unit
class TestThreadsRouting:

    def test_queuer_rejects_non_positive(self):
        for bad in (0, -2):
            with pytest.raises(ValueError):
                StreamingQueuer(threads=bad)

    def test_explicit_threads_on_cli_path(self, mocker):
        run_mock = mocker.patch.object(run_commands, "_queue_single_command", return_value=(True, ""))
        queuer = StreamingQueuer(pueue_cli=True, threads=3)
        queuer.start(10)
        queuer.submit(CMD)
        assert queuer.finish() == (1, 0)
        env_vars = run_mock.call_args[0][2]
        for var in THREAD_VARS:
            assert f"{var}=3 " in env_vars + " "

    def test_explicit_threads_not_capped_or_floored_by_auto_rule(self, mocker):
        mocker.patch.object(os, "cpu_count", return_value=8)
        run_mock = mocker.patch.object(run_commands, "_queue_single_command", return_value=(True, ""))
        queuer = StreamingQueuer(pueue_cli=True, threads=1)
        queuer.start(2)
        queuer.submit(CMD)
        queuer.finish()
        assert "OMP_NUM_THREADS=1 " in run_mock.call_args[0][2] + " "

    def test_default_unchanged_cli_path(self, mocker):
        mocker.patch.object(os, "cpu_count", return_value=176)
        run_mock = mocker.patch.object(run_commands, "_queue_single_command", return_value=(True, ""))
        queuer = StreamingQueuer(pueue_cli=True)
        queuer.start(10)
        queuer.submit(CMD)
        queuer.finish()
        assert "OMP_NUM_THREADS=17 " in run_mock.call_args[0][2] + " "

    def test_log_says_which_was_used(self, mocker, caplog):
        mocker.patch.object(run_commands, "_queue_single_command", return_value=(True, ""))
        with caplog.at_level("INFO", logger=run_commands.logger.name):
            StreamingQueuer(pueue_cli=True, threads=5).start(2)
            StreamingQueuer(pueue_cli=True).start(2)
        assert "5 threads each (set by --threads" in caplog.text
        assert "auto split" in caplog.text
