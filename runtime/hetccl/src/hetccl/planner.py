"""Explainable collective transport selection for heterogeneous hosts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from .discovery import CapabilityReport


@dataclass(frozen=True)
class CollectivePlan:
    backend: str
    executable: bool
    mode: str
    native_backends: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def plan_collective(reports: Sequence[CapabilityReport], prefer_device_direct: bool = False) -> CollectivePlan:
    if not reports:
        return CollectivePlan("none", False, "invalid", (), ("no hosts were supplied",), ())
    runtimes = {device.runtime for report in reports for device in report.devices}
    native = tuple(sorted({native_backend(runtime) for runtime in runtimes if native_backend(runtime)}))
    heterogeneous = len(runtimes) > 1
    all_rdma = all(report.transports.get("rdma") == "qualified" for report in reports)
    all_direct = all(report.transports.get("device_direct") == "qualified" for report in reports)
    if prefer_device_direct and heterogeneous and all_rdma and all_direct:
        return CollectivePlan(
            "rdma",
            True,
            "hierarchical-device-direct",
            native,
            ("every host advertises a qualified RDMA and device-memory path",),
            (),
        )
    warnings: list[str] = []
    if prefer_device_direct:
        warnings.append("device-direct was requested but at least one host lacks qualified RDMA or peer-memory evidence")
    if heterogeneous:
        reasons = ("multiple accelerator runtimes require a common cross-vendor transport", "TCP host staging is qualified on every supported operating system")
        mode = "hierarchical-host-staged"
    elif runtimes:
        reasons = ("the portable package uses host staging until the detected vendor adapter is qualified",)
        mode = "host-staged"
    else:
        reasons = ("no accelerator was detected; the CPU reference path remains executable",)
        mode = "cpu-reference"
    return CollectivePlan("tcp", True, mode, native, reasons, tuple(warnings))


def native_backend(runtime: str) -> str:
    return {"cuda": "nccl", "rocm": "rccl", "oneapi": "oneccl", "metal": "mps"}.get(runtime, "")
