"""The coordinator: one process that owns both stage workers and their health.

It runs inside WSL, beneath a single foreground `wsl.exe` held open by the
Windows launcher for the model's lifetime. That arrangement is not a style
choice. Measured behaviour on this machine: when the last Windows-side client
disconnects, WSL2 terminates the VM and takes every Linux process with it --
including ones started with `setsid nohup`, which died within twenty seconds of
the client exiting. A detached Linux process cannot keep this runtime alive, so
the Windows client is the runtime's lifetime and is treated as such.

The coordinator therefore:

* starts the ROCm stage, waits for it to bind and signal readiness, then starts
  the CUDA stage;
* drives a health state machine from worker events and heartbeats, never from
  the absence of log output -- a silent worker is dead, not busy;
* streams generated text to the user as tokens arrive;
* reports failure with a reason, and exits non-zero, instead of hanging.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from audit import new_run_id  # noqa: E402
from control import ControlRecord, boot_of, remove_if_matches, update_state  # noqa: E402
from event_gate import EventGate  # noqa: E402
from paths import runtime_root, worker_driver, worker_pythonpath  # noqa: E402
from health import (DEGRADED, FAILED, LOADING_CUDA, LOADING_ROCM,  # noqa: E402
                    QUALIFYING, READY, RECOVERING, STARTING, Health)

DEFAULT_ROOT = runtime_root()


def wsl_path(path: str) -> str:
    """Accept a Windows path from the launcher and use it from inside WSL.

    The user types the path they can see in Explorer; the workers need the
    /mnt mount. Loading weights over /mnt/c is markedly slower than from the
    Linux filesystem, so a slow first load on a Windows path is expected and is
    reported rather than hidden.
    """
    if len(path) > 2 and path[1] == ":" and path[2] in "\\/":
        drive, rest = path[0].lower(), path[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}".rstrip("/")
    return path


class Tail:
    """Follow a JSONL event file that workers append to."""

    def __init__(self, path: str):
        self.path = path
        self.offset = 0

    def poll(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as handle:
            handle.seek(self.offset)
            for line in handle:
                if not line.endswith("\n"):
                    break
                self.offset += len(line.encode("utf-8"))
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out


class Coordinator:
    def __init__(self, args):
        self.args = args
        # The coordinator issues the run id. Depending on a caller to have set
        # one makes construction fail for anything that builds args directly,
        # and the coordinator is the component the contract names as the
        # authority anyway.
        if not getattr(args, "run_id", None):
            args.run_id = new_run_id()
        self.health = Health(heartbeat_timeout=args.heartbeat_timeout)
        self.health.register("rocm")
        self.health.register("cuda")
        self.processes: Dict[str, subprocess.Popen] = {}
        self.events_path = os.path.join(args.work_dir, "events.jsonl")
        self.ready_path = os.path.join(args.work_dir, f"ready.{args.port}")
        self.placement_path = args.placement
        self.tail = Tail(self.events_path)
        # Admission happens before the health model sees anything, so a stale
        # or foreign record cannot move the state an operator acts on.
        self.gate = EventGate(run_id=args.run_id)
        if getattr(args, "placement_manifest", None):
            self.gate.expect_placement(args.placement_manifest)
        self.text_parts: List[str] = []
        self.stopping = False
        # Published so `ps` can find this runtime and `stop` can end it without
        # guessing at a PID. Written before any worker starts, so a runtime that
        # dies during load is still discoverable as a stale record.
        self.control = ControlRecord(
            run_id=args.run_id, pid=os.getpid(),
            start_ticks=None, boot_id="", model=args.model,
            model_name=os.path.basename(str(args.model).rstrip("/")),
            placement_id=str(getattr(args, "placement_manifest", {}).get(
                "placementId", "")),
            manifest_digest=str(getattr(args, "placement_manifest", {}).get(
                "manifestDigest", "")),
            port=int(getattr(args, "port", 0)),
            work_dir=str(getattr(args, "work_dir", "")),
            command=getattr(args, "invoked_as", "run"))
        self.control_path = self.control.write()
        for path in (self.events_path, self.ready_path):
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------- workers
    def _command(self, role: str) -> List[str]:
        python = self.args.rocm_python if role == "rocm" else self.args.cuda_python
        driver = worker_driver()
        command = [python, driver, "--role", role, "--model", self.args.model,
                   "--mode", "generate", "--prompt", self.args.prompt,
                   "--max-new-tokens", str(self.args.max_new_tokens),
                   "--port", str(self.args.port), "--events", self.events_path,
                   "--ready-file", self.ready_path,
                   "--placement", self.placement_path,
                   "--run-id", self.args.run_id,
                   "--accept-timeout", str(self.args.load_timeout),
                   "--context-length", str(self.args.context_length)]
        if role == "cuda":
            command += ["--peer", "127.0.0.1"]
        if self.args.allow_cpu:
            command += ["--allow-cpu"]
        return command

    def _spawn(self, role: str) -> None:
        log = open(os.path.join(self.args.work_dir, f"{role}.log"), "w",
                   encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = worker_pythonpath(
            environment.get("PYTHONPATH", ""))
        ledger = getattr(getattr(self.args, "resolved_config", None),
                         "ledger", "") or getattr(self.args, "ledger", "")
        if ledger:
            environment.setdefault("OM_XVENDOR_LEDGER", ledger)
        self.processes[role] = subprocess.Popen(
            self._command(role), stdout=subprocess.PIPE, stderr=log,
            text=True, env=environment, start_new_session=True)

    def _reap(self) -> None:
        for role, process in list(self.processes.items()):
            code = process.poll()
            if code is not None:
                self.health.worker_exited(role, code)

    def _publish_state(self) -> None:
        summary = self.health.summary()
        update_state(self.control_path, summary["state"],
                     workers={name: {"stage": w["stage"],
                                     "device": w["device"],
                                     "residentMiB": w["residentMiB"]}
                              for name, w in summary["workers"].items()})

    def terminate(self) -> None:
        self.stopping = True
        # The record is removed only if it still names this run: a later attempt
        # may have replaced it, and deleting that would hide a live runtime.
        try:
            update_state(self.control_path, "STOPPING")
        except Exception:                                     # noqa: BLE001
            pass
        for role, process in self.processes.items():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in self.processes.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
        remove_if_matches(self.control_path, self.args.run_id)

    # -------------------------------------------------------------- events
    def _handle(self, event: Dict[str, Any]) -> None:
        rejection = self.gate.admit(event)
        if rejection is not None:
            self._say(f"  [audit] rejected event: {rejection}")
            return
        kind = event.get("event")
        if event.get("placementValidation") == "failed":
            self._say(f"\n[{event.get('workerRole')}] placement rejected "
                      f"({event.get('failurePhase')}): "
                      f"{event.get('failureReason', '')}")
            self.health.observe({"worker": event.get("workerRole"),
                                 "event": "failed",
                                 "detail": event.get("failureReason", "")})
            return
        self.health.observe(event)
        if kind == "token":
            piece = event.get("text", "")
            self.text_parts.append(piece)
            if not self.args.quiet:
                sys.stdout.write(piece)
                sys.stdout.flush()
        elif kind == "connected":
            self._say(f"[{QUALIFYING}] transport connected; warming both "
                      f"runtimes (first BF16 kernels are slow to select)")
        elif kind == "warmed":
            self._say(f"[{QUALIFYING}] runtimes warmed in "
                      f"{event.get('warmupMs', 0) / 1000:.1f} s")
        elif kind == "failed":
            self._say(f"\n[{event.get('worker')}] failed: "
                      f"{event.get('detail', 'no detail')}")

    def _advance(self) -> None:
        """Move the runtime state only when the evidence supports it."""
        rocm = self.health.workers["rocm"]
        cuda = self.health.workers["cuda"]
        state = self.health.state
        if state == STARTING and rocm.stage == LOADING_ROCM:
            self.health.to(LOADING_ROCM, "ROCm stage loading weights")
        elif state == LOADING_ROCM and cuda.stage in (LOADING_CUDA, QUALIFYING):
            self.health.to(LOADING_CUDA, "CUDA stage loading weights")
        elif state in (LOADING_CUDA, LOADING_ROCM) and rocm.loaded and cuda.loaded:
            self.health.to(QUALIFYING, "both stages resident, checking transport")
        elif state == QUALIFYING and rocm.stage == READY and cuda.stage == READY:
            self.health.to(READY, "both stages resident and connected")
        if any(w.stage == FAILED for w in self.health.workers.values()) \
                and self.health.state not in (FAILED, DEGRADED):
            reason = "; ".join(f"{w.name}: {w.detail}"
                               for w in self.health.workers.values()
                               if w.stage == FAILED)
            self.health.to(DEGRADED if self.health.state == READY else FAILED,
                           reason)

    def _say(self, message: str) -> None:
        if not self.args.quiet:
            print(message, file=sys.stderr, flush=True)

    # ----------------------------------------------------------------- run
    def run(self) -> int:
        placement = self.args.placement_manifest
        self._say(
            f"[{STARTING}] placement {placement['placementId']} "
            f"boundary={placement['pipeline']['boundaryAfterLayer']} "
            f"digest={placement['manifestDigest'][:12]}")
        self._say(f"[{STARTING}] launching ROCm stage (second half of the model)")
        self._spawn("rocm")

        deadline = time.monotonic() + self.args.load_timeout
        cuda_started = False
        last_state = self.health.state

        while True:
            for event in self.tail.poll():
                self._handle(event)
            self._reap()
            self._advance()
            self.health.sweep()

            if self.health.state != last_state:
                last_state = self.health.state
                self._say(f"[{last_state}] {self._describe()}")
                self._publish_state()

            if not cuda_started and os.path.exists(self.ready_path):
                self._say(f"[{LOADING_CUDA}] ROCm stage bound; launching CUDA stage")
                self._spawn("cuda")
                cuda_started = True
                deadline = time.monotonic() + self.args.load_timeout

            if self.health.state == FAILED:
                self._say(f"[{FAILED}] {json.dumps(self.health.summary())}")
                self.terminate()
                return 2

            finished = all(p.poll() is not None for p in self.processes.values())
            if finished and len(self.processes) == 2:
                break

            if time.monotonic() > deadline and self.health.state != READY:
                self.health.to(FAILED, "timed out before both stages were ready")
                self._say(f"[{FAILED}] timed out; {json.dumps(self.health.summary())}")
                self.terminate()
                return 3
            time.sleep(0.05)

        for event in self.tail.poll():
            self._handle(event)
        return self._finish()

    def _describe(self) -> str:
        parts = []
        for name, worker in self.health.workers.items():
            piece = f"{name}={worker.stage}"
            if worker.resident_mib:
                piece += f" ({worker.resident_mib:.0f} MiB)"
            parts.append(piece)
        return "  ".join(parts)

    def _finish(self) -> int:
        result = None
        process = self.processes.get("cuda")
        if process is not None and process.stdout is not None:
            for line in process.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        result = json.loads(line)
                    except json.JSONDecodeError:
                        pass
        codes = {role: p.returncode for role, p in self.processes.items()}
        if not self.args.quiet:
            print()
        if result is None or "generate" not in result:
            self._say(f"[{FAILED}] no result from the CUDA stage; exit codes {codes}")
            for role in ("cuda", "rocm"):
                path = os.path.join(self.args.work_dir, f"{role}.log")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as handle:
                        tail = [l for l in handle.read().splitlines()
                                if "NumPy" not in l and "conversion_method" not in l]
                    if tail:
                        self._say(f"--- {role} ---\n" + "\n".join(tail[-8:]))
            return 4
        self._report(result, codes)
        return 0 if all(code == 0 for code in codes.values()) else 5

    def _report(self, result: Dict[str, Any], codes: Dict[str, int]) -> None:
        generate = result["generate"]
        workers = self.health.summary()["workers"]
        if self.args.json:
            print(json.dumps({"result": generate, "health": self.health.summary(),
                              "exitCodes": codes,
                              "placement": self.args.placement_manifest}, indent=2))
            return
        inter = generate.get("interTokenMs", {})
        lines = [
            "",
            f"  model            {os.path.basename(self.args.model)}",
            f"  placement        {self.args.placement_manifest['placementId']}  "
            f"{self.args.placement_manifest['manifestDigest'][:12]}",
            f"  CUDA stage       {workers['cuda']['device']}  "
            f"{workers['cuda']['residentMiB']:.0f} MiB resident",
            f"  ROCm stage       {workers['rocm']['device']}  "
            f"{workers['rocm']['residentMiB']:.0f} MiB resident",
            f"  boundary         after layer {result.get('boundary')}",
            f"  decode           greedy (temperature 0, do_sample false)",
            f"  prompt tokens    {generate['promptTokens']}",
            f"  generated        {len(generate['generatedTokens'])} tokens "
            f"({generate['stopReason']})",
            f"  warmup           {generate.get('warmupMs', 0):.0f} ms "
            f"(one cold forward per runtime, before READY)",
            f"  TTFT             {generate['ttftMs']:.0f} ms",
            f"  prefill          {generate.get('prefillTokensPerSecond')} tok/s",
            f"  decode rate      {generate.get('decodeTokensPerSecond')} tok/s",
        ]
        if inter:
            lines.append(f"  inter-token      p50 {inter['p50']:.1f} ms  "
                         f"p95 {inter['p95']:.1f} ms  p99 {inter['p99']:.1f} ms")
        cache = generate.get("cacheAtEnd", {})
        if cache:
            lines.append(f"  KV cache (ROCm)  {cache.get('layerCount')} layers, "
                         f"length {cache.get('uniformLength')}, "
                         f"{cache.get('bytes', 0) / (1 << 20):.1f} MiB")
        lines.append(f"  runtime state    {self.health.state}")
        print("\n".join(lines))


def prepare_placement(args) -> None:
    """Issue one authoritative placement before either worker is launched.

    Both the one-shot coordinator and the persistent chat runtime call this
    function. Workers validate and execute the resulting manifest; they never
    independently choose a split.
    """
    serving_dir = os.path.join(runtime_root(), "runtime", "serving")
    fabric_dir = os.path.join(runtime_root(), "runtime", "fabric")
    scheduler_dir = os.path.join(runtime_root(), "runtime", "scheduler")
    for path in (serving_dir, fabric_dir, scheduler_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    from fabric import discover  # noqa: PLC0415
    from model_inspect import parse_size  # noqa: PLC0415
    from placement import create_placement, write_placement  # noqa: PLC0415

    if args.allow_cpu:
        fabric_report = {"schemaVersion": 2, "identitiesUnique": True,
                         "devices": [], "probedAt": time.time(),
                         "fromCache": False}
        cuda_budget = rocm_budget = parse_size("48MiB")
    else:
        fabric_report = discover(
            {"nvidia": args.cuda_python, "amd": args.rocm_python},
            use_cache=False)
        cuda_budget = parse_size(args.cuda_budget)
        rocm_budget = parse_size(args.rocm_budget)
    args.placement_manifest = create_placement(
        args.model, fabric_report, cuda_budget, rocm_budget,
        args.context_length, allow_cpu=args.allow_cpu)
    args.placement = os.path.join(args.work_dir, "placement.json")
    write_placement(args.placement, args.placement_manifest)

    # Qualification, on the execution path only. `openmycelium plan` compiles a
    # placement for an unqualified situation quite deliberately -- you have to
    # be able to look at what would run before you can qualify it. What must not
    # happen is executing it, and this function is what every executing entry
    # point (run, chat, serve, console) calls before a worker exists.
    from placement import require_qualified_manifest  # noqa: PLC0415
    args.qualification = require_qualified_manifest(args.placement_manifest)

    args.safety_observer = _shadow_observe(args, fabric_report)


def _shadow_observe(args, fabric_report: Dict[str, Any]):
    """Gate D.2: watch, record, change nothing. Returns the observer or None.

    Off by default and off unless `OM_SAFETY_MODE=shadow` says otherwise, in
    which case this reads the Fabric snapshot that was *already* taken -- it
    issues no device query of its own, so it cannot contend with the workload it
    is watching.

    `observe()` returns nothing on purpose. A return value is how an observer
    becomes a decision-maker: the first caller to branch on it turns shadow mode
    into enforcement without anyone deciding to. Nothing here inspects a result,
    and a failure inside it is swallowed -- an observer that can stop a
    production run is not an observer.
    """
    # The off path is one dictionary lookup and a return: no sys.path change,
    # no import, nothing loaded. "Preserves current behaviour exactly" has to
    # mean exactly, and importing a module to discover you are switched off is
    # already a difference. Any value at all defers to safety_mode(), which owns
    # the off/shadow/typo decision -- so the rule lives in one place.
    if not os.environ.get("OM_SAFETY_MODE"):
        return None

    safety = os.path.join(runtime_root(), "runtime", "safety")
    if safety not in sys.path:
        sys.path.insert(0, safety)
    try:
        from shadow import MODE_OFF, ShadowObserver, safety_mode  # noqa: PLC0415
        mode = safety_mode()
        if mode == MODE_OFF:
            return None

        from audit import boot_id  # noqa: PLC0415

        # Its own file, never the production audit trail. Two reasons: the audit
        # writer refuses events before a manifest is bound, which is exactly when
        # this observes; and shadow mode must not alter the stream a real run's
        # evidence is read from. A separate file is preserved evidence that
        # cannot be mistaken for something the runtime acted on.
        path = os.path.join(args.work_dir, "safety-shadow.jsonl")

        def record_observation(record):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            print(f"  [safety/shadow] {record['safetyPhase']}: "
                  f"would {record['wouldAction']} "
                  f"({record['wouldTransition']}) -- {record['wouldDetail']}",
                  file=sys.stderr)

        observer = ShadowObserver(mode=mode, boot_id=boot_id(),
                                  emit=record_observation)
        observer.observe("admission", fabric_report,
                         manifest=args.placement_manifest, force=True)
        return observer
    except Exception as error:                                # noqa: BLE001
        print(f"  [safety/shadow] observation skipped: "
              f"{type(error).__name__}: {error}", file=sys.stderr)
        return None

    # One coordinator attempt. Correlation metadata only: independent of the
    # placement id and excluded from the manifest digest, so re-running the same
    # placement produces a new run id while the digest is unchanged.
    if not getattr(args, "run_id", None):
        args.run_id = new_run_id()   # idempotent; Coordinator also guarantees it



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
        description="Run one model across a CUDA and a ROCm GPU")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--port", type=int, default=31970)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--cuda-budget", default="14GiB")
    parser.add_argument("--rocm-budget", default="14GiB")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--work-dir", default="/opt/openmycelium")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--load-timeout", type=float, default=1800.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=1800.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true",
                        help="smoke-test the supervisor without GPUs")
    args = parser.parse_args()
    _apply_config(args)
    sys.path.insert(0, _HERE)
    from models import resolve_verbose  # noqa: PLC0415

    # A short name, a Linux path, or the Windows path the user can see in
    # Explorer -- all three resolve here, so nobody has to know about /mnt.
    resolved = resolve_verbose(args.model, sys.stderr)
    if resolved is None:
        print(f"  no model found for {args.model!r}", file=sys.stderr)
        print("  try:  openmycelium models list", file=sys.stderr)
        print('  or a full path:  --model "C:\\path\\to\\model"', file=sys.stderr)
        return 66
    args.model = resolved
    os.makedirs(args.work_dir, exist_ok=True)

    try:
        prepare_placement(args)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"  placement refused: {error}", file=sys.stderr)
        return 65

    coordinator = Coordinator(args)

    def stop(signum, frame):                                   # noqa: ARG001
        coordinator._say("\ninterrupted; stopping both stages")
        coordinator.terminate()
        raise SystemExit(130)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        return coordinator.run()
    finally:
        coordinator.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
