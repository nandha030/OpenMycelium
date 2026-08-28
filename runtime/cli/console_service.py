"""Typed service layer behind `openmycelium console`.

The console does not shell out to the CLI. The CLI's `--json` output was the
reference for these shapes, but it is not the interface: a UI that parses
another program's stdout inherits every formatting decision that program ever
makes, and breaks silently when one changes. These functions call the same
runtime modules the CLI calls, and return dictionaries the HTTP layer
serialises directly.

Every response carries `schemaVersion`, so a browser holding a cached bundle
can tell that it is talking to a runtime it does not understand instead of
mis-rendering it.

Nothing here starts a process. Anything that mutates state lives in the server,
behind an explicit gate, so the read paths can be reasoned about on their own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
# The runtime's modules import each other by bare name, so the same
# directories the CLI puts on the path have to be here too.
for _relative in (".", "../serving", "../fabric", "../scheduler"):
    _path = os.path.abspath(os.path.join(_HERE, _relative))
    if _path not in sys.path:
        sys.path.insert(0, _path)

CONSOLE_SCHEMA_VERSION = 1
GIB = 1 << 30

#: Deliberate wording. Two cards with 16 GiB each do not become one 32 GiB
#: pool, and a console that implies otherwise teaches the wrong mental model on
#: the very first screen.
CAPACITY_PHRASE = "aggregated capacity across separate GPUs"


def envelope(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Every response looks the same from the outside."""
    return {
        "schemaVersion": CONSOLE_SCHEMA_VERSION,
        "kind": kind,
        "observedAt": time.time(),
        **payload,
    }


# --------------------------------------------------------------- TTL cache

#: Readiness asks both GPU interpreters to import torch and report. That costs
#: seconds and real CPU, and a console that polls it would spawn interpreters
#: faster than they finish -- which is exactly what happened: requests stacked
#: up and the log filled with readiness calls. Answers are cached briefly, and
#: every response says whether it was served from the cache and how old it is.
_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()
READINESS_TTL = 20.0


def cached(key: str, ttl: float, produce):
    """Compute at most once per ttl, and never twice at the same moment."""
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and now - entry["at"] < ttl:
            fresh = dict(entry["value"])
            fresh["cached"] = True
            fresh["cacheAgeSeconds"] = round(now - entry["at"], 1)
            return fresh
    # Produced outside the lock so a slow probe does not block readers, but
    # stored under it so two callers cannot both pay for it.
    value = produce()
    with _CACHE_LOCK:
        _CACHE[key] = {"at": time.time(), "value": value}
    result = dict(value)
    result["cached"] = False
    result["cacheAgeSeconds"] = 0.0
    return result


def invalidate(key: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(key, None)


def _probe(python: str, script: str, timeout: float = 300) -> Dict[str, Any]:
    """Ask one of the GPU interpreters a question.

    A fixed argument list, never a shell string: the interpreter path comes
    from configuration and the script is a constant in this file, so there is
    nothing for a caller to inject into.
    """
    if not (python and os.path.isfile(python) and os.access(python, os.X_OK)):
        return {"ok": False, "detail": f"no interpreter at {python or '(unset)'}"}
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "detail": str(error)[:200]}
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False,
            "detail": (out.stderr or "no output").strip().splitlines()[-1:][0]
            if (out.stderr or "").strip() else "no output"}


GPU_STATUS = (
    "import json,torch;"
    "ok=torch.cuda.is_available();"
    "free,total=(torch.cuda.mem_get_info(0) if ok else (0,0));"
    "print(json.dumps({'ok':ok,"
    "'name':torch.cuda.get_device_name(0) if ok else '',"
    "'freeBytes':free,'totalBytes':total,'torch':torch.__version__,"
    "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda'}))")


# ------------------------------------------------------------------ overview

def readiness() -> Dict[str, Any]:
    """Cached briefly; see the note on _CACHE."""
    return cached("readiness", READINESS_TTL, _readiness_uncached)


