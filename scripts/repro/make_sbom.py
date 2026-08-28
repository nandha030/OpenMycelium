"""Generate a CycloneDX SBOM for an OpenMycelium release.

Everything listed here is something that was actually installed and measured on
the validated machine, with the SHA-256 recorded at the time it was fetched:

  - the two OpenMycelium wheels, hashed from the frozen release directory
  - every Python wheel in both GPU environments, from the verified wheelhouse
    lock, which carries the exact bytes each install consumed
  - the AMD system prerequisite, which is deliberately NOT in the wheelhouse
    because it is an operating-system package installed by hand

A dependency the project cannot vouch for is worse than one it omits, so the
system prerequisite is listed with a note saying it is manually installed and
not cached.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys

REPO = "/mnt/c/Users/User/Documents/Open_Mycelium"
VERSION = sys.argv[1] if len(sys.argv) > 1 else "0.1.0rc1"
RELEASE = f"{REPO}/release/{VERSION}"
LOCK = f"{REPO}/release/wheelhouse/LOCK.json"


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def component(name, version, kind, purl, sha, extra=None):
    entry = {
        "type": kind,
        "name": name,
        "version": version,
        "purl": purl,
    }
    if sha:
        entry["hashes"] = [{"alg": "SHA-256", "content": sha}]
    if extra:
        entry.update(extra)
    return entry


components = []

# ------------------------------------------------- the project's own wheels
for filename in sorted(os.listdir(RELEASE)):
    if not filename.endswith((".whl", ".tar.gz")):
        continue
    path = os.path.join(RELEASE, filename)
    stem = filename.split("-")
    components.append(component(
        stem[0], stem[1] if len(stem) > 1 else VERSION, "library",
        f"pkg:pypi/{stem[0].replace('_', '-')}@{stem[1] if len(stem) > 1 else VERSION}",
        sha256(path),
        {"description": "OpenMycelium first-party artifact",
         "properties": [{"name": "file", "value": filename}]}))

# ------------------------------------------- the two GPU Python environments
lock = json.load(open(LOCK))
for vendor, data in sorted(lock["environments"].items()):
    for entry in data["files"]:
        name = entry["name"].replace("_", "-")
        components.append(component(
            name, entry["version"], "library",
            f"pkg:pypi/{name}@{entry['version']}", entry["sha256"],
            {"scope": "required",
             "properties": [
                 {"name": "environment", "value": vendor},
                 {"name": "sourceIndex", "value": data["index"]},
                 {"name": "bytes", "value": str(entry["bytes"])},
             ]}))

# ------------------------------------------------- the system prerequisite
for name, version, note in (
    ("rocm-core", "7.2.0.70200-43~24.04",
     "AMD ROCm system component. Installed manually from repo.radeon.com; "
     "NOT part of the Python wheelhouse and NOT cached by it."),
    ("hsa-runtime-rocr4wsl-amdgpu", "25.30.13-2281980.24.04",
     "AMD ROCm-for-WSL HSA runtime. Required because the HSA runtime shipped "
     "inside the PyTorch ROCm wheel targets /dev/kfd, which does not exist "
     "under WSL. Installed manually; NOT part of the Python wheelhouse."),
):
    components.append(component(
        name, version, "operating-system-package",
        f"pkg:deb/ubuntu/{name}@{version}?arch=amd64", "",
        {"description": note,
         "properties": [
             {"name": "repository",
              "value": "https://repo.radeon.com/rocm/apt/7.2 noble main"},
             {"name": "signingKeySha256",
              "value": "3e660bdc559b34781b5498e79b9c117394b46ce55592812db0b84d65986b4df2"},
             {"name": "installedBy", "value": "manual, documented prerequisite"},
         ]}))

bom = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "metadata": {
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
                     .isoformat(timespec="seconds"),
        "component": {
            "type": "application",
            "name": "openmycelium",
            "version": VERSION,
            "description": ("Cross-vendor inference runtime. Aggregates model "
                            "capacity across one NVIDIA CUDA GPU and one AMD "
                            "ROCm GPU. Does not create unified VRAM."),
        },
        "properties": [
            {"name": "validatedOn",
             "value": "Windows 11 + WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB"},
            {"name": "validatedModel", "value": "Mistral-Nemo-Instruct-2407"},
            {"name": "modelSha256",
             "value": "f3298db2f04eb440bc235a9709fb120e38a72758ae5e2b14bbefcad552ada966"},
            {"name": "modelFingerprintScheme", "value": "om-model-fingerprint-1"},
        ],
    },
    "components": components,
}

out = f"{RELEASE}/SBOM.cyclonedx.json"
with open(out, "w", encoding="utf-8") as handle:
    json.dump(bom, handle, indent=2, sort_keys=True)

first = sum(1 for c in components if "OpenMycelium first-party" in
            str(c.get("description", "")))
system = sum(1 for c in components if c["type"] == "operating-system-package")
print(f"  wrote {out}")
print(f"    {len(components)} components")
print(f"      {first} first-party artifacts")
print(f"      {len(components) - first - system} Python wheels, hashed from the lock")
print(f"      {system} system packages, manually installed and not cached")
