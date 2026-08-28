"""Transport qualification for cross-vendor device memory.

A transport may receive into device memory only when it has a verified
device-copy path for that vendor. Host-accessibility is a related but distinct
fact, and measurement separated them: on ROCm-for-WSL neither CUDA nor ROCm
device memory answers a CPU load or store, yet UCX receives into CUDA memory
correctly and segfaults on ROCm. `uct_cuda_copy` imports only `cuMemcpyAsync`;
`uct_rocm_copy` also imports `memcpy` and `ucs_x86_memcpy_sse_movntdqa`, and
its short path stores from the CPU.

Two distinct UCX paths are unsafe: `uct_rocm_copy_ep_put_short`, and the
generic eager unpack that memcpys when no memory-type copier is selected.
Removing the `access` capability would address the first only, so the decision
is made here, above the transport, and fails closed: a vendor whose receive
path has not been verified is treated as unsafe.

The measured input comes from `runtime/bridge/host_access_probe`, which traps
SIGSEGV/SIGBUS around one real load and one real store rather than inferring
from version numbers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

#: Transport that may receive straight into device memory.
DIRECT = "ucx-direct"
#: Transport that stages every device transfer through pinned host memory.
HOST_STAGED = "mccl-tcp-host-staged"


@dataclass(frozen=True)
class VendorAccess:
    vendor: str
    available: bool = False
    allocated: bool = False
    host_store_ok: bool = False
    host_load_ok: bool = False

    @property
    def host_accessible(self) -> bool:
        return self.available and self.allocated and self.host_store_ok and self.host_load_ok

    @classmethod
    def from_mapping(cls, vendor: str, payload: Mapping[str, Any]) -> "VendorAccess":
        return cls(
            vendor=vendor,
            available=bool(payload.get("available", False)),
            allocated=bool(payload.get("allocated", False)),
            host_store_ok=bool(payload.get("host_store_ok", False)),
            host_load_ok=bool(payload.get("host_load_ok", False)),
        )


@dataclass(frozen=True)
class TransportDecision:
    receive_vendor: str
    transport: str
    direct_receive_permitted: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_receive_transport(
    access: VendorAccess,
    transport_receive_verified: Optional[bool] = None,
) -> TransportDecision:
    """Choose how a transfer may land in `access.vendor` device memory.

    Host-accessibility alone is the wrong gate, and measurement showed why:
    on this platform neither CUDA nor ROCm device memory answers a CPU load or
    store, yet UCX receives into CUDA memory correctly. `uct_cuda_copy` routes
    its short path through `cuMemcpyAsync`, while `uct_rocm_copy` uses a CPU
    memcpy and faults. So the deciding fact is whether the transport has a
    *verified* device-copy path for that vendor, not whether the host could
    address the memory itself.

    `transport_receive_verified` carries that measurement from the conformance
    matrix. Unknown is treated as unsafe.
    """
    if not access.available:
        return TransportDecision(
            access.vendor, HOST_STAGED, False,
            (f"no {access.vendor} runtime is present; nothing may target its device memory",),
        )
    if not access.allocated:
        return TransportDecision(
            access.vendor, HOST_STAGED, False,
            (f"{access.vendor} device allocation failed during qualification",),
        )

    faults = [name for name, ok in (("store", access.host_store_ok),
                                    ("load", access.host_load_ok)) if not ok]
    accessibility = (
        f"{access.vendor} device memory answered a host load and store"
        if not faults
        else f"{access.vendor} device memory faulted on host {' and '.join(faults)}"
    )

    if transport_receive_verified is True:
        return TransportDecision(
            access.vendor, DIRECT, True,
            (
                accessibility,
                f"transport receive into {access.vendor} memory was verified byte-for-byte",
            ),
        )
    if transport_receive_verified is False:
        return TransportDecision(
            access.vendor, HOST_STAGED, False,
            (
                accessibility,
                f"transport receive into {access.vendor} memory failed correctness testing",
                "staging through pinned host memory is the only qualified path",
            ),
        )
    return TransportDecision(
        access.vendor, HOST_STAGED, False,
        (
            accessibility,
            f"transport receive into {access.vendor} memory has not been verified",
            "unverified device receive is treated as unsafe",
        ),
    )


def qualify(
    probe_output: Mapping[str, Any],
    transport_receive_verified: Optional[Mapping[str, bool]] = None,
) -> dict[str, TransportDecision]:
    """Turn raw probe JSON plus conformance results into a receive policy."""
    verified = transport_receive_verified or {}
    decisions: dict[str, TransportDecision] = {}
    for vendor in ("cuda", "rocm"):
        payload = probe_output.get(vendor)
        access = (
            VendorAccess.from_mapping(vendor, payload)
            if isinstance(payload, Mapping)
            else VendorAccess(vendor)
        )
        decisions[vendor] = decide_receive_transport(access, verified.get(vendor))
    return decisions


def run_probe(
    executable: str = "host_access_probe",
    run: Any = subprocess.run,
    search_paths: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Execute the probe binary and parse its JSON, or return an empty report.

    An unreadable probe yields an empty mapping, which `qualify` turns into
    host-staged for every vendor. Failing closed is the point.
    """
    candidates = list(search_paths or ()) + [executable]
    for candidate in candidates:
        path = candidate if candidate.startswith("/") else shutil.which(candidate)
        if not path:
            continue
        try:
            completed = run([path], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}
