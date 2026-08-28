"""`openmycelium pool inspect` -- what the two cards can actually hold together.

The honest framing matters here. This is not a unified 32 GiB address space:
CUDA and ROCm keep separate memory spaces and neither card can read the other's.
What the pool provides is aggregate *model capacity* through pipeline
parallelism -- a model whose weights fit in neither card can be resident across
both, with one activation crossing the boundary per forward.

Usable capacity is below the nameplate sum because each card also needs
attention workspace, its half of the KV cache, and runtime allocations.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))
sys.path.insert(0, os.path.join(_HERE, "..", "fabric"))
sys.path.insert(0, os.path.join(_HERE, "..", "scheduler"))

GIB = 1 << 30


def probe(python: str, label: str) -> Dict[str, Any]:
    """Ask one vendor runtime what it sees, in its own environment."""
    script = (
        "import json,torch;"
        "ok=torch.cuda.is_available();"
        "free,total=(torch.cuda.mem_get_info(0) if ok else (0,0));"
        "print(json.dumps({'available':ok,"
        "'name':torch.cuda.get_device_name(0) if ok else '',"
        "'freeBytes':free,'totalBytes':total,"
        "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda',"
        "'version':torch.__version__}))")
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=180)
        line = [l for l in out.stdout.splitlines() if l.startswith("{")]
        if not line:
            return {"label": label, "available": False,
                    "detail": (out.stderr or "no output").strip()[-200:]}
        record = json.loads(line[-1])
        record["label"] = label
        return record
    except Exception as error:                                # noqa: BLE001
        return {"label": label, "available": False, "detail": str(error)[:200]}



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
    parser = argparse.ArgumentParser(description="Inspect the mixed-vendor pool")
    parser.add_argument("command", nargs="?", default="inspect",
                        choices=("inspect",))
    parser.add_argument("--model", default="")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _apply_config(args)

    from fabric import discover  # noqa: PLC0415
    fabric_report = discover(
        {"nvidia": args.cuda_python, "amd": args.rocm_python}, use_cache=True)
    devices = []
    for device in fabric_report["devices"]:
        devices.append({
            "label": device["runtime"], "available": True,
            "name": device["name"], "freeBytes": device["free_bytes"],
            "totalBytes": device["total_bytes"],
            "runtime": device["runtime"], "version": device["torch_version"],
            "identity": device["identity"],
        })
    for vendor, detail in fabric_report.get("unavailable", {}).items():
        devices.append({"label": "cuda" if vendor == "nvidia" else "rocm",
                        "available": False, "detail": detail})
    total = sum(d.get("totalBytes", 0) for d in devices)
    free = sum(d.get("freeBytes", 0) for d in devices)

    plan: Optional[Dict[str, Any]] = None
    if args.model:
        from model_inspect import inspect_model  # noqa: PLC0415
        from placement import create_placement  # noqa: PLC0415
        try:
            model = inspect_model(args.model)
            cuda = next(d for d in fabric_report["devices"]
                        if d["runtime"] == "cuda")
            rocm = next(d for d in fabric_report["devices"]
                        if d["runtime"] == "rocm")
            cuda_budget = int(cuda["free_bytes"] * 0.85)
            rocm_budget = int(rocm["free_bytes"] * 0.85)
            placement = create_placement(
                args.model, fabric_report, cuda_budget, rocm_budget,
                args.context_length)
            pipeline = placement["pipeline"]
            stages = {stage["role"]: stage for stage in placement["stages"]}
            first, second = stages["cuda"], stages["rocm"]
            plan = {
                "placementId": placement["placementId"],
                "manifestDigest": placement["manifestDigest"],
                "weightsGiB": round(model.total_bytes / GIB, 3),
                "boundaryAfterLayer": pipeline["boundaryAfterLayer"],
                "cudaLayers": [int(first["layers"][0]), int(first["layers"][-1])],
                "rocmLayers": [int(second["layers"][0]), int(second["layers"][-1])],
                "cudaIdentity": first["deviceIdentity"],
                "rocmIdentity": second["deviceIdentity"],
                "kvBytesPerToken": model.kv_bytes_per_token(),
                "contextLength": args.context_length,
            }
        except Exception as error:                            # noqa: BLE001
            plan = {"error": str(error)}

    if args.json:
        print(json.dumps({"devices": devices, "plan": plan}, indent=2))
        return 0

    print("Logical pool: mixed-gpu-0")
    for device in devices:
        if device.get("available"):
            print(f"  {device['label']:<5} {device['name']:<28} "
                  f"{device['totalBytes'] / GIB:6.2f} GiB total, "
                  f"{device['freeBytes'] / GIB:6.2f} GiB free   "
                  f"(torch {device['version']})")
            print(f"        identity {device['identity']}")
        else:
            print(f"  {device['label']:<5} unavailable: "
                  f"{device.get('detail', 'unknown')}")
    print(f"  aggregate     {total / GIB:.2f} GiB nameplate, "
          f"{free / GIB:.2f} GiB free")
    print()
    print("  Aggregate model capacity via pipeline parallelism, not a unified")
    print("  address space: CUDA and ROCm keep separate memory spaces and one")
    print("  activation crosses the boundary per forward.")
    if plan and "error" not in plan:
        kv = plan["kvBytesPerToken"] * plan["contextLength"]
        print()
        print(f"  placement        {plan['placementId']}  "
              f"{plan['manifestDigest'][:12]}")
        print(f"  model            {os.path.basename(args.model)}")
        print(f"  weights          {plan['weightsGiB']:.3f} GiB")
        print(f"  boundary         after layer {plan['boundaryAfterLayer']}")
        print(f"  CUDA stage       layers {plan['cudaLayers'][0]}-"
              f"{plan['cudaLayers'][1]}")
        print(f"  ROCm stage       layers {plan['rocmLayers'][0]}-"
              f"{plan['rocmLayers'][1]}")
        print(f"  KV cache         {plan['kvBytesPerToken']} B/token, "
              f"{kv / GIB:.2f} GiB at {plan['contextLength']} tokens")
        print(f"  transport        MCCL host-staged cross-vendor pipeline")
    elif plan:
        print(f"\n  no plan: {plan['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
