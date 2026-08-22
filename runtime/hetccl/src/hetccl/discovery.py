"""Conservative cross-platform accelerator and transport discovery."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class Device:
    vendor: str
    runtime: str
    name: str
    index: int
    memory_mib: int = 0
    driver: str = ""
    architecture: str = ""


@dataclass(frozen=True)
class CapabilityReport:
    hostname: str
    operating_system: str
    machine: str
    devices: tuple[Device, ...]
    adapters: dict[str, str]
    transports: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["devices"] = [asdict(device) for device in self.devices]
        return payload


def discover_capabilities(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> CapabilityReport:
    devices: list[Device] = []
    adapters = {"host": "ready", "cuda": "unavailable", "rocm": "unavailable", "oneapi": "unavailable", "metal": "unavailable"}
    devices.extend(_discover_nvidia(run))
    if any(device.runtime == "cuda" for device in devices):
        adapters["cuda"] = "detected-unqualified"
    devices.extend(_discover_amd(run))
    if any(device.runtime == "rocm" for device in devices):
        adapters["rocm"] = "detected-unqualified"
    devices.extend(_discover_intel(run))
    if any(device.runtime == "oneapi" for device in devices):
        adapters["oneapi"] = "detected-unqualified"
    devices.extend(_discover_apple(run))
    if any(device.runtime == "metal" for device in devices):
        adapters["metal"] = "detected-unqualified"
    transports = {
        "tcp": "ready",
        "gloo": "available" if _module_available("torch") else "unavailable",
        "rdma": _rdma_status(),
        "device_direct": "blocked-until-qualified",
    }
    return CapabilityReport(
        hostname=platform.node() or os.environ.get("COMPUTERNAME", "unknown"),
        operating_system=platform.system().lower(),
        machine=platform.machine().lower(),
        devices=tuple(devices),
        adapters=adapters,
        transports=transports,
    )


def _command(run: Callable[..., subprocess.CompletedProcess[str]], arguments: list[str]) -> str:
    executable = shutil.which(arguments[0])
    if executable is None:
        return ""
    try:
        completed = run([executable, *arguments[1:]], capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _discover_nvidia(run: Callable[..., subprocess.CompletedProcess[str]]) -> list[Device]:
    output = _command(run, ["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    devices: list[Device] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        devices.append(Device("nvidia", "cuda", parts[1], _safe_int(parts[0], len(devices)), _safe_int(parts[2]), parts[3], parts[4] if len(parts) > 4 else ""))
    return devices


def _discover_amd(run: Callable[..., subprocess.CompletedProcess[str]]) -> list[Device]:
    output = _command(run, ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--showdriverversion", "--json"])
    if not output:
        return []
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    devices: list[Device] = []
    for index, (_, details) in enumerate(sorted(payload.items())):
        if not isinstance(details, dict):
            continue
        name = str(details.get("Card series", details.get("Card model", "AMD accelerator")))
        memory_bytes = _first_number(details, "VRAM Total Memory (B)", "VRAM Total Used Memory (B)")
        driver = str(details.get("Driver version", ""))
        devices.append(Device("amd", "rocm", name, index, memory_bytes // (1024 * 1024), driver))
    return devices


def _discover_intel(run: Callable[..., subprocess.CompletedProcess[str]]) -> list[Device]:
    output = _command(run, ["xpu-smi", "discovery", "-j"])
    if not output:
        return []
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    entries = payload.get("device_list", payload if isinstance(payload, list) else [])
    devices: list[Device] = []
    if isinstance(entries, list):
        for index, details in enumerate(entries):
            if isinstance(details, dict):
                devices.append(Device("intel", "oneapi", str(details.get("device_name", "Intel accelerator")), index))
    return devices


def _discover_apple(run: Callable[..., subprocess.CompletedProcess[str]]) -> list[Device]:
    if platform.system() != "Darwin":
        return []
    output = _command(run, ["system_profiler", "SPDisplaysDataType", "-json"])
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    entries = payload.get("SPDisplaysDataType", [])
    return [Device("apple", "metal", str(item.get("sppci_model", "Apple GPU")), index) for index, item in enumerate(entries) if isinstance(item, dict)]


def _module_available(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _rdma_status() -> str:
    if platform.system() != "Linux":
        return "unavailable"
    if os.path.isdir("/sys/class/infiniband") and os.listdir("/sys/class/infiniband"):
        return "detected-unqualified"
    return "unavailable"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return default


def _first_number(payload: dict[str, object], *keys: str) -> int:
    for key in keys:
        if key in payload:
            return _safe_int(payload[key])
    return 0
