"""Compare a post-refactor capture against the pre-refactor baseline.

The refactor's claim is that it changes what the runtime *refuses*, not what it
computes. So the comparison is split in two, and both halves are asserted:

  Must be identical  -- the generated token ids, the decoded text, the boundary
                        byte digest, the model fingerprint, the split, and the
                        exclusive ownership counts. If any of these moved, the
                        refactor changed the arithmetic and is not a refactor.

  Must have changed  -- the manifest schema version and the manifest digest.
                        New manifests pin adapter identity, and those fields are
                        covered by the digest, so an unchanged digest would mean
                        the pinning did not actually reach the manifest.

Timings are reported, never asserted: a single run is not a measurement, and
this project does not quote p95 from a handful of observations.

    compare_refactor.py /var/log/om-baseline /var/log/om-refactor
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, Optional

IDENTICAL = (
    ("run-summary.json", "generatedTokens", "generated token ids"),
    ("run-summary.json", "text", "decoded text"),
    ("placement.json", "model.fingerprint", "model fingerprint"),
    ("placement.json", "pipeline.boundaryAfterLayer", "boundary layer"),
    ("placement.json", "pipeline.transport", "transport"),
    ("placement.json", "pipeline.activationBytesPerToken", "activation bytes/token"),
    ("placement.json", "model.tensorCount", "tensor count"),
    ("placement.json", "model.weightBytes", "weight bytes"),
)

CHANGED = (
    ("placement.json", "schemaVersion", "manifest schema version"),
    ("placement.json", "manifestDigest", "manifest digest"),
)

ADDED = ("adapterId", "adapterVersion", "adapterConfigDigest")


def load(directory: str, name: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(directory, name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def dig(document: Optional[Dict[str, Any]], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def boundary_digest(directory: str) -> Optional[str]:
    """The received digest from boundary_exact_clean.sh's own output."""
    path = os.path.join(directory, "boundary.txt")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    found = re.findall(r"sha256 received ([0-9a-f]{32,64})", text)
    return found[-1] if found else None


def ownership(document: Optional[Dict[str, Any]]) -> Any:
    stages = (document or {}).get("stages") or []
    if len(stages) != 2:
        return None
    first, second = (set(stage.get("tensors") or []) for stage in stages)
    return {"counts": sorted([len(first), len(second)]),
            "overlap": len(first & second)}


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "/var/log/om-baseline"
    after = sys.argv[2] if len(sys.argv) > 2 else "/var/log/om-refactor"
    failures = 0
    cache: Dict[str, Any] = {}

    def document(directory: str, name: str):
        key = f"{directory}:{name}"
        if key not in cache:
            cache[key] = load(directory, name)
        return cache[key]

    print(f"  baseline  {base}")
    print(f"  refactor  {after}")

    print("\n  == must be identical ==")
    for name, path, label in IDENTICAL:
        left = dig(document(base, name), path)
        right = dig(document(after, name), path)
        if left is None and right is None:
            print(f"    MISSING  {label}: absent from both captures")
            failures += 1
            continue
        if left == right:
            shown = str(left)
            print(f"    ok       {label}: {shown[:88]}")
        else:
            print(f"    CHANGED  {label}")
            print(f"               baseline {str(left)[:120]}")
            print(f"               refactor {str(right)[:120]}")
            failures += 1

    left, right = boundary_digest(base), boundary_digest(after)
    if left and right and left == right:
        print(f"    ok       boundary byte digest: {left}")
    else:
        print(f"    CHANGED  boundary byte digest: {left} -> {right}")
        failures += 1

    left, right = ownership(document(base, "placement.json")), \
        ownership(document(after, "placement.json"))
    if left and right and left == right and right["overlap"] == 0:
        print(f"    ok       exclusive ownership: {right['counts']}, overlap 0")
    else:
        print(f"    CHANGED  ownership: {left} -> {right}")
        failures += 1

    print("\n  == must have changed ==")
    for name, path, label in CHANGED:
        left = dig(document(base, name), path)
        right = dig(document(after, name), path)
        if left != right:
            print(f"    ok       {label}: {str(left)[:40]} -> {str(right)[:40]}")
        else:
            print(f"    UNCHANGED {label}: {left}")
            print("               adapter pinning did not reach the manifest")
            failures += 1

    manifest = document(after, "placement.json") or {}
    for field in ADDED:
        value = manifest.get(field)
        if value:
            print(f"    ok       {field}: {value}")
        else:
            print(f"    MISSING  {field} is absent from the new manifest")
            failures += 1

    print("\n  == reported, not asserted (one run is not a measurement) ==")
    for name, path, label in (("run-summary.json", "ttftMs", "TTFT ms"),
                              ("run-summary.json", "decodeTokensPerSecond",
                               "decode tok/s")):
        print(f"    {label:<14} baseline {dig(document(base, name), path)}"
              f"   refactor {dig(document(after, name), path)}")

    print()
    if failures:
        print(f"  == comparison FAILED: {failures} difference(s) ==")
    else:
        print("  == comparison PASSED: computation identical, pinning present ==")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
