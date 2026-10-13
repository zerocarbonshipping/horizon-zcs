# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for direct pueue-daemon submission and its CLI fallback.

A fake daemon (a unix-socket server thread speaking pueue 4's protocol:
8-byte big-endian length framing, shared-secret handshake, CBOR messages)
stands in for pueued, so these tests pin the wire behavior without a real
daemon. Like the real daemon, the fake commits a task before acknowledging
it, so the drop-mid-request tests exercise the ambiguous in-flight case.

The fallback tests prove the invariants that matter: no task is ever lost to
the fast path (anything not delivered is resubmitted through the pueue CLI),
and no task is ever *duplicated* by it (a command whose acknowledgement was
lost is only resubmitted when the daemon provably does not have it).

Discovery mirrors pueue's own path resolution (pueue_lib settings.rs):
socket = unix_socket_path or <runtime_directory>/pueue_<user>.socket with
runtime_directory = config > $XDG_RUNTIME_DIR > pueue_directory, and
PUEUE_CONFIG_PATH is authoritative when set.
"""

import getpass
import os
import socket
import struct
import threading

import pytest

from horizon.run import _cbor, pueue_client, run_commands
from horizon.run.run_commands import StreamingQueuer

pytestmark = pytest.mark.skipif(os.name != "posix", reason="unix sockets are POSIX-only")

SECRET = b"test-secret"


class FakeDaemon:
    """Unix-socket server that mimics pueued's add handling.

    ``drop_at`` mirrors the daemon's save-then-respond ordering: the N-th
    add (1-based) is recorded (committed) and the connection then closes
    without sending the acknowledgement.
    """

    def __init__(self, sock_dir, secret_dir=None, drop_at=None):
        self.socket_path = os.path.join(sock_dir, f"pueue_{getpass.getuser()}.socket")
        self.secret_path = os.path.join(secret_dir or sock_dir, "shared_secret")
        os.makedirs(os.path.dirname(self.secret_path), exist_ok=True)
        with open(self.secret_path, "wb") as fh:
            fh.write(SECRET)
        self.received = []
        self._drop_at = drop_at
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        self._server.listen(2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            secret = _recv_frame(conn)
            if secret != SECRET:
                return
            _send_frame(conn, b"9.9.9-fake")
            adds = 0
            while True:
                try:
                    request = _cbor.loads(_recv_frame(conn))
                except (ConnectionError, _cbor.CBORError):
                    return
                adds += 1
                self.received.append(request)
                if self._drop_at is not None and adds == self._drop_at:
                    return  # committed, but the ack never leaves the daemon
                _send_frame(conn, _cbor.dumps(
                    {"AddedTask": {"task_id": adds, "enqueue_at": None, "group_is_paused": False}}))

    def close(self):
        self._server.close()

    def received_labels(self):
        return [req["Add"]["label"] for req in self.received]


def _send_frame(conn, payload):
    conn.sendall(struct.pack(">Q", len(payload)) + payload)


def _recv_frame(conn):
    header = b""
    while len(header) < 8:
        chunk = conn.recv(8 - len(header))
        if not chunk:
            raise ConnectionError("closed")
        header += chunk
    (length,) = struct.unpack(">Q", header)
    payload = b""
    while len(payload) < length:
        chunk = conn.recv(length - len(payload))
        if not chunk:
            raise ConnectionError("closed")
        payload += chunk
    return payload


def _bind_socket_node(path):
    """Create a filesystem socket node (no server behind it)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    return sock


