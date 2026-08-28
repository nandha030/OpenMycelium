"""Hierarchical cross-vendor collective: NCCL/RCCL inside a vendor, MCCL between vendors.

NCCL and RCCL are not wire-compatible, and neither exposes a transport that a
third library can join. A single communicator therefore cannot span an NVIDIA
and an AMD group. This module composes them instead:

    1. reduce   -- every vendor group reduces internally with its own native
                   collective (NCCL on CUDA, RCCL on ROCm, Gloo on CPU)
    2. bridge   -- the group leaders exchange their partial results through the
                   portable MCCL host-staged TCP coordinator
    3. fan-out  -- each leader broadcasts the global result down its own group

Only step 2 crosses a vendor boundary, and it carries host memory, so no vendor
library ever sees a peer it cannot speak to.

PyTorch is imported lazily so this module can be inspected, unit-tested for
topology behaviour, and shipped in images that do not carry a GPU runtime.

A Gloo process group spans every rank, but it is used only for rendezvous,
sub-group construction, and barriers. No payload crosses a vendor boundary
through it. `scripts/wsl_bridge_is_load_bearing.sh` proves this by running a
multi-group reduction with the MCCL coordinator stopped: if Gloo were
secretly carrying the payload the run would still succeed, and it does not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

_TORCH_TO_MCCL_DTYPE = {
    "torch.float32": "f32",
    "torch.float64": "f64",
    "torch.int32": "i32",
    "torch.int64": "i64",
}

_HIERARCHICAL_REDUCTIONS = ("sum", "min", "max", "avg")


@dataclass(frozen=True)
class VendorGroup:
    """One vendor's ranks, reduced internally by that vendor's native collective."""

    group_id: int
    vendor: str
    ranks: tuple[int, ...]

    @property
    def leader(self) -> int:
        """The rank that carries this group's partial result across the bridge."""
        return self.ranks[0]

    @property
    def native_backend(self) -> str:
        return {"cuda": "nccl", "rocm": "rccl", "cpu": "gloo"}.get(self.vendor, "")

    def validate(self) -> None:
        if self.vendor not in {"cuda", "rocm", "cpu"}:
            raise ValueError(f"unsupported vendor {self.vendor!r}: expected cuda, rocm, or cpu")
        if not self.ranks:
            raise ValueError(f"vendor group {self.group_id} contains no ranks")
        if list(self.ranks) != sorted(set(self.ranks)):
            raise ValueError(f"vendor group {self.group_id} ranks must be unique and ascending")


@dataclass(frozen=True)
class Topology:
    """How global ranks are partitioned across vendor groups."""

    groups: tuple[VendorGroup, ...]

    @property
    def world_size(self) -> int:
        return sum(len(group.ranks) for group in self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def leaders(self) -> tuple[int, ...]:
        return tuple(group.leader for group in self.groups)

    @property
    def is_heterogeneous(self) -> bool:
        return len({group.vendor for group in self.groups}) > 1

    def group_of(self, rank: int) -> VendorGroup:
        for group in self.groups:
            if rank in group.ranks:
                return group
        raise ValueError(f"rank {rank} does not belong to any vendor group")

    def validate(self) -> None:
        if not self.groups:
            raise ValueError("a topology requires at least one vendor group")
        for group in self.groups:
            group.validate()
        assigned = [rank for group in self.groups for rank in group.ranks]
        if sorted(assigned) != list(range(len(assigned))):
            raise ValueError("vendor groups must cover global ranks 0..world_size-1 exactly once")
        if [group.group_id for group in self.groups] != list(range(len(self.groups))):
            raise ValueError("group ids must be 0..group_count-1 in order")

    @classmethod
    def from_spec(cls, spec: str) -> "Topology":
        """Build a topology from `vendor:count[,vendor:count...]`, e.g. `cuda:2,rocm:2`."""
        groups: list[VendorGroup] = []
        next_rank = 0
        for group_id, chunk in enumerate(item for item in spec.split(",") if item.strip()):
            vendor, separator, raw_count = chunk.strip().partition(":")
            if not separator:
                raise ValueError(f"topology entry {chunk!r} must look like vendor:count")
            try:
                count = int(raw_count)
            except ValueError as error:
                raise ValueError(f"topology entry {chunk!r} has a non-numeric count") from error
            if count < 1:
                raise ValueError(f"topology entry {chunk!r} must claim at least one rank")
            groups.append(VendorGroup(group_id, vendor.strip(), tuple(range(next_rank, next_rank + count))))
            next_rank += count
        topology = cls(tuple(groups))
        topology.validate()
        return topology


@dataclass(frozen=True)
class HierarchyConfig:
    rank: int
    topology: Topology
    coordinator_host: str = "127.0.0.1"
    coordinator_port: int = 29500
    group_name: str = "mycelium-hierarchy"
    rendezvous_host: str = "127.0.0.1"
    rendezvous_port: int = 29400
    timeout_seconds: float = 120

    def validate(self) -> None:
        self.topology.validate()
        if not 0 <= self.rank < self.topology.world_size:
            raise ValueError("rank must be in the range [0, world_size)")

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None) -> "HierarchyConfig":
        env = os.environ if environment is None else environment
        spec = env.get("MYCELIUM_TOPOLOGY", "")
        if not spec:
            raise ValueError("MYCELIUM_TOPOLOGY is required, for example cuda:2,rocm:2")
        config = cls(
            rank=_integer(env, "RANK", 0),
            topology=Topology.from_spec(spec),
            coordinator_host=env.get("MCCL_COORDINATOR_HOST", "127.0.0.1"),
            coordinator_port=_integer(env, "MCCL_COORDINATOR_PORT", 29500),
            group_name=env.get("MCCL_GROUP", "mycelium-hierarchy"),
            rendezvous_host=env.get("MASTER_ADDR", "127.0.0.1"),
            rendezvous_port=_integer(env, "MASTER_PORT", 29400),
            timeout_seconds=float(env.get("MCCL_TIMEOUT_SECONDS", "120")),
        )
        config.validate()
        return config


