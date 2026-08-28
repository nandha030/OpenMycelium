"""`openmycelium stats` -- where the time went in the last run.

Reads the event log the workers wrote. Every figure here is a difference
between two recorded timestamps, not an estimate: load is measured inside each
worker, warm-up is one real forward across both stages, and inter-token latency
comes from the gaps between token events.

Load time and first-token time are reported separately on purpose. They trade
against each other -- a fast loader leaves the runtimes cold, so the first
forward pays for kernel selection instead. Reporting one number would hide that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

EVENTS = "/opt/openmycelium/events.jsonl"


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def load_runs(path: str) -> List[List[Dict[str, Any]]]:
    """Split the log into runs, each starting at a ROCm `loading` event."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    runs: List[List[Dict[str, Any]]] = []
    for row in rows:
        if row.get("worker") == "rocm" and row.get("event") == "loading":
            runs.append([])
        if runs:
            runs[-1].append(row)
    return runs


def first(rows, worker: str, event: str) -> Optional[Dict[str, Any]]:
    for row in rows:
        if row.get("worker") == worker and row.get("event") == event:
            return row
    return None


def report(rows: List[Dict[str, Any]], index: int, total: int) -> None:
    base = rows[0]["t"]
    tokens = [r for r in rows if r.get("event") == "token"]
    rocm_loaded = first(rows, "rocm", "loaded")
    cuda_loaded = first(rows, "cuda", "loaded")
    cuda_ready = first(rows, "cuda", "ready")
    warmed = first(rows, "cuda", "warmed")
    failed = [r for r in rows if r.get("event") == "failed"]

    print(f"  run {index + 1} of {total}")
    print()
    print("  load")
    for label, record in (("ROCm  layers 20-39 + head", rocm_loaded),
                          ("CUDA  embedding + layers 0-19", cuda_loaded)):
        if record:
            print(f"    {label:<32} {record.get('loadSeconds', 0):7.1f} s   "
                  f"{record.get('residentMiB', 0):8.1f} MiB resident")
        else:
            print(f"    {label:<32}       -   did not report loading")
    if rocm_loaded and cuda_loaded:
        wall = max(rocm_loaded["t"], cuda_loaded["t"]) - base
        serial = rocm_loaded.get("loadSeconds", 0) + cuda_loaded.get("loadSeconds", 0)
        print(f"    {'wall clock until both loaded':<32} {wall:7.1f} s"
              f"   (sum of both stages {serial:.1f} s)")

    print()
    print("  startup")
    if warmed:
        print(f"    {'warm-up (cold BF16 kernels)':<32} "
              f"{warmed.get('warmupMs', 0) / 1000:7.1f} s")
    if cuda_ready:
        print(f"    {'time until READY':<32} {cuda_ready['t'] - base:7.1f} s")
    if tokens:
        print(f"    {'time until first token':<32} "
              f"{tokens[0]['t'] - base:7.1f} s")

    if tokens:
        gaps = [(tokens[i + 1]["t"] - tokens[i]["t"]) * 1000
                for i in range(len(tokens) - 1)]
        span = tokens[-1]["t"] - tokens[0]["t"]
        print()
        print("  generation")
        print(f"    {'tokens':<32} {len(tokens):7d}")
        print(f"    {'generation wall time':<32} {span:7.1f} s")
        if gaps:
            rate = len(gaps) / (sum(gaps) / 1000)
            print(f"    {'decode rate':<32} {rate:7.2f} tok/s")
            print(f"    {'inter-token p50':<32} {percentile(gaps, 50):7.1f} ms")
            print(f"    {'inter-token p95':<32} {percentile(gaps, 95):7.1f} ms")
            print(f"    {'inter-token p99':<32} {percentile(gaps, 99):7.1f} ms")
            print(f"    {'inter-token min / max':<32} "
                  f"{min(gaps):7.1f} / {max(gaps):.1f} ms")
        if cuda_ready:
            ready_to_token = tokens[0]["t"] - cuda_ready["t"]
            print(f"    {'TTFT measured from READY':<32} "
                  f"{ready_to_token * 1000:7.1f} ms")
        total_wall = tokens[-1]["t"] - base
        share = 100.0 * span / total_wall if total_wall else 0.0
        print()
        print(f"    of {total_wall:.1f} s end to end, {span:.1f} s "
              f"({share:.1f}%) was generation; the rest was load and warm-up")
    else:
        print("\n  no tokens were produced")
    if failed:
        print()
        for row in failed:
            print(f"    FAILED {row.get('worker')}: {row.get('detail', '')[:120]}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Timing of recent runs")
    parser.add_argument("--events", default=EVENTS)
    parser.add_argument("--last", type=int, default=1,
                        help="how many recent runs to show")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    runs = load_runs(args.events)
    if not runs:
        print(f"  no runs recorded in {args.events}")
        return 1
    chosen = runs[-args.last:]
    if args.json:
        print(json.dumps(chosen, indent=2))
        return 0
    print()
    for offset, rows in enumerate(chosen):
        report(rows, len(runs) - len(chosen) + offset, len(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
