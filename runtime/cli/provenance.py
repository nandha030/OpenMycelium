"""What exactly is running: versions, hashes, and where the settings came from.

A result is only reproducible if the thing that produced it can be named. This
collects that naming in one place so it can appear in `version --verbose`, in a
server's startup banner, and in the record of every run -- the same facts each
time rather than three approximations.

The wheel's SHA-256 matters most. Two builds can carry the same version string
and different bytes, which is exactly what happened here: 0.1.0a1 was built
twice with different content before the version was bumped. A hash makes that
visible; a version string does not.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _package_version() -> str:
    try:
        from importlib.metadata import version           # noqa: PLC0415
        return version("openmycelium")
    except Exception:                                     # noqa: BLE001
        return "unknown (not installed as a distribution)"


def _wheel_digest() -> Dict[str, Any]:
    """Hash the installed package's own files, not a wheel that may be gone.

    The wheel is often deleted after installation, so hashing it is unreliable.
    Digesting the installed `RECORD`-listed contents gives a stable identity for
    what is actually on disk.
    """
    try:
        from importlib.metadata import distribution      # noqa: PLC0415
        dist = distribution("openmycelium")
        files = sorted(str(f) for f in (dist.files or [])
                       if str(f).endswith(".py"))
        digest = hashlib.sha256()
        counted = 0
        for name in files:
            path = dist.locate_file(name)
            try:
                with open(path, "rb") as handle:
                    digest.update(name.encode("utf-8"))
                    digest.update(handle.read())
                counted += 1
            except OSError:
                continue
        return {"installedContentSha256": digest.hexdigest(),
                "pythonFiles": counted}
    except Exception as error:                            # noqa: BLE001
        return {"installedContentSha256": None, "detail": str(error)[:120]}


def _git_commit() -> Optional[str]:
    """The commit, when running from a checkout. Absent from an install."""
    try:
        result = subprocess.run(
            ["git", "-C", os.path.dirname(os.path.dirname(_HERE)),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _runtime_versions(config: Any) -> Dict[str, Any]:
    """torch, CUDA and ROCm versions from each worker environment."""
    script = ("import json,torch;"
              "print(json.dumps({'torch':torch.__version__,"
              "'cuda':getattr(torch.version,'cuda',None),"
              "'hip':getattr(torch.version,'hip',None),"
              "'device':torch.cuda.get_device_name(0)"
              " if torch.cuda.is_available() else None}))")
    out: Dict[str, Any] = {}
    for name in ("cuda_python", "rocm_python"):
        interpreter = getattr(config, name, "")
        if not interpreter or not os.path.isfile(interpreter):
            out[name] = {"available": False, "detail": "not configured"}
            continue
        try:
            result = subprocess.run([interpreter, "-c", script],
                                    capture_output=True, text=True, timeout=300)
            lines = [l for l in result.stdout.splitlines() if l.startswith("{")]
            out[name] = json.loads(lines[-1]) if lines else {
                "available": False,
                "detail": (result.stderr or "no output").strip()[-120:]}
        except Exception as error:                        # noqa: BLE001
            out[name] = {"available": False, "detail": str(error)[:120]}
    return out


def _placement_schema_version() -> int:
    """What this build's planner writes, asked of the planner itself.

    Falls back to 1 rather than raising: a provenance report must still be
    produced on a build where the scheduler directory is not importable, and
    1 is what every such build wrote.
    """
    try:
        scheduler = os.path.join(os.path.dirname(_HERE), "scheduler")
        if scheduler not in sys.path:
            sys.path.insert(0, scheduler)
        from placement import CURRENT_MANIFEST_SCHEMA_VERSION  # noqa: PLC0415
        return int(CURRENT_MANIFEST_SCHEMA_VERSION)
    except Exception:                                     # noqa: BLE001
        return 1


def collect(config: Any = None, deep: bool = False) -> Dict[str, Any]:
    """Everything that identifies this build and this machine's setup."""
    if config is None:
        from config import load                          # noqa: PLC0415
        config = load()
    record: Dict[str, Any] = {
        "openmycelium": _package_version(),
        "python": sys.version.split()[0],
        "pythonExecutable": sys.executable,
        "packageLocation": os.path.dirname(os.path.dirname(_HERE)),
        "gitCommit": _git_commit(),
        "eventSchemaVersion": 1,
        # Read from the producer rather than restated here. A constant would
        # have kept reporting 1 after producers began writing 2, which is a
        # provenance record making a false statement about its own build.
        "placementSchemaVersion": _placement_schema_version(),
    }
    record.update(_wheel_digest())
    try:
        from mccl.protocol_version import (               # noqa: PLC0415
            ACTIVATION_PROTOCOL_VERSION, COLLECTIVE_PROTOCOL_VERSION,
            TRANSPORT_NAME)
        record.update({"mcclActivationProtocol": ACTIVATION_PROTOCOL_VERSION,
                       "mcclCollectiveProtocol": COLLECTIVE_PROTOCOL_VERSION,
                       "transport": TRANSPORT_NAME})
        try:
            from importlib.metadata import version        # noqa: PLC0415
            record["mcclVersion"] = version("openmycelium-mccl")
        except Exception:                                 # noqa: BLE001
            pass
    except ImportError:
        record["mccl"] = "not importable"

    record["configuration"] = {
        name: {"value": entry.value, "source": config.source_of(name)}
        for name, entry in config.settings.items()}
    record["configFile"] = config.config_file or None

    # Which HSA runtime the ROCm environment will load, and where it came
    # from. Two files with the same name and the same version behave
    # completely differently here -- one reaches /dev/dxg, the other /dev/kfd
    # -- so recording the name alone would record nothing.
    try:
        import rocm_prereq                                  # noqa: PLC0415
        rocm_python = getattr(config, "rocm_python", "")
        if rocm_python:
            lib_dir = rocm_prereq.torch_lib_dir(rocm_python)
            active = rocm_prereq.active_hsa_runtime(lib_dir) if lib_dir else ""
            if active:
                details = rocm_prereq.hsa_flavor(active)
                system = rocm_prereq.system_hsa_runtime()
                details["source"] = (
                    "system-provided"
                    if system and system.get("sha256") == details.get("sha256")
                    else ("wsl-capable, origin unrecorded"
                          if details.get("flavor") == "dxcore"
                          else "pip-shipped"))
                details["systemRuntimePresent"] = bool(system)
                record["hsaRuntime"] = details
    except Exception:                                       # noqa: BLE001
        pass

    if deep:
        record["runtimes"] = _runtime_versions(config)
    return record


