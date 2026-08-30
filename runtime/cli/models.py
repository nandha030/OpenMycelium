"""`openmycelium models` -- list what is loadable, and import from Windows.

A model on a Windows path is usable directly: the workers read it through
/mnt/c. It is just slow, because every one of the 22.8 GiB of weights crosses
the 9P filesystem boundary on each load. Importing copies the checkpoint onto
the Linux filesystem once, after which loading is bounded by disk and page
cache instead.

Both paths work. `import` is a convenience, not a requirement, and this says so
rather than pretending a Windows path is unsupported.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from config import load as _load_config  # noqa: E402

#: Where imported and pulled models live. Resolved through the configuration
#: precedence rather than pinned to one machine's layout.
STORE = _load_config().model_store
GIB = 1 << 30


def wsl_path(path: str) -> str:
    if len(path) > 2 and path[1] == ":" and path[2] in "\\/":
        drive, rest = path[0].lower(), path[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}".rstrip("/")
    return path


def is_model(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "config.json"))


def resolve(name_or_path: str) -> Optional[str]:
    """A short name, a Linux path, or a Windows path -- all acceptable.

    An imported copy wins over the same model on a Windows mount. Honouring the
    typed path instead cost a measured 208 seconds of loading where the local
    copy needs about 22, for byte-identical weights. The substitution is
    announced by `resolve_verbose` rather than done silently, and passing an
    explicit Linux path still forces that exact directory.
    """
    if not name_or_path:
        return None
    candidate = wsl_path(name_or_path)
    on_windows_mount = candidate.startswith("/mnt/")

    if os.path.isdir(candidate) and is_model(candidate):
        if on_windows_mount:
            imported = os.path.join(STORE, os.path.basename(candidate.rstrip("/")))
            if is_model(imported) and same_checkpoint(candidate, imported):
                return imported
        return candidate

    local = os.path.join(STORE, name_or_path)
    if is_model(local):
        return local
    for entry in sorted(os.listdir(STORE)) if os.path.isdir(STORE) else []:
        if entry.lower().startswith(name_or_path.lower()) \
                and is_model(os.path.join(STORE, entry)):
            return os.path.join(STORE, entry)
    return None


def resolve_for_removal(name_or_path: str) -> Optional[str]:
    """Resolve a name for deletion, with no substitution of any kind.

    `resolve` deliberately prefers an imported copy when given a Windows path,
    because loading the same weights from the Linux filesystem is an order of
    magnitude faster and the substitution is harmless. For deletion it is not
    harmless: asking to remove a Windows path and having the tool delete a
    *different* directory destroyed 22.84 GiB during testing.

    So removal resolves strictly. A bare name must name a directory directly
    inside the store; a path must already be inside the store. Nothing else is
    accepted, and nothing is ever substituted.
    """
    if not name_or_path:
        return None
    candidate = wsl_path(name_or_path)
    if os.path.isabs(candidate) or candidate.startswith("/mnt/"):
        resolved = os.path.realpath(candidate)
        store = os.path.realpath(STORE)
        if resolved == store or not resolved.startswith(store + os.sep):
            return None
        return resolved if is_model(resolved) else None
    if "/" in name_or_path or "\\" in name_or_path:
        return None
    direct = os.path.join(STORE, name_or_path)
    return direct if is_model(direct) else None


def same_checkpoint(left: str, right: str) -> bool:
    """Cheap evidence that two directories hold the same weights.

    Compares the config and the size of every shard. Not a hash -- hashing 22.8
    GiB to decide which directory to open would cost more than the load it
    saves -- but a differing shard size or config means they are not the same
    checkpoint, and the typed path is used.
    """
    try:
        with open(os.path.join(left, "config.json"), "rb") as handle:
            left_config = handle.read()
        with open(os.path.join(right, "config.json"), "rb") as handle:
            right_config = handle.read()
        if left_config != right_config:
            return False
        shards = sorted(n for n in os.listdir(left) if n.endswith(".safetensors"))
        mirror = sorted(n for n in os.listdir(right) if n.endswith(".safetensors"))
        if not shards or shards != mirror:
            return False
        return all(os.path.getsize(os.path.join(left, name))
                   == os.path.getsize(os.path.join(right, name))
                   for name in shards)
    except OSError:
        return False


def resolve_verbose(name_or_path: str, out) -> Optional[str]:
    """Resolve, and say plainly when the answer is not what was typed."""
    resolved = resolve(name_or_path)
    if resolved is None:
        return None
    typed = wsl_path(name_or_path)
    if typed.startswith("/mnt/") and resolved.startswith(STORE):
        print(f"  using the imported copy at {resolved}", file=out)
        print(f"  (same checkpoint as {typed}, but loads in seconds rather "
              f"than minutes)", file=out)
    elif resolved.startswith("/mnt/"):
        print(f"  note: loading from a Windows path ({resolved}).", file=out)
        print("        Every byte crosses the 9P filesystem, so this load takes "
              "minutes.", file=out)
        print(f'        Import it once:  openmycelium models import '
              f'"{name_or_path}"', file=out)
    return resolved


def describe(path: str) -> Dict[str, Any]:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    record: Dict[str, Any] = {"path": path, "bytes": total,
                              "onWindowsMount": path.startswith("/mnt/")}
    config = os.path.join(path, "config.json")
    if os.path.isfile(config):
        with open(config, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        record.update({
            "architecture": (raw.get("architectures") or ["?"])[0],
            "layers": raw.get("num_hidden_layers"),
            "hidden": raw.get("hidden_size"),
            "dtype": raw.get("torch_dtype"),
        })
        record.update(_compatibility(raw))
    return record


def _compatibility(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Additive adapter metadata. Read-only, and it always answers.

    Inspection must describe a model it cannot run -- that is the whole point
    of separating compatibility from execution -- so this reports a status
    rather than raising, and reports one even when the adapters package cannot
    be imported at all.
    """
    try:
        serving = os.path.join(os.path.dirname(_HERE), "serving")
        if serving not in sys.path:
            sys.path.insert(0, serving)
        from adapters import describe_compatibility  # noqa: PLC0415
        from adapters.qualification import QualificationStatus  # noqa: PLC0415
        fields = describe_compatibility(raw)
        fields.setdefault("architectures", raw.get("architectures") or [])
        # Qualification is a property of a whole situation -- checkpoint,
        # runtimes, device pair, boundary -- and none of that is known from a
        # config file. `openmycelium qualify status` answers it properly; here
        # the honest answer is that this view cannot tell.
        fields["qualificationStatus"] = QualificationStatus.UNQUALIFIED
        fields["qualificationScope"] = "not evaluated from config alone"
        return fields
    except Exception as error:                            # noqa: BLE001
        return {"compatibilityStatus": "ADAPTER_UNAVAILABLE",
                "compatibilityReason": f"{type(error).__name__}: {error}",
                "adapterId": None, "adapterVersion": None}


