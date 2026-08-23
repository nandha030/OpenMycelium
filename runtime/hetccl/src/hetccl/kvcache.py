"""Versioned, checksummed KV-cache transfer for heterogeneous inference."""

from __future__ import annotations

import hashlib
import hmac
import socket
import socketserver
import threading
import time
from dataclasses import asdict, dataclass
from math import prod
from typing import Any, Mapping

from .protocol import MAX_PAYLOAD_BYTES, ProtocolError, receive_frame, send_frame

KV_MAGIC = "HETCCL-KV/1"
CANONICAL_LAYOUT = "kv-layer-batch-head-sequence-dim"

_DTYPE_BYTES = {
    "bf16": 2,
    "f16": 2,
    "f32": 4,
    "f64": 8,
    "i8": 1,
    "u8": 1,
}
_RUNTIMES = {"cuda", "rocm", "metal", "oneapi", "cpu"}


@dataclass(frozen=True)
class KVCacheDescriptor:
    """Portable description of one contiguous KV-cache transfer.

    Shape uses ``(2, layers, batch, heads, sequence, head_dim)`` for the
    canonical layout. Vendor adapters are responsible for translating their
    native cache layout before upload and after download.
    """

    cache_id: str
    model: str
    source_runtime: str
    dtype: str
    shape: tuple[int, ...]
    sequence: int = 0
    revision: str = ""
    layout: str = CANONICAL_LAYOUT

    @property
    def byte_size(self) -> int:
        self.validate()
        return prod(self.shape) * _DTYPE_BYTES[self.dtype]

    def validate(self, payload_size: int | None = None) -> None:
        if not self.cache_id or len(self.cache_id) > 256:
            raise ProtocolError("cache_id must contain between 1 and 256 characters")
        if not self.model or len(self.model) > 512:
            raise ProtocolError("model must contain between 1 and 512 characters")
        if self.source_runtime not in _RUNTIMES:
            raise ProtocolError(f"unsupported KV-cache runtime {self.source_runtime}")
        if self.dtype not in _DTYPE_BYTES:
            raise ProtocolError(f"unsupported KV-cache dtype {self.dtype}")
        if self.layout != CANONICAL_LAYOUT:
            raise ProtocolError(f"unsupported KV-cache layout {self.layout}")
        if len(self.shape) != 6 or self.shape[0] != 2 or any(value <= 0 for value in self.shape):
            raise ProtocolError("canonical KV-cache shape must be (2, layers, batch, heads, sequence, head_dim)")
        if self.sequence < 0:
            raise ProtocolError("KV-cache sequence must be non-negative")
        expected = prod(self.shape) * _DTYPE_BYTES[self.dtype]
        if payload_size is not None and payload_size != expected:
            raise ProtocolError(f"KV-cache payload is {payload_size} bytes; descriptor requires {expected}")

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["shape"] = list(self.shape)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "KVCacheDescriptor":
        try:
            shape = tuple(int(value) for value in payload["shape"])
            descriptor = cls(
                cache_id=str(payload["cache_id"]),
                model=str(payload["model"]),
                source_runtime=str(payload["source_runtime"]),
                dtype=str(payload["dtype"]),
                shape=shape,
                sequence=int(payload.get("sequence", 0)),
                revision=str(payload.get("revision", "")),
                layout=str(payload.get("layout", CANONICAL_LAYOUT)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError("invalid KV-cache descriptor") from error
        descriptor.validate()
        return descriptor


@dataclass(frozen=True)
class KVCacheRecord:
    descriptor: KVCacheDescriptor
    payload: bytes
    checksum: str
    created_at: float


class _KVCacheState:
    def __init__(self, max_bytes: int, ttl_seconds: float, auth_token: str):
        if max_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("KV-cache max_bytes and ttl_seconds must be positive")
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.auth_token = auth_token
        self.records: dict[str, KVCacheRecord] = {}
        self.total_bytes = 0
        self.lock = threading.Lock()

    def authorize(self, header: Mapping[str, Any]) -> None:
        if self.auth_token and not hmac.compare_digest(str(header.get("auth_token", "")), self.auth_token):
            raise ProtocolError("KV-cache broker authentication failed")

    def put(self, descriptor: KVCacheDescriptor, payload: bytes, checksum: str) -> KVCacheRecord:
        descriptor.validate(len(payload))
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, checksum):
            raise ProtocolError("KV-cache checksum mismatch")
        now = time.time()
        with self.lock:
            self._expire(now)
            previous = self.records.get(descriptor.cache_id)
            projected = self.total_bytes - (len(previous.payload) if previous else 0) + len(payload)
            if projected > self.max_bytes:
                raise ProtocolError("KV-cache broker capacity exceeded")
            record = KVCacheRecord(descriptor, payload, actual, now)
            self.records[descriptor.cache_id] = record
            self.total_bytes = projected
            return record

    def get(self, cache_id: str) -> KVCacheRecord:
        with self.lock:
            self._expire(time.time())
            try:
                return self.records[cache_id]
            except KeyError as error:
                raise ProtocolError(f"KV-cache {cache_id} was not found") from error

    def delete(self, cache_id: str) -> None:
        with self.lock:
            record = self.records.pop(cache_id, None)
            if record is not None:
                self.total_bytes -= len(record.payload)

    def _expire(self, now: float) -> None:
        expired = [key for key, value in self.records.items() if now - value.created_at >= self.ttl_seconds]
        for key in expired:
            self.total_bytes -= len(self.records[key].payload)
            del self.records[key]


class _KVCacheHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            header, payload = receive_frame(self.request)
            if header.get("magic") != KV_MAGIC:
                raise ProtocolError("unsupported KV-cache protocol")
            self.server.state.authorize(header)  # type: ignore[attr-defined]
            operation = header.get("operation")
            if operation == "put":
                raw_descriptor = header.get("descriptor")
                if not isinstance(raw_descriptor, dict):
                    raise ProtocolError("KV-cache put requires a descriptor")
                descriptor = KVCacheDescriptor.from_mapping(raw_descriptor)
                record = self.server.state.put(descriptor, payload, str(header.get("checksum", "")))  # type: ignore[attr-defined]
                send_frame(self.request, _record_header(record))
            elif operation == "get":
                record = self.server.state.get(_required_cache_id(header))  # type: ignore[attr-defined]
                send_frame(self.request, _record_header(record), record.payload)
            elif operation == "delete":
                cache_id = _required_cache_id(header)
                self.server.state.delete(cache_id)  # type: ignore[attr-defined]
                send_frame(self.request, {"magic": KV_MAGIC, "ok": True, "cache_id": cache_id})
            else:
                raise ProtocolError("KV-cache operation must be put, get, or delete")
        except Exception as error:
            try:
                send_frame(self.request, {"magic": KV_MAGIC, "ok": False, "error": str(error)})
            except OSError:
                return


class _KVCacheServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: _KVCacheState):
        self.state = state
        super().__init__(address, _KVCacheHandler)


