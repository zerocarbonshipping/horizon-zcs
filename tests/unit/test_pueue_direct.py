# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Tests for direct pueue-daemon submission and its CLI fallback.

A fake daemon (a unix-socket server thread speaking pueue 4's protocol:
8-byte big-endian length framing, shared-secret handshake, CBOR messages)
stands in for pueued, so these tests pin the wire behavior without a real
daemon. The fallback tests prove the invariant that matters: no task is ever
lost to the fast path - anything the direct connection cannot deliver is
resubmitted through the pueue CLI.
"""

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

    ``break_after`` closes the connection after N successful adds, to test
    mid-stream fallback.
    """

    def __init__(self, sock_dir, break_after=None):
        self.socket_path = os.path.join(sock_dir, f"pueue_{_username()}.socket")
        self.secret_path = os.path.join(sock_dir, "shared_secret")
        with open(self.secret_path, "wb") as fh:
            fh.write(SECRET)
        self.received = []
        self._break_after = break_after
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
                if self._break_after is not None and adds >= self._break_after:
                    return  # close mid-stream
                self.received.append(request)
                adds += 1
                _send_frame(conn, _cbor.dumps(
                    {"AddedTask": {"task_id": adds, "enqueue_at": None, "group_is_paused": False}}))

    def close(self):
        self._server.close()


def _username():
    import getpass
    return getpass.getuser()


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


@pytest.fixture
def fake_daemon_env(tmp_path, monkeypatch):
    """Point pueue discovery at a fake daemon via PUEUE_CONFIG_PATH."""
    config = tmp_path / "pueue.yml"
    config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
    monkeypatch.setenv("PUEUE_CONFIG_PATH", str(config))
    return tmp_path


@pytest.mark.unit
class TestDiscovery:

    def test_no_daemon_raises(self, tmp_path, monkeypatch):
        config = tmp_path / "pueue.yml"
        config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
        monkeypatch.setenv("PUEUE_CONFIG_PATH", str(config))
        with pytest.raises(pueue_client.PueueDirectError):
            pueue_client.connect()

    def test_tcp_configured_raises(self, tmp_path, monkeypatch):
        config = tmp_path / "pueue.yml"
        config.write_text('shared:\n  use_unix_socket: false\n')
        monkeypatch.setenv("PUEUE_CONFIG_PATH", str(config))
        with pytest.raises(pueue_client.PueueDirectError):
            pueue_client.connect()


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


@pytest.mark.unit
class TestFallback:

    def test_no_daemon_falls_back_to_cli(self, tmp_path, monkeypatch, mocker):
        config = tmp_path / "pueue.yml"
        config.write_text(f'shared:\n  pueue_directory: "{tmp_path}"\n')
        monkeypatch.setenv("PUEUE_CONFIG_PATH", str(config))
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))

        queuer = StreamingQueuer()
        queuer.start(2)
        queuer.submit('navigate "/d/b_sample001/b_sample001.nav"')
        queuer.submit('navigate "/d/b_sample002/b_sample002.nav"')
        ok, fail = queuer.finish()

        assert (ok, fail) == (2, 0)
        assert run_mock.call_count == 2

    def test_mid_stream_break_resubmits_remainder_via_cli(self, fake_daemon_env, mocker):
        daemon = FakeDaemon(str(fake_daemon_env), break_after=2)
        run_mock = mocker.patch.object(
            run_commands, "_queue_single_command", return_value=(True, ""))
        try:
            queuer = StreamingQueuer()
            queuer.start(5)
            for i in range(5):
                queuer.submit(f'navigate "/d/c_sample00{i}/c_sample00{i}.nav"')
            ok, fail = queuer.finish()
        finally:
            daemon.close()

        # 2 via the direct connection, 3 resubmitted via the CLI, 0 lost
        assert (ok, fail) == (5, 0)
        assert len(daemon.received) == 2
        assert run_mock.call_count == 3
