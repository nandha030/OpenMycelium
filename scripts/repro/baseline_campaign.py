"""Step 3: the eager-attention, CUDA-first, boundaryAfterLayer=19 baseline.

This produces the noise floor every later Intelligence decision is judged
against. It makes no optimisation claim and tests no alternative boundary.

Design follows docs/INTELLIGENCE_V0_CONTRACT.md:

* the **session** is the replicate -- an independent launch of both workers
* within a session, cold-start and first-request are recorded and kept out of
  steady-state statistics; the configured warm-ups are executed and discarded
* prompt lengths are randomised within each session, so a length cannot be
  confounded with position in the run
* identical token ids, seed, decoding policy and tokenizer identity across every
  session, verified by digest rather than assumed
* every attempt keeps its runId; failures are preserved under the ratified
  failure policy rather than replaced silently

The dataset is written once and sealed with a digest over its own contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

ROOT = "/mnt/c/Users/User/Documents/Open_Mycelium"
for _sub in ("serving", "scheduler", "fabric", "cli"):
    sys.path.insert(0, os.path.join(ROOT, "runtime", _sub))

from audit import prompt_ids_hash, tokenizer_identity  # noqa: E402
from clusterstats import summarise  # noqa: E402

DATASET_SCHEMA = 1


def gpu_state() -> Dict[str, Any]:
    """Clocks and temperature where the vendor exposes them."""
    state: Dict[str, Any] = {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=clocks.sm,clocks.mem,temperature.gpu,"
             "power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 4:
            state["nvidia"] = {"smClockMHz": _num(parts[0]),
                               "memClockMHz": _num(parts[1]),
                               "tempC": _num(parts[2]),
                               "powerW": _num(parts[3])}
    except (OSError, subprocess.SubprocessError):
        pass
    # AMD telemetry does not exist under WSL: no amdgpu module, no /dev/kfd, no
    # hwmon. Recorded as unavailable rather than as zero.
    state["amd"] = {"available": False,
                    "reason": "rocm-smi needs the amdgpu kernel module; "
                              "WSL exposes /dev/dxg instead"}
    return state


def _num(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class Session:
    """One independent launch of both workers, driven over the serve loop."""

    def __init__(self, args, index: int, work: str):
        self.args = args
        self.index = index
        self.work = work
        self.run_id = str(uuid.uuid4())
        self.events = os.path.join(work, f"events.{self.run_id}.jsonl")
        self.ready = os.path.join(work, f"ready.{args.port + index}")
        self.processes: Dict[str, subprocess.Popen] = {}
        self.requests: List[Dict[str, Any]] = []
        self.failure: Optional[str] = None
        self.cold_start_s: Optional[float] = None
        self.first_request: Optional[Dict[str, Any]] = None

    def _command(self, role: str) -> List[str]:
        python = "/opt/rocmenv/bin/python" if role == "rocm" \
            else "/opt/hetenv/bin/python"
        return [python, os.path.join(ROOT, "runtime", "serving",
                                     "pipeline_run.py"),
                "--role", role, "--model", self.args.model, "--mode", "serve",
                "--prompt", "warmup", "--placement", self.args.placement,
                "--run-id", self.run_id, "--events", self.events,
                "--ready-file", self.ready,
                "--port", str(self.args.port + self.index),
                "--accept-timeout", "1200",
                "--context-length", str(self.args.context_length),
                "--timing-method", self.args.timing_method,
                "--max-new-tokens", str(self.args.max_new_tokens)] \
            + (["--peer", "127.0.0.1"] if role == "cuda" else [])

    def start(self) -> bool:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join([
            os.path.join(ROOT, "runtime", "serving"),
            os.path.join(ROOT, "runtime", "scheduler"),
            os.path.join(ROOT, "runtime", "mccl", "src")])
        began = time.monotonic()
        for stale in (self.ready,):
            if os.path.exists(stale):
                os.remove(stale)
        self.processes["rocm"] = subprocess.Popen(
            self._command("rocm"), stdout=subprocess.DEVNULL,
            stderr=open(os.path.join(self.work, f"rocm.{self.index}.log"), "w"),
            env=environment)
        deadline = time.monotonic() + self.args.load_timeout
        while not os.path.exists(self.ready):
            if self.processes["rocm"].poll() is not None:
                self.failure = "rocm worker exited before binding"
                return False
            if time.monotonic() > deadline:
                self.failure = "rocm worker never became ready"
                return False
            time.sleep(0.5)
        self.processes["cuda"] = subprocess.Popen(
            self._command("cuda"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(os.path.join(self.work, f"cuda.{self.index}.log"), "w"),
            text=True, env=environment)

        # Cold start ends when the CUDA worker announces readiness, which it
        # does only after a warm-up forward has crossed both stages.
        deadline = time.monotonic() + self.args.load_timeout
        while True:
            if self.processes["cuda"].poll() is not None:
                self.failure = "cuda worker exited during load"
                return False
            if self._saw_event("ready", "cuda"):
                break
            if time.monotonic() > deadline:
                self.failure = "cuda worker never became ready"
                return False
            time.sleep(0.5)
        self.cold_start_s = time.monotonic() - began
        return True

    def _saw_event(self, name: str, role: str) -> bool:
        if not os.path.exists(self.events):
            return False
        with open(self.events, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") == name and \
                        record.get("workerRole") == role:
                    return True
        return False

    def ask(self, ids: List[int], label: str) -> Optional[Dict[str, Any]]:
        worker = self.processes.get("cuda")
        if worker is None or worker.poll() is not None:
            self.failure = "cuda worker exited before the request"
            return None
        began = time.monotonic()
        worker.stdin.write(json.dumps({
            "id": label, "tokenIds": ids,
            "maxNewTokens": self.args.max_new_tokens}) + "\n")
        worker.stdin.flush()
        record = self._await_request(label, began)
        return record

    def _await_request(self, label: str, began: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + self.args.request_timeout
        offset = 0
        while time.monotonic() < deadline:
            if self.processes["cuda"].poll() is not None:
                self.failure = "cuda worker exited mid-request"
                return None
            if os.path.exists(self.events):
                with open(self.events, "r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    for line in handle:
                        if not line.endswith("\n"):
                            break
                        offset += len(line.encode("utf-8"))
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("event") == "request_done" and \
                                str(record.get("requestId")) == str(label):
                            record["wallSeconds"] = time.monotonic() - began
                            return record
                        if record.get("event") == "request_failed":
                            self.failure = str(record.get("detail", ""))[:200]
                            return None
            time.sleep(0.02)
        self.failure = f"no reply within {self.args.request_timeout}s"
        return None

    def stop(self) -> None:
        worker = self.processes.get("cuda")
        if worker is not None and worker.poll() is None and worker.stdin:
            try:
                worker.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                worker.stdin.flush()
                worker.stdin.close()
            except (OSError, ValueError):
                pass
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for process in self.processes.values():
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()


def build_workloads(args) -> Dict[int, Dict[str, Any]]:
    """Identical token ids for every session, keyed by prompt length."""
    sys.path.insert(0, os.path.join(ROOT, "runtime", "serving"))
    from prompt_tokens import ids_of_length, load_tokenizer  # noqa: PLC0415

    tokenizer = load_tokenizer(args.model)
    workloads: Dict[int, Dict[str, Any]] = {}
    for length in args.prompt_lengths:
        ids, provenance = ids_of_length(tokenizer, args.prompt, length, True)
        workloads[length] = {
            "tokenIds": ids,
            "promptIdsHash": prompt_ids_hash(ids),
            "provenance": provenance,
        }
    return workloads


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish the step-3 baseline")
    parser.add_argument("--model", default="/opt/models/Mistral-Nemo-Instruct-2407")
    parser.add_argument("--placement", required=True)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--steady", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--prompt-lengths", default="8,64,256")
    parser.add_argument("--max-new-tokens", type=int, default=16)
    parser.add_argument("--prompt", default="Explain cross-vendor GPU inference")
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--timing-method", default="stream-event")
    parser.add_argument("--port", type=int, default=32200)
    parser.add_argument("--load-timeout", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--out", default="/opt/openmycelium/baseline")
    parser.add_argument("--expect-boundary", type=int, default=19,
                        help="the baseline is defined at 19; other values are "
                             "for smoke-testing the harness only")
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    args.prompt_lengths = [int(v) for v in args.prompt_lengths.split(",")]

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "sessions")
    os.makedirs(work, exist_ok=True)

    placement = json.load(open(args.placement, encoding="utf-8"))
    boundary = placement["pipeline"]["boundaryAfterLayer"]
    if boundary != args.expect_boundary:
        print(f"  refusing: expected boundaryAfterLayer="
              f"{args.expect_boundary}, manifest says {boundary}")
        return 2

    workloads = build_workloads(args)
    identity = {
        "tokenizerIdentity": tokenizer_identity(args.model, True),
        "promptIdsHash": {str(k): v["promptIdsHash"]
                          for k, v in workloads.items()},
        "decodingPolicy": "greedy-argmax (temperature 0, do_sample false)",
        "seed": args.seed,
        "attentionBackendRequested": "eager",
        "stageOrientation": placement["pipeline"].get("stageOrientation",
                                                      "cuda-first"),
        "boundaryAfterLayer": boundary,
        "placementId": placement["placementId"],
        "manifestDigest": placement["manifestDigest"],
        "modelFingerprint": placement["model"]["fingerprint"],
    }
    print(f"  baseline: boundaryAfterLayer={boundary}, "
          f"{args.sessions} sessions x {args.steady} steady-state requests")
    print(f"  prompt lengths {args.prompt_lengths}, "
          f"{args.warmups} warm-ups discarded per session\n")

    attempts: List[Dict[str, Any]] = []
    successes = 0
    attempt = 0
    rng = random.Random(args.seed)
    while successes < args.sessions and attempt < args.max_attempts:
        session = Session(args, attempt, work)
        attempt += 1
        print(f"  session attempt {attempt} (runId {session.run_id[:8]}) ...",
              flush=True)
        record: Dict[str, Any] = {
            "attempt": attempt, "runId": session.run_id,
            "gpuStateAtStart": gpu_state(),
        }
        if not session.start():
            record.update({"outcome": "failed", "phase": "startup",
                           "reason": session.failure})
            attempts.append(record)
            session.stop()
            print(f"    failed during startup: {session.failure}")
            continue
        record["coldStartSeconds"] = round(session.cold_start_s or 0.0, 2)

        # Order randomised within the session, so a length cannot be confounded
        # with position in the run.
        # Balanced across lengths, then shuffled. Sampling with replacement
        # left one session with a single request at one length, making its
        # within-session variance zero by construction and the variance split
        # an artefact of the sampling rather than a property of the runtime.
        per_length = -(-args.steady // len(args.prompt_lengths))
        steady_plan = [n for n in args.prompt_lengths for _ in range(per_length)]
        rng.shuffle(steady_plan)
        steady_plan = steady_plan[:args.steady]
        warm_plan = [rng.choice(args.prompt_lengths)
                     for _ in range(args.warmups)]
        plan = warm_plan + steady_plan
        steady: List[Dict[str, Any]] = []
        first_request: Optional[Dict[str, Any]] = None
        for index, length in enumerate(plan):
            label = f"{session.run_id[:8]}-{index}"
            reply = session.ask(workloads[length]["tokenIds"], label)
            if reply is None:
                break
            reply["promptLength"] = length
            reply["promptIdsHash"] = workloads[length]["promptIdsHash"]
            if index == 0:
                first_request = reply
            elif index >= args.warmups:
                steady.append(reply)
        record["gpuStateAtEnd"] = gpu_state()
        session.stop()

        if session.failure or len(steady) < args.steady:
            record.update({"outcome": "failed", "phase": "requests",
                           "reason": session.failure or "incomplete",
                           "steadyCollected": len(steady)})
            attempts.append(record)
            print(f"    failed during requests: {record['reason']}")
            continue

        record.update({"outcome": "complete", "firstRequest": first_request,
                       "steadyState": steady})
        attempts.append(record)
        successes += 1
        print(f"    complete: cold start {record['coldStartSeconds']}s, "
              f"{len(steady)} steady-state requests")

    dataset = {
        "datasetSchema": DATASET_SCHEMA,
        "identity": identity,
        "configuration": {k: v for k, v in vars(args).items()
                          if k not in ("out",)},
        "attempts": attempts,
        "successfulSessions": successes,
        "totalAttempts": attempt,
        "reliability": f"{successes}/{attempt}",
        "createdAt": time.time(),
    }
    dataset["statistics"] = compute_statistics(dataset)
    blob = json.dumps(dataset, sort_keys=True, separators=(",", ":")).encode()
    dataset["datasetDigest"] = hashlib.sha256(blob).hexdigest()

    path = os.path.join(args.out, "baseline.json")
    if os.path.exists(path):
        print(f"\n  refusing to overwrite the sealed dataset at {path}")
        return 3
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, sort_keys=True)
    os.chmod(path, 0o444)
    print(f"\n  sealed {path}")
    print(f"  digest {dataset['datasetDigest']}")
    report(dataset)
    return 0 if successes >= args.sessions else 1


def compute_statistics(dataset: Dict[str, Any]) -> Dict[str, Any]:
    complete = [a for a in dataset["attempts"] if a.get("outcome") == "complete"]
    lengths = sorted({r["promptLength"] for a in complete
                      for r in a["steadyState"]})
    stats: Dict[str, Any] = {"byPromptLength": {}}
    for length in lengths:
        grouped = [[r for r in a["steadyState"] if r["promptLength"] == length]
                   for a in complete]
        metrics: Dict[str, Any] = {}
        for name, extract in (
            ("ttftMs", lambda r: r.get("ttftMs")),
            ("decodeTokensPerSecond", lambda r: r.get("decodeTokensPerSecond")),
            ("cudaLayerSumMs",
             lambda r: (r.get("decomposition") or {}).get("layerSumMs")),
            ("cudaEmbeddingMs",
             lambda r: (r.get("decomposition") or {}).get("embeddingMs")),
            ("rocmLayerSumMs",
             lambda r: (r.get("breakdown") or {}).get("rocmLayerSumMs")),
            ("rocmLmHeadMs",
             lambda r: (r.get("breakdown") or {}).get("rocmLmHeadMs")),
            ("transferMs",
             lambda r: sum(filter(None, [
                 (r.get("breakdown") or {}).get("deviceToHostMs"),
                 (r.get("breakdown") or {}).get("wireMs"),
                 (r.get("breakdown") or {}).get("hostToDeviceMs")]))),
            ("unprofiledRuntimeOverheadMs",
             lambda r: (r.get("breakdown") or {}).get(
                 "unprofiledRuntimeOverheadMs")),
            ("breakdownCoverage",
             lambda r: (r.get("breakdown") or {}).get("coverage")),
        ):
            sessions = [[extract(r) for r in group if extract(r) is not None]
                        for group in grouped]
            sessions = [s for s in sessions if s]
            if sessions:
                metrics[name] = summarise(sessions)
        stats["byPromptLength"][str(length)] = metrics

    cold = [[a["coldStartSeconds"]] for a in complete
            if a.get("coldStartSeconds")]
    if cold:
        stats["coldStartSeconds"] = summarise(cold)
    first = [[a["firstRequest"]["ttftMs"]] for a in complete
             if a.get("firstRequest", {}).get("ttftMs")]
    if first:
        stats["firstRequestTtftMs"] = summarise(first)
    return stats


def report(dataset: Dict[str, Any]) -> None:
    stats = dataset["statistics"]
    print(f"\n  reliability: {dataset['reliability']} sessions complete")
    cold = stats.get("coldStartSeconds", {})
    if cold:
        print(f"  cold start   p50 {cold.get('point')} s  "
              f"95% CI {cold.get('ci95')}   (separate population)")
    first = stats.get("firstRequestTtftMs", {})
    if first:
        print(f"  first request TTFT p50 {first.get('point')} ms  "
              f"95% CI {first.get('ci95')}   (separate population)")
    print()
    print(f"  steady state, boundaryAfterLayer="
          f"{dataset['identity']['boundaryAfterLayer']}, eager, cuda-first")
    for length, metrics in sorted(stats["byPromptLength"].items(),
                                  key=lambda kv: int(kv[0])):
        print(f"\n    prompt {length} tokens")
        for name, summary in metrics.items():
            variance = summary.get("variance", {})
            print(f"      {name:<30} p50 {summary.get('point')}  "
                  f"95% CI {summary.get('ci95')}")
            if variance:
                print(f"      {'':<30} within-session sd "
                      f"{variance.get('withinSessionStdDev')}  "
                      f"between-session sd "
                      f"{variance.get('betweenSessionStdDev')}  "
                      f"between share {variance.get('betweenSessionShare')}")
    print("\n  No optimisation claim follows from this dataset. It is the noise")
    print("  floor against which later Intelligence decisions are judged.")


if __name__ == "__main__":
    raise SystemExit(main())
