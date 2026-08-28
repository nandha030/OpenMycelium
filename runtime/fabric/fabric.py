"""Mycelium Fabric: discover accelerators and give each a stable identity.

Everything above this layer -- Intelligence recording history per device,
Scheduler committing a placement to a device, HetFabric deciding which edges are
cross-vendor -- depends on being able to name a device and have that name still
mean the same card tomorrow. So identity is the point of this module, and the
inventory is a by-product.

What the hardware here actually offers, measured rather than assumed:

* NVIDIA reports a real UUID (`GPU-cbb3d045-...`), stable across runs, plus a
  full PCI bus id from `nvidia-smi`. Strong identity.
* AMD under WSL reports a UUID that is *not stable*: two consecutive probes
  returned `66666666-6666-6666-6666-666666666666` and
  `61353962-3739-3433-3630-306438396535`. That is uninitialised memory, not an
  identity, and using it would silently scatter one card's history across many
  identities. AMD is therefore keyed on its PCI bus, and the UUID is recorded
  only as evidence that it must not be trusted.
* Both runtimes call their own card `cuda:0`, so the runtime-local index is
  never an identity -- it is a coordinate that means different things in
  different processes.

Each device records which source its identity came from, so a caller can tell a
UUID-backed name from a bus-backed one rather than treating all names as equally
durable.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

GIB = 1 << 30
CACHE_PATH = "/opt/openmycelium/fabric.json"
CACHE_TTL_SECONDS = 300.0
CACHE_SCHEMA_VERSION = 2

#: How much a name can be trusted to survive a reboot or a driver reload.
IDENTITY_UUID = "uuid"          # vendor-issued, unique, stable
IDENTITY_PCI = "pci"            # slot-based: stable until the card is moved
IDENTITY_PCI_BUS = "pci-bus"    # bus only: useful but may collide by domain
IDENTITY_INDEX = "index"        # enumeration order: not stable, last resort

CONFIDENCE = {IDENTITY_UUID: "strong", IDENTITY_PCI: "slot-stable",
              IDENTITY_PCI_BUS: "bus-stable", IDENTITY_INDEX: "unstable"}


@dataclass
class Device:
    """One accelerator, named so the name still means this card tomorrow."""

    vendor: str                       # nvidia | amd | intel
    identity: str                     # the stable name, e.g. nvidia:GPU-cbb3...
    identity_source: str              # which field produced it
    name: str = ""
    runtime: str = ""                 # cuda | rocm | xpu
    runtime_index: int = 0            # local coordinate, NOT an identity
    total_bytes: int = 0
    free_bytes: int = 0
    pci_bus: Optional[str] = None
    uuid: Optional[str] = None
    uuid_trusted: bool = False
    driver_version: str = ""
    torch_version: str = ""
    arch: str = ""
    power_watts: Optional[float] = None
    power_limit_watts: Optional[float] = None
    power_source: str = "unavailable"
    notes: List[str] = field(default_factory=list)

    @property
    def identity_confidence(self) -> str:
        return CONFIDENCE.get(self.identity_source, "unknown")

    def to_dict(self) -> Dict[str, Any]:
        record = asdict(self)
        record["identityConfidence"] = self.identity_confidence
        record["totalGiB"] = round(self.total_bytes / GIB, 2)
        record["freeGiB"] = round(self.free_bytes / GIB, 2)
        return record


# ------------------------------------------------------------------- probes

_TORCH_PROBE = (
    "import json,torch;"
    "ok=torch.cuda.is_available();"
    "d=(torch.cuda.get_device_properties(0) if ok else None);"
    "free,total=(torch.cuda.mem_get_info(0) if ok else (0,0));"
    "print(json.dumps({'available':ok,"
    "'name':getattr(d,'name',''),"
    "'uuid':(str(getattr(d,'uuid','')) if getattr(d,'uuid',None) is not None else None),"
    "'pciBus':getattr(d,'pci_bus_id',None),"
    "'pciDevice':getattr(d,'pci_device_id',None),"
    "'pciDomain':getattr(d,'pci_domain_id',None),"
    "'arch':str(getattr(d,'gcnArchName','')) or "
    "  (str(getattr(d,'major',''))+'.'+str(getattr(d,'minor','')) if d else ''),"
    "'free':free,'total':total,"
    "'torch':torch.__version__,"
    "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda'}))")


def _run(command: List[str], timeout: float = 300.0) -> Optional[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _torch_probe(python: str) -> Optional[Dict[str, Any]]:
    out = _run([python, "-c", _TORCH_PROBE])
    if not out:
        return None
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _nvidia_smi() -> Optional[Dict[str, str]]:
    out = _run(["nvidia-smi", "--query-gpu=index,name,pci.bus_id,uuid,"
                "memory.total,driver_version,power.draw,power.limit",
                "--format=csv,noheader,nounits"])
    if not out:
        return None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            return {"index": parts[0], "name": parts[1], "pci": parts[2],
                    "uuid": parts[3], "memory": parts[4], "driver": parts[5],
                    "power": parts[6] if len(parts) > 6 else "",
                    "powerLimit": parts[7] if len(parts) > 7 else ""}
    return None


def _float_or_none(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_nvidia_uuid(value: Any) -> Optional[str]:
    """Normalize a real UUID and reject stringified missing values."""
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown", "n/a"}:
        return None
    if text.startswith("GPU-") and len(text) > 8:
        return text
    compact = text.replace("-", "")
    if len(compact) >= 16 and all(ch in "0123456789abcdefABCDEF" for ch in compact):
        return f"GPU-{text}"
    return None


def _amd_pci_location(torch_info: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Return the strongest PCI location the runtime exposes.

    A bus number alone is not globally unique when PCI domains are present, so
    it receives a weaker source/confidence label than a domain:bus:device BDF.
    GPUs normally use function zero; PyTorch does not expose the function.
    """
    bus = _int_or_none(torch_info.get("pciBus"))
    device = _int_or_none(torch_info.get("pciDevice"))
    domain = _int_or_none(torch_info.get("pciDomain"))
    if bus is not None and device is not None and domain is not None:
        return f"{domain:04x}:{bus:02x}:{device:02x}.0", IDENTITY_PCI
    if bus is not None:
        return f"bus-{bus:02x}", IDENTITY_PCI_BUS
    return None, IDENTITY_INDEX


