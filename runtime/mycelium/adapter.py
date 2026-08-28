"""PyTorch reference adapter for Mycelium CPU-forwarded collectives.

This module intentionally imports PyTorch lazily. It can be included in CUDA
and ROCm training images while OpenMycelium uses Gloo as the common host-side
collective backend. Direct MCCL and RDMA transports remain native adapters.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RuntimeConfig:
    plan_id: str
    algorithm: str
    algorithm_version: str
    transport: str
    group_id: str
    group_size: int
    rank_base: int
    worker_index: int
    world_size: int
    rendezvous_host: str
    rendezvous_port: int
    plan: Mapping[str, Any]

    @property
    def rank(self) -> int:
        return self.rank_base + self.worker_index

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None) -> "RuntimeConfig":
        env = os.environ if environment is None else environment
        raw_plan = env.get("OPENMYCELIUM_EXECUTION_PLAN_JSON", "{}")
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as error:
            raise ValueError("OPENMYCELIUM_EXECUTION_PLAN_JSON is invalid") from error
        optimization = plan.get("optimization") or {}
        config = cls(
            plan_id=env.get("OPENMYCELIUM_EXECUTION_PLAN_ID", plan.get("id", "")),
            algorithm=env.get("OPENMYCELIUM_ALGORITHM", optimization.get("algorithm", "Mycelium")),
            algorithm_version=env.get("OPENMYCELIUM_ALGORITHM_VERSION", optimization.get("version", "unknown")),
            transport=env.get("OPENMYCELIUM_EXECUTION_TRANSPORT", plan.get("transport", "")),
            group_id=env.get("OPENMYCELIUM_EXECUTION_GROUP_ID", ""),
            group_size=_integer(env, "OPENMYCELIUM_EXECUTION_GROUP_SIZE", 1),
            rank_base=_integer(env, "OPENMYCELIUM_RANK_BASE", 0),
            worker_index=_integer(env, "OPENMYCELIUM_WORKER_INDEX", 0),
            world_size=_integer(env, "OPENMYCELIUM_WORLD_SIZE", 1),
            rendezvous_host=env.get("OPENMYCELIUM_RENDEZVOUS_SERVICE", "127.0.0.1"),
            rendezvous_port=_integer(env, "OPENMYCELIUM_RENDEZVOUS_PORT", 29500),
            plan=plan,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.plan_id:
            raise ValueError("an OpenMycelium execution plan is required")
        if self.algorithm != "Mycelium":
            raise ValueError(f"unsupported execution algorithm: {self.algorithm}")
        if self.transport not in {"gloo", "cpu-forwarding", "mccl", "mccl-tcp", "device-direct", "nccl", "rccl", "oneccl"}:
            raise ValueError(f"unsupported execution transport: {self.transport}")
        if self.world_size < 1 or self.rank < 0 or self.rank >= self.world_size:
            raise ValueError("computed rank is outside OPENMYCELIUM_WORLD_SIZE")


def _integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


class _CPUForwardedWork:
    def __init__(self, work: Any, host_tensor: Any, device_tensor: Any):
        self._work = work
        self._host_tensor = host_tensor
        self._device_tensor = device_tensor

    def wait(self) -> Any:
        self._work.wait()
        self._device_tensor.copy_(self._host_tensor, non_blocking=True)
        _synchronize_device(self._device_tensor)
        return self._device_tensor


class CPUForwardedCollective:
    """Functional cross-vendor collective using pinned host-memory staging.

    CUDA and ROCm builds both expose accelerator tensors through ``torch.cuda``.
    Gloo only sees CPU tensors, allowing ranks from vendor-specific images to
    participate in one collective without pretending their VRAM is coherent.
    """

    def __init__(self, config: Optional[RuntimeConfig] = None, torch_module: Any = None):
        self.config = config or RuntimeConfig.from_environment()
        if self.config.transport not in {"gloo", "cpu-forwarding"}:
            raise ValueError("CPUForwardedCollective requires the gloo or cpu-forwarding transport")
        self._torch = torch_module
        self._buffers: dict[tuple[Any, ...], Any] = {}

    @property
    def torch(self) -> Any:
        if self._torch is None:
            try:
                import torch
            except ImportError as error:
                raise RuntimeError("PyTorch is required in the workload image") from error
            self._torch = torch
        return self._torch

    def initialize(self) -> None:
        distributed = self.torch.distributed
        if distributed.is_initialized():
            return
        os.environ.setdefault("MASTER_ADDR", self.config.rendezvous_host)
        os.environ.setdefault("MASTER_PORT", str(self.config.rendezvous_port))
        distributed.init_process_group(
            backend="gloo",
            rank=self.config.rank,
            world_size=self.config.world_size,
        )

    def _host_buffer(self, tensor: Any) -> Any:
        key = (tuple(tensor.shape), tensor.dtype)
        buffer = self._buffers.get(key)
        if buffer is None:
            buffer = self.torch.empty_like(tensor, device="cpu", pin_memory=True)
            self._buffers[key] = buffer
        return buffer

    def all_reduce(self, tensor: Any, op: Any = None, async_op: bool = False) -> Any:
        self.initialize()
        distributed = self.torch.distributed
        reduce_op = distributed.ReduceOp.SUM if op is None else op
        if getattr(tensor.device, "type", "cpu") == "cpu":
            return distributed.all_reduce(tensor, op=reduce_op, async_op=async_op)
        host_tensor = self._host_buffer(tensor)
        host_tensor.copy_(tensor, non_blocking=True)
        _synchronize_device(tensor)
        work = distributed.all_reduce(host_tensor, op=reduce_op, async_op=async_op)
        if async_op:
            return _CPUForwardedWork(work, host_tensor, tensor)
        tensor.copy_(host_tensor, non_blocking=True)
        _synchronize_device(tensor)
        return tensor

    def barrier(self) -> Any:
        self.initialize()
        return self.torch.distributed.barrier()

    def close(self) -> None:
        distributed = self.torch.distributed
        if distributed.is_initialized():
            distributed.destroy_process_group()


class MCCLForwardedCollective:
    """PyTorch bridge to the portable MCCL host-staged backend.

    This path is functional across vendor-specific PyTorch builds, but moves
    tensor values through host memory. It is a qualification and compatibility
    backend, not the eventual direct RDMA data plane.
    """

    def __init__(self, config: Optional[RuntimeConfig] = None, torch_module: Any = None, client: Any = None):
        self.config = config or RuntimeConfig.from_environment()
        if self.config.transport not in {"mccl", "mccl-tcp"}:
            raise ValueError("MCCLForwardedCollective requires a MCCL transport")
        self._torch = torch_module
        if client is None:
            try:
                from mccl import MCCLCollective
            except ImportError as error:
                raise RuntimeError("install openmycelium-mccl in the workload image") from error
            client = MCCLCollective()
        self._client = client

    @property
    def torch(self) -> Any:
        if self._torch is None:
            try:
                import torch
            except ImportError as error:
                raise RuntimeError("PyTorch is required in the workload image") from error
            self._torch = torch
        return self._torch

    def initialize(self) -> None:
        return None

    def all_reduce(self, tensor: Any, op: Any = None, async_op: bool = False) -> Any:
        if async_op:
            raise ValueError("portable MCCL does not expose asynchronous PyTorch work handles")
        if op is not None:
            raise ValueError("portable MCCL PyTorch bridge currently supports SUM only")
        host = tensor.detach().to(device="cpu").contiguous()
        dtype = _mccl_dtype(self.torch, host.dtype)
        result = self._client.all_reduce(host.reshape(-1).tolist(), dtype=dtype, reduction="sum")
        reduced = self.torch.tensor(result, dtype=host.dtype).reshape(host.shape)
        tensor.copy_(reduced.to(device=tensor.device), non_blocking=True)
        _synchronize_device(tensor)
        return tensor

    def barrier(self) -> Any:
        return self._client.all_reduce([1], dtype="i32", reduction="sum")

    def close(self) -> None:
        return None


def _mccl_dtype(torch_module: Any, dtype: Any) -> str:
    mapping = {
        torch_module.float32: "f32",
        torch_module.float64: "f64",
        torch_module.int32: "i32",
        torch_module.int64: "i64",
    }
    try:
        return mapping[dtype]
    except KeyError as error:
        raise ValueError("portable MCCL supports float32, float64, int32, and int64 tensors") from error


def _synchronize_device(tensor: Any) -> None:
    if getattr(tensor.device, "type", "cpu") != "cpu":
        tensor.device.type  # Validate the tensor still owns a device before synchronization.
        try:
            import torch
            torch.cuda.current_stream(device=tensor.device).synchronize()
        except (ImportError, RuntimeError):
            # Test doubles and non-CUDA accelerator APIs may synchronize copies themselves.
            return
