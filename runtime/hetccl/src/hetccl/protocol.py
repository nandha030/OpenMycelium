"""Wire protocol shared by the portable HetCCL client and coordinator."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any, Mapping, Sequence

MAGIC = "HETCCL/1"
MAX_HEADER_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024

_DTYPES: dict[str, tuple[str, int]] = {
    "f32": ("f", 4),
    "f64": ("d", 8),
    "i32": ("i", 4),
    "i64": ("q", 8),
}


class ProtocolError(RuntimeError):
    pass


def send_frame(connection: socket.socket, header: Mapping[str, Any], payload: bytes = b"") -> None:
    encoded = json.dumps(dict(header), separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_HEADER_BYTES:
        raise ProtocolError("HetCCL frame header is too large")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("HetCCL frame payload is too large")
    connection.sendall(struct.pack("!I", len(encoded)))
    connection.sendall(encoded)
    connection.sendall(struct.pack("!Q", len(payload)))
    if payload:
        connection.sendall(payload)


def receive_frame(connection: socket.socket) -> tuple[dict[str, Any], bytes]:
    header_size = struct.unpack("!I", receive_exact(connection, 4))[0]
    if header_size < 2 or header_size > MAX_HEADER_BYTES:
        raise ProtocolError("invalid HetCCL frame header size")
    try:
        header = json.loads(receive_exact(connection, header_size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid HetCCL JSON header") from error
    if not isinstance(header, dict):
        raise ProtocolError("HetCCL frame header must be an object")
    payload_size = struct.unpack("!Q", receive_exact(connection, 8))[0]
    if payload_size > MAX_PAYLOAD_BYTES:
        raise ProtocolError("HetCCL frame payload exceeds the configured limit")
    return header, receive_exact(connection, payload_size)


def receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(min(remaining, 1024 * 1024))
        if not chunk:
            raise ProtocolError("connection closed before the HetCCL frame completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def pack_values(values: Sequence[int | float], dtype: str) -> bytes:
    code, _ = dtype_info(dtype)
    try:
        return struct.pack(f"<{len(values)}{code}", *values)
    except (struct.error, TypeError) as error:
        raise ProtocolError(f"values do not fit dtype {dtype}") from error


def unpack_values(payload: bytes, dtype: str, count: int) -> list[int | float]:
    code, size = dtype_info(dtype)
    if count < 0 or len(payload) != count * size:
        raise ProtocolError("payload size does not match dtype and element count")
    return list(struct.unpack(f"<{count}{code}", payload))


def dtype_info(dtype: str) -> tuple[str, int]:
    try:
        return _DTYPES[dtype]
    except KeyError as error:
        raise ProtocolError(f"unsupported dtype {dtype}; expected one of {', '.join(_DTYPES)}") from error


def reduce_payloads(payloads: Sequence[bytes], dtype: str, count: int, reduction: str) -> bytes:
    if not payloads:
        raise ProtocolError("a collective cannot reduce an empty rank set")
    if reduction == "avg" and dtype.startswith("i"):
        raise ProtocolError("average reduction requires a floating-point dtype")
    rows = [unpack_values(payload, dtype, count) for payload in payloads]
    result: list[int | float] = []
    for column in zip(*rows):
        if reduction == "sum" or reduction == "avg":
            value: int | float = sum(column)
            if reduction == "avg":
                value = value / len(rows)
        elif reduction == "min":
            value = min(column)
        elif reduction == "max":
            value = max(column)
        else:
            raise ProtocolError("reduction must be sum, avg, min, or max")
        result.append(value)
    return pack_values(result, dtype)
