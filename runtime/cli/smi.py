"""`openmycelium smi` -- every accelerator and what the runtime is doing, at a glance.

Modelled on `nvidia-smi`: one screen, dense, no arguments needed. It differs
from `nvidia-smi` in the places where this system knows something that tool
cannot, and in the places where a number would be a lie:

  * Two vendors in one table. `nvidia-smi` cannot see the AMD card and
    `rocm-smi` cannot see the NVIDIA one, so neither can answer "what does this
    machine have".

  * A reading is never invented. AMD power under WSL has no source at all --
    `rocm-smi` needs the `amdgpu` driver and WSL exposes `/dev/dxg` -- so it
    prints `n/a` with a footnote saying why. Printing `0 W` would be a false
    reading of a real card, which is worse than no reading.

  * Device memory totals are labelled as the whole card. Under WSL that
    includes the Windows host's own usage and cannot be attributed to this
    runtime. What this runtime holds is shown separately, from its own records.

  * Aggregate capacity is stated as separate memories, never as a pool. Adding
    two cards' VRAM produces a number that no single allocation can use.

Read-only. Starts nothing, allocates nothing, and touches no device beyond the
discovery probe the Fabric already caches.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _relative in ("..", "../fabric", "../serving", "../scheduler"):
    _path = os.path.normpath(os.path.join(_HERE, _relative))
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fabric import GIB, discover  # noqa: E402

WIDTH = 78
RULE = "  " + "-" * WIDTH
HEAVY = "  " + "=" * WIDTH


def _version() -> Dict[str, str]:
    versions = {"openmycelium": "unknown", "mccl": "unknown"}
    try:
        from importlib.metadata import distribution  # noqa: PLC0415
        versions["openmycelium"] = distribution("openmycelium").version
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from importlib.metadata import distribution  # noqa: PLC0415
        versions["mccl"] = distribution("openmycelium-mccl").version
    except Exception:                                          # noqa: BLE001
        pass
    return versions


def _running() -> List[Dict[str, Any]]:
    """Runs this machine believes are live, from the control records."""
    try:
        import control  # noqa: PLC0415
        return [r for r in control.list_records() if r.get("alive")]
    except Exception:                                          # noqa: BLE001
        return []


def _qualification() -> List[Dict[str, Any]]:
    try:
        sys.path.insert(0, os.path.join(_HERE, "..", "serving", "adapters"))
        from qualification import load_records  # noqa: PLC0415
        return load_records()
    except Exception:                                          # noqa: BLE001
        return []


def _safety_mode() -> str:
    return (os.environ.get("OM_SAFETY_MODE") or "off").strip() or "off"


def _power(device: Dict[str, Any]) -> tuple:
    """The reading, and a footnote marker when there is none.

    Three-valued, like the rest of this system's telemetry: a value, a platform
    that never had a sensor, or a source that named itself and returned nothing.
    Collapsing the last two into `0` is how a missing reading becomes a false
    one.
    """
    watts = device.get("power_watts")
    source = device.get("power_source") or "unavailable"
    if watts is not None:
        limit = device.get("power_limit_watts")
        # Compact like nvidia-smi's "17W / 180W": the column has to hold a
        # reading and a footnote marker without wrapping the table.
        return (f"{watts:.0f}W/{limit:.0f}W" if limit else f"{watts:.0f}W", "")
    if source in ("", "unavailable"):
        return ("n/a", "*")                      # expected: no source exists
    return ("n/a", "!")                          # unexpected: source failed


def render(report: Dict[str, Any], versions: Dict[str, str],
           runs: List[Dict[str, Any]], records: List[Dict[str, Any]],
           safety: str, now: Optional[float] = None) -> str:
    devices = report.get("devices") or []
    stamp = time.strftime("%a %b %d %H:%M:%S %Y",
                          time.localtime(now or time.time()))
    lines: List[str] = []
    add = lines.append

    add("")
    add(f"  OpenMycelium SMI {versions['openmycelium']:<12} "
        f"MCCL {versions['mccl']:<10} {stamp:>28}")
    add(RULE)
    add(f"  {'GPU':<4}{'Vendor':<8}{'Name':<27}{'Runtime':<9}"
        f"{'Memory (whole card)':<21}{'Power':>9}")
    add(HEAVY)

    footnotes = set()
    if not devices:
        add("  no accelerators discovered")
    for index, device in enumerate(devices):
        used = device.get("total_bytes", 0) - device.get("free_bytes", 0)
        total = device.get("total_bytes", 0)
        memory = (f"{used / GIB:5.2f} / {total / GIB:5.2f} GiB"
                  if total else "unreadable")
        reading, mark = _power(device)
        footnotes.add(mark)
        name = device.get("name") or ""
        if len(name) > 26:
            name = name[:25] + "…"       # elide, never silently clip a word
        add(f"  {index:<4}{device.get('vendor', ''):<8}{name:<27}"
            f"{device.get('runtime', ''):<9}{memory:<21}"
            f"{reading + mark:>9}")
        add(f"      {device.get('identity', '')}")
        add(f"      identity from {device.get('identity_source', '?')}"
            f" ({device.get('identityConfidence', '?')}), "
            f"torch {device.get('torch_version', '?')}")
    add(RULE)

    if "*" in footnotes:
        add("  * no power source on this platform. `rocm-smi` needs the amdgpu")
        add("    driver and WSL exposes /dev/dxg. Reported unavailable, never 0.")
    if "!" in footnotes:
        add("  ! a power source named itself and returned nothing -- a fault,")
        add("    not a platform gap.")

    if devices:
        aggregate = sum(d.get("total_bytes", 0) for d in devices) / GIB
        largest = max(d.get("total_bytes", 0) for d in devices) / GIB
        add("")
        add(f"  Capacity  {aggregate:.2f} GiB across {len(devices)} separate "
            f"memories; largest single {largest:.2f} GiB")
        add("            separate physical memories, NOT pooled VRAM: no single")
        add("            allocation can use the aggregate.")

    add("")
    add("  Runtime")
    add(f"    transport      host-staged (not GPU-direct)")
    add(f"    safety mode    {safety}"
        + ("  (observes, enforces nothing)" if safety == "shadow" else ""))
    add(f"    decoding       deterministic greedy only")

    add("")
    add(f"  Qualification  {len(records)} record(s) on this machine")
    if not records:
        add("    none -- a run will be refused until this situation is qualified")
    shown = records[-4:]
    if len(records) > len(shown):
        add(f"    ... {len(records) - len(shown)} earlier, most recent last")
    for record in shown:
        situation = record.get("situation", record)
        add(f"    {str(record.get('recordId', ''))[:16]:<18}"
            f"{situation.get('adapterId', '?')}@"
            f"{situation.get('adapterVersion', '?'):<6}"
            f"openmycelium {situation.get('openmyceliumVersion', '?')}")

    add("")
    add(f"  Processes      {len(runs)} run(s) live")
    if not runs:
        add("    none. A run occupies both cards; one request is served at a time.")
    for run in runs:
        add(f"    {str(run.get('runId', ''))[:18]:<20}"
            f"{str(run.get('model', ''))[:28]:<30}"
            f"{run.get('state', '?')}")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Every accelerator and what the runtime is doing")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable, same facts")
    parser.add_argument("--refresh", action="store_true",
                        help="re-probe devices instead of using the cache")
    args = parser.parse_args()

    report = discover(use_cache=not args.refresh)
    versions, runs = _version(), _running()
    records, safety = _qualification(), _safety_mode()

    if args.json:
        print(json.dumps({
            "openmyceliumVersion": versions["openmycelium"],
            "mcclVersion": versions["mccl"],
            "devices": report.get("devices") or [],
            "identitiesUnique": report.get("identitiesUnique"),
            "aggregateBytes": sum(d.get("total_bytes", 0)
                                  for d in (report.get("devices") or [])),
            # Named so a consumer cannot read the aggregate as a pool.
            "aggregateIsPooled": False,
            "largestSingleBytes": max(
                (d.get("total_bytes", 0)
                 for d in (report.get("devices") or [])), default=0),
            "safetyMode": safety,
            "safetyEnforced": False,
            "qualificationRecords": len(records),
            "runs": runs,
        }, indent=2))
        return 0

    print(render(report, versions, runs, records, safety))
    return 0 if report.get("identitiesUnique", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
