"""`openmycelium plan` -- compile one authoritative placement manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for relative in ("../serving", "../fabric", "../scheduler"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, relative)))

from fabric import discover  # noqa: E402
from model_inspect import parse_size  # noqa: E402
from models import resolve_verbose  # noqa: E402
from placement import ManifestError, create_placement, write_placement  # noqa: E402



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
    parser = argparse.ArgumentParser(
        description="Compile an authoritative Mycelium placement")
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--cuda-budget", default="14GiB")
    parser.add_argument("--rocm-budget", default="14GiB")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--output", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cached-fabric", action="store_true",
                        help="permit a non-expired inventory cache for dry planning")
    parser.add_argument("--allow-cpu", action="store_true",
                        help="create a synthetic two-stage manifest for smoke tests")
    args = parser.parse_args()
    _apply_config(args)

    model = resolve_verbose(args.model, sys.stderr)
    if model is None:
        print(f"  no model found for {args.model!r}", file=sys.stderr)
        return 66
    try:
        if args.allow_cpu:
            report = {"schemaVersion": 2, "identitiesUnique": True,
                      "devices": [], "probedAt": time.time(),
                      "fromCache": False}
        else:
            report = discover(
                {"nvidia": args.cuda_python, "amd": args.rocm_python},
                use_cache=args.cached_fabric)
        manifest = create_placement(
            model, report, parse_size(args.cuda_budget),
            parse_size(args.rocm_budget), args.context_length,
            allow_cpu=args.allow_cpu)
        if args.output:
            write_placement(args.output, manifest)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"  placement refused: {error}", file=sys.stderr)
        return 65

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    print(f"  placement       {manifest['placementId']}")
    print(f"  digest          {manifest['manifestDigest']}")
    print(f"  model           {os.path.basename(model)}")
    print(f"  boundary        after layer "
          f"{manifest['pipeline']['boundaryAfterLayer']}")
    for stage in manifest["stages"]:
        layers = stage["layers"]
        print(f"  {stage['role']:<6}          {stage['deviceIdentity']}")
        print(f"                  layers {layers[0]}-{layers[-1]}, "
              f"{stage['weightBytes'] / (1 << 30):.3f} GiB weights, "
              f"{stage['budgetBytes'] / (1 << 30):.1f} GiB budget")
    print(f"  transport       {manifest['pipeline']['transport']}")
    if args.output:
        print(f"  written         {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
