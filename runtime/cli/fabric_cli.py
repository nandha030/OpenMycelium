"""`openmycelium fabric` -- what accelerators exist, and what to call them."""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "fabric"))

from fabric import GIB, discover  # noqa: E402



def _apply_config(args) -> None:
    """Fill unset paths from the configuration precedence.

    A flag that was not given must not fall back to a constant naming one
    machine's hand-built environment; it falls through to environment, file,
    discovery and finally a documented default.
    """
    import config as _config  # noqa: PLC0415
    resolved = _config.load({
        name: getattr(args, name, None)
        for name in ("cuda_python", "rocm_python", "model_store",
                     "state_dir", "ledger")})
    for name in ("cuda_python", "rocm_python"):
        if not getattr(args, name, None):
            setattr(args, name, getattr(resolved, name))
    args.resolved_config = resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the Mycelium Fabric")
    parser.add_argument("command", nargs="?", default="list",
                        choices=("list", "refresh"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    args = parser.parse_args()
    _apply_config(args)

    report = discover({"nvidia": args.cuda_python, "amd": args.rocm_python},
                      use_cache=args.command != "refresh")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["identitiesUnique"] else 1

    devices = report["devices"]
    print()
    print(f"  Mycelium Fabric -- {len(devices)} accelerator(s)"
          + ("  (cached)" if report.get("fromCache") else ""))
    print()
    if not devices:
        print("  none found")
    for device in devices:
        print(f"  {device['identity']}")
        print(f"    {device['name']:<30} {device['runtime']} "
              f"{device['torch_version']}")
        print(f"    memory      {device['totalGiB']:.2f} GiB total, "
              f"{device['freeGiB']:.2f} GiB free")
        print(f"    identity    from {device['identity_source']} "
              f"({device['identityConfidence']})")
        if device.get("pci_bus"):
            print(f"    pci         {device['pci_bus']}")
        if device.get("arch"):
            print(f"    arch        {device['arch']}")
        if device.get("power_watts") is not None:
            print(f"    power       {device['power_watts']:.1f} W of "
                  f"{device.get('power_limit_watts') or 0:.0f} W "
                  f"({device['power_source']})")
        else:
            print(f"    power       unavailable ({device['power_source']})")
        for note in device.get("notes", []):
            print(f"    note        {note}")
        print()

    for vendor, reason in report.get("unavailable", {}).items():
        print(f"  {vendor}: {reason}")

    print(f"  aggregate   {report['totalBytes'] / GIB:.2f} GiB across "
          f"{', '.join(report['vendors']) or 'nothing'}"
          + ("  (cross-vendor)" if report["crossVendor"] else ""))
    if not report["identitiesUnique"]:
        print(f"  FAULT: identities are not unique: "
              f"{report['duplicateIdentities']}")
        print("         placement decisions about these devices would be "
              "ambiguous")
    print()
    print("  Aggregate model capacity via pipeline parallelism, not a unified")
    print("  address space: each runtime keeps its own memory space.")
    print()
    return 0 if report["identitiesUnique"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
