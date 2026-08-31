"""`openmycelium console` -- a local operator console for one machine.

Binds 127.0.0.1 only. This release has no authentication, so it must not be
reachable from anywhere else; `--host` is deliberately absent rather than
defaulted, so exposing it takes a code change and a decision, not a flag.

What it can do: read everything, start one inference run, and stop the run it
started. What it cannot do: pull, import or remove models, provision, change
configuration, manage the API server, or hold a token. That surface is small on
purpose -- the console is new, the runtime it drives took a long time to
qualify, and a console bug should not be able to damage a qualified machine.

The Run gate is the interesting part. Before a run is offered, readiness must
pass, the model must verify, and a placement must compile; the expected GPU
allocation is shown before anything starts; and Stop matches on run id, process
start time and placement identity together, because a PID alone is reused by
the kernel and a stale record that happens to name a live process would
otherwise let the console kill something it never launched.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _relative in (".", "../serving", "../fabric", "../scheduler"):
    _path = os.path.abspath(os.path.join(_HERE, _relative))
    if _path not in sys.path:
        sys.path.insert(0, _path)

import console_service as service  # noqa: E402
from coordinator import TOKEN_FRAME  # noqa: E402

DEFAULT_PORT = 11501
ASSETS = os.path.join(_HERE, "console_assets")
CONSOLE_OWNER = "console"


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for JSON."""
    print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------- run manager

