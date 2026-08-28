"""`openmycelium provision` -- build the two GPU environments this needs.

Until now those environments were built by hand and nothing recreated them, so
the wheel installed anywhere and ran only on the machine where they happened to
exist. This is the missing piece: it detects the host, creates a CUDA and a
ROCm Python environment, installs pinned wheels into each, proves each one can
actually execute on its GPU, and writes the qualification ledger.

Idempotent by construction. Every step checks whether it is already satisfied
before doing anything, so a rerun after a partial failure resumes rather than
destroying environments that already work. Nothing is deleted unless `--clean`
is given explicitly.

The ROCm-on-WSL detail is the one that is easy to get wrong: WSL exposes
`/dev/dxg` rather than `/dev/kfd`, so `rocm-smi` and anything else expecting the
`amdgpu` kernel module will fail even though PyTorch works. That is verified
here rather than assumed, and the check is for a working GPU operation, not for
the presence of a device node.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from config import Config, SETTINGS, add_arguments, from_args  # noqa: E402
import rocm_prereq                                             # noqa: E402


def previous_rocm_record(config: Config) -> Dict[str, Any]:
    """What the ledger last recorded about ROCm, for reinstall detection.

    A torch upgrade puts the /dev/kfd runtime back. Knowing that this
    environment qualified before, with a different runtime, is what separates
    "never set up" from "was working until something replaced a file".
    """
    try:
        with open(config.ledger, "r", encoding="utf-8") as handle:
            ledger = json.load(handle)
    except (OSError, ValueError):
        return {}
    provisioned = (ledger.get("provision") or {}).get("environments") or {}
    # The last state in which this environment actually drove the card, not
    # merely the last state observed. A failed run must not overwrite the
    # evidence that it once worked -- that evidence is the whole basis for
    # saying "this regressed" instead of "this was never set up".
    return (provisioned.get("rocmLastQualified")
            or provisioned.get("rocmPrerequisite") or {})

#: Pinned so two machines get the same runtime. Index URLs are vendor-specific:
#: the CUDA and ROCm builds of torch have the same version numbers and entirely
#: different binaries.
#:
#: `torch` comes from the vendor index and everything else from PyPI, and they
#: are installed by two separate pip invocations. `--index-url` replaces PyPI
#: rather than adding to it, so a single invocation cannot find transformers at
#: all; `--extra-index-url` would find it, but would also let either index
#: answer for every name, which is how dependency confusion happens. Keeping the
#: vendor index scoped to the one package that must come from it avoids both.
PINS = {
    "cuda": {
        "index": "https://download.pytorch.org/whl/cu128",
        "torch": ["torch==2.11.0"],
        "packages": ["transformers==5.15.1", "tokenizers==0.22.2",
                     "safetensors", "numpy"],
    },
    "rocm": {
        "index": "https://download.pytorch.org/whl/rocm7.0",
        "torch": ["torch==2.10.0"],
        "packages": ["transformers==5.15.1", "tokenizers==0.22.2",
                     "safetensors", "numpy"],
    },
}

GPU_PROBE = (
    "import json,torch;"
    "ok=torch.cuda.is_available();"
    "a=torch.randn(512,512,device='cuda',dtype=torch.bfloat16) if ok else None;"
    "fine=bool((a@a).isfinite().all().item()) if ok else False;"
    "print(json.dumps({'available':ok,'matmulFinite':fine,"
    "'name':torch.cuda.get_device_name(0) if ok else '',"
    "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda',"
    "'torch':torch.__version__}))")


def _run(command: List[str], timeout: float = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout)


def _say(message: str) -> None:
    print(message, flush=True)


# ------------------------------------------------------------------ detection

def detect_host() -> Dict[str, Any]:
    """What this machine is, and whether it can host both runtimes."""
    facts: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "isWsl": False,
        "distro": "",
        "dxg": os.path.exists("/dev/dxg"),
        "kfd": os.path.exists("/dev/kfd"),
    }
    try:
        with open("/proc/version", "r", encoding="utf-8") as handle:
            facts["isWsl"] = "microsoft" in handle.read().lower()
    except OSError:
        pass
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    facts["distro"] = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    facts["nvidiaSmi"] = bool(shutil.which("nvidia-smi"))
    if facts["nvidiaSmi"]:
        out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                   timeout=120)
        facts["nvidiaGpus"] = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    else:
        facts["nvidiaGpus"] = []
    return facts


def report_host(facts: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    _say(f"    platform       {facts['platform']}")
    _say(f"    distribution   {facts['distro'] or 'unknown'}")
    _say(f"    WSL            {facts['isWsl']}")
    _say(f"    /dev/dxg       {facts['dxg']}"
         + ("   (WSL GPU paravirtualisation)" if facts["dxg"] else ""))
    _say(f"    /dev/kfd       {facts['kfd']}"
         + ("" if facts["kfd"] else "   (absent under WSL; expected)"))
    _say(f"    nvidia-smi     {facts['nvidiaSmi']}  {facts['nvidiaGpus']}")

    if sys.version_info < (3, 10):
        problems.append(f"Python {facts['python']} is too old; 3.10+ is required")
    if not (facts["dxg"] or facts["kfd"]):
        problems.append(
            "no GPU device node found: neither /dev/dxg (WSL) nor /dev/kfd "
            "(native ROCm) exists, so no GPU is reachable from this container "
            "or VM")
    if not facts["nvidiaGpus"]:
        problems.append(
            "nvidia-smi reports no GPU. Install the Windows NVIDIA driver "
            "(WSL uses the Windows driver, not a Linux one)")
    return problems


# --------------------------------------------------------------- environments

def env_path(config: Config, vendor: str) -> str:
    configured = getattr(config, f"{vendor}_python", "")
    if configured:
        # .../bin/python -> ...
        return os.path.dirname(os.path.dirname(configured))
    base = os.path.join(config.state_dir, "env")
    return os.path.join(base, vendor)


def probe_environment(python: str, expect: str) -> Tuple[bool, Dict[str, Any]]:
    """Run a real GPU operation, not merely an import.

    `torch.cuda.is_available()` can be true while the first matmul fails, so
    the check multiplies a matrix and requires the result to be finite.
    """
    if not (python and os.path.isfile(python)):
        return False, {"detail": "interpreter missing"}
    out = _run([python, "-c", GPU_PROBE], timeout=600)
    lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
    if not lines:
        return False, {"detail": (out.stderr or "no output").strip()[-300:]}
    try:
        info = json.loads(lines[-1])
    except ValueError:
        return False, {"detail": "unreadable probe output"}
    ok = bool(info.get("available") and info.get("matmulFinite")
              and info.get("runtime") == expect)
    return ok, info


def create_environment(vendor: str, target: str, dry_run: bool) -> Tuple[bool, str]:
    python = os.path.join(target, "bin", "python")
    if os.path.isfile(python):
        return True, "already present"
    if dry_run:
        return True, f"would create {target}"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    made = _run([sys.executable, "-m", "venv", target], timeout=900)
    if made.returncode != 0:
        return False, (made.stderr or made.stdout)[-300:]
    return True, "created"


def install_packages(vendor: str, python: str, dry_run: bool) -> Tuple[bool, str]:
    pins = PINS[vendor]
    if dry_run:
        return True, (f"would install {pins['torch']} from {pins['index']}, "
                      f"then {pins['packages']} from PyPI")
    upgrade = _run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"],
                   timeout=900)
    if upgrade.returncode != 0:
        return False, (upgrade.stderr or upgrade.stdout)[-300:]

    # torch first, from the vendor index only. This is the long one: several
    # gigabytes, and the ROCm wheel carries its whole runtime.
    torch_install = _run([python, "-m", "pip", "install", "-q",
                          "--index-url", pins["index"], *pins["torch"]],
                         timeout=7200)
    if torch_install.returncode != 0:
        return False, ("torch: "
                       + (torch_install.stderr or torch_install.stdout)[-500:])

    # everything else from PyPI, with the vendor index out of the picture so it
    # cannot answer for a name it has no business answering for
    rest = _run([python, "-m", "pip", "install", "-q", *pins["packages"]],
                timeout=3600)
    if rest.returncode != 0:
        return False, "packages: " + (rest.stderr or rest.stdout)[-500:]
    return True, "installed"


def write_ledger(config: Config, results: Dict[str, Any]) -> str:
    path = config.ledger
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError):
            existing = {}
    # Merged, not replaced: a ledger may already hold qualification records
    # from transport testing that provisioning knows nothing about.
    existing.setdefault("provision", {})
    # Carry the last qualified ROCm record forward. The block below replaces
    # "provision" wholesale, so without this a failed run erases the evidence
    # that the environment ever worked -- and with it the ability to tell a
    # regression from a machine that was never set up.
    prior = (existing.get("provision") or {}).get("environments") or {}
    if prior.get("rocmLastQualified") and "rocmLastQualified" not in results:
        results["rocmLastQualified"] = prior["rocmLastQualified"]

    existing["provision"] = {
        "qualifiedAt": time.time(),
        "environments": results,
        "host": detect_host(),
    }
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, sort_keys=True)
    os.replace(temporary, path)
    return path


# ---------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision the CUDA and ROCm environments")
    add_arguments(parser)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen, change nothing")
    parser.add_argument("--only", choices=("cuda", "rocm"), default=None)
    parser.add_argument("--clean", action="store_true",
                        help="remove and rebuild the environments (destructive)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    config = from_args(args)

    print()
    print("  == host ==")
    facts = detect_host()
    problems = report_host(facts)
    if problems:
        print()
        for item in problems:
            print(f"    BLOCKED: {item}")
        print()
        return 2

    print()
    print("  == configuration ==")
    for line in config.describe():
        print(line)

    vendors = [args.only] if args.only else ["cuda", "rocm"]
    results: Dict[str, Any] = {}
    failures: List[str] = []

    for vendor in vendors:
        print()
        print(f"  == {vendor} environment ==")
        target = env_path(config, vendor)
        python = os.path.join(target, "bin", "python")
        print(f"    location       {target}")

        if args.clean and not args.dry_run and os.path.isdir(target):
            print("    removing (--clean)")
            shutil.rmtree(target, ignore_errors=True)

        # Already working? Then nothing is touched. This is what makes a rerun
        # safe after a partial failure elsewhere.
        ok, info = probe_environment(python, vendor)
        if ok:
            print(f"    already usable {info.get('name')} "
                  f"(torch {info.get('torch')})")
            results[vendor] = {"python": python, "status": "already-usable",
                               **info}
            # Record which HSA runtime this working environment loads, even
            # though nothing needed doing. Without it the ledger has no
            # qualified runtime to compare against, and a later torch upgrade
            # that swaps the WSL runtime for the /dev/kfd one reads as a
            # machine that was never set up rather than one that regressed.
            if vendor == "rocm":
                current = rocm_prereq.detect(python, previous_rocm_record(config),
                                            config.state_dir)
                results["rocmPrerequisite"] = current
                if current.get("gpuQualified"):
                    results["rocmLastQualified"] = current
            continue

        made, detail = create_environment(vendor, target, args.dry_run)
        print(f"    environment    {detail}")
        if not made:
            failures.append(f"{vendor}: {detail}")
            results[vendor] = {"status": "failed", "detail": detail}
            continue

        print(f"    installing     {PINS[vendor]['torch'][0]} from "
              f"{PINS[vendor]['index']}, then "
              f"{len(PINS[vendor]['packages'])} packages from PyPI "
              f"(this takes a while)")
        put, detail = install_packages(vendor, python, args.dry_run)
        print(f"    packages       {detail}")
        if not put:
            failures.append(f"{vendor}: {detail}")
            results[vendor] = {"status": "failed", "detail": detail}
            continue
        if args.dry_run:
            results[vendor] = {"status": "dry-run"}
            continue

        # The ROCm Python packages installing successfully says nothing about
        # whether an AMD device can be reached: pip's wheel carries the
        # /dev/kfd runtime, which is inert under WSL. Establish that before
        # attempting the GPU operation, so the failure names its own cause
        # rather than reporting an unexplained absence of any device.
        if vendor == "rocm":
            prereq = rocm_prereq.detect(python, previous_rocm_record(config),
                                        config.state_dir)
            results["rocmPrerequisite"] = prereq
            if prereq.get("gpuQualified"):
                results["rocmLastQualified"] = prereq
            for line in rocm_prereq.summary(prereq):
                print(line)
            if prereq["state"] in ("system-runtime-missing",
                                   "incompatible-runtime-restored"):
                for line in rocm_prereq.remediation(prereq):
                    print(line)
                results[vendor] = {"python": python,
                                   "status": prereq["state"],
                                   "detail": prereq.get("detail", "")}
                failures.append(f"rocm: {prereq['state']}")
                continue

        # A real GPU operation, because an import proves nothing.
        ok, info = probe_environment(python, vendor)
        print(f"    GPU operation  "
              + (f"{info.get('name')} matmul finite" if ok
                 else f"FAILED: {info.get('detail', info)}"))
        results[vendor] = {"python": python,
                           "status": "qualified" if ok else "failed", **info}
        if not ok:
            failures.append(f"{vendor}: GPU operation failed")

    print()
    if failures and not args.dry_run:
        print("  == not provisioned ==")
        for item in failures:
            print(f"    {item}")
        print()
        print("    rerun after fixing the cause; existing working environments")
        print("    are never removed unless --clean is given.")
        print()
        return 1

    if not args.dry_run:
        path = write_ledger(config, results)
        print(f"  qualification ledger written to {path}")
        print()
        print("  add these to your configuration so they are found next time:")
        for vendor in vendors:
            entry = results.get(vendor, {})
            if entry.get("python"):
                print(f"    {vendor}_python = \"{entry['python']}\"")
        print(f"    (in {config.config_file or os.path.expanduser('~/.config/openmycelium/config.toml')})")
    if args.json:
        print(json.dumps({"host": facts, "environments": results}, indent=2))
    print()
    print("  next:  openmycelium doctor")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
