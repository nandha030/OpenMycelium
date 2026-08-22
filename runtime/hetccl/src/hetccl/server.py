"""Threaded reference coordinator for portable heterogeneous collectives."""

from __future__ import annotations

import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .protocol import MAGIC, ProtocolError, receive_frame, reduce_payloads, send_frame


@dataclass
class _Operation:
    world_size: int
    dtype: str
    count: int
    reduction: str
    payloads: dict[int, bytes] = field(default_factory=dict)
    result: bytes | None = None
    delivered: int = 0
    error: str = ""


class _CollectiveState:
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.condition = threading.Condition()
        self.operations: dict[tuple[str, int], _Operation] = {}

    def submit(self, header: dict[str, Any], payload: bytes) -> bytes:
        group = required_string(header, "group")
        sequence = required_integer(header, "sequence", minimum=0)
        rank = required_integer(header, "rank", minimum=0)
        world_size = required_integer(header, "world_size", minimum=1)
        count = required_integer(header, "count", minimum=0)
        dtype = required_string(header, "dtype")
        reduction = required_string(header, "reduction")
        if rank >= world_size:
            raise ProtocolError("rank must be smaller than world_size")
        key = (group, sequence)
        deadline = time.monotonic() + self.timeout_seconds
        with self.condition:
            operation = self.operations.get(key)
            if operation is None:
                operation = _Operation(world_size, dtype, count, reduction)
                self.operations[key] = operation
            elif (operation.world_size, operation.dtype, operation.count, operation.reduction) != (
                world_size,
                dtype,
                count,
                reduction,
            ):
                operation.error = "collective metadata differs between ranks"
                self.condition.notify_all()
            if rank in operation.payloads:
                operation.error = f"rank {rank} submitted sequence {sequence} more than once"
                self.condition.notify_all()
            if operation.error:
                raise ProtocolError(operation.error)
            operation.payloads[rank] = payload
            if len(operation.payloads) == operation.world_size:
                try:
                    ordered = [operation.payloads[index] for index in range(operation.world_size)]
                    operation.result = reduce_payloads(ordered, dtype, count, reduction)
                except (KeyError, ProtocolError) as error:
                    operation.error = str(error)
                self.condition.notify_all()
            while operation.result is None and not operation.error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    operation.error = (
                        f"collective {group}/{sequence} timed out with "
                        f"{len(operation.payloads)}/{operation.world_size} ranks"
                    )
                    self.condition.notify_all()
                    break
                self.condition.wait(remaining)
            if operation.error:
                operation.delivered += 1
                if operation.delivered >= operation.world_size and self.operations.get(key) is operation:
                    del self.operations[key]
                raise ProtocolError(operation.error)
            result = operation.result
            if result is None:
                raise ProtocolError("collective completed without a result")
            operation.delivered += 1
            if operation.delivered == operation.world_size:
                del self.operations[key]
            return result


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            self.request.settimeout(self.server.state.timeout_seconds)  # type: ignore[attr-defined]
            header, payload = receive_frame(self.request)
            if header.get("magic") != MAGIC or header.get("operation") != "all_reduce":
                raise ProtocolError("unsupported HetCCL protocol or operation")
            result = self.server.state.submit(header, payload)  # type: ignore[attr-defined]
            send_frame(
                self.request,
                {
                    "magic": MAGIC,
                    "ok": True,
                    "group": header["group"],
                    "sequence": header["sequence"],
                    "dtype": header["dtype"],
                    "count": header["count"],
                },
                result,
            )
        except Exception as error:
            try:
                send_frame(self.request, {"magic": MAGIC, "ok": False, "error": str(error)})
            except OSError:
                return


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: _CollectiveState):
        self.state = state
        super().__init__(address, _Handler)


class Coordinator:
    """Lifecycle wrapper around the portable HetCCL coordinator."""

    def __init__(self, host: str = "0.0.0.0", port: int = 29500, timeout_seconds: float = 120):
        self._server = _Server((host, port), _CollectiveState(timeout_seconds))
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> "Coordinator":
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever, name="hetccl-coordinator", daemon=True)
            self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "Coordinator":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()


def required_string(header: dict[str, Any], name: str) -> str:
    value = header.get(name)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{name} must be a non-empty string")
    return value


def required_integer(header: dict[str, Any], name: str, minimum: int) -> int:
    value = header.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ProtocolError(f"{name} must be an integer greater than or equal to {minimum}")
    return value
