"""Tell three different ROCm situations apart, and refuse precisely.

A clean-bootstrap run on 0.1.0a6 installed `torch==2.10.0+rocm7.0` without
error and then found no AMD device at all. The cause was not the packages:
pip's ROCm wheel carries the *native Linux* HSA runtime, which talks to
`/dev/kfd`. WSL has no `/dev/kfd`; it exposes the GPU through `/dev/dxg`, and
AMD ships a separate WSL build of that runtime with its system components.

Nothing in the Python layer can see this. `pip list` is identical either way,
`import torch` succeeds either way, and `torch.cuda.is_available()` returns
False with no explanation. So these three states are reported separately and
never collapsed into one "ROCm not working" message:

    packagesInstalled       the Python side is complete
    systemRuntimeAvailable  a WSL-capable HSA runtime is what torch will load
    gpuQualified            a real BF16 operation ran on the AMD card

Installing the system components means adding AMD's apt repositories and
several gigabytes of system-wide packages. That is not authority an ordinary
provisioning command should take, so this module only detects and explains.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

#: Byte patterns that identify which device node a runtime was built to use.
#: Reading the ELF directly avoids depending on `strings`, which a minimal
#: image does not have.
KFD_MARKER = b"/dev/kfd"
DXCORE_MARKERS = (b"libdxcore.so", b"dxcore")

#: Where AMD's system components put their WSL runtime.
SYSTEM_HSA_CANDIDATES = (
    "/opt/rocm/lib/libhsa-runtime64.so.1",
    "/opt/rocm/lib/libhsa-runtime64.so",
    "/usr/lib/x86_64-linux-gnu/libhsa-runtime64.so.1",
)

#: The documented AMD procedure. Quoted rather than executed.
INSTALL_DOCS = (
    "https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/"
    "installrad/wsl/install-radeon.html")
PYTORCH_DOCS = (
    "https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/"
    "installrad/wsl/legacywsl/install-pytorch.html")


# --------------------------------------------------------------- inspection

def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def hsa_flavor(path: str) -> Dict[str, Any]:
    """Which device node this HSA runtime was built to drive.

    A WSL build references dxcore and reaches the GPU through /dev/dxg. A
    native Linux build references /dev/kfd and cannot work under WSL at all.
    Both are named libhsa-runtime64.so, so the name proves nothing.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError as error:
        return {"path": path, "flavor": "unreadable", "detail": str(error)}
    kfd = blob.count(KFD_MARKER)
    dxcore = sum(blob.count(marker) for marker in DXCORE_MARKERS)
    if dxcore > 0:
        flavor = "dxcore"
    elif kfd > 0:
        flavor = "kfd"
    else:
        flavor = "unknown"
    return {
        "path": path,
        "flavor": flavor,
        "kfdReferences": kfd,
        "dxcoreReferences": dxcore,
        "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
        "sha256": _sha256(path),
    }


def torch_lib_dir(python: str) -> str:
    """Where the ROCm interpreter's torch keeps its shared libraries."""
    script = ("import os,torch;"
              "print(os.path.join(os.path.dirname(torch.__file__),'lib'))")
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in reversed(out.stdout.splitlines()):
        if line.strip().endswith("lib"):
            return line.strip()
    return ""


def active_hsa_runtime(lib_dir: str) -> str:
    """The file torch's HIP library will actually load.

    The loader takes the soname first, so a `.so.1` present beside a `.so`
    wins. Checking in that order is what makes a half-finished substitution --
    one file replaced, the other not -- visible rather than confusing.
    """
    for name in ("libhsa-runtime64.so.1", "libhsa-runtime64.so"):
        candidate = os.path.join(lib_dir, name)
        if os.path.exists(candidate):
            return candidate
    return ""


def system_hsa_runtime() -> Dict[str, Any]:
    """AMD's system-provided runtime, if its components are installed."""
    for candidate in SYSTEM_HSA_CANDIDATES:
        if os.path.exists(candidate):
            return hsa_flavor(os.path.realpath(candidate))
    return {}


def _torch_facts(python: str) -> Dict[str, Any]:
    script = (
        "import json;"
        "out={'importable':False};"
        "\ntry:\n"
        "    import torch\n"
        "    ok=torch.cuda.is_available()\n"
        "    pci=''\n"
        "    if ok:\n"
        "        try:\n"
        "            pci=getattr(torch.cuda.get_device_properties(0),'pci_bus_id','') or ''\n"
        "        except Exception:\n"
        "            pci=''\n"
        "    out={'importable':True,'torch':torch.__version__,"
        "'hip':getattr(torch.version,'hip',None),'available':ok,"
        "'name':torch.cuda.get_device_name(0) if ok else '','pci':pci}\n"
        "except Exception as e:\n"
        "    out={'importable':False,'error':f'{type(e).__name__}: {e}'}\n"
        "print(json.dumps(out))")
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        return {"importable": False, "error": str(error)}
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"importable": False,
            "error": (out.stderr or "no output").strip()[-200:]}