class HierarchicalCollective:
    """AllReduce across vendor groups without a cross-vendor communicator."""

    def __init__(self, config: HierarchyConfig, torch_module: Any = None):
        config.validate()
        self.config = config
        self.group = config.topology.group_of(config.rank)
        self._torch = torch_module
        self._intra_group: Any = None
        self._bridge: Any = None
        self._initialized = False

    @property
    def torch(self) -> Any:
        if self._torch is None:
            import torch  # noqa: PLC0415 - deliberately lazy

            self._torch = torch
        return self._torch

    @property
    def is_leader(self) -> bool:
        return self.config.rank == self.group.leader

    @property
    def needs_bridge(self) -> bool:
        """A single-vendor job reduces entirely inside its own group.

        Bridging one group to itself is mathematically a no-op, so a homogeneous
        job must not depend on a MCCL coordinator being reachable.
        """
        return self.config.topology.group_count > 1

    @staticmethod
    def backend_for(group: VendorGroup) -> str:
        """The native collective a group reduces with, decided by vendor alone.

        This must not depend on the local build: every rank calls `new_group`
        for every group, so they all have to agree on each group's backend.
        Ranks outside a group are handed NON_GROUP_MEMBER and never construct
        the backend, so a CPU-only rank can safely name `nccl` for someone
        else's group. A ROCm build of PyTorch registers RCCL under the name
        `nccl`, so both vendors select the same string and resolve to different
        libraries.
        """
        return "nccl" if group.vendor in {"cuda", "rocm"} else "gloo"

    @property
    def intra_backend(self) -> str:
        """The native collective this rank reduces with inside its own group."""
        return self.backend_for(self.group)

    def initialize(self) -> None:
        if self._initialized:
            return
        distributed = self.torch.distributed
        if not distributed.is_initialized():
            os.environ.setdefault("MASTER_ADDR", self.config.rendezvous_host)
            os.environ.setdefault("MASTER_PORT", str(self.config.rendezvous_port))
            distributed.init_process_group(
                backend="gloo",
                rank=self.config.rank,
                world_size=self.config.topology.world_size,
            )
        self._assert_backend_is_built()
        # Every rank must construct every sub-group in the same order, even the
        # ones it does not join, or new_group deadlocks. Each group carries its
        # own vendor's backend; non-members get NON_GROUP_MEMBER and never
        # instantiate it.
        for group in self.config.topology.groups:
            handle = distributed.new_group(ranks=list(group.ranks), backend=self.backend_for(group))
            if group.group_id == self.group.group_id:
                self._intra_group = handle
        if self.is_leader and self.needs_bridge:
            self._bridge = self._build_bridge()
        self._initialized = True

    def _assert_backend_is_built(self) -> None:
        """Fail with the deployment problem, not a backend-internal error."""
        if self.intra_backend != "nccl":
            return
        distributed = self.torch.distributed
        if not distributed.is_nccl_available():
            library = "RCCL" if self.group.vendor == "rocm" else "NCCL"
            raise RuntimeError(
                f"rank {self.config.rank} is in a {self.group.vendor} group, but this PyTorch "
                f"build has no {library}. Run {self.group.vendor} ranks on a "
                f"{'ROCm' if self.group.vendor == 'rocm' else 'CUDA'} build of PyTorch."
            )

    def _build_bridge(self) -> Any:
        from mccl.collective import CollectiveConfig, MCCLCollective  # noqa: PLC0415

        return MCCLCollective(
            CollectiveConfig(
                rank=self.group.group_id,
                world_size=self.config.topology.group_count,
                host=self.config.coordinator_host,
                port=self.config.coordinator_port,
                group=self.config.group_name,
                timeout_seconds=self.config.timeout_seconds,
            )
        )

    def all_reduce(self, tensor: Any, reduction: str = "sum") -> Any:
        """Reduce `tensor` across every rank of every vendor group, in place."""
        if reduction not in _HIERARCHICAL_REDUCTIONS:
            raise ValueError(f"reduction must be one of {_HIERARCHICAL_REDUCTIONS}")
        self.initialize()
        distributed = self.torch.distributed

        # An average is not associative across unequal groups, so it is carried
        # as a sum and divided once by the global world size at the end.
        carried = "sum" if reduction == "avg" else reduction

        distributed.all_reduce(tensor, op=self._reduce_op(carried), group=self._intra_group)
        if self.is_leader and self.needs_bridge:
            self._bridge_in_place(tensor, carried)
        distributed.broadcast(tensor, src=self.group.leader, group=self._intra_group)
        if reduction == "avg":
            tensor /= self.config.topology.world_size
        return tensor

    def _bridge_in_place(self, tensor: Any, reduction: str) -> None:
        """Carry one group's partial result across the vendor boundary."""
        torch = self.torch
        dtype_name = _TORCH_TO_MCCL_DTYPE.get(str(tensor.dtype))
        if dtype_name is None:
            raise ValueError(f"MCCL cannot carry {tensor.dtype}; use float32/64 or int32/64")
        host = tensor.detach().to("cpu").reshape(-1)
        reduced = self._bridge.all_reduce(host.tolist(), dtype=dtype_name, reduction=reduction)
        tensor.copy_(torch.tensor(reduced, dtype=tensor.dtype).reshape(tensor.shape).to(tensor.device))

    def _reduce_op(self, reduction: str) -> Any:
        operations = self.torch.distributed.ReduceOp
        return {"sum": operations.SUM, "min": operations.MIN, "max": operations.MAX}[reduction]

    def barrier(self) -> Any:
        self.initialize()
        return self.torch.distributed.barrier()

    def close(self) -> None:
        distributed = self.torch.distributed
        if distributed.is_initialized():
            distributed.destroy_process_group()
        self._initialized = False

    def describe(self) -> dict[str, Any]:
        return {
            "rank": self.config.rank,
            "worldSize": self.config.topology.world_size,
            "groupId": self.group.group_id,
            "vendor": self.group.vendor,
            "groupRanks": list(self.group.ranks),
            "isLeader": self.is_leader,
            "intraBackend": self.intra_backend,
            "nativeBackend": self.group.native_backend,
            "bridge": "mccl-tcp",
            "groupCount": self.config.topology.group_count,
            "heterogeneous": self.config.topology.is_heterogeneous,
        }


def expected_all_reduce(contributions: Sequence[float], reduction: str = "sum") -> float:
    """Ground truth for a validation harness, computed without any collective."""
    if not contributions:
        raise ValueError("expected_all_reduce requires at least one contribution")
    if reduction == "sum":
        return float(sum(contributions))
    if reduction == "avg":
        return float(sum(contributions)) / len(contributions)
    if reduction == "min":
        return float(min(contributions))
    if reduction == "max":
        return float(max(contributions))
    raise ValueError(f"reduction must be one of {_HIERARCHICAL_REDUCTIONS}")


def _integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