class RunManager:
    """Owns the single run this console is allowed to start.

    One at a time, because the runtime serves one request at a time and a
    console that could queue work would be promising something the runtime
    does not do.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.run_id: str = ""
        self.placement_id: str = ""
        self.model: str = ""
        self.started_at: float = 0.0
        self.subscribers: List["queue.Queue[Dict[str, Any]]"] = []
        self.transcript: List[Dict[str, Any]] = []
        self.finished: bool = False
        self.exit_code: Optional[int] = None

    # -- event fan-out --------------------------------------------------
    def subscribe(self) -> "queue.Queue[Dict[str, Any]]":
        channel: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self.lock:
            for event in self.transcript:
                channel.put(event)
            self.subscribers.append(channel)
        return channel

    def unsubscribe(self, channel) -> None:
        with self.lock:
            if channel in self.subscribers:
                self.subscribers.remove(channel)

    def emit(self, kind: str, **fields: Any) -> None:
        event = {"kind": kind, "at": time.time(), "runId": self.run_id, **fields}
        with self.lock:
            self.transcript.append(event)
            channels = list(self.subscribers)
        for channel in channels:
            channel.put(event)

    # -- lifecycle ------------------------------------------------------
    def active(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, model: str, prompt: str, max_new_tokens: int,
              placement_id: str) -> Dict[str, Any]:
        self.run_id = str(uuid.uuid4())
        self.placement_id = placement_id
        self.model = model
        self.started_at = time.time()
        self.transcript = []
        self.finished = False
        self.exit_code = None

        command = [
            sys.executable, os.path.join(_HERE, "coordinator.py"),
            "--model", model,
            "--prompt", prompt,
            "--max-new-tokens", str(int(max_new_tokens)),
            "--json",
        ]
        # A fixed argument list. No shell, so a prompt containing shell
        # metacharacters is a prompt and nothing else.
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        self.emit("started", model=model, placementId=placement_id,
                  pid=self.process.pid, maxNewTokens=max_new_tokens)
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        return {"runId": self.run_id, "pid": self.process.pid}

    def _pump_stdout(self) -> None:
        assert self.process and self.process.stdout
        buffered: List[str] = []
        for line in self.process.stdout:
            buffered.append(line)
            self.emit("stdout", line=line.rstrip("\n"))
        text = "".join(buffered)
        start = text.find("{")
        if start >= 0:
            try:
                self.emit("result", result=json.loads(text[start:]))
            except ValueError:
                pass
        code = self.process.wait()
        self.exit_code = code
        self.finished = True
        self.emit("finished", exitCode=code)

    def _pump_stderr(self) -> None:
        """Progress lines and framed tokens share stderr; they are not the same.

        The coordinator frames each streamed token under `--json`, so this
        classifies on the frame rather than on which stream the line arrived by.
        Classifying by stream is what sent generated text to the worker log the
        moment the token stream moved off stdout.
        """
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            if stripped.startswith(TOKEN_FRAME):
                try:
                    payload = json.loads(stripped[len(TOKEN_FRAME):])
                except ValueError:
                    self.emit("worker", line=stripped)
                    continue
                self.emit("token", text=payload.get("text", ""))
                continue
            self.emit("worker", line=stripped)

    def stop(self) -> Dict[str, Any]:
        """Stop only what this console started, identified by more than a PID."""
        import control

        if self.process is None:
            return {"stopped": False, "detail": "this console has not started a run"}

        pid = self.process.pid
        start_ticks = control.process_start_ticks(pid)
        matched = []
        for record in control.list_records():
            if record.get("runId") and record.get("placementId") == self.placement_id:
                matched.append(record)

        if self.process.poll() is not None:
            return {"stopped": True, "alreadyExited": True,
                    "exitCode": self.process.returncode,
                    "matchedRecords": len(matched)}

        # Ask the runtime first; only escalate if it does not go.
        for record in matched:
            try:
                control.request_stop(record, timeout=20.0)
            except Exception as error:                        # noqa: BLE001
                self.emit("worker", line=f"stop request failed: {error}")

        self.emit("stopping", pid=pid, matchedRecords=len(matched))
        deadline = time.time() + 25
        while time.time() < deadline and self.process.poll() is None:
            time.sleep(0.5)

        escalated = False
        if self.process.poll() is None:
            # Confirm identity again before signalling: a PID can be reused,
            # and the start time is what makes it the same process.
            if control.alive(pid, start_ticks):
                self.process.terminate()
                escalated = True
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()

        self.finished = True
        self.exit_code = self.process.returncode
        self.emit("finished", exitCode=self.exit_code, stopped=True)
        return {"stopped": True, "escalated": escalated,
                "exitCode": self.exit_code,
                "identity": {"pid": pid, "startTicks": start_ticks,
                             "placementId": self.placement_id,
                             "runId": self.run_id},
                "matchedRecords": len(matched)}


RUNS = RunManager()


# ------------------------------------------------------------------- gating

def _qualification_gate(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Has anything actually measured this exact situation on this machine?

    Answered from the compiled placement, so it is the situation a run would
    execute rather than a guess from the model name. An explicit override is
    reported as passing *and* named, because a gate that quietly went green on
    an override would hide the one thing worth seeing.
    """
    gate = {"id": "qualification", "label": "Adapter qualified here",
            "ok": False, "detail": "no placement to evaluate"}
    if not plan.get("ok"):
        return gate
    try:
        from adapters.qualification import (find_record,
                                            override_from_environment,
                                            situation_from_manifest)
        situation = situation_from_manifest(plan.get("manifest") or {})
        record = find_record(situation)
        if record is not None:
            gate.update(ok=True,
                        detail=f"record {str(record.get('recordId'))[:16]}, "
                               f"{situation['adapterId']}@"
                               f"{situation['adapterVersion']}")
            return gate
        override = override_from_environment()
        if override:
            gate.update(ok=True,
                        detail=f"UNQUALIFIED, overridden by "
                               f"{override.get('actor')} "
                               f"({override.get('mode')})")
            return gate
        # Naming the command matters: this is the one gate an operator cannot
        # clear from the console, so a detail that only says "run the hardware
        # gate" leaves them with no next step.
        # The command has to be one that works. This text used to name
        # `run` without `--json`, and `run --json` used to stream the decoded
        # tokens to stdout ahead of the document, so the file it told you to
        # create could not be parsed -- and `qualify record` then refused it a
        # second time for reporting no `failures`. Both are fixed; the wording
        # is exact so it stays followable.
        gate["detail"] = (
            "no record covers this checkpoint, these runtimes and this device "
            "pair. Qualify it from a terminal, in two steps: "
            "OM_QUALIFICATION_MODE=qualify OM_QUALIFICATION_ACTOR=<you> "
            "openmycelium run --model <MODEL> --prompt hello "
            "--max-new-tokens 24 --json > gate.json   then   "
            "OM_QUALIFICATION_MODE=qualify OM_QUALIFICATION_ACTOR=<you> "
            "openmycelium qualify record --model <MODEL> --evidence gate.json")
    except Exception as error:                                # noqa: BLE001
        gate["detail"] = f"could not be evaluated: {str(error)[:120]}"
    return gate


