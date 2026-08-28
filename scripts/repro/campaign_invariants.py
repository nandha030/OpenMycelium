"""What must not change across a throughput campaign.

The campaign is one claim assembled from seven separate executions: a byte-exact
qualification, five performance sessions, and a second qualification. Those add
up to a single result only if they all ran against the same system. If the VM
rebooted, a package was reinstalled, or the model changed underneath, the parts
describe different machines and the campaign has to be split or rerun.

Emits one JSON object. Run it before and after the campaign and compare.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

OM = "/opt/om/venv/bin/openmycelium"
CUDA_ENV = "/var/lib/openmycelium/state/env/cuda"
ROCM_ENV = "/var/lib/openmycelium/state/env/rocm"


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _env_fingerprint(env: str) -> dict:
    """Package set and torch identity for one GPU environment."""
    try:
        freeze = subprocess.run([f"{env}/bin/pip", "freeze", "--all"],
                                capture_output=True, text=True, timeout=300).stdout
    except Exception:                                         # noqa: BLE001
        return {}
    packages = sorted(line.strip() for line in freeze.splitlines() if line.strip())
    torch_version = next((p.split("==")[1] for p in packages
                          if p.startswith("torch==")), "")
    return {
        "packageCount": len(packages),
        "packageListSha256": hashlib.sha256("\n".join(packages).encode()).hexdigest(),
        "torch": torch_version,
    }


def _placement_digest() -> dict:
    out = subprocess.run([OM, "plan", "--model", "Mistral-Nemo-Instruct-2407",
                          "--output", "/tmp/inv-plan.json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    try:
        manifest = json.load(open("/tmp/inv-plan.json"))
    except Exception:                                         # noqa: BLE001
        return {}
    stages = manifest["stages"]
    # Digest the ownership itself, not the manifest file: the file carries a
    # generated placement id that changes every run, which would make an
    # unchanged placement look different on every comparison.
    canonical = json.dumps(
        {"boundaryAfterLayer": manifest["pipeline"]["boundaryAfterLayer"],
         "stages": [sorted(stage["tensors"]) for stage in stages]},
        sort_keys=True)
    return {
        "boundaryAfterLayer": manifest["pipeline"]["boundaryAfterLayer"],
        "tensorCounts": sorted(len(stage["tensors"]) for stage in stages),
        "placementDigest": hashlib.sha256(canonical.encode()).hexdigest(),
    }


def main() -> int:
    provenance = {}
    try:
        provenance = json.loads(subprocess.run(
            [OM, "version", "--json"], capture_output=True, text=True).stdout)
    except Exception:                                         # noqa: BLE001
        pass

    hsa = provenance.get("hsaRuntime") or {}
    record = {
        # Same boot: CLOCK_MONOTONIC and every in-memory driver state reset
        # when the WSL VM restarts, so a campaign spanning a reboot is two
        # campaigns.
        "bootId": _read("/proc/sys/kernel/random/boot_id"),
        "uptimeSeconds": int(float((_read("/proc/uptime") or "0 0").split()[0])),
        "wslDistro": os.environ.get("WSL_DISTRO_NAME", ""),

        "openmycelium": provenance.get("openmycelium"),
        "installedContentSha256": provenance.get("installedContentSha256"),
        "mcclVersion": provenance.get("mcclVersion"),
        "mcclActivationProtocol": provenance.get("mcclActivationProtocol"),
        "mcclCollectiveProtocol": provenance.get("mcclCollectiveProtocol"),
        "transport": provenance.get("transport"),

        "cudaEnvironment": _env_fingerprint(CUDA_ENV),
        "rocmEnvironment": _env_fingerprint(ROCM_ENV),
        "hsaRuntimeSha256": hsa.get("sha256"),
        "hsaRuntimeFlavor": hsa.get("flavor"),
        "hsaRuntimeSource": hsa.get("source"),
        "rocmSystemVersion": _read("/opt/rocm/.info/version"),
    }
    record.update(_placement_digest())
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
