# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the vendored CBOR subset codec used by the pueue protocol."""

import pytest

from horizon.run import _cbor


@pytest.mark.unit
class TestRoundtrip:

    @pytest.mark.parametrize("value", [
        None, True, False,
        0, 1, 23, 24, 255, 256, 65535, 65536, 2**32 - 1, 2**32, 2**63,
        -1, -24, -25, -256, -257, -2**32,
        "", "hello", "æøå ünicode ✓",
        b"", b"\x00\xff",
        [], [1, 2, 3], ["a", None, True],
        {}, {"k": "v"}, {"nested": {"list": [1, {"deep": None}]}},
    ])
    def test_roundtrip(self, value):
        assert _cbor.loads(_cbor.dumps(value)) == value

    def test_add_request_shape(self):
        """The exact message shape sent to the daemon survives a roundtrip."""
        message = {
            "Add": {
                "command": 'navigate "/data/s_sample001/s_sample001.nav" --solver highs',
                "path": "/data",
                "envs": {"PATH": "/usr/bin", "OMP_NUM_THREADS": "2"},
                "start_immediately": False,
                "stashed": False,
                "group": "default",
                "enqueue_at": None,
                "dependencies": [],
                "priority": -5,
                "label": "s_sample001",
            }
        }
        assert _cbor.loads(_cbor.dumps(message)) == message


@pytest.mark.unit
class TestDecodeOnlyForms:
    """Forms the daemon may emit that we never encode ourselves."""

    def test_float64(self):
        import struct
        data = b"\xfb" + struct.pack(">d", 1.5)
        assert _cbor.loads(data) == 1.5

    def test_float32(self):
        import struct
        data = b"\xfa" + struct.pack(">f", 2.0)
        assert _cbor.loads(data) == 2.0

    def test_float16(self):
        import struct
        data = b"\xf9" + struct.pack(">e", 0.5)
        assert _cbor.loads(data) == 0.5

    def test_tagged_value_returns_inner(self):
        # tag 0 (standard datetime string tag) wrapping a text string
        inner = _cbor.dumps("2026-08-27T10:00:00Z")
        assert _cbor.loads(b"\xc0" + inner) == "2026-08-27T10:00:00Z"


@pytest.mark.unit
class TestErrors:

    def test_truncated_raises(self):
        data = _cbor.dumps({"a": [1, 2, 3]})
        with pytest.raises(_cbor.CBORError):
            _cbor.loads(data[:-1])

    def test_trailing_bytes_raise(self):
        with pytest.raises(_cbor.CBORError):
            _cbor.loads(_cbor.dumps(1) + b"\x00")

    def test_indefinite_length_rejected(self):
        with pytest.raises(_cbor.CBORError):
            _cbor.loads(b"\x9f\x01\xff")  # indefinite-length array

    def test_unsupported_encode_type(self):
        with pytest.raises(_cbor.CBORError):
            _cbor.dumps(object())

    def test_non_string_map_key_rejected(self):
        with pytest.raises(_cbor.CBORError):
            _cbor.dumps({1: "x"})