@pytest.fixture
def clean_pueue_env(monkeypatch):
    """Neutral discovery environment: no ambient pueue vars leak in."""
    for var in ("PUEUE_CONFIG_PATH", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def fake_daemon_env(tmp_path, clean_pueue_env):
    """Point pueue discovery at a fake daemon via PUEUE_CONFIG_PATH."""
    config = tmp_path / "pueue.yml"
    config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
    clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
    return tmp_path


@pytest.mark.unit
class TestDiscovery:

    def test_no_daemon_raises(self, tmp_path, clean_pueue_env):
        config = tmp_path / "pueue.yml"
        config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
        with pytest.raises(pueue_client.PueueDirectError):
            pueue_client.connect()

    def test_tcp_configured_raises(self, tmp_path, clean_pueue_env):
        config = tmp_path / "pueue.yml"
        config.write_text('shared:\n  use_unix_socket: false\n')
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
        with pytest.raises(pueue_client.PueueDirectError):
            pueue_client.connect()

    def test_xdg_runtime_dir_socket_is_discovered(self, tmp_path, clean_pueue_env):
        """Default socket location is the runtime dir, not the data dir
        (pueue: runtime_directory = config > $XDG_RUNTIME_DIR > pueue_dir)."""
        runtime = tmp_path / "run"
        data = tmp_path / "data"
        runtime.mkdir()
        (data / "pueue").mkdir(parents=True)
        clean_pueue_env.setenv("XDG_RUNTIME_DIR", str(runtime))
        clean_pueue_env.setenv("XDG_DATA_HOME", str(data))
        socket_path = str(runtime / f"pueue_{getpass.getuser()}.socket")
        holder = _bind_socket_node(socket_path)
        (data / "pueue" / "shared_secret").write_bytes(SECRET)
        try:
            found_socket, found_secret = pueue_client.discover_socket_and_secret()
        finally:
            holder.close()
        assert found_socket == socket_path
        assert found_secret == str(data / "pueue" / "shared_secret")

    def test_runtime_directory_config_beats_env(self, tmp_path, clean_pueue_env):
        configured_runtime = tmp_path / "configured-rt"
        env_runtime = tmp_path / "env-rt"
        configured_runtime.mkdir()
        env_runtime.mkdir()
        config = tmp_path / "pueue.yml"
        config.write_text(
            f'shared:\n  pueue_directory: "{tmp_path}"\n'
            f'  runtime_directory: "{configured_runtime}"\n')
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
        clean_pueue_env.setenv("XDG_RUNTIME_DIR", str(env_runtime))
        socket_path = str(configured_runtime / f"pueue_{getpass.getuser()}.socket")
        holder = _bind_socket_node(socket_path)
        (tmp_path / "shared_secret").write_bytes(SECRET)
        try:
            found_socket, _ = pueue_client.discover_socket_and_secret()
        finally:
            holder.close()
        assert found_socket == socket_path

    def test_yaml_null_values_treated_as_unset(self, tmp_path, clean_pueue_env):
        """pueued writes unset options as YAML null; a config like
        `unix_socket_path: null` must not resolve to a literal 'null' path
        (regression: production fell back to the CLI with 'no pueue daemon
        socket at null')."""
        config = tmp_path / "pueue.yml"
        config.write_text(
            'shared:\n'
            f'  pueue_directory: "{tmp_path}"\n'
            '  runtime_directory: null\n'
            '  unix_socket_path: null\n'
            '  shared_secret_path: ~\n'
            '  use_unix_socket: true\n')
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
        socket_path = str(tmp_path / f"pueue_{getpass.getuser()}.socket")
        holder = _bind_socket_node(socket_path)
        (tmp_path / "shared_secret").write_bytes(SECRET)
        try:
            found_socket, found_secret = pueue_client.discover_socket_and_secret()
        finally:
            holder.close()
        assert found_socket == socket_path
        assert found_secret == str(tmp_path / "shared_secret")

    def test_pueue_config_path_is_authoritative(self, tmp_path, clean_pueue_env):
        """A set-but-unreadable PUEUE_CONFIG_PATH must fail discovery even
        when a daemon would be discoverable through the defaults - pueue
        itself errors on that file rather than falling back."""
        runtime = tmp_path / "run"
        data = tmp_path / "data"
        runtime.mkdir()
        (data / "pueue").mkdir(parents=True)
        clean_pueue_env.setenv("XDG_RUNTIME_DIR", str(runtime))
        clean_pueue_env.setenv("XDG_DATA_HOME", str(data))
        holder = _bind_socket_node(str(runtime / f"pueue_{getpass.getuser()}.socket"))
        (data / "pueue" / "shared_secret").write_bytes(SECRET)
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(tmp_path / "does-not-exist.yml"))
        try:
            with pytest.raises(pueue_client.PueueDirectError, match="PUEUE_CONFIG_PATH"):
                pueue_client.discover_socket_and_secret()
        finally:
            holder.close()


@pytest.mark.unit
class TestDirectSubmission:

    def test_all_tasks_reach_daemon_with_env_and_label(self, fake_daemon_env):
        daemon = FakeDaemon(str(fake_daemon_env))
        try:
            queuer = StreamingQueuer(priority="high")
            queuer.start(3)
            for i in range(3):
                queuer.submit(f'navigate "/data/s_sample00{i}/s_sample00{i}.nav" --solver highs')
            ok, fail = queuer.finish()
        finally:
            daemon.close()

        assert (ok, fail) == (3, 0)
        assert len(daemon.received) == 3
        add = daemon.received[0]["Add"]
        assert add["label"] == "s_sample000"
        assert add["priority"] == 5
        assert add["group"] == "default"
        assert add["path"] == os.getcwd()
        # thread limits travel in the task env on the direct path
        assert add["envs"]["OMP_NUM_THREADS"] == add["envs"]["MKL_NUM_THREADS"]
        # the minimal env applies here too
        assert "PATH" in add["envs"]

    def test_pueue_cli_flag_skips_direct(self, fake_daemon_env, mocker):
        daemon = FakeDaemon(str(fake_daemon_env))
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))
        try:
            queuer = StreamingQueuer(pueue_cli=True)
            queuer.start(2)
            queuer.submit('navigate "/d/a_sample001/a_sample001.nav"')
            queuer.submit('navigate "/d/a_sample002/a_sample002.nav"')
            ok, fail = queuer.finish()
        finally:
            daemon.close()

        assert (ok, fail) == (2, 0)
        assert run_mock.call_count == 2
        assert daemon.received == []

    def test_undecodable_env_value_is_dropped_not_fatal(self, fake_daemon_env, monkeypatch):
        """A surrogate-containing env value (surrogateescape) cannot ride the
        CBOR protocol; it is dropped instead of breaking the direct path."""
        monkeypatch.setenv("HORIZON_TASK_ENV", "BROKEN_VAR")
        monkeypatch.setenv("BROKEN_VAR", "bad-\udcff-bytes")
        daemon = FakeDaemon(str(fake_daemon_env))
        try:
            queuer = StreamingQueuer()
            queuer.start(1)
            queuer.submit('navigate "/d/e_sample001/e_sample001.nav"')
            ok, fail = queuer.finish()
        finally:
            daemon.close()

        assert (ok, fail) == (1, 0)
        assert "BROKEN_VAR" not in daemon.received[0]["Add"]["envs"]