# ------------------------------------------------------------------ identity

#: The fields that must agree before a stored qualification may be read as
#: evidence about the environment in front of us.
IDENTITY_FIELDS = ("wslDistro", "rocmPython", "stateDir", "torchVersion",
                   "torchLibSha256", "rocmSystemVersion")


def rocm_system_version() -> str:
    for path in ("/opt/rocm/.info/version", "/opt/rocm/.info/version-dev"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            continue
    return ""


def environment_identity(rocm_python: str, lib_dir: str, facts: Dict[str, Any],
                         state_dir: str = "") -> Dict[str, Any]:
    """What makes this ROCm environment *this* one and not another.

    A stored qualification is evidence about the environment it was taken
    from. Without this tuple, a record written on one machine -- or in another
    distribution, or against a different torch -- would be read as proof that
    the environment in front of us used to work, and any failure would be
    reported as a regression. That is a confident, specific, wrong diagnosis,
    which is worse than no diagnosis at all.
    """
    hip_lib = os.path.join(lib_dir, "libamdhip64.so") if lib_dir else ""
    return {
        "wslDistro": os.environ.get("WSL_DISTRO_NAME", ""),
        "rocmPython": rocm_python,
        "stateDir": state_dir,
        "torchVersion": facts.get("torch") or "",
        "torchLibSha256": _sha256(hip_lib) if hip_lib and os.path.exists(hip_lib)
                          else "",
        "rocmSystemVersion": rocm_system_version(),
        "amdPciId": facts.get("pci") or "",
        "deviceName": facts.get("name") or "",
    }


def identity_matches(previous: Dict[str, Any], current: Dict[str, Any]
                     ) -> Tuple[bool, List[str]]:
    """Whether a stored record describes the environment we are looking at.

    Only fields present and non-empty on both sides are compared: the PCI
    address and device name cannot be read when the card is unreachable, which
    is exactly the situation a regression check runs in, so requiring them
    would make the check useless precisely when it is needed.
    """
    old = previous.get("identity") or {}
    new = current.get("identity") or {}
    if not old or not new:
        return False, ["no identity recorded"]
    differences = []
    for field in IDENTITY_FIELDS:
        before, after = old.get(field, ""), new.get(field, "")
        if before and after and before != after:
            differences.append(f"{field}: {before!r} -> {after!r}")
    # The PCI address is compared only when both sides have one.
    for field in ("amdPciId", "deviceName"):
        before, after = old.get(field, ""), new.get(field, "")
        if before and after and before != after:
            differences.append(f"{field}: {before!r} -> {after!r}")
    return (not differences), differences


# ------------------------------------------------------------------ detection

def detect(rocm_python: str, previous: Optional[Dict[str, Any]] = None,
           state_dir: str = "") -> Dict[str, Any]:
    """The three states, plus everything needed to explain the verdict."""
    report: Dict[str, Any] = {
        "rocmPython": rocm_python,
        "dxgPresent": os.path.exists("/dev/dxg"),
        "kfdPresent": os.path.exists("/dev/kfd"),
        "systemRocmPresent": os.path.isdir("/opt/rocm"),
        "packagesInstalled": False,
        "systemRuntimeAvailable": False,
        "gpuQualified": False,
    }
    report["isWsl"] = report["dxgPresent"] and not report["kfdPresent"]

    if not (rocm_python and os.path.isfile(rocm_python)
            and os.access(rocm_python, os.X_OK)):
        report["state"] = "packages-missing"
        report["detail"] = f"no ROCm interpreter at {rocm_python or '(unset)'}"
        return report

    facts = _torch_facts(rocm_python)
    report["torch"] = facts.get("torch")
    report["hip"] = facts.get("hip")
    if not facts.get("importable") or not facts.get("hip"):
        report["state"] = "packages-missing"
        report["detail"] = facts.get("error") or "torch is not a ROCm build"
        return report
    report["packagesInstalled"] = True

    lib_dir = torch_lib_dir(rocm_python)
    report["torchLibDir"] = lib_dir
    report["identity"] = environment_identity(rocm_python, lib_dir, facts,
                                              state_dir)
    active_path = active_hsa_runtime(lib_dir) if lib_dir else ""
    if not active_path:
        report["state"] = "system-runtime-missing"
        report["detail"] = "torch ships no HSA runtime and none was substituted"
        return report

    active = hsa_flavor(active_path)
    report["activeHsaRuntime"] = active
    system = system_hsa_runtime()
    report["systemHsaRuntime"] = system or None

    if system and system.get("sha256") and system["sha256"] == active["sha256"]:
        active["source"] = "system-provided"
    elif active["flavor"] == "dxcore":
        active["source"] = "wsl-capable, origin unrecorded"
    else:
        active["source"] = "pip-shipped"

    # Under WSL a kfd-flavoured runtime cannot work, whatever else is true.
    if report["isWsl"] and active["flavor"] != "dxcore":
        was_qualified = bool((previous or {}).get("gpuQualified"))
        previous_sha = ((previous or {}).get("activeHsaRuntime") or {}).get("sha256")
        same_environment, differences = identity_matches(previous or {}, report)
        if differences:
            report["identityMismatch"] = differences
        if (was_qualified and same_environment
                and previous_sha and previous_sha != active["sha256"]):
            report["state"] = "incompatible-runtime-restored"
            report["previousHsaSha256"] = previous_sha
            report["detail"] = (
                "this environment qualified previously with a different HSA "
                "runtime; the current one talks to /dev/kfd, which is what "
                "reinstalling or upgrading torch puts back")
        else:
            report["state"] = "system-runtime-missing"
            report["detail"] = (
                "the HSA runtime torch will load references /dev/kfd, which "
                "does not exist under WSL")
        return report

    report["systemRuntimeAvailable"] = True
    if facts.get("available"):
        report["gpuQualified"] = True
        report["deviceName"] = facts.get("name")
        report["state"] = "qualified"
    else:
        report["state"] = "no-device"
        report["detail"] = ("the HSA runtime is WSL-capable but no AMD device "
                            "was enumerated; check the Windows AMD driver")
    return report


# --------------------------------------------------------------- explanation

def summary(report: Dict[str, Any]) -> List[str]:
    """Three lines that never collapse into one."""
    def mark(value: bool) -> str:
        return "yes" if value else "no "
    rows = [
        f"    ROCm Python packages installed   {mark(report['packagesInstalled'])}"
        + (f"  (torch {report['torch']})" if report.get("torch") else ""),
        f"    ROCm WSL system runtime          "
        f"{mark(report['systemRuntimeAvailable'])}",
        f"    ROCm GPU operation qualified     {mark(report['gpuQualified'])}"
        + (f"  ({report['deviceName']})" if report.get("deviceName") else ""),
    ]
    active = report.get("activeHsaRuntime") or {}
    if active:
        rows.append(f"      active HSA runtime   {active.get('path')}")
        rows.append(f"        flavour            {active.get('flavor')}"
                    f"  ({active.get('source', 'unknown origin')})")
        rows.append(f"        sha256             {str(active.get('sha256'))[:32]}")
    return rows


def remediation(report: Dict[str, Any]) -> List[str]:
    """What to do, quoted from AMD's documentation. Never run automatically."""
    state = report.get("state")
    if state == "packages-missing":
        return ["    rerun:  openmycelium provision"]
    if state not in ("system-runtime-missing", "incompatible-runtime-restored"):
        return []

    restored = state == "incompatible-runtime-restored"
    lines = [""]
    if restored:
        lines += [
            "    A torch reinstall or upgrade replaced the WSL HSA runtime with",
            "    the /dev/kfd one from the wheel. The system components are still",
            "    installed; only the substitution into torch was undone.",
            "",
        ]
    else:
        lines += [
            "    ROCm Python packages are installed, but the AMD ROCm-for-WSL",
            "    system runtime is missing. OpenMycelium will not add system",
            "    repositories automatically. Install the supported ROCm WSL",
            "    runtime, then rerun `openmycelium provision`.",
            "",
            "    AMD's documented procedure adds an apt repository and installs",
            "    system-wide packages, which needs root and can affect software",
            "    other than OpenMycelium. Read it before running it:",
            f"      {INSTALL_DOCS}",
            f"      {PYTORCH_DOCS}",
            "",
            "    In outline, and subject to those pages:",
            "      1. install AMD's amdgpu-install package for this Ubuntu release",
            "      2. amdgpu-install --usecase=wsl,rocm --no-dkms",
            "",
        ]
    active = (report.get("activeHsaRuntime") or {}).get("path", "")
    lines += [
        "      3. put the WSL runtime where torch will load it, keeping a backup:",
        f"           cp {active} {active}.orig" if active else
        "           back up torch's libhsa-runtime64.so before replacing it",
        "           cp /opt/rocm/lib/libhsa-runtime64.so.1 "
        + (active or "<torch>/lib/libhsa-runtime64.so.1"),
        "",
        "      4. openmycelium provision        (reruns safely; installs nothing",
        "                                        that is already present)",
        "",
        "    Nothing above is executed for you. Step 3 modifies a file inside an",
        "    installed package, so a later `pip install --upgrade torch` will put",
        "    the incompatible runtime back; provision detects that and says so.",
    ]
    return lines
