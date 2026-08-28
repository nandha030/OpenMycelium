"""The rest of the model lifecycle: inspect, verify, remove, pull.

Split from `models.py` so the resolver that every other command depends on stays
small and unentangled with the commands built on top of it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from models import (GIB, STORE, describe, resolve,  # noqa: E402
                    resolve_for_removal)


def cmd_inspect(args) -> int:
    path = resolve(args.model)
    if path is None:
        print(f"  no model found for {args.model!r}")
        return 66
    record = describe(path)
    source = os.path.join(path, "openmycelium-source.json")
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as handle:
            record["source"] = json.load(handle)
    if getattr(args, "json", False):
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    print()
    print(f"  {os.path.basename(path)}")
    print(f"    path          {path}")
    print(f"    size          {record['bytes'] / GIB:.2f} GiB")
    print(f"    architecture  {record.get('architecture', '?')}")
    print(f"    layers        {record.get('layers', '?')}")
    print(f"    hidden        {record.get('hidden', '?')}")
    print(f"    dtype         {record.get('dtype', '?')}")
    print(f"    on /mnt       {record['onWindowsMount']}"
          + ("   (loads slowly; import it)" if record["onWindowsMount"] else ""))
    origin = record.get("source")
    if origin:
        print(f"    repository    {origin.get('repository')}")
        print(f"    commit        {str(origin.get('commit', ''))[:16]}")
        if origin.get("skipped"):
            print(f"    not fetched   {len(origin['skipped'])} files "
                  f"(repository code and pickle archives are never pulled)")
    print()
    return 0


def cmd_verify(args) -> int:
    """Check every shard against its own declared length.

    A truncated or half-resumed download is the failure this catches, and it
    catches it now rather than after a ten-minute load.
    """
    path = resolve(args.model)
    if path is None:
        print(f"  no model found for {args.model!r}")
        return 66
    from model_inspect import inspect_model, read_safetensors_header  # noqa: PLC0415

    problems: List[str] = []
    try:
        model = inspect_model(path)
    except Exception as error:                                # noqa: BLE001
        print(f"  FAIL: {error}")
        return 1
    print()
    print(f"  {os.path.basename(path)}: {len(model.tensors)} tensors, "
          f"{model.layer_count} layers")

    shards = sorted({t.shard for t in model.tensors})
    for shard in shards:
        full = os.path.join(path, shard)
        if not os.path.isfile(full):
            problems.append(f"{shard} is missing")
            print(f"    {shard:<44}     -      MISSING")
            continue
        try:
            header = read_safetensors_header(full)
        except Exception as error:                            # noqa: BLE001
            problems.append(f"{shard} header unreadable: {error}")
            continue
        end = max((meta["data_offsets"][1] for meta in header.values()
                   if isinstance(meta, dict) and "data_offsets" in meta),
                  default=0)
        with open(full, "rb") as handle:
            prefix = int.from_bytes(handle.read(8), "little")
        actual = os.path.getsize(full)
        expected = 8 + prefix + end
        ok = actual == expected
        if not ok:
            problems.append(f"{shard} is {actual} bytes, expected {expected}")
        print(f"    {shard:<44} {actual / GIB:6.2f} GiB  "
              f"{'ok' if ok else 'TRUNCATED'}")

    source = os.path.join(path, "openmycelium-source.json")
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        for entry in manifest.get("files", []):
            full = os.path.join(path, entry["name"])
            if entry.get("size") and os.path.isfile(full) \
                    and os.path.getsize(full) != entry["size"]:
                problems.append(
                    f"{entry['name']} differs from the pull manifest")
        print(f"    pull manifest: {len(manifest.get('files', []))} files "
              f"from {manifest.get('repository')} @ "
              f"{str(manifest.get('commit', ''))[:12]}")

    print()
    if problems:
        print(f"  FAIL: {len(problems)} problem(s)")
        for item in problems[:10]:
            print(f"    - {item}")
        print()
        return 1
    print("  PASS: every shard matches its declared length")
    print()
    return 0


def cmd_remove(args) -> int:
    # Strict resolution: no substitution, no prefix matching, store only.
    path = resolve_for_removal(args.model)
    if path is None:
        elsewhere = resolve(args.model)
        print()
        print(f"  refusing to remove {args.model!r}")
        if elsewhere:
            print(f"  it resolves to {elsewhere}, which is not a model this")
            print("  command may delete. Removal never substitutes one path")
            print("  for another and only ever deletes from the store.")
            print(f"  to delete the imported copy, name it directly:")
            print(f"      openmycelium model remove "
                  f"{os.path.basename(elsewhere)} --yes")
        else:
            print("  no model of that name exists in the store")
        print()
        return 2
    record = describe(path)
    if not args.yes:
        print(f"  would remove {path} ({record['bytes'] / GIB:.2f} GiB)")
        print("  pass --yes to delete it")
        return 0
    shutil.rmtree(path)
    print(f"  removed {path} ({record['bytes'] / GIB:.2f} GiB freed)")
    return 0


def cmd_pull(args) -> int:
    from puller import PullError, pull  # noqa: PLC0415
    try:
        result = pull(args.repository, args.revision, args.name or None,
                      STORE, args.force)
    except PullError as error:
        print(f"\n  {error}\n")
        return 1
    if result["outcome"] == "exists":
        print(f"  {result['path']} already exists; {result['detail']}")
        return 0
    print()
    print(f"  pulled {result['files']} files, {result['bytes'] / GIB:.2f} GiB "
          f"in {result['seconds']}s ({result['throughputMBps']} MB/s)")
    print(f"  commit {result['commit'][:16]}")
    print(f"  next:  openmycelium model verify "
          f"{os.path.basename(result['path'])}")
    print()
    return 0