def lines(record: Dict[str, Any]) -> List[str]:
    out = [
        f"  openmycelium            {record.get('openmycelium')}",
        f"  installed content       "
        f"{str(record.get('installedContentSha256'))[:32]}"
        f"  ({record.get('pythonFiles', 0)} files)",
        f"  package location        {record.get('packageLocation')}",
        f"  python                  {record.get('python')}",
    ]
    if record.get("gitCommit"):
        out.append(f"  git commit              {record['gitCommit']}")
    out += [
        f"  mccl                    {record.get('mcclVersion', '?')}  "
        f"(activation v{record.get('mcclActivationProtocol')}, "
        f"collective v{record.get('mcclCollectiveProtocol')})",
        f"  transport               {record.get('transport')}",
        f"  event schema            v{record.get('eventSchemaVersion')}",
        f"  placement schema        v{record.get('placementSchemaVersion')}",
        "",
        f"  configuration file      {record.get('configFile') or '(none)'}",
    ]
    for name, entry in (record.get("configuration") or {}).items():
        out.append(f"    {name:<18} {entry['value'] or '(unset)':<46} "
                   f"{entry['source']}")
    runtimes = record.get("runtimes")
    if runtimes:
        out.append("")
        for name, info in runtimes.items():
            if info.get("torch"):
                out.append(f"  {name:<22} torch {info['torch']}  "
                           f"cuda={info.get('cuda')}  hip={info.get('hip')}")
                out.append(f"  {'':<22} {info.get('device')}")
            else:
                out.append(f"  {name:<22} unavailable: "
                           f"{info.get('detail', 'unknown')}")
    return out
