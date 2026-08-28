"""Decide whether a campaign's parts describe one unchanged system.

Takes the before and after invariant snapshots. Any difference in the fields
below invalidates the campaign as a single result: it must be split or rerun.
"""

from __future__ import annotations

import json
import sys

MUST_MATCH = (
    ("bootId", "boot ID"),
    ("wslDistro", "WSL distribution"),
    ("openmycelium", "package version"),
    ("installedContentSha256", "package content hash"),
    ("mcclVersion", "MCCL version"),
    ("mcclActivationProtocol", "MCCL activation protocol"),
    ("mcclCollectiveProtocol", "MCCL collective protocol"),
    ("transport", "transport configuration"),
    ("hsaRuntimeSha256", "HSA runtime"),
    ("hsaRuntimeFlavor", "HSA runtime flavour"),
    ("rocmSystemVersion", "ROCm system version"),
    ("placementDigest", "placement digest"),
    ("boundaryAfterLayer", "pipeline boundary"),
)

NESTED = (("cudaEnvironment", "packageListSha256", "CUDA package set"),
          ("cudaEnvironment", "torch", "CUDA torch version"),
          ("rocmEnvironment", "packageListSha256", "ROCm package set"),
          ("rocmEnvironment", "torch", "ROCm torch version"))


def main(before_path: str, after_path: str) -> int:
    before = json.load(open(before_path))
    after = json.load(open(after_path))
    problems = []

    for key, label in MUST_MATCH:
        if before.get(key) != after.get(key):
            problems.append(f"{label}: {before.get(key)!r} -> {after.get(key)!r}")
    for outer, inner, label in NESTED:
        b = (before.get(outer) or {}).get(inner)
        a = (after.get(outer) or {}).get(inner)
        if b != a:
            problems.append(f"{label}: {b!r} -> {a!r}")

    print(f"    boot ID            {before.get('bootId')}")
    print(f"    uptime             {before.get('uptimeSeconds')} s -> "
          f"{after.get('uptimeSeconds')} s")
    print(f"    package content    {str(before.get('installedContentSha256'))[:32]}")
    print(f"    placement digest   {str(before.get('placementDigest'))[:32]}"
          f"  boundary {before.get('boundaryAfterLayer')} "
          f"{before.get('tensorCounts')}")
    print(f"    CUDA torch         {(before.get('cudaEnvironment') or {}).get('torch')}")
    print(f"    ROCm torch         {(before.get('rocmEnvironment') or {}).get('torch')}")
    print(f"    HSA runtime        {before.get('hsaRuntimeFlavor')} "
          f"{str(before.get('hsaRuntimeSha256'))[:32]} "
          f"({before.get('hsaRuntimeSource')})")
    print(f"    MCCL               {before.get('mcclVersion')} "
          f"activation v{before.get('mcclActivationProtocol')} "
          f"{before.get('transport')}")
    print()
    if problems:
        for item in problems:
            print(f"    CHANGED: {item}")
        print("    the campaign spans more than one system state; "
              "split it or rerun it")
        return 1
    print("    every invariant held across the campaign")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
