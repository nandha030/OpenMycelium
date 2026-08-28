"""Framing and validation for hierarchical cross-vendor collectives.

A collective frame carries strictly more than an activation transfer: which
operation, which collective instance, which epoch, and which rank is root. All
of it is validated on arrival, because a border rank is the single point where
a malformed or stale frame would otherwise be fanned out to every rank behind
it.

Two properties this module exists to guarantee:

* **Epoch fencing.** After a border-rank failure the surviving side bumps its
  epoch. Frames from the previous epoch are rejected rather than consumed, so a
  later collective cannot pick up a stale frame left in flight by a failed one.
* **Zero-length is legal, negative is not.** A broadcast of an empty tensor is
  a real case with a real shape and a zero-byte payload; a negative dimension is
  always corruption. `ActivationHeader` rejects both, which is right for a
  pipeline activation and wrong here.
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

#: "OMC2" -- distinct from the activation magic so the two cannot be confused.
_MAGIC = 0x4F4D4332
_VERSION = 1

OPERATIONS = ("broadcast", "all-gather")
VENDORS = ("cuda", "rocm", "cpu")

_DTYPES = {
    "f16": 2, "bf16": 2, "f32": 4, "f64": 8,
    "i8": 1, "i32": 4, "i64": 8, "u8": 1,
}

#: magic, version, op, vendor, root_rank, dtype, reserved, cid, epoch, seq, nbytes
#:
#: The dtype tag is carried explicitly. Deriving it from the byte length looks
#: tidy but is ambiguous: f16/bf16 both occupy 2 bytes, f32/i32 4, f64/i64 8,
#: i8/u8 1. An i64 broadcast decoded as f64 produces a silently wrong tensor of
#: the right size, which is exactly the failure this field prevents. `nbytes`
#: is still carried and cross-checked against shape x dtype on arrival.
_HEAD = "<IHHHHHHQQqQ"
_HEAD_SIZE = struct.calcsize(_HEAD)
_NBYTES_OFFSET = struct.calcsize("<IHHHHHHQQq")


class CollectiveError(RuntimeError):
    """Framing, ordering, or epoch violation on a collective frame."""


class StaleFrameError(CollectiveError):
    """A frame from a superseded epoch or an already-consumed sequence."""


@dataclass(frozen=True)
class CollectiveFrame:
    operation: str
    collective_id: int
    epoch: int
    root_vendor: str
    root_rank: int
    sequence: int
    dtype: str
    shape: tuple[int, ...]

    @property
    def element_size(self) -> int:
        if self.dtype not in _DTYPES:
            raise CollectiveError(f"unsupported dtype {self.dtype!r}")
        return _DTYPES[self.dtype]

    @property
    def byte_size(self) -> int:
        """Zero when any dimension is zero -- an empty tensor is still a tensor."""
        total = self.element_size
        for dim in self.shape:
            total *= dim
        return total

    @property
    def is_empty(self) -> bool:
        return self.byte_size == 0

    def validate(self) -> None:
        if self.operation not in OPERATIONS:
            raise CollectiveError(f"unknown operation {self.operation!r}")
        if self.root_vendor not in VENDORS:
            raise CollectiveError(f"unknown root vendor {self.root_vendor!r}")
        if self.root_rank < 0:
            raise CollectiveError("root_rank must be non-negative")
        if self.collective_id < 0:
            raise CollectiveError("collective_id must be non-negative")
        if self.epoch < 0:
            raise CollectiveError("epoch must be non-negative")
        if self.sequence < 0:
            raise CollectiveError("sequence must be non-negative")
        if self.dtype not in _DTYPES:
            raise CollectiveError(f"unsupported dtype {self.dtype!r}")
        if not self.shape:
            raise CollectiveError("shape must have at least one dimension")
        if len(self.shape) > 8:
            raise CollectiveError("shape may not exceed 8 dimensions")
        # Zero is permitted (empty tensor); negative never is.
        if any(dim < 0 for dim in self.shape):
            raise CollectiveError(f"shape {self.shape} has a negative dimension")

    def pack(self) -> bytes:
        self.validate()
        head = struct.pack(
            _HEAD, _MAGIC, _VERSION,
            OPERATIONS.index(self.operation), VENDORS.index(self.root_vendor),
            self.root_rank, list(_DTYPES).index(self.dtype), 0,
            self.collective_id, self.epoch, self.sequence, self.byte_size,
        )
        dims = struct.pack("<H", len(self.shape)) + b"".join(
            struct.pack("<q", d) for d in self.shape
        )
        return head + dims

    @classmethod
    def unpack(cls, payload: bytes) -> "CollectiveFrame":
        if len(payload) < _HEAD_SIZE + 2:
            raise CollectiveError("collective frame header is truncated")
        (magic, version, op, vendor, root_rank, dtype_index, _reserved,
         cid, epoch, sequence, nbytes) = struct.unpack(_HEAD, payload[:_HEAD_SIZE])
        if magic != _MAGIC:
            raise CollectiveError("collective frame has a bad magic value")
        if version != _VERSION:
            raise CollectiveError(f"unsupported collective frame version {version}")
        if op >= len(OPERATIONS):
            raise CollectiveError(f"unknown operation index {op}")
        if vendor >= len(VENDORS):
            raise CollectiveError(f"unknown vendor index {vendor}")
        names = list(_DTYPES)
        if dtype_index >= len(names):
            raise CollectiveError(f"unknown dtype index {dtype_index}")
        rank_count = struct.unpack("<H", payload[_HEAD_SIZE:_HEAD_SIZE + 2])[0]
        if rank_count == 0 or rank_count > 8:
            raise CollectiveError(f"frame claims {rank_count} dimensions")
        need = _HEAD_SIZE + 2 + 8 * rank_count
        if len(payload) < need:
            raise CollectiveError("collective frame dimensions are truncated")
        dims = tuple(
            struct.unpack("<q", payload[_HEAD_SIZE + 2 + 8 * i:_HEAD_SIZE + 10 + 8 * i])[0]
            for i in range(rank_count)
        )
        frame = cls(OPERATIONS[op], cid, epoch, VENDORS[vendor], root_rank,
                    sequence, names[dtype_index], dims)
        frame.validate()
        # The declared payload length must agree with shape x dtype, or a
        # receiver could be talked into reading the wrong number of bytes.
        if frame.byte_size != nbytes:
            raise CollectiveError(
                f"declared payload {nbytes} disagrees with shape/dtype {frame.byte_size}"
            )
        return frame

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def frame_for(tensor_shape, dtype: str, *, operation: str = "broadcast",
              collective_id: int = 0, epoch: int = 0, root_vendor: str = "cuda",
              root_rank: int = 0, sequence: int = 0) -> CollectiveFrame:
    frame = CollectiveFrame(operation, collective_id, epoch, root_vendor,
                            root_rank, sequence, dtype,
                            tuple(int(d) for d in tensor_shape))
    frame.validate()
    return frame


class FrameGate:
    """Accepts frames for one collective, rejecting stale and out-of-order ones.

    A border rank fans whatever it accepts out to every rank behind it, so this
    is the point where a stale frame has to be stopped.
    """

    def __init__(self, collective_id: int = 0, epoch: int = 0):
        self.collective_id = collective_id
        self.epoch = epoch
        self.next_sequence = 0
        self.failed = False

    def fail(self) -> int:
        """Mark the collective failed and fence off every frame in flight."""
        self.failed = True
        self.epoch += 1
        self.next_sequence = 0
        return self.epoch

    def reset_for(self, collective_id: int) -> None:
        self.collective_id = collective_id
        self.next_sequence = 0
        self.failed = False

    def accept(self, frame: CollectiveFrame) -> CollectiveFrame:
        frame.validate()
        if self.failed:
            raise StaleFrameError(
                f"collective {self.collective_id} has failed; frame at epoch "
                f"{frame.epoch} seq {frame.sequence} refused"
            )
        if frame.epoch != self.epoch:
            raise StaleFrameError(
                f"frame epoch {frame.epoch} does not match current epoch "
                f"{self.epoch}; refusing a frame from a superseded collective"
            )
        if frame.collective_id != self.collective_id:
            raise CollectiveError(
                f"frame belongs to collective {frame.collective_id}, "
                f"expected {self.collective_id}"
            )
        if frame.sequence != self.next_sequence:
            raise CollectiveError(
                f"out of order: expected sequence {self.next_sequence}, "
                f"got {frame.sequence}"
            )
        self.next_sequence += 1
        return frame


@dataclass
class CollectiveTiming:
    """Local-collective, bridge, and total latency, reported separately."""

    local_ms: float = 0.0
    bridge_ms: float = 0.0
    total_ms: float = 0.0
    local_degenerate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "localCollectiveMs": round(self.local_ms, 3),
            "bridgeMs": round(self.bridge_ms, 3),
            "totalMs": round(self.total_ms, 3),
            "localDegenerate": self.local_degenerate,
        }


def degeneracy_note(group_sizes: Mapping[str, int]) -> Optional[str]:
    """Describe which vendor-local groups are too small to prove anything.

    With one GPU per vendor the local NCCL and RCCL broadcasts have a single
    participant, so they exercise orchestration and routing but say nothing
    about multi-rank vendor-local behaviour. The ledger records that rather
    than letting a green result imply more than it does.
    """
    degenerate = sorted(v for v, n in group_sizes.items() if n <= 1)
    if not degenerate:
        return None
    return (
        f"vendor-local groups {', '.join(degenerate)} contain a single rank; "
        "their local collectives are degenerate and validate orchestration and "
        "cross-vendor routing only, not multi-rank NCCL/RCCL behaviour"
    )


class AllGatherAssembler:
    """Assembles an all-gather output in global-rank order.

    Ordering comes from the `root_rank` field carried by each contribution --
    which for all-gather means the *contributing* rank -- never from arrival
    order. A border rank may forward contributions in any order, and a slow
    vendor group may deliver late; the output is identical either way.

    There is no designated root. Every rank contributes and every rank receives
    the same assembled result.

    The output buffer is allocated only after the first contribution has been
    validated, so a malformed shape or dtype cannot size an allocation.
    """

    def __init__(self, world_size: int, collective_id: int, epoch: int):
        if world_size < 1:
            raise CollectiveError("world_size must be positive")
        self.world_size = world_size
        self.collective_id = collective_id
        self.epoch = epoch
        self.dtype: Optional[str] = None
        self.shape: Optional[tuple[int, ...]] = None
        self._parts: dict[int, bytes] = {}
        self.failed = False

    @property
    def missing(self) -> tuple[int, ...]:
        return tuple(r for r in range(self.world_size) if r not in self._parts)

    @property
    def is_complete(self) -> bool:
        return not self.failed and len(self._parts) == self.world_size

    def accept(self, frame: CollectiveFrame, payload: bytes) -> None:
        if self.failed:
            raise StaleFrameError("assembler has been fenced; refusing further contributions")
        frame.validate()
        if frame.operation != "all-gather":
            raise CollectiveError(f"expected an all-gather frame, got {frame.operation!r}")
        if frame.epoch != self.epoch:
            raise StaleFrameError(
                f"contribution epoch {frame.epoch} does not match {self.epoch}")
        if frame.collective_id != self.collective_id:
            raise CollectiveError(
                f"contribution belongs to collective {frame.collective_id}, "
                f"expected {self.collective_id}")
        if not 0 <= frame.root_rank < self.world_size:
            raise CollectiveError(
                f"contribution from rank {frame.root_rank} is outside "
                f"[0, {self.world_size})")
        if frame.root_rank in self._parts:
            raise CollectiveError(f"duplicate contribution from rank {frame.root_rank}")

        # The first validated contribution fixes the contract; every later one
        # must match it. Uniform dtype and shape is the initial requirement.
        if self.dtype is None:
            self.dtype, self.shape = frame.dtype, frame.shape
        else:
            if frame.dtype != self.dtype:
                raise CollectiveError(
                    f"rank {frame.root_rank} contributed dtype {frame.dtype}, "
                    f"expected {self.dtype}")
            if frame.shape != self.shape:
                raise CollectiveError(
                    f"rank {frame.root_rank} contributed shape {frame.shape}, "
                    f"expected {self.shape}")
        if len(payload) != frame.byte_size:
            raise CollectiveError(
                f"rank {frame.root_rank} payload is {len(payload)} bytes, "
                f"frame declares {frame.byte_size}")
        self._parts[frame.root_rank] = payload

    def fail(self) -> int:
        self.failed = True
        self.epoch += 1
        self._parts.clear()
        return self.epoch

    def result(self) -> bytes:
        """Concatenated contributions in rank order.

        Raises while incomplete: a partially assembled output is never returned
        as a success, because a caller could not tell it apart from a whole one.
        """
        if self.failed:
            raise StaleFrameError("assembler was fenced; no result is available")
        if not self.is_complete:
            raise CollectiveError(
                f"all-gather is incomplete; missing ranks {self.missing}")
        return b"".join(self._parts[r] for r in range(self.world_size))

    def output_shape(self) -> tuple[int, ...]:
        if self.shape is None:
            raise CollectiveError("no contribution has established a shape yet")
        return (self.shape[0] * self.world_size,) + tuple(self.shape[1:])


#: Fencing policy when a collective violates its contract.
#:
#: `FENCE_CONNECTION` tears down every collective sharing the link. It is the
#: default and the only implemented policy, because per-collective recovery
#: requires knowing that no partially-read frame remains in the stream -- which
#: needs real stream multiplexing (length-delimited channels, per-collective
#: resynchronisation points) rather than a shared byte stream. Fencing wide is
#: the safe reading of an ambiguous stream state.
FENCE_CONNECTION = "fence-connection"
#: Reserved. Selecting it raises rather than silently degrading to the above.
FENCE_COLLECTIVE = "fence-collective"


class ConnectionFenced(StaleFrameError):
    """The whole connection was fenced; no collective on it may continue."""


class ConnectionMultiplexer:
    """Routes interleaved collective frames on one persistent connection.

    Several collectives may be in flight over a single link, distinguished by
    collective ID. Each gets its own `FrameGate`, so per-collective ordering and
    epoch rules still apply, while the multiplexer owns the policy question of
    what a violation does to its neighbours.
    """

    def __init__(self, policy: str = FENCE_CONNECTION):
        if policy != FENCE_CONNECTION:
            raise CollectiveError(
                f"policy {policy!r} is not implemented; only {FENCE_CONNECTION} is "
                "safe without stream multiplexing"
            )
        self.policy = policy
        self.gates: dict[int, FrameGate] = {}
        self.fenced = False
        self.fenced_by: Optional[int] = None
        self.fence_reason: str = ""

    def open(self, collective_id: int, epoch: int = 0) -> FrameGate:
        if self.fenced:
            raise ConnectionFenced(self._fence_message())
        if collective_id in self.gates:
            raise CollectiveError(f"collective {collective_id} is already open")
        gate = FrameGate(collective_id=collective_id, epoch=epoch)
        self.gates[collective_id] = gate
        return gate

    def _fence_message(self) -> str:
        return (f"connection fenced by collective {self.fenced_by} "
                f"({self.fence_reason}); every collective on this link is refused")

    def fence(self, collective_id: int, reason: str) -> None:
        """Tear down every collective on the link, not just the offender."""
        self.fenced = True
        self.fenced_by = collective_id
        self.fence_reason = reason
        for gate in self.gates.values():
            gate.fail()

    def accept(self, frame: CollectiveFrame) -> CollectiveFrame:
        if self.fenced:
            raise ConnectionFenced(self._fence_message())
        frame.validate()
        gate = self.gates.get(frame.collective_id)
        if gate is None:
            self.fence(frame.collective_id, "frame for an unopened collective")
            raise ConnectionFenced(self._fence_message())
        try:
            return gate.accept(frame)
        except CollectiveError as error:
            # A bad frame means the byte stream can no longer be trusted to be
            # positioned correctly for anyone sharing it.
            self.fence(frame.collective_id, str(error))
            raise ConnectionFenced(self._fence_message()) from error

    def state(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "fenced": self.fenced,
            "fencedBy": self.fenced_by,
            "fenceReason": self.fence_reason,
            "open": sorted(self.gates),
            "nextSequence": {cid: g.next_sequence for cid, g in sorted(self.gates.items())},
        }
