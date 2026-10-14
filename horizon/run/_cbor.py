# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Minimal CBOR (RFC 8949) codec for the pueue daemon protocol.

pueue >= 4 serializes its request/response messages with CBOR (via ciborium).
The message shapes Horizon exchanges are shallow: maps with string keys,
strings, integers, booleans, null, and small arrays. This module implements
exactly that subset - vendored so Horizon needs no extra dependency for the
optional direct-daemon fast path (any decoding surprise simply fails the
direct path, and submission falls back to the pueue CLI).

Encoding supports: None, bool, int, str, bytes, list/tuple, dict (str keys).
Decoding additionally supports: floats (16/32/64-bit) and tagged values
(the tag is dropped, the inner value returned). Indefinite-length items are
rejected - ciborium always writes definite lengths.
"""

import io
import struct


class CBORError(ValueError):
    """Raised on malformed or unsupported CBOR data."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

_HEAD_SIZES = {24: 1, 25: 2, 26: 4, 27: 8}


def _encode_head(major, value, out):
    if value < 24:
        out.append((major << 5) | value)
        return
    for info, size in _HEAD_SIZES.items():
        if value < 1 << (8 * size):
            out.append((major << 5) | info)
            out += value.to_bytes(size, "big")
            return
    raise CBORError(f"integer too large to encode: {value}")


def _encode(obj, out):
    if obj is None:
        out.append(0xF6)
    elif obj is True:
        out.append(0xF5)
    elif obj is False:
        out.append(0xF4)
    elif isinstance(obj, int):
        if obj >= 0:
            _encode_head(0, obj, out)
        else:
            _encode_head(1, -1 - obj, out)
    elif isinstance(obj, str):
        data = obj.encode("utf-8")
        _encode_head(3, len(data), out)
        out += data
    elif isinstance(obj, bytes):
        _encode_head(2, len(obj), out)
        out += obj
    elif isinstance(obj, (list, tuple)):
        _encode_head(4, len(obj), out)
        for item in obj:
            _encode(item, out)
    elif isinstance(obj, dict):
        _encode_head(5, len(obj), out)
        for key, value in obj.items():
            if not isinstance(key, str):
                raise CBORError(f"only string map keys are supported, got {type(key).__name__}")
            _encode(key, out)
            _encode(value, out)
    else:
        raise CBORError(f"cannot encode {type(obj).__name__}")


def dumps(obj):
    """Encode obj as CBOR bytes."""
    out = bytearray()
    _encode(obj, out)
    return bytes(out)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _take(reader, n):
    chunk = reader.read(n)
    if len(chunk) != n:
        raise CBORError("truncated CBOR data")
    return chunk


def _decode_length(reader, info):
    if info < 24:
        return info
    if info in _HEAD_SIZES:
        return int.from_bytes(_take(reader, _HEAD_SIZES[info]), "big")
    raise CBORError(f"unsupported length encoding: info={info} (indefinite lengths are not supported)")


_FLOAT_FORMATS = {25: ">e", 26: ">f", 27: ">d"}


def _decode(reader):
    initial = _take(reader, 1)[0]
    major = initial >> 5
    info = initial & 0x1F

    if major == 0:  # unsigned int
        return _decode_length(reader, info)
    if major == 1:  # negative int
        return -1 - _decode_length(reader, info)
    if major == 2:  # byte string
        return _take(reader, _decode_length(reader, info))
    if major == 3:  # text string
        return _take(reader, _decode_length(reader, info)).decode("utf-8")
    if major == 4:  # array
        return [_decode(reader) for _ in range(_decode_length(reader, info))]
    if major == 5:  # map
        result = {}
        for _ in range(_decode_length(reader, info)):
            key = _decode(reader)
            result[key] = _decode(reader)
        return result
    if major == 6:  # tag: drop it, return the inner value
        _decode_length(reader, info)
        return _decode(reader)
    # major == 7: simple values and floats
    if info == 20:
        return False
    if info == 21:
        return True
    if info == 22 or info == 23:  # null / undefined
        return None
    if info in _FLOAT_FORMATS:
        return struct.unpack(_FLOAT_FORMATS[info], _take(reader, _HEAD_SIZES[info]))[0]
    raise CBORError(f"unsupported CBOR item: major={major} info={info}")


def loads(data):
    """Decode one CBOR item from bytes."""
    reader = io.BytesIO(data)
    value = _decode(reader)
    if trailing := len(data) - reader.tell():
        raise CBORError(f"{trailing} trailing byte(s) after CBOR item")
    return value
