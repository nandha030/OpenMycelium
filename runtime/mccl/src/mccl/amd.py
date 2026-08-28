"""AMD processor and accelerator discovery across Linux and Windows.

Two things this module refuses to do, because the rest of OpenMycelium depends
on discovery being literally true:

* It never reports an AMD GPU as ROCm-capable merely because the card exists.
  A Radeon with only a display driver is reported as a display adapter with
  `compute_ready=False`. ROCm is claimed only when ROCm tooling answers.
* It never reports `Win32_VideoController.AdapterRAM` as VRAM. That property is
  a signed 32-bit value and saturates at 4 GiB, so a 16 GiB card reports 4 GiB.
  On Windows the 64-bit `HardwareInformation.qwMemorySize` registry value is
  read instead, and its provenance is recorded in `source`.

Every entry point takes an injectable command runner so the platform paths can
be tested on a machine that has neither ROCm nor the hardware.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, Sequence

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# Display-class GUID; each numbered subkey below it is one display adapter.
_DISPLAY_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

# SIMD extensions worth reporting for collective and kernel planning. Zen 4 and
# Zen 5 carry avx512f; earlier AMD parts stop at avx2.
_TRACKED_CPU_FLAGS = ("avx", "avx2", "avx512f", "avx512bw", "avx512vnni", "fma", "sse4_2")


@dataclass(frozen=True)
class AMDProcessor:
    model: str
    vendor: str = "amd"
    physical_cores: int = 0
    logical_cores: int = 0
    base_mhz: int = 0
    numa_nodes: int = 1
    simd: tuple[str, ...] = ()
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def supports_avx512(self) -> bool:
        return any(flag.startswith("avx512") for flag in self.simd)


@dataclass(frozen=True)
class AMDAccelerator:
    name: str
    index: int
    memory_mib: int = 0
    driver: str = ""
    architecture: str = ""
    integrated: bool = False
    compute_ready: bool = False
    compute_runtime: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def runtime(self) -> str:
        """`rocm` only when a ROCm stack answered; otherwise display-only."""
        return self.compute_runtime or "amd-display"


@dataclass(frozen=True)
class AMDInventory:
    processor: Optional[AMDProcessor] = None
    accelerators: tuple[AMDAccelerator, ...] = ()
    rocm_tools: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "processor": self.processor.to_dict() if self.processor else None,
            "accelerators": [item.to_dict() for item in self.accelerators],
            "rocmTools": list(self.rocm_tools),
            "computeReady": self.compute_ready,
            "notes": list(self.notes),
        }

    @property
    def compute_ready(self) -> bool:
        return any(item.compute_ready for item in self.accelerators)


def discover_amd(run: Runner = subprocess.run, system: str = "") -> AMDInventory:
    """Discover the AMD processor and accelerators visible on this host."""
    system = system or platform.system()
    tools = rocm_tools(run)
    accelerators = discover_amd_gpus(run, system=system, tools=tools)
    processor = discover_amd_processor(run, system=system)
    notes: list[str] = []
    if accelerators and not tools:
        notes.append(
            "AMD graphics hardware is present but no ROCm tooling answered; "
            "these adapters are reported as display-only, not as compute capacity"
        )
    if system == "Windows" and accelerators:
        notes.append(
            "ROCm and RCCL have no Windows build; AMD compute requires Linux with /dev/kfd"
        )
    return AMDInventory(processor, tuple(accelerators), tools, tuple(notes))


def rocm_tools(run: Runner = subprocess.run) -> tuple[str, ...]:
    """Which ROCm command-line tools are actually resolvable on PATH."""
    return tuple(name for name in ("rocm-smi", "rocminfo", "hipInfo") if shutil.which(name))


def discover_amd_gpus(
    run: Runner = subprocess.run,
    system: str = "",
    tools: Sequence[str] = (),
) -> list[AMDAccelerator]:
    system = system or platform.system()
    tools = tuple(tools) if tools else rocm_tools(run)
    if "rocm-smi" in tools:
        found = _from_rocm_smi(run)
        if found:
            return found
    if system == "Windows":
        return _from_windows_registry()
    return []


def discover_amd_processor(run: Runner = subprocess.run, system: str = "") -> Optional[AMDProcessor]:
    system = system or platform.system()
    if system == "Linux":
        return _processor_from_proc_cpuinfo()
    if system == "Windows":
        return _processor_from_windows(run)
    return None


def _from_rocm_smi(run: Runner) -> list[AMDAccelerator]:
    output = _command(
        run,
        ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--showdriverversion", "--json"],
    )
    if not output:
        return []
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    accelerators: list[AMDAccelerator] = []
    for index, (_, details) in enumerate(sorted(payload.items())):
        if not isinstance(details, dict):
            continue
        name = str(details.get("Card series") or details.get("Card model") or "AMD accelerator")
        memory_bytes = _first_number(details, "VRAM Total Memory (B)", "VRAM Total Used Memory (B)")
        accelerators.append(
            AMDAccelerator(
                name=name,
                index=index,
                memory_mib=memory_bytes // (1024 * 1024),
                driver=str(details.get("Driver version", "")),
                architecture=str(details.get("Card SKU", "")),
                compute_ready=True,
                compute_runtime="rocm",
                source="rocm-smi",
            )
        )
    return accelerators


def _from_windows_registry() -> list[AMDAccelerator]:
    """Read AMD display adapters with their true 64-bit VRAM size.

    `Win32_VideoController.AdapterRAM` cannot represent more than 4 GiB, so the
    display-class registry key is the only stdlib-reachable source of honest
    VRAM on Windows.
    """
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return []
    accelerators: list[AMDAccelerator] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY)
    except OSError:
        return []
    with root:
        for position in range(64):
            try:
                subkey_name = winreg.EnumKey(root, position)
            except OSError:
                break
            if not re.fullmatch(r"\d{4}", subkey_name):
                continue
            try:
                with winreg.OpenKey(root, subkey_name) as subkey:
                    description = _registry_text(subkey, "DriverDesc")
                    if not _is_amd(description):
                        continue
                    memory_bytes = _registry_value(subkey, "HardwareInformation.qwMemorySize")
                    accelerators.append(
                        AMDAccelerator(
                            name=description,
                            index=len(accelerators),
                            memory_mib=int(memory_bytes) // (1024 * 1024) if memory_bytes else 0,
                            driver=_registry_text(subkey, "DriverVersion"),
                            architecture=_registry_text(subkey, "HardwareInformation.ChipType"),
                            integrated=_is_integrated(description),
                            compute_ready=False,
                            compute_runtime="",
                            source="windows-registry:qwMemorySize",
                        )
                    )
            except OSError:
                continue
    return accelerators


def _processor_from_proc_cpuinfo() -> Optional[AMDProcessor]:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as source:
            content = source.read()
    except OSError:
        return None
    if "AuthenticAMD" not in content:
        return None
    model = ""
    flags: tuple[str, ...] = ()
    physical = 0
    base_mhz = 0
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if key == "model name" and not model:
            model = value
        elif key == "flags" and not flags:
            present = set(value.split())
            flags = tuple(flag for flag in _TRACKED_CPU_FLAGS if flag in present)
        elif key == "cpu cores" and not physical:
            physical = _safe_int(value)
        elif key == "cpu MHz" and not base_mhz:
            base_mhz = _safe_int(value)
    logical = content.count("processor\t:") or content.count("processor :")
    return AMDProcessor(
        model=model or "AMD processor",
        physical_cores=physical,
        logical_cores=logical,
        base_mhz=base_mhz,
        numa_nodes=_linux_numa_nodes(),
        simd=flags,
        source="/proc/cpuinfo",
    )


def _processor_from_windows(run: Runner) -> Optional[AMDProcessor]:
    output = _command(
        run,
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Processor | "
            "Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | "
            "ConvertTo-Json -Compress",
        ],
    )
    if not output:
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None
    manufacturer = str(payload.get("Manufacturer", ""))
    name = str(payload.get("Name", "")).strip()
    if "AMD" not in manufacturer.upper() and not _is_amd(name):
        return None
    return AMDProcessor(
        model=name or "AMD processor",
        physical_cores=_safe_int(payload.get("NumberOfCores")),
        logical_cores=_safe_int(payload.get("NumberOfLogicalProcessors")),
        base_mhz=_safe_int(payload.get("MaxClockSpeed")),
        numa_nodes=1,
        simd=_windows_simd(),
        source="Win32_Processor",
    )


def _linux_numa_nodes() -> int:
    try:
        entries = [name for name in os.listdir("/sys/devices/system/node") if re.fullmatch(r"node\d+", name)]
    except OSError:
        return 1
    return len(entries) or 1


def _is_amd(description: str) -> bool:
    lowered = description.lower()
    return "amd" in lowered or "radeon" in lowered


def _is_integrated(description: str) -> bool:
    lowered = description.lower()
    # Integrated Radeon parts report as plain "AMD Radeon(TM) Graphics" with no
    # discrete model number.
    return "graphics" in lowered and not re.search(r"\b(rx|pro|instinct|mi)\b", lowered)


def _registry_value(key: object, name: str) -> object:
    import winreg  # noqa: PLC0415 - Windows only

    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value


def _registry_text(key: object, name: str) -> str:
    """Decode a registry value that may be REG_BINARY holding UTF-16LE text.

    `HardwareInformation.ChipType` is stored as raw bytes, so reading it
    directly yields a byte repr rather than the chip name.
    """
    value = _registry_value(key, name)
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
    return str(value).strip()


# Values accepted by kernel32!IsProcessorFeaturePresent. Windows exposes no
# cpuinfo-style flag list, so this is the supported way to read SIMD support.
_WINDOWS_PROCESSOR_FEATURES = {
    "sse4_2": 38,
    "avx": 39,
    "avx2": 40,
    "avx512f": 41,
}


def _windows_simd() -> tuple[str, ...]:
    try:
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError):
        return ()
    present: list[str] = []
    for flag in _TRACKED_CPU_FLAGS:
        feature = _WINDOWS_PROCESSOR_FEATURES.get(flag)
        if feature is None:
            continue
        try:
            if kernel32.IsProcessorFeaturePresent(feature):
                present.append(flag)
        except OSError:
            continue
    return tuple(present)


def _command(run: Runner, arguments: list[str]) -> str:
    executable = shutil.which(arguments[0])
    if executable is None:
        return ""
    try:
        completed = run([executable, *arguments[1:]], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _first_number(payload: dict[str, object], *keys: str) -> int:
    for key in keys:
        if key in payload:
            return _safe_int(payload[key])
    return 0
