"""Validate the profiler before any timing it produces is believed.

Step 2 closes on three conditions, and none of them is about performance:

1. **Reconciliation** -- the sum of per-operation times must account for the
   stage-level end-to-end time measured the existing way. A profiler whose
   parts do not add up to the whole is measuring something other than the work.
2. **Output invariance** -- generated tokens must be identical with the
   profiler off, in stream-event mode, and in global-sync control mode. An
   observer that changes the result is not an observer.
3. **Measured overhead** -- the cost of profiling is measured, not assumed, by
   comparing the same workload across the three methods.

Runs on the CPU-sized checkpoint so it is cheap to repeat; the same assertions
then run against the real model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

ROOT = "/mnt/c/Users/User/Documents/Open_Mycelium"
sys.path.insert(0, os.path.join(ROOT, "runtime", "serving"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "scheduler"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "fabric"))

METHODS = ("off", "stream-event", "global-sync-control")

#: Timed passes per sequence length. The first is first-touch and is excluded
#: from steady-state statistics; the rest give a median rather than one sample.
REPEATS = 4


def run_pair(model: str, method: str, port: int, work: str,
             allow_cpu: bool, seq_lens: str) -> Dict[str, Any]:
    """One prefill sweep with both workers, at one timing method."""
    from placement import create_placement, write_placement  # noqa: PLC0415
    from model_inspect import parse_size  # noqa: PLC0415
    import uuid

    manifest_path = os.path.join(work, f"placement.{method}.json")
    if allow_cpu:
        fabric = {"schemaVersion": 2, "identitiesUnique": True, "devices": [],
                  "probedAt": 0.0, "fromCache": False}
        budget = parse_size("48MiB")
    else:
        from fabric import discover  # noqa: PLC0415
        fabric = discover(use_cache=False)
        budget = parse_size("14GiB")
    manifest = create_placement(model, fabric, budget, budget, 512,
                                allow_cpu=allow_cpu)
    write_placement(manifest_path, manifest)

    run_id = str(uuid.uuid4())
    ready = os.path.join(work, f"ready.{port}")
    for stale in (ready,):
        if os.path.exists(stale):
            os.remove(stale)
    common = ["--model", model, "--mode", "prefill", "--seq-lens", seq_lens,
              "--placement", manifest_path, "--run-id", run_id,
              "--port", str(port), "--ready-file", ready,
              "--accept-timeout", "600", "--context-length", "512",
              "--timing-method", method,
              "--prefill-repeats", str(REPEATS)]
    if allow_cpu:
        common += ["--allow-cpu"]
    driver = os.path.join(ROOT, "runtime", "serving", "pipeline_run.py")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([
        os.path.join(ROOT, "runtime", "serving"),
        os.path.join(ROOT, "runtime", "scheduler"),
        os.path.join(ROOT, "runtime", "mccl", "src")])

    rocm_python = "/opt/hetenv/bin/python" if allow_cpu else "/opt/rocmenv/bin/python"
    receiver = subprocess.Popen(
        [rocm_python, driver, "--role", "rocm"] + common,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=environment)
    import time
    deadline = time.monotonic() + 900
    while not os.path.exists(ready):
        if receiver.poll() is not None or time.monotonic() > deadline:
            return {"error": "receiver never became ready"}
        time.sleep(0.5)
    sender = subprocess.run(
        ["/opt/hetenv/bin/python", driver, "--role", "cuda",
         "--peer", "127.0.0.1"] + common,
        capture_output=True, text=True, env=environment)
    receiver_out, _ = receiver.communicate(timeout=900)

    def last_json(text: str) -> Dict[str, Any]:
        for line in reversed((text or "").splitlines()):
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}

    return {"cuda": last_json(sender.stdout), "rocm": last_json(receiver_out)}


def tokens_of(result: Dict[str, Any]) -> List[int]:
    rows = result.get("rocm", {}).get("prefill") or \
        result.get("cuda", {}).get("prefill") or []
    return [row["logits"]["greedyToken"] for row in rows
            if isinstance(row, dict) and "logits" in row]


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "/opt/models/tiny-mistral"
    allow_cpu = "--gpu" not in sys.argv
    seq_lens = "1,8,64" if allow_cpu else "1,8,64,256"
    work = tempfile.mkdtemp(prefix="profval-")
    print(f"  model {model}   cpu={allow_cpu}   work {work}\n")

    results: Dict[str, Dict[str, Any]] = {}
    port = 32100
    for method in METHODS:
        port += 1
        print(f"  running timing-method={method} ...")
        results[method] = run_pair(model, method, port, work, allow_cpu, seq_lens)
        if "error" in results[method]:
            print(f"    FAILED: {results[method]['error']}")
            return 2

    failures: List[str] = []

    # --- 2. output invariance --------------------------------------------
    print("\n  output invariance")
    baseline = tokens_of(results["off"])
    for method in METHODS:
        produced = tokens_of(results[method])
        same = produced == baseline
        print(f"    {method:<22} tokens {produced}  {'same' if same else 'DIFFERENT'}")
        if not same:
            failures.append(f"{method} changed the generated tokens")
    if not baseline:
        failures.append("no tokens were captured; invariance is unproven")

    # --- 1. reconciliation ------------------------------------------------
    # Scored on steady state only. The first touch of a shape selects kernels --
    # measured at 113x the repeat cost on ROCm at sequence 1 -- and that time is
    # spent outside any timed region, so pooling it with steady state would
    # judge the profiler on work it cannot see. Cold rows are reported, not
    # scored, and the steady-state band is tighter than the old blanket one.
    print("\n  reconciliation, steady state (summed operations vs stage)")
    for method in ("stream-event", "global-sync-control"):
        for role in ("cuda", "rocm"):
            rows = results[method].get(role, {}).get("reconciliation") or []
            steady = [r for r in rows if r.get("warmState") == "steady-state"]
            cold = [r for r in rows if r.get("warmState") != "steady-state"]
            if not steady:
                print(f"    {method:<22} {role:<5} no steady-state rows")
                failures.append(f"{method}/{role} produced no steady-state rows")
                continue
            for row in steady:
                coverage = row.get("coverage")
                flag = ""
                if coverage is None or not 0.90 <= coverage <= 1.05:
                    flag = "  <-- outside 90-105%"
                    failures.append(
                        f"{method}/{role}/{row['label']} coverage {coverage}")
                print(f"    {method:<22} {role:<5} {row['label']:<14} "
                      f"stage {row['stageEndToEndMs']:>9.3f} ms  "
                      f"ops {row['summedOperationsMs']:>9.3f} ms  "
                      f"coverage {coverage}{flag}")
            if cold:
                worst = min(r.get("coverage") or 0.0 for r in cold)
                print(f"    {method:<22} {role:<5} {len(cold)} first-touch rows "
                      f"excluded, worst coverage {worst:.3f} (kernel selection)")

    # --- 3. measured overhead ---------------------------------------------
    print("\n  overhead (stage end-to-end, timed pass, vs profiler off)")
    for role in ("cuda", "rocm"):
        base_rows = _timed_rows(results["off"], role)
        for method in ("stream-event", "global-sync-control"):
            rows = _timed_rows(results[method], role)
            for length, value in sorted(rows.items()):
                reference = base_rows.get(length)
                if not reference:
                    continue
                delta = 100.0 * (value - reference) / reference
                print(f"    {role:<5} seq {length:>5}  {method:<22} "
                      f"{reference:>8.2f} -> {value:>8.2f} ms  "
                      f"{delta:+7.1f}%   {_spread(results[method], length, role)}")

    print("\n  per-layer detail (stream-event, cuda)")
    totals = results["stream-event"].get("cuda", {}).get(
        "profile", {}).get("layerTotals", {}).get("prefill", {})
    if totals:
        print(f"    layers {totals['layers']}  mean {totals['meanMs']:.3f} ms  "
              f"min {totals['minMs']:.3f}  max {totals['maxMs']:.3f}  "
              f"between-layer spread {totals['betweenLayerSpreadMs']:.3f} ms")
    else:
        print("    none captured")
        failures.append("stream-event produced no per-layer totals")

    print()
    if failures:
        print("RESULT: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("RESULT: PASS - measurements reconcile, output is unchanged, and "
          "overhead is measured")
    return 0


def _spread(result: Dict[str, Any], length: int, role: str = "cuda") -> str:
    """The range of the quantity actually compared, so noise stays visible."""
    key = "cudaComputeSamplesMs" if role == "cuda" else "rocmComputeSamplesMs"
    for row in result.get("cuda", {}).get("prefill") or []:
        if row.get("sequenceLength") == length:
            values = row.get(key) or []
            if len(values) > 1:
                return (f"samples {min(values):.1f}-{max(values):.1f} "
                        f"(n={len(values)})")
    return "single sample -- not an overhead measurement"


def _timed_rows(result: Dict[str, Any], role: str) -> Dict[int, float]:
    """Median steady-state compute time per length, not the last pass.

    `decomposition` holds only the final pass; taking it would make an n=1
    figure look like a summary of several.
    """
    rows = result.get("cuda", {}).get("prefill") or []
    key = "cudaComputeSamplesMs" if role == "cuda" else "rocmComputeSamplesMs"
    out: Dict[int, float] = {}
    for row in rows:
        values = sorted(row.get(key) or [])
        if values:
            out[row["sequenceLength"]] = values[len(values) // 2]
    return out


if __name__ == "__main__":
    raise SystemExit(main())
