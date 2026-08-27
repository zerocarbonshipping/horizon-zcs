# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the per-task environment forwarded to pueue.

pueue stores the submitting client's entire environment inside every task and
rewrites the whole task list to disk on every add, so the environment payload
size directly drives submission throughput and daemon state size. Horizon
therefore forwards a whitelist by default, extendable via HORIZON_TASK_ENV,
with --full-task-env as the escape hatch.
"""

import pytest

from horizon.run.run_commands import _build_task_env


@pytest.mark.unit
class TestBuildTaskEnv:

    def test_full_env_returns_none(self):
        """None means the subprocess inherits everything (pueue stock behavior)."""
        assert _build_task_env(full_task_env=True) is None

    def test_whitelist_keeps_path_and_home(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/someone")
        env = _build_task_env()
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/someone"

    def test_unrelated_variables_are_dropped(self, monkeypatch):
        monkeypatch.setenv("SOME_HUGE_CI_TOKEN", "x" * 4096)
        monkeypatch.setenv("LS_COLORS", "di=34")
        env = _build_task_env()
        assert "SOME_HUGE_CI_TOKEN" not in env
        assert "LS_COLORS" not in env

    def test_solver_and_navigate_vars_survive(self, monkeypatch):
        monkeypatch.setenv("GRB_LICENSE_FILE", "/opt/gurobi/gurobi.lic")
        monkeypatch.setenv("ASSUMPTIONS_DATA_DIR", "/data/assumptions")
        env = _build_task_env()
        assert env["GRB_LICENSE_FILE"] == "/opt/gurobi/gurobi.lic"
        assert env["ASSUMPTIONS_DATA_DIR"] == "/data/assumptions"

    def test_horizon_task_env_extends_whitelist(self, monkeypatch):
        monkeypatch.setenv("MY_CLUSTER_VAR", "42")
        monkeypatch.setenv("OTHER_VAR", "7")
        monkeypatch.setenv("HORIZON_TASK_ENV", "MY_CLUSTER_VAR, OTHER_VAR")
        env = _build_task_env()
        assert env["MY_CLUSTER_VAR"] == "42"
        assert env["OTHER_VAR"] == "7"

    def test_horizon_task_env_missing_names_ignored(self, monkeypatch):
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        monkeypatch.setenv("HORIZON_TASK_ENV", "NOT_SET_ANYWHERE,,  ")
        env = _build_task_env()
        assert "NOT_SET_ANYWHERE" not in env