class KVCacheBroker:
    """Lifecycle wrapper for a bounded in-memory reference KV-cache broker."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 29600,
        max_bytes: int = MAX_PAYLOAD_BYTES,
        ttl_seconds: float = 300,
        auth_token: str = "",
    ):
        self._server = _KVCacheServer((host, port), _KVCacheState(max_bytes, ttl_seconds, auth_token))
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> "KVCacheBroker":
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever, name="hetccl-kv-broker", daemon=True)
            self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "KVCacheBroker":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()


class KVCacheClient:
    def __init__(self, host: str, port: int = 29600, timeout_seconds: float = 120, auth_token: str = ""):
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.auth_token = auth_token

    def put(self, descriptor: KVCacheDescriptor, payload: bytes) -> str:
        descriptor.validate(len(payload))
        checksum = hashlib.sha256(payload).hexdigest()
        header, _ = self._request(
            "put",
            payload,
            descriptor=descriptor.to_dict(),
            checksum=checksum,
        )
        return str(header["checksum"])

    def get(self, cache_id: str) -> KVCacheRecord:
        header, payload = self._request("get", cache_id=cache_id)
        raw_descriptor = header.get("descriptor")
        if not isinstance(raw_descriptor, dict):
            raise ProtocolError("KV-cache broker returned no descriptor")
        descriptor = KVCacheDescriptor.from_mapping(raw_descriptor)
        descriptor.validate(len(payload))
        checksum = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(checksum, str(header.get("checksum", ""))):
            raise ProtocolError("downloaded KV-cache checksum mismatch")
        return KVCacheRecord(descriptor, payload, checksum, float(header.get("created_at", 0)))

    def delete(self, cache_id: str) -> None:
        self._request("delete", cache_id=cache_id)

    def _request(self, operation: str, payload: bytes = b"", **fields: object) -> tuple[dict[str, Any], bytes]:
        header: dict[str, object] = {
            "magic": KV_MAGIC,
            "operation": operation,
            "auth_token": self.auth_token,
            **fields,
        }
        with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as connection:
            connection.settimeout(self.timeout_seconds)
            send_frame(connection, header, payload)
            response, response_payload = receive_frame(connection)
        if response.get("magic") != KV_MAGIC:
            raise ProtocolError("KV-cache broker returned an incompatible protocol")
        if not response.get("ok"):
            raise ProtocolError(str(response.get("error", "KV-cache operation failed")))
        return response, response_payload


def _record_header(record: KVCacheRecord) -> dict[str, object]:
    return {
        "magic": KV_MAGIC,
        "ok": True,
        "descriptor": record.descriptor.to_dict(),
        "checksum": record.checksum,
        "created_at": record.created_at,
    }


def _required_cache_id(header: Mapping[str, Any]) -> str:
    cache_id = header.get("cache_id")
    if not isinstance(cache_id, str) or not cache_id:
        raise ProtocolError("cache_id must be a non-empty string")
    return cache_id