def run_readiness(model: str) -> Dict[str, Any]:
    """Everything that must be true before a run may be offered."""
    gates: List[Dict[str, Any]] = []

    ready = service.readiness()
    gates.append({"id": "doctor", "label": "Readiness checks",
                  "ok": bool(ready.get("ready")),
                  "detail": "all checks passed" if ready.get("ready")
                            else "one or more checks failed"})

    verified = service.verify_model(model) if model else {"ok": False}
    gates.append({"id": "verify", "label": "Model verified",
                  "ok": bool(verified.get("ok")),
                  "detail": (f"{len(verified.get('shards', []))} shard(s) complete"
                             if verified.get("ok")
                             else "; ".join(verified.get("problems", []))
                                  or verified.get("detail", "not verified"))})

    plan = service.placement(model) if model else {"ok": False}
    gates.append({"id": "placement", "label": "Placement compiles",
                  "ok": bool(plan.get("ok")),
                  "detail": (f"boundary after layer "
                             f"{plan.get('pipeline', {}).get('boundaryAfterLayer')}, "
                             f"{plan.get('totalTensors')} tensors, "
                             f"overlap {plan.get('overlapCount')}"
                             if plan.get("ok") else plan.get("detail", ""))})

    # Rendered by iteration like every other gate, so this reaches the operator
    # without a frontend change -- and it tells them *before* they press Run
    # what the coordinator would otherwise refuse afterwards.
    gates.append(_qualification_gate(plan))

    busy = service.workloads()
    idle = not busy.get("busy") and not RUNS.active()
    gates.append({"id": "idle", "label": "No other workload active",
                  "ok": idle,
                  "detail": "idle" if idle
                            else f"{len(busy.get('running', []))} workload(s) running"})

    allocation = None
    if plan.get("ok"):
        allocation = [{
            "role": stage["role"],
            "runtime": stage["runtime"],
            "layers": stage["layers"],
            "weightBytes": stage["weightBytes"],
            "budgetBytes": stage["budgetBytes"],
            "deviceIdentity": stage["deviceIdentity"],
        } for stage in plan.get("stages", [])]

    return service.envelope("runGate", {
        "model": model,
        "canRun": all(gate["ok"] for gate in gates),
        "gates": gates,
        "expectedAllocation": allocation,
        "placementId": plan.get("placementId"),
        "note": "Starting a run occupies both GPUs. The runtime serves one "
                "request at a time.",
    })