@pytest.mark.unit
class TestFallback:

    def test_no_daemon_falls_back_to_cli(self, tmp_path, clean_pueue_env, mocker):
        config = tmp_path / "pueue.yml"
        config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
        clean_pueue_env.setenv("PUEUE_CONFIG_PATH", str(config))
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))

        queuer = StreamingQueuer()
        queuer.start(2)
        queuer.submit('navigate "/d/b_sample001/b_sample001.nav"')
        queuer.submit('navigate "/d/b_sample002/b_sample002.nav"')
        ok, fail = queuer.finish()

        assert (ok, fail) == (2, 0)
        assert run_mock.call_count == 2

    def test_worker_survives_unexpected_exception(self, fake_daemon_env, mocker):
        """A non-protocol exception (here: a surrogate in the command that
        fails CBOR encoding before anything is sent) must not kill the
        worker thread silently - every command is handed to the CLI."""
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))
        daemon = FakeDaemon(str(fake_daemon_env))
        try:
            queuer = StreamingQueuer()
            queuer.start(3)
            queuer.submit('navigate "/d/bad-\udcff/bad.nav"')
            queuer.submit('navigate "/d/f_sample002/f_sample002.nav"')
            queuer.submit('navigate "/d/f_sample003/f_sample003.nav"')
            ok, fail = queuer.finish()
        finally:
            daemon.close()

        # nothing reached the daemon; nothing was lost or duplicated
        assert daemon.received == []
        assert (ok, fail) == (3, 0)
        assert run_mock.call_count == 3


@pytest.mark.unit
class TestAckLossDisambiguation:
    """The daemon persists a task before acknowledging it, so the command in
    flight when the connection breaks may already be queued. It is only
    resubmitted when the daemon provably does not have it."""

    def _run_with_drop(self, fake_daemon_env, mocker, label_lookup):
        daemon = FakeDaemon(str(fake_daemon_env), drop_at=3)
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))
        mocker.patch.object(run_commands, "_get_pueue_tasks", side_effect=label_lookup)
        try:
            queuer = StreamingQueuer()
            queuer.start(5)
            for i in range(5):
                queuer.submit(f'navigate "/d/c_sample00{i}/c_sample00{i}.nav"')
            ok, fail = queuer.finish()
        finally:
            daemon.close()
        return daemon, run_mock, ok, fail

    def test_committed_in_flight_task_is_not_resubmitted(self, fake_daemon_env, mocker):
        # the daemon reports the in-flight label as queued
        daemon, run_mock, ok, fail = self._run_with_drop(
            fake_daemon_env, mocker,
            lambda: [{"label": "c_sample002"}])

        assert daemon.received_labels() == ["c_sample000", "c_sample001", "c_sample002"]
        # 2 acked + 1 committed-without-ack + 2 via CLI; the committed one
        # must NOT be among the CLI resubmissions
        assert (ok, fail) == (5, 0)
        assert run_mock.call_count == 2
        resubmitted = [call.args[0] for call in run_mock.call_args_list]
        assert not any("c_sample002" in cmd for cmd in resubmitted)

    def test_unqueued_in_flight_task_is_resubmitted(self, fake_daemon_env, mocker):
        # the daemon does not report the in-flight label
        daemon, run_mock, ok, fail = self._run_with_drop(
            fake_daemon_env, mocker, lambda: [])

        assert (ok, fail) == (5, 0)
        assert run_mock.call_count == 3
        resubmitted = [call.args[0] for call in run_mock.call_args_list]
        assert any("c_sample002" in cmd for cmd in resubmitted)

    def test_unknown_state_is_surfaced_not_duplicated(self, fake_daemon_env, mocker):
        # the daemon state cannot be read at all
        daemon, run_mock, ok, fail = self._run_with_drop(
            fake_daemon_env, mocker, lambda: None)

        # the ambiguous task is reported as failed, never resubmitted
        assert (ok, fail) == (4, 1)
        assert run_mock.call_count == 2
        resubmitted = [call.args[0] for call in run_mock.call_args_list]
        assert not any("c_sample002" in cmd for cmd in resubmitted)
