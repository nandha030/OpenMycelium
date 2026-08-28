"""`host-staged-xvendor`: a point-to-point transport across a vendor boundary.

This is deliberately *not* a collective backend. It provides `send`, `recv`,
and activation transfer between exactly two ranks that sit in different vendor
memory domains. Collectives are built above it later, with NCCL and RCCL still
owning all vendor-local communication.

The data path never hands device memory to the wire:

    NVIDIA VRAM --cudaMemcpyAsync--> pinned host --TCP--> pinned host
                                                 --hipMemcpyAsync--> AMD VRAM

That is a correctness requirement, not a preference. On ROCm-for-WSL a
`hipMalloc` pointer faults on any CPU load or store, and UCX's ROCm receive
path memcpys into it. See `mccl.qualification` for the measured gate.

Defaults come from measurement on the reference machine (RTX 5060 Ti +
RX 9060 XT): two slots, because deeper pools showed no gain and eight were
slower; 4 MiB chunks, mid-plateau of the 2-8 MiB range; payload CRC off,
because it cost 3.3x throughput. Metadata is always validated.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

TRANSPORT = "host-staged-xvendor"

#: Plateau measured on the reference machine. This is a *profile*, not a law:
#: PCIe topology, NUMA layout, WSL versus bare metal, and link width all move
#: the optimum. A chunk outside the profile is allowed but unqualified, and must
#: be requalified for the platform before it is used in production.
QUALIFIED_CHUNK_MIN = 2 * 1024 * 1024
QUALIFIED_CHUNK_MAX = 8 * 1024 * 1024
DEFAULT_CHUNK = 4 * 1024 * 1024
DEFAULT_SLOTS = 2

#: Absolute framing limits, unlike the profile above these are hard.
CHUNK_FLOOR = 4096
CHUNK_CEILING = 1024 * 1024 * 1024


def chunk_profile_status(chunk_bytes: int) -> str:
    """`qualified` inside the measured profile, `unqualified` outside it."""
    if not CHUNK_FLOOR <= chunk_bytes <= CHUNK_CEILING:
        return "invalid"
    if QUALIFIED_CHUNK_MIN <= chunk_bytes <= QUALIFIED_CHUNK_MAX:
        return "qualified"
    return "unqualified"

_DTYPES = {
    "f16": 2, "bf16": 2, "f32": 4, "f64": 8,
    "i8": 1, "i32": 4, "i64": 8, "u8": 1,
}

#: Wire framing for one activation: magic, dtype tag, rank count, then dims.
_MAGIC = 0x584D4331  # "XMC1"


class TransportError(RuntimeError):
    """Raised for framing, capability, and qualification failures."""


@dataclass(frozen=True)
class ActivationHeader:
    """Metadata describing one activation transfer.

    Shapes and dtypes are carried per transfer rather than fixed at setup, so a
    pipeline stage can change batch size or sequence length between requests.
    """

    dtype: str
    shape: tuple[int, ...]
    sequence: int = 0
    crc: int = 0

    @property
    def element_size(self) -> int:
        if self.dtype not in _DTYPES:
            raise TransportError(f"unsupported dtype {self.dtype!r}")
        return _DTYPES[self.dtype]

    @property
    def byte_size(self) -> int:
        total = self.element_size
        for dim in self.shape:
            total *= dim
        return total

    def validate(self) -> None:
        """Metadata validation runs in every mode, including production."""
        if self.dtype not in _DTYPES:
            raise TransportError(f"unsupported dtype {self.dtype!r}")
        if not self.shape:
            raise TransportError("activation shape must have at least one dimension")
        if any(dim <= 0 for dim in self.shape):
            raise TransportError(f"activation shape {self.shape} has a non-positive dimension")
        if len(self.shape) > 8:
            raise TransportError("activation shape may not exceed 8 dimensions")
        if self.sequence < 0:
            raise TransportError("sequence must be non-negative")

    def pack(self) -> bytes:
        self.validate()
        tag = list(_DTYPES).index(self.dtype)
        head = struct.pack("<IHHQI", _MAGIC, tag, len(self.shape), self.sequence, self.crc)
        return head + b"".join(struct.pack("<q", dim) for dim in self.shape)

    @classmethod
    def unpack(cls, payload: bytes) -> "ActivationHeader":
        if len(payload) < 20:
            raise TransportError("activation header is truncated")
        magic, tag, rank, sequence, crc = struct.unpack("<IHHQI", payload[:20])
        if magic != _MAGIC:
            raise TransportError("activation header has a bad magic value")
        if rank == 0 or rank > 8:
            raise TransportError(f"activation header claims {rank} dimensions")
        if len(payload) < 20 + 8 * rank:
            raise TransportError("activation header dimensions are truncated")
        names = list(_DTYPES)
        if tag >= len(names):
            raise TransportError(f"activation header has an unknown dtype tag {tag}")
        dims = tuple(
            struct.unpack("<q", payload[20 + 8 * i: 28 + 8 * i])[0] for i in range(rank)
        )
        header = cls(names[tag], dims, sequence, crc)
        header.validate()
        return header

    @property
    def header_size(self) -> int:
        return 20 + 8 * len(self.shape)


@dataclass(frozen=True)
class TransportConfig:
    peer_host: str = "127.0.0.1"
    port: int = 21000
    chunk_bytes: int = DEFAULT_CHUNK
    slots: int = DEFAULT_SLOTS
    window: int = DEFAULT_SLOTS
    #: Payload CRC is a qualification tool; it cost 3.3x throughput when measured.
    verify_payload: bool = False
    timeout_seconds: float = 60.0
    #: Opt in to a chunk size outside this platform's qualified profile, for
    #: exploring a different PCIe/NUMA/bare-metal configuration.
    allow_unqualified_chunk: bool = False

    def validate(self) -> None:
        status = chunk_profile_status(self.chunk_bytes)
        if status == "invalid":
            raise TransportError(
                f"chunk_bytes {self.chunk_bytes} is outside the framing limits "
                f"{CHUNK_FLOOR}-{CHUNK_CEILING}"
            )
        if status == "unqualified" and not self.allow_unqualified_chunk:
            raise TransportError(
                f"chunk_bytes {self.chunk_bytes} is outside this platform's qualified "
                f"profile ({QUALIFIED_CHUNK_MIN}-{QUALIFIED_CHUNK_MAX}); requalify the "
                f"platform or pass allow_unqualified_chunk=True to explore a new one"
            )
        if self.slots < 2:
            raise TransportError("at least two slots are required for double buffering")
        if self.window > self.slots:
            raise TransportError("credit window may not exceed the slot count")
        if self.timeout_seconds <= 0:
            raise TransportError("timeout_seconds must be positive")

    @classmethod
    def for_qualification(cls, **overrides: Any) -> "TransportConfig":
        """Qualification runs enable payload verification; production does not."""
        fields = {"verify_payload": True}
        fields.update(overrides)
        return cls(**fields)


@dataclass(frozen=True)
class QualificationRecord:
    """The exact tuple a direction was verified under.

    A transfer is permitted only when a record matching its direction exists.
    Recording GPU models, OS build, and runtime versions means a driver or WSL
    upgrade invalidates the qualification rather than silently inheriting it.
    """

    direction: str                 # "cuda->rocm"
    send_gpu: str
    recv_gpu: str
    windows_version: str
    wsl_kernel: str
    cuda_driver: str
    rocm_version: str
    chunk_bytes: int
    slots: int
    verified_bytes: int
    throughput_mbps: float
    byte_verified: bool
    recorded_at: str = ""

    def key(self) -> str:
        return self.direction

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "QualificationRecord":
        known = {f: payload.get(f) for f in cls.__dataclass_fields__}
        missing = [k for k, v in known.items() if v is None and k != "recorded_at"]
        if missing:
            raise TransportError(f"qualification record is missing {', '.join(sorted(missing))}")
        known["recorded_at"] = payload.get("recorded_at", "")
        return cls(**known)  # type: ignore[arg-type]


class QualificationLedger:
    """Directions that have passed byte verification on this exact platform."""

    def __init__(self, records: Optional[Sequence[QualificationRecord]] = None):
        self._records: dict[str, QualificationRecord] = {}
        for record in records or ():
            self.record(record)

    def record(self, record: QualificationRecord) -> None:
        if not record.byte_verified:
            raise TransportError(
                f"refusing to qualify {record.direction}: the run was not byte-verified"
            )
        self._records[record.key()] = record

    def is_qualified(self, direction: str) -> bool:
        return direction in self._records

    def get(self, direction: str) -> Optional[QualificationRecord]:
        return self._records.get(direction)

    def directions(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))

    def assert_permitted(self, send_vendor: str, recv_vendor: str) -> QualificationRecord:
        """Fail closed unless this exact direction was verified.

        Direction matters: the measured failure mode was asymmetric -- receive
        into ROCm memory faulted while ROCm as a source worked -- so qualifying
        `rocm->cuda` says nothing about `cuda->rocm`.
        """
        direction = f"{send_vendor}->{recv_vendor}"
        record = self._records.get(direction)
        if record is None:
            raise TransportError(
                f"{TRANSPORT} refuses {direction}: no byte-verified qualification "
                f"for this direction (qualified: {', '.join(self.directions()) or 'none'})"
            )
        return record

    def to_json(self) -> str:
        return json.dumps(
            {d: r.to_dict() for d, r in sorted(self._records.items())},
            indent=2, sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "QualificationLedger":
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TransportError("qualification ledger must be a JSON object")
        return cls([QualificationRecord.from_mapping(v) for v in payload.values()])

    @classmethod
    def load(cls, path: str) -> "QualificationLedger":
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_json(handle.read())


@dataclass
class TransferMetrics:
    """Latency and throughput, accumulated across transfers."""

    transfers: int = 0
    bytes_moved: int = 0
    seconds: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)

    def observe(self, byte_count: int, seconds: float) -> None:
        self.transfers += 1
        self.bytes_moved += byte_count
        self.seconds += seconds
        self.latencies_ms.append(seconds * 1000.0)

    @property
    def throughput_mbps(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return (self.bytes_moved / (1024.0 * 1024.0)) / self.seconds

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    def percentile_ms(self, fraction: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return ordered[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transfers": self.transfers,
            "bytesMoved": self.bytes_moved,
            "seconds": round(self.seconds, 6),
            "throughputMBps": round(self.throughput_mbps, 2),
            "meanLatencyMs": round(self.mean_latency_ms, 3),
            "p50LatencyMs": round(self.percentile_ms(0.50), 3),
            "p95LatencyMs": round(self.percentile_ms(0.95), 3),
            "p99LatencyMs": round(self.percentile_ms(0.99), 3),
        }


def describe() -> dict[str, Any]:
    """What this transport is and is not, for the control plane."""
    return {
        "transport": TRANSPORT,
        "kind": "point-to-point",
        "operations": ["send", "recv", "activation"],
        "collective": False,
        "stagesThroughPinnedHost": True,
        "defaults": {
            "chunkBytes": DEFAULT_CHUNK,
            "qualifiedChunkRange": [QUALIFIED_CHUNK_MIN, QUALIFIED_CHUNK_MAX],
            "framingLimits": [CHUNK_FLOOR, CHUNK_CEILING],
            "slots": DEFAULT_SLOTS,
            "verifyPayload": False,
        },
        "notes": [
            "device memory is never exposed to the wire; only vendor adapters touch it",
            "UCX receive into ROCm memory stays disabled on the DXG path",
            "collectives are layered above this transport, not provided by it",
        ],
    }