def _readiness_uncached() -> Dict[str, Any]:
    """Everything `doctor` checks, as data rather than printed lines."""
    import config as _config
    resolved = _config.load()

    checks: List[Dict[str, Any]] = []
    devices: List[Dict[str, Any]] = []
    for vendor in ("cuda", "rocm"):
        python = getattr(resolved, f"{vendor}_python", "")
        info = _probe(python, GPU_STATUS)
        ok = bool(info.get("ok") and info.get("runtime") == vendor)
        checks.append({
            "id": f"{vendor}Runtime",
            "label": f"{vendor.upper()} runtime",
            "ok": ok,
            "detail": (f"{info.get('name')} (torch {info.get('torch')})" if ok
                       else info.get("detail", "unavailable")),
            "interpreter": python,
            "source": resolved.source_of(f"{vendor}_python"),
        })
        if ok:
            devices.append({
                "vendor": vendor,
                "name": info.get("name"),
                "totalBytes": info.get("totalBytes", 0),
                "freeBytes": info.get("freeBytes", 0),
                "torch": info.get("torch"),
            })

    dxg = os.path.exists("/dev/dxg")
    checks.append({"id": "gpuDeviceNode", "label": "GPU device node",
                   "ok": dxg or os.path.exists("/dev/kfd"),
                   "detail": "/dev/dxg present" if dxg else
                             ("/dev/kfd present" if os.path.exists("/dev/kfd")
                              else "neither /dev/dxg nor /dev/kfd")})

    store = getattr(resolved, "model_store", "")
    models = _model_names(store)
    checks.append({"id": "modelStore", "label": "Model store",
                   "ok": bool(models),
                   "detail": (f"{len(models)} model(s) in {store}" if models
                              else f"no models in {store or '(unset)'}")})

    # The ROCm three-state answer, because "no GPU" is true and useless.
    rocm_detail: Dict[str, Any] = {}
    try:
        import rocm_prereq
        rocm_detail = rocm_prereq.detect(
            getattr(resolved, "rocm_python", ""),
            state_dir=getattr(resolved, "state_dir", ""))
    except Exception:                                         # noqa: BLE001
        pass

    total = sum(d["totalBytes"] for d in devices)
    return envelope("readiness", {
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "devices": devices,
        "aggregateBytes": total,
        "aggregateLabel": f"{total / GIB:.1f} GiB {CAPACITY_PHRASE}",
        "rocmPrerequisite": {
            "packagesInstalled": rocm_detail.get("packagesInstalled"),
            "systemRuntimeAvailable": rocm_detail.get("systemRuntimeAvailable"),
            "gpuQualified": rocm_detail.get("gpuQualified"),
            "state": rocm_detail.get("state"),
            "detail": rocm_detail.get("detail"),
        } if rocm_detail else None,
    })


def provenance() -> Dict[str, Any]:
    import provenance as _prov
    record = _prov.collect(deep=False)
    return envelope("provenance", {"provenance": record})


# -------------------------------------------------------------------- fabric

def fabric(use_cache: bool = True) -> Dict[str, Any]:
    """Discovered accelerators, each with a stable identity.

    The cache is used by default because probing spawns two interpreters and
    the console polls. `refresh` forces a live probe.
    """
    import config as _config
    from fabric import discover
    resolved = _config.load()
    report = discover({"nvidia": getattr(resolved, "cuda_python", ""),
                       "amd": getattr(resolved, "rocm_python", "")},
                      use_cache=use_cache)
    return envelope("fabric", {"fabric": report})


# -------------------------------------------------------------------- models

def _model_names(store: str) -> List[str]:
    if not store or not os.path.isdir(store):
        return []
    return [name for name in sorted(os.listdir(store))
            if os.path.isfile(os.path.join(store, name, "config.json"))]


def models() -> Dict[str, Any]:
    import config as _config
    from models import describe
    resolved = _config.load()
    store = getattr(resolved, "model_store", "")
    listed = []
    for name in _model_names(store):
        try:
            entry = describe(os.path.join(store, name))
            entry.setdefault("name", name)
            listed.append(entry)
        except Exception as error:                            # noqa: BLE001
            listed.append({"name": name, "error": str(error)[:120]})
    return envelope("models", {"store": store, "models": listed})


def verify_model(name: str) -> Dict[str, Any]:
    """Every shard checked against its own declared length.

    This is what catches a truncated or half-resumed download, and it catches
    it now rather than after a ten-minute load. It is not a content hash:
    reading 22.8 GiB on every check would make the button useless.
    """
    from models import resolve
    path = resolve(name)
    if path is None:
        return envelope("verify", {"model": name, "ok": False,
                                   "detail": "not in the model store"})
    problems: List[str] = []
    shards: List[Dict[str, Any]] = []
    try:
        from model_inspect import inspect_model, read_safetensors_header
        model = inspect_model(path)
        # Shards are named by the tensors themselves. An earlier version of
        # this function looked for an attribute that does not exist, found
        # nothing, and reported success on zero shards -- a gate that passes
        # without evidence is worse than no gate.
        names = sorted({tensor.shard for tensor in model.tensors})
        for shard in names:
            full = os.path.join(path, shard)
            if not os.path.isfile(full):
                problems.append(f"{shard} is missing")
                shards.append({"file": shard, "ok": False, "detail": "missing"})
                continue
            try:
                header = read_safetensors_header(full)
            except Exception as error:                        # noqa: BLE001
                problems.append(f"{shard} header unreadable: {str(error)[:80]}")
                shards.append({"file": shard, "ok": False,
                               "detail": "header unreadable"})
                continue
            end = max((meta["data_offsets"][1] for meta in header.values()
                       if isinstance(meta, dict) and "data_offsets" in meta),
                      default=0)
            with open(full, "rb") as handle:
                prefix = int.from_bytes(handle.read(8), "little")
            actual = os.path.getsize(full)
            declared = 8 + prefix + end
            ok = actual >= declared
            if not ok:
                problems.append(f"{shard} is short: {actual} < {declared}")
            shards.append({"file": shard, "bytes": actual,
                           "declaredBytes": declared, "ok": ok})
    except Exception as error:                                # noqa: BLE001
        return envelope("verify", {"model": name, "ok": False,
                                   "detail": str(error)[:200]})
    if not shards:
        problems.append("no shards were found to check")
    return envelope("verify", {
        "model": name, "path": path,
        "ok": bool(shards) and not problems,
        "tensorCount": len(model.tensors), "layerCount": model.layer_count,
        "shards": shards, "problems": problems,
        "note": "every shard checked against its own declared length; "
                "not a content hash",
    })