def cmd_list(args) -> int:
    entries: List[Dict[str, Any]] = []
    if os.path.isdir(STORE):
        for name in sorted(os.listdir(STORE)):
            path = os.path.join(STORE, name)
            if os.path.isfile(os.path.join(path, "config.json")):
                record = describe(path)
                record["name"] = name
                entries.append(record)
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    if not entries:
        print(f"  no models imported yet (looked in {STORE})")
        print("  import one with:  openmycelium models import \"C:\\path\\to\\model\"")
        return 0
    print(f"  models in {STORE}")
    for record in entries:
        print(f"    {record['name']:<34} {record['bytes'] / GIB:6.2f} GiB  "
              f"{record.get('architecture', '?')}  "
              f"{record.get('layers', '?')} layers  {record.get('dtype', '?')}")
    return 0


def cmd_import(args) -> int:
    source = wsl_path(args.source)
    if not os.path.isfile(os.path.join(source, "config.json")):
        print(f"  ERROR: no config.json under {source}")
        return 2
    name = args.name or os.path.basename(source.rstrip("/"))
    target = os.path.join(STORE, name)
    if os.path.exists(target) and not args.force:
        print(f"  {name} is already imported at {target}")
        print("  pass --force to replace it")
        return 0

    os.makedirs(STORE, exist_ok=True)
    info = describe(source)
    free = shutil.disk_usage(STORE).free
    if free < info["bytes"] * 1.05:
        print(f"  ERROR: {info['bytes'] / GIB:.1f} GiB needed, "
              f"{free / GIB:.1f} GiB free on the Linux filesystem")
        return 3

    print(f"  copying {info['bytes'] / GIB:.2f} GiB")
    print(f"    from {source}")
    print(f"    to   {target}")
    started = time.perf_counter()
    staging = target + ".partial"
    if os.path.exists(staging):
        shutil.rmtree(staging)
    # Copy to a staging name and rename at the end, so an interrupted import
    # never leaves something that looks like a complete model.
    subprocess.run(["cp", "-r", source, staging], check=True)
    if os.path.exists(target):
        shutil.rmtree(target)
    os.rename(staging, target)
    elapsed = time.perf_counter() - started
    print(f"  done in {elapsed:.0f}s "
          f"({info['bytes'] / GIB / max(elapsed, 1e-9):.2f} GiB/s)")
    print(f"  now:  openmycelium run --model {name} --prompt \"...\"")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage local models")
    sub = parser.add_subparsers(dest="command")
    listing = sub.add_parser("list")
    listing.add_argument("--json", action="store_true")
    importer = sub.add_parser("import")
    importer.add_argument("source")
    importer.add_argument("--name", default="")
    importer.add_argument("--force", action="store_true")
    inspector = sub.add_parser("inspect")
    inspector.add_argument("model")
    inspector.add_argument("--json", action="store_true")
    verifier = sub.add_parser("verify")
    verifier.add_argument("model")
    remover = sub.add_parser("remove")
    remover.add_argument("model")
    remover.add_argument("--yes", action="store_true")
    fetcher = sub.add_parser("pull")
    fetcher.add_argument("repository")
    fetcher.add_argument("--revision", default="main")
    fetcher.add_argument("--name", default="")
    fetcher.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.command == "import":
        return cmd_import(args)
    if args.command in ("inspect", "verify", "remove", "pull"):
        from model_cmds import (cmd_inspect, cmd_pull,  # noqa: PLC0415
                                cmd_remove, cmd_verify)
        return {"inspect": cmd_inspect, "verify": cmd_verify,
                "remove": cmd_remove, "pull": cmd_pull}[args.command](args)
    if args.command is None:
        args.json = False
    return cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