# -------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "OpenMyceliumConsole"

    def log_message(self, fmt: str, *args: Any) -> None:
        log("  " + fmt % args)

    # -- helpers --------------------------------------------------------
    def _json(self, payload: Dict[str, Any], status: int = 200) -> bool:
        """Write a JSON response. False means the client had already gone.

        A browser that navigates away or reloads mid-request closes the socket
        while the server is still writing. That is normal, not an error, and it
        must not produce a traceback -- especially not a second one from trying
        to report the first down the same dead socket.
        """
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
            return False

    def _asset(self, name: str) -> None:
        safe = os.path.normpath(name).lstrip("/")
        if safe.startswith(".."):
            self._json({"error": "not found"}, 404)
            return
        path = os.path.join(ASSETS, safe)
        if not os.path.isfile(path):
            self._json({"error": "not found", "path": safe}, 404)
            return
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            body = handle.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    # -- routes ---------------------------------------------------------
    def do_GET(self) -> None:                                 # noqa: N802
        path = self.path.split("?", 1)[0]
        query = {}
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    query[key] = value

        try:
            if path in ("/", "/index.html"):
                self._asset("index.html")
            elif path == "/favicon.ico":
                # Browsers ask for this on every page load. Serving the mark
                # is one line; letting it 404 forever is log noise that hides
                # real problems.
                self._asset("openmycelium-logo.png")
            elif path.startswith("/assets/"):
                self._asset(path[len("/assets/"):])
            elif path == "/api/readiness":
                self._json(service.readiness())
            elif path == "/api/provenance":
                self._json(service.provenance())
            elif path == "/api/fabric":
                self._json(service.fabric(use_cache=query.get("refresh") != "1"))
            elif path == "/api/models":
                self._json(service.models())
            elif path == "/api/verify":
                self._json(service.verify_model(query.get("model", "")))
            elif path == "/api/placement":
                self._json(service.placement(query.get("model", "")))
            elif path == "/api/workloads":
                self._json(service.workloads())
            elif path == "/api/residency":
                self._json(service.gpu_residency())
            elif path == "/api/run/gate":
                self._json(run_readiness(query.get("model", "")))
            elif path == "/api/run/events":
                self._events()
            else:
                self._json({"error": "not found", "path": path}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The client went away mid-response. Nothing to report and nowhere
            # to report it to.
            self.close_connection = True
        except Exception as error:                            # noqa: BLE001
            log(f"  error handling {path}: {error}")
            self._json({"error": type(error).__name__, "detail": str(error)[:300]},
                       500)

    def do_POST(self) -> None:                                # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/run/start":
                self._start()
            elif path == "/api/run/stop":
                self._json(service.envelope("stop", RUNS.stop()))
            else:
                self._json({"error": "not found", "path": path}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as error:                            # noqa: BLE001
            log(f"  error handling {path}: {error}")
            self._json({"error": type(error).__name__, "detail": str(error)[:300]},
                       500)

    def _start(self) -> None:
        body = self._body()
        model = str(body.get("model") or "")
        prompt = str(body.get("prompt") or "")
        tokens = int(body.get("maxNewTokens") or 64)
        if not model or not prompt:
            self._json({"error": "model and prompt are required"}, 400)
            return
        if RUNS.active():
            self._json({"error": "a run is already active",
                        "runId": RUNS.run_id}, 409)
            return
        gate = run_readiness(model)
        if not gate.get("canRun"):
            self._json({"error": "the run gate refused",
                        "gate": gate}, 409)
            return
        started = RUNS.start(model, prompt, tokens, gate.get("placementId") or "")
        self._json(service.envelope("runStarted", {**started, "gate": gate}))

    def _events(self) -> None:
        """Server-sent events: tokens, worker lines, lifecycle."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        channel = RUNS.subscribe()
        try:
            while True:
                try:
                    event = channel.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            RUNS.unsubscribe(channel)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        """A disconnected browser is not a server error.

        The default prints a full traceback for every dropped connection,
        which buries anything that actually matters.
        """
        kind = sys.exc_info()[0]
        if kind and issubclass(kind, (BrokenPipeError, ConnectionResetError,
                                      ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local operator console for one machine")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true",
                        help="print the URL and nothing else on stdout")
    args = parser.parse_args()

    if not os.path.isdir(ASSETS):
        log(f"  console assets are missing from {ASSETS}")
        return 70

    # 127.0.0.1 only. There is no --host: this release has no authentication,
    # and a flag would make exposing it a typo away.
    server = ConsoleServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    log("")
    log("  openmycelium console")
    log(f"    {url}")
    log("    loopback only, no authentication in this release")
    log("    read-only, plus one inference run and stopping that run")
    log("")
    if args.open:
        print(json.dumps({"schemaVersion": service.CONSOLE_SCHEMA_VERSION,
                          "url": url}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n  stopping")
        if RUNS.active():
            RUNS.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