def probe_nvidia(python: str) -> List[Device]:
    torch_info = _torch_probe(python)
    if not torch_info or not torch_info.get("available"):
        return []
    if torch_info.get("runtime") != "cuda":
        return []
    smi = _nvidia_smi()

    uuid = None
    source = IDENTITY_INDEX
    if smi:
        uuid = _valid_nvidia_uuid(smi.get("uuid"))
    if uuid is None:
        uuid = _valid_nvidia_uuid(torch_info.get("uuid"))
    if uuid is not None:
        source = IDENTITY_UUID

    pci = smi.get("pci") if smi else None
    if source == IDENTITY_UUID:
        identity = f"nvidia:{uuid}"
    elif pci:
        identity, source = f"nvidia:pci-{pci}", IDENTITY_PCI
    else:
        identity = "nvidia:index-0"

    device = Device(
        vendor="nvidia", identity=identity, identity_source=source,
        name=torch_info.get("name") or (smi or {}).get("name", ""),
        runtime="cuda", runtime_index=0,
        total_bytes=int(torch_info.get("total", 0)),
        free_bytes=int(torch_info.get("free", 0)),
        pci_bus=pci, uuid=uuid, uuid_trusted=source == IDENTITY_UUID,
        driver_version=(smi or {}).get("driver", ""),
        torch_version=torch_info.get("torch", ""),
        arch=torch_info.get("arch", ""),
    )
    if smi:
        device.power_watts = _float_or_none(smi.get("power", ""))
        device.power_limit_watts = _float_or_none(smi.get("powerLimit", ""))
        if device.power_watts is not None:
            device.power_source = "nvidia-smi"
    else:
        device.notes.append("nvidia-smi unavailable; identity fell back to torch")
    return [device]


