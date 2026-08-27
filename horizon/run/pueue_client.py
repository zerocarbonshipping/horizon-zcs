# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Direct connection to a pueue daemon over its unix socket (pueue >= 4).

Submitting through the pueue CLI costs one process spawn plus a full client
handshake per task. The daemon protocol itself is simple, so Horizon can hold
one connection and stream every Add over it:

  transport   unix domain socket (POSIX only; Windows pueue uses TCP+TLS and
              stays on the CLI path)
  framing     unsigned 64-bit big-endian length header, then the payload
  handshake   client sends the shared secret, daemon replies with its version
  messages    CBOR-serialized Request/Response enums (serde externally tagged)

Everything here is best-effort by contract: any failure - no config, no
socket, handshake refused, protocol mismatch, connection drop mid-stream -
raises PueueDirectError, and the caller (run_commands.StreamingQueuer) falls
back to the pueue CLI so no task is ever lost to the fast path.
"""

import getpass
import logging
import os
import socket
import struct
import sys

from horizon.run import _cbor

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class PueueDirectError(Exception):
    """Direct daemon submission is unavailable or broke; use the CLI."""


# ---------------------------------------------------------------------------
# Config / socket discovery
# ---------------------------------------------------------------------------

def _read_shared_settings(config_path):
    """Extract the keys we need from the ``shared:`` section of pueue.yml.

    Deliberately tiny (no YAML dependency): flat ``key: value`` lines inside
    the top-level ``shared:`` section. Anything this can't read simply means
    discovery falls through to the defaults - and ultimately to the CLI.
    """
    settings = {}
    section = None
    try:
        with open(config_path, "r") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not line[0].isspace():
                    section = stripped.split(":", 1)[0].strip()
                    continue
                if section == "shared" and ":" in stripped:
                    key, _, value = stripped.partition(":")
                    value = value.strip().strip('"').strip("'")
                    if value:
                        settings[key.strip()] = value
    except OSError as exc:
        raise PueueDirectError(f"cannot read pueue config {config_path}: {exc}")
    return settings


def _read_config_settings():
    """Locate and read the pueue config, mirroring ``Settings::read``.

    PUEUE_CONFIG_PATH is authoritative, exactly as in pueue: when it is set,
    that specific file must be readable — a missing or unreadable file makes
    the direct path unavailable rather than silently discovering (and
    submitting to) a different daemon than the user selected. Without it,
    the standard config locations are tried and an absent config just means
    defaults.
    """
    explicit = os.environ.get("PUEUE_CONFIG_PATH")
    if explicit:
        if not os.path.isfile(explicit):
            raise PueueDirectError(
                f"PUEUE_CONFIG_PATH points at {explicit}, which does not exist")
        return _read_shared_settings(explicit)

    home = os.path.expanduser("~")
    config_candidates = []
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        config_candidates.append(os.path.join(xdg_config, "pueue", "pueue.yml"))
    config_candidates.append(os.path.join(home, ".config", "pueue", "pueue.yml"))
    config_candidates.append(os.path.join(home, "Library", "Application Support", "pueue", "pueue.yml"))

    for candidate in config_candidates:
        if os.path.isfile(candidate):
            return _read_shared_settings(candidate)
    return {}


def _pueue_directory(settings):
    """Mirror ``Shared::pueue_directory``: config value, else the platform
    data dir ($XDG_DATA_HOME/pueue, ~/Library/Application Support/pueue on
    macOS, ~/.local/share/pueue elsewhere)."""
    if settings.get("pueue_directory"):
        return os.path.expanduser(settings["pueue_directory"])
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return os.path.join(xdg_data, "pueue")
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "pueue")
    return os.path.join(home, ".local", "share", "pueue")


def _runtime_directory(settings, pueue_dir):
    """Mirror ``Shared::runtime_directory``: config value, else the platform
    runtime dir ($XDG_RUNTIME_DIR — Linux/BSD only, like dirs::runtime_dir),
    else the pueue directory."""
    if settings.get("runtime_directory"):
        return os.path.expanduser(settings["runtime_directory"])
    if sys.platform != "darwin":
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            return xdg_runtime
    return pueue_dir


def discover_socket_and_secret():
    """Locate the daemon's unix socket and shared secret.

    Mirrors pueue 4's own resolution (pueue_lib settings.rs) so Horizon
    connects to exactly the daemon the pueue CLI would talk to:

      socket  ``unix_socket_path`` from config, else
              ``<runtime_directory>/pueue_<user>.socket`` where
              runtime_directory = config value > $XDG_RUNTIME_DIR >
              pueue_directory;
      secret  ``shared_secret_path`` from config, else
              ``<pueue_directory>/shared_secret``.

    Raises PueueDirectError when the daemon is not reachable this way.
    """
    if os.name != "posix":
        raise PueueDirectError("unix sockets are POSIX-only; Windows uses the CLI path")

    settings = _read_config_settings()

    if settings.get("use_unix_socket", "").lower() == "false":
        raise PueueDirectError("pueue is configured for TCP; only unix sockets are supported")

    pueue_dir = _pueue_directory(settings)

    if settings.get("unix_socket_path"):
        socket_path = os.path.expanduser(settings["unix_socket_path"])
    else:
        try:
            user = getpass.getuser()
        except Exception:
            raise PueueDirectError("cannot determine the username for the default socket path")
        socket_path = os.path.join(_runtime_directory(settings, pueue_dir),
                                   f"pueue_{user}.socket")

    if not _is_socket(socket_path):
        raise PueueDirectError(f"no pueue daemon socket at {socket_path}")

    if settings.get("shared_secret_path"):
        secret_path = os.path.expanduser(settings["shared_secret_path"])
    else:
        secret_path = os.path.join(pueue_dir, "shared_secret")

    if not os.path.isfile(secret_path):
        raise PueueDirectError(f"no pueue shared_secret at {secret_path}")

    return socket_path, secret_path


def _is_socket(path):
    try:
        import stat
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class PueueDirectConnection:
    """One authenticated request/response connection to the daemon."""

    def __init__(self, socket_path, secret_path, timeout=30.0):
        self._sock = None
        try:
            with open(secret_path, "rb") as fh:
                secret = fh.read()
        except OSError as exc:
            raise PueueDirectError(f"cannot read pueue shared secret: {exc}")

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(socket_path)
            self._sock = sock
            self._send_frame(secret)
            version = self._recv_frame()
        except (OSError, PueueDirectError) as exc:
            self.close()
            raise PueueDirectError(f"pueue daemon handshake failed: {exc}")

        self.daemon_version = version.decode("utf-8", errors="replace")
        logger.debug("Connected to pueue daemon (protocol version %s) via %s",
                     self.daemon_version, socket_path)

    def _send_frame(self, payload):
        self._sock.sendall(struct.pack(">Q", len(payload)) + payload)

    def _recv_frame(self):
        header = self._recv_exact(8)
        (length,) = struct.unpack(">Q", header)
        if length > _MAX_RESPONSE_BYTES:
            raise PueueDirectError(f"daemon response of {length} bytes exceeds limit")
        return self._recv_exact(length)

    def _recv_exact(self, n):
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._sock.recv(min(65536, remaining))
            if not chunk:
                raise PueueDirectError("daemon closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def request(self, message):
        """Send one Request and return the decoded Response."""
        try:
            self._send_frame(_cbor.dumps(message))
            return _cbor.loads(self._recv_frame())
        except (OSError, _cbor.CBORError) as exc:
            raise PueueDirectError(f"pueue daemon request failed: {exc}")

    def add_task(self, command, path, envs, pueue_priority, label):
        """Submit one task; returns (success, error_message)."""
        response = self.request({
            "Add": {
                "command": command,
                "path": path,
                "envs": envs,
                "start_immediately": False,
                "stashed": False,
                "group": "default",
                "enqueue_at": None,
                "dependencies": [],
                "priority": pueue_priority,
                "label": label if label else None,
            }
        })
        if isinstance(response, dict) and "AddedTask" in response:
            return (True, "")
        if isinstance(response, dict) and "Failure" in response:
            # The daemon understood us but rejected the task (e.g. unknown
            # group): a per-task failure, not a protocol failure.
            return (False, str(response["Failure"]))
        raise PueueDirectError(f"unexpected daemon response: {response!r:.200}")

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


def connect():
    """Discover the daemon and return an authenticated connection."""
    socket_path, secret_path = discover_socket_and_secret()
    return PueueDirectConnection(socket_path, secret_path)
