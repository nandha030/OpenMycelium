"""Client API for portable and native MCCL collective backends."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Mapping, Sequence

from .protocol import MAGIC, ProtocolError, pack_values, receive_frame, send_frame, unpack_values


@dataclass(frozen=True)
class CollectiveConfig:
    rank: int
    world_size: int
    host: str = "127.0.0.1"
    port: int = 29500
    group: str = "default"
    timeout_seconds: float = 120
    backend: str = "tcp"

    def validate(self) -> None:
        if self.world_size < 1:
            raise ValueError("world_size must be positive")
        if self.rank < 0 or self.rank >= self.world_size:
            raise ValueError("rank must be in the range [0, world_size)")
        if not self.host or self.port < 1 or self.port > 65535:
            raise ValueError("a valid MCCL coordinator host and port are required")
        if not self.group:
            raise ValueError("group must not be empty")
        if self.backend != "tcp":
            raise ValueError("this build only contains the qualified tcp backend")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "CollectiveConfig":
        env = os.environ if environment is None else environment
        rank_base = integer(env, "OPENMYCELIUM_RANK_BASE", 0)
        worker_index = integer(env, "OPENMYCELIUM_WORKER_INDEX", integer(env, "RANK", 0))
        return cls(
            rank=rank_base + worker_index,
            world_size=integer(env, "OPENMYCELIUM_WORLD_SIZE", integer(env, "WORLD_SIZE", 1)),
            host=env.get("MCCL_COORDINATOR_HOST", env.get("OPENMYCELIUM_RENDEZVOUS_SERVICE", "127.0.0.1")),
            port=integer(env, "MCCL_COORDINATOR_PORT", integer(env, "OPENMYCELIUM_RENDEZVOUS_PORT", 29500)),
            group=env.get("MCCL_GROUP", env.get("OPENMYCELIUM_EXECUTION_PLAN_ID", "default")),
            timeout_seconds=float(env.get("MCCL_TIMEOUT_SECONDS", "120")),
            backend=env.get("MCCL_BACKEND", "tcp"),
        )


class MCCLCollective:
    """Sequence-safe AllReduce client.

    The TCP backend works on CPU, CUDA, ROCm, Intel and Apple hosts by staging
    values in host memory. Native device adapters use the same configuration
    contract but are only selectable after platform qualification.
    """

    def __init__(self, config: CollectiveConfig | None = None):
        self.config = config or CollectiveConfig.from_environment()
        self.config.validate()
        self._sequence = 0

    def all_reduce(
        self,
        values: Sequence[int | float],
        dtype: str = "f64",
        reduction: str = "sum",
    ) -> list[int | float]:
        payload = pack_values(values, dtype)
        sequence = self._sequence
        self._sequence += 1
        with socket.create_connection(
            (self.config.host, self.config.port), timeout=self.config.timeout_seconds
        ) as connection:
            connection.settimeout(self.config.timeout_seconds)
            send_frame(
                connection,
                {
                    "magic": MAGIC,
                    "operation": "all_reduce",
                    "group": self.config.group,
                    "sequence": sequence,
                    "rank": self.config.rank,
                    "world_size": self.config.world_size,
                    "dtype": dtype,
                    "count": len(values),
                    "reduction": reduction,
                },
                payload,
            )
            header, result = receive_frame(connection)
        if header.get("magic") != MAGIC:
            raise ProtocolError("coordinator returned an incompatible protocol")
        if not header.get("ok"):
            raise ProtocolError(str(header.get("error", "collective failed")))
        if header.get("sequence") != sequence:
            raise ProtocolError("coordinator returned the wrong sequence")
        return unpack_values(result, dtype, len(values))


def integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