def probe_amd(python: str) -> List[Device]:
    torch_info = _torch_probe(python)
    if not torch_info or not torch_info.get("available"):
        return []
    if torch_info.get("runtime") != "rocm":
        return []

    pci, source = _amd_pci_location(torch_info)
    reported_uuid = torch_info.get("uuid")
    # Deliberately not used as an identity: probed twice in a row under WSL and
    # returned two different values, so it identifies nothing.
    if pci is not None:
        identity = f"amd:pci-{pci}"
    else:
        identity, source = "amd:index-0", IDENTITY_INDEX

    device = Device(
        vendor="amd", identity=identity, identity_source=source,
        name=torch_info.get("name", ""), runtime="rocm", runtime_index=0,
        total_bytes=int(torch_info.get("total", 0)),
        free_bytes=int(torch_info.get("free", 0)),
        pci_bus=pci,
        uuid=reported_uuid, uuid_trusted=False,
        torch_version=torch_info.get("torch", ""),
        arch=torch_info.get("arch", ""),
    )
    device.notes.append(
        "UUID reported by the ROCm runtime is not stable across processes and "
        "is recorded for evidence only; identity is the PCI bus")
    device.notes.append(
        "power telemetry unavailable under WSL: rocm-smi needs the amdgpu "
        "kernel module, and WSL exposes /dev/dxg instead")
    return [device]


PROBES = {"nvidia": probe_nvidia, "amd": probe_amd}
def _default_interpreters() -> Dict[str, str]:
    """Resolved through the configuration, not pinned to one machine."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli"))
        import config as _config              # noqa: PLC0415
        resolved = _config.load()
        return {"nvidia": resolved.cuda_python, "amd": resolved.rocm_python}
    except Exception:                          # noqa: BLE001
        return {"nvidia": "", "amd": ""}


DEFAULT_INTERPRETERS = _default_interpreters()


# ------------------------------------------------------------------ fabric

def discover(interpreters: Optional[Dict[str, str]] = None,
             use_cache: bool = True, cache_path: str = CACHE_PATH,
             ttl: float = CACHE_TTL_SECONDS) -> Dict[str, Any]:
    """Every accelerator this host can reach, each with a stable identity."""
    if use_cache:
        cached = _read_cache(cache_path, ttl)
        if cached is not None:
            cached["fromCache"] = True
            return cached

    interpreters = interpreters or DEFAULT_INTERPRETERS
    devices: List[Device] = []
    unavailable: Dict[str, str] = {}
    for vendor, probe in PROBES.items():
        python = interpreters.get(vendor)
        if not python or not os.path.exists(python):
            unavailable[vendor] = f"no interpreter at {python}"
            continue
        found = probe(python)
        if found:
            devices.extend(found)
        else:
            unavailable[vendor] = "probe reported no usable device"

    identities = [d.identity for d in devices]
    duplicates = sorted({i for i in identities if identities.count(i) > 1})
    report = {
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "devices": [d.to_dict() for d in devices],
        "unavailable": unavailable,
        "totalBytes": sum(d.total_bytes for d in devices),
        "freeBytes": sum(d.free_bytes for d in devices),
        "vendors": sorted({d.vendor for d in devices}),
        "crossVendor": len({d.vendor for d in devices}) > 1,
        # Two devices sharing one identity would make every placement decision
        # about them ambiguous, so it is reported as a fault rather than merged.
        "duplicateIdentities": duplicates,
        "identitiesUnique": not duplicates,
        "probedAt": round(time.time(), 3),
        "fromCache": False,
    }
    _write_cache(cache_path, report)
    return report


def _read_cache(path: str, ttl: float) -> Optional[Dict[str, Any]]:
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        if report.get("schemaVersion") != CACHE_SCHEMA_VERSION:
            return None
        return report
    except (OSError, ValueError):
        return None


def _write_cache(path: str, report: Dict[str, Any]) -> None:
    temporary = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def find(identity: str, report: Optional[Dict[str, Any]] = None
         ) -> Optional[Dict[str, Any]]:
    """Look a device up by identity, or by a unique prefix of one."""
    report = report or discover()
    devices = report["devices"]
    for device in devices:
        if device["identity"] == identity:
            return device
    matches = [d for d in devices if d["identity"].startswith(identity)]
    return matches[0] if len(matches) == 1 else None