# ---------------------------------------------------------------- placement

def placement(name: str, context_length: int = 4096,
              cuda_budget: str = "14GiB", rocm_budget: str = "14GiB",
              use_cache: bool = True) -> Dict[str, Any]:
    """Compile the authoritative placement and explain it. Launches nothing.

    Exactly the call `openmycelium plan` makes, so the console cannot show a
    split that differs from the one a run would use.
    """
    import config as _config
    from fabric import discover
    from models import resolve
    from model_inspect import parse_size
    from placement import ManifestError, create_placement

    path = resolve(name)
    if path is None:
        return envelope("placement", {"model": name, "ok": False,
                                      "detail": "not in the model store"})
    resolved = _config.load()
    try:
        report = discover({"nvidia": getattr(resolved, "cuda_python", ""),
                           "amd": getattr(resolved, "rocm_python", "")},
                          use_cache=use_cache)
        manifest = create_placement(path, report, parse_size(cuda_budget),
                                    parse_size(rocm_budget), context_length)
    except (ManifestError, RuntimeError, OSError, ValueError) as error:
        # A refusal is a result, not a crash: the planner declining to split a
        # model that will not fit is the scheduler working.
        return envelope("placement", {"model": name, "ok": False,
                                      "refused": True,
                                      "detail": str(error)[:300]})
    except Exception as error:                                # noqa: BLE001
        return envelope("placement", {"model": name, "ok": False,
                                      "detail": str(error)[:300]})
    return envelope("placement", {"model": name, "ok": True,
                                  **summarise_placement(manifest)})


def summarise_placement(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The parts a person needs to judge whether a split is sane."""
    stages = manifest.get("stages", [])
    names = [set(stage.get("tensors", [])) for stage in stages]
    overlap = sorted(names[0] & names[1]) if len(names) == 2 else []
    return {
        "placementId": manifest.get("placementId"),
        "manifestDigest": manifest.get("manifestDigest"),
        "pipeline": manifest.get("pipeline", {}),
        "exclusive": not overlap,
        "overlapCount": len(overlap),
        "totalTensors": sum(len(s.get("tensors", [])) for s in stages),
        "stages": [{
            "role": stage.get("role"),
            "runtime": stage.get("runtime"),
            "layers": stage.get("layers"),
            "tensorCount": len(stage.get("tensors", [])),
            "weightBytes": stage.get("weightBytes"),
            "kvBytes": stage.get("kvBytes"),
            "budgetBytes": stage.get("budgetBytes"),
            "holdsEmbedding": stage.get("holdsEmbedding"),
            "holdsLMHead": stage.get("holdsLMHead"),
            "deviceIdentity": stage.get("deviceIdentity"),
            "identityConfidence": stage.get("identityConfidence"),
            "identitySource": stage.get("identitySource"),
        } for stage in stages],
        "manifest": manifest,
    }


# ---------------------------------------------------------------- lifecycle

def workloads() -> Dict[str, Any]:
    """What is running, and what merely left a record behind."""
    import control
    records = control.list_records()
    running, stale = [], []
    for record in records:
        pid = record.get("pid")
        start = record.get("startTicks")
        entry = {
            "runId": record.get("runId"),
            "model": record.get("model"),
            "state": record.get("state"),
            "pid": pid,
            "port": record.get("port"),
            "placementId": record.get("placementId"),
            "startedAt": record.get("startedAt"),
            "owner": record.get("owner"),
        }
        if pid and control.alive(int(pid), start):
            running.append(entry)
        else:
            stale.append(entry)
    return envelope("workloads", {"running": running, "stale": stale,
                                  "busy": bool(running)})


def gpu_residency() -> Dict[str, Any]:
    """VRAM in use per card, for confirming a stop actually released it."""
    devices = []
    smi = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if os.path.exists(smi):
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=60)
            for line in out.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    devices.append({"vendor": "cuda", "name": parts[0],
                                    "usedMiB": int(parts[1]),
                                    "totalMiB": int(parts[2])})
        except Exception:                                     # noqa: BLE001
            pass

    # The AMD card has no equivalent tool under WSL: rocm-smi needs the amdgpu
    # kernel module, which does not exist here. Ask torch instead, and say so
    # rather than showing a blank where a number should be.
    import config as _config
    resolved = _config.load()
    info = _probe(getattr(resolved, "rocm_python", ""), GPU_STATUS, timeout=120)
    if info.get("ok"):
        used = (info["totalBytes"] - info["freeBytes"]) // (1 << 20)
        devices.append({"vendor": "rocm", "name": info.get("name"),
                        "usedMiB": used,
                        "totalMiB": info["totalBytes"] // (1 << 20),
                        "source": "torch.cuda.mem_get_info; rocm-smi is "
                                  "unavailable under WSL"})
    return envelope("residency", {"devices": devices})
