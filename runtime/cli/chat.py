"""`openmycelium chat` -- load once, then prompt as often as you like.

The one-shot `run` command reloads 22.8 GiB for every prompt, which on this
machine measured 208 seconds of loading for 6 seconds of generation. This keeps
both stages resident and sends each prompt down the already-open pipeline, so
everything after the first prompt costs only prefill and decode.

The weights live for as long as this process does, which -- because WSL2 stops
the VM when its last Windows client goes away -- means for as long as the
terminal window stays open. That constraint is stated rather than worked around,
since the measured alternative was both workers dying silently.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from coordinator import Coordinator, prepare_placement  # noqa: E402
from paths import runtime_root, worker_pythonpath  # noqa: E402
from health import FAILED, READY  # noqa: E402


class ChatSession(Coordinator):
    """A coordinator whose workers stay up, taking prompts one after another."""

    def __init__(self, args):
        super().__init__(args)
        self.request_id = 0
        self.current: List[str] = []
        self.done = False
        self.last_summary: Optional[Dict[str, Any]] = None
        #: Set by the HTTP server so tokens reach the client as they arrive
        #: rather than only the terminal.
        self.stream_callback = None

    def _command(self, role: str) -> List[str]:
        command = super()._command(role)
        # The serving loop differs only on the sending side; the receiver is
        # already request-agnostic and simply processes whatever arrives.
        return [("serve" if (role == "cuda" and part == "generate") else part)
                for part in command]

    def _spawn(self, role: str) -> None:
        if role != "cuda":
            super()._spawn(role)
            return
        log = open(os.path.join(self.args.work_dir, "cuda.log"), "w",
                   encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = worker_pythonpath(
            environment.get("PYTHONPATH", ""))
        ledger = getattr(getattr(self.args, "resolved_config", None),
                         "ledger", "") or getattr(self.args, "ledger", "")
        if ledger:
            environment.setdefault("OM_XVENDOR_LEDGER", ledger)
        self.processes[role] = subprocess.Popen(
            self._command(role), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=log, text=True, env=environment, start_new_session=True)

    def _handle(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "token":
            piece = event.get("text", "")
            self.current.append(piece)
            if self.stream_callback is not None:
                self.stream_callback(piece)
            elif not getattr(self.args, "quiet", False):
                sys.stdout.write(piece)
                sys.stdout.flush()
            return
        if kind == "request_done":
            self.done = True
            self.last_summary = event
            return
        if kind == "request_failed":
            self.done = True
            self.last_summary = None
            print(f"\n  request failed: {event.get('detail', '')}",
                  file=sys.stderr)
            return
        super()._handle(event)

    # ------------------------------------------------------------- lifecycle
    def wait_ready(self) -> bool:
        deadline = time.monotonic() + self.args.load_timeout
        cuda_started = False
        last = self.health.state
        self._say("[STARTING] launching ROCm stage (second half of the model)")
        self._spawn("rocm")
        while True:
            for event in self.tail.poll():
                self._handle(event)
            self._reap()
            self._advance()
            self.health.sweep()
            if self.health.state != last:
                last = self.health.state
                self._say(f"[{last}] {self._describe()}")
            if not cuda_started and os.path.exists(self.ready_path):
                self._say("[LOADING_CUDA] ROCm stage bound; launching CUDA stage")
                self._spawn("cuda")
                cuda_started = True
                deadline = time.monotonic() + self.args.load_timeout
            if self.health.state == READY:
                return True
            if self.health.state == FAILED:
                self._say(f"[FAILED] {json.dumps(self.health.summary())}")
                return False
            if any(p.poll() is not None for p in self.processes.values()):
                self._say("[FAILED] a stage exited before becoming ready")
                self._dump_logs()
                return False
            if time.monotonic() > deadline:
                self._say("[FAILED] timed out before both stages were ready")
                return False
            time.sleep(0.05)

    def ask(self, messages: List[Dict[str, str]], max_new_tokens: int) -> bool:
        """Send the whole conversation, not just the latest turn.

        Each request runs from a fresh KV cache, so the history has to travel
        with it. Re-prefilling the transcript costs prompt processing every turn
        -- reusing the cache across turns is the obvious optimisation and is not
        done yet, because a cache that survives a request would also have to be
        invalidated correctly when the conversation is edited or reset.
        """
        worker = self.processes.get("cuda")
        if worker is None or worker.poll() is not None:
            print("  the CUDA stage is gone; the runtime must be restarted",
                  file=sys.stderr)
            return False
        self.request_id += 1
        self.current, self.done = [], False
        worker.stdin.write(json.dumps({
            "id": self.request_id, "messages": messages,
            "maxNewTokens": max_new_tokens}) + "\n")
        worker.stdin.flush()

        deadline = time.monotonic() + self.args.request_timeout
        while not self.done:
            for event in self.tail.poll():
                self._handle(event)
            self._reap()
            if any(p.poll() is not None for p in self.processes.values()):
                print("\n  a stage exited mid-request", file=sys.stderr)
                self._dump_logs()
                return False
            if time.monotonic() > deadline:
                print(f"\n  no reply within {self.args.request_timeout:.0f}s",
                      file=sys.stderr)
                return False
            time.sleep(0.02)
        return True

    def _dump_logs(self) -> None:
        for role in ("cuda", "rocm"):
            path = os.path.join(self.args.work_dir, f"{role}.log")
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                lines = [l.rstrip() for l in handle
                         if "NumPy" not in l and "conversion_method" not in l
                         and "tokenizer you are loading" not in l]
            if lines:
                self._say(f"--- {role} ---\n" + "\n".join(lines[-8:]))

    def shutdown(self) -> None:
        worker = self.processes.get("cuda")
        if worker is not None and worker.poll() is None and worker.stdin:
            try:
                worker.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                worker.stdin.flush()
                worker.stdin.close()
            except (OSError, ValueError):
                pass
        self.terminate()



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
        description="Interactive chat across a CUDA and a ROCm GPU")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="Hello",
                        help="used only to warm the runtimes before the prompt")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--port", type=int, default=31990)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--cuda-budget", default="14GiB")
    parser.add_argument("--rocm-budget", default="14GiB")
    parser.add_argument("--root", default=runtime_root())
    parser.add_argument("--work-dir", default="/opt/openmycelium/chat")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--load-timeout", type=float, default=1800.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    _apply_config(args)

    from models import resolve_verbose  # noqa: PLC0415
    resolved = resolve_verbose(args.model, sys.stderr)
    if resolved is None:
        print(f"  no model found for {args.model!r}", file=sys.stderr)
        print("  try:  openmycelium models list", file=sys.stderr)
        return 66
    args.model = resolved
    os.makedirs(args.work_dir, exist_ok=True)

    try:
        prepare_placement(args)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"  placement refused: {error}", file=sys.stderr)
        return 65

    session = ChatSession(args)

    def stop(signum, frame):                                   # noqa: ARG001
        session.shutdown()
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, stop)

    started = time.monotonic()
    try:
        if not session.wait_ready():
            session.shutdown()
            return 2
        elapsed = time.monotonic() - started
        workers = session.health.summary()["workers"]
        print()
        print(f"  ready in {elapsed:.0f}s -- weights stay loaded until you exit")
        print(f"    CUDA  {workers['cuda']['residentMiB']:.0f} MiB   "
              f"ROCm  {workers['rocm']['residentMiB']:.0f} MiB")
        print(f"  greedy decoding, up to {args.max_new_tokens} new tokens")
        print("  type a prompt; /help for commands, /bye to exit")
        print()

        history: List[Dict[str, str]] = []
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not prompt:
                continue
            if prompt in ("/bye", "/exit", "/quit"):
                break
            if prompt in ("/reset", "/clear"):
                history.clear()
                print("  conversation cleared")
                continue
            if prompt in ("/help", "/?", "help"):
                print("  /stats  timing for the last turn")
                print("  /reset  clear the conversation history")
                print("  /bye /quit /exit   leave and unload the model")
                continue
            if prompt == "/stats":
                summary = session.last_summary
                if summary is None:
                    print("  nothing generated yet")
                else:
                    inter = summary.get("interTokenMs", {})
                    print(f"  prompt {summary.get('promptTokens')} tokens, "
                          f"generated {summary.get('generatedTokens')} "
                          f"({summary.get('stopReason')})")
                    print(f"  TTFT {summary.get('ttftMs', 0):.0f} ms, "
                          f"{summary.get('decodeTokensPerSecond')} tok/s"
                          + (f", inter-token p50 {inter.get('p50')} ms "
                             f"p95 {inter.get('p95')} ms p99 {inter.get('p99')} ms"
                             if inter else ""))
                continue

            history.append({"role": "user", "content": prompt})
            if not session.ask(history, args.max_new_tokens):
                return 3
            history.append({"role": "assistant",
                            "content": "".join(session.current)})
            print()
            summary = session.last_summary or {}
            print(f"  [{summary.get('generatedTokens', 0)} tokens, "
                  f"TTFT {summary.get('ttftMs', 0):.0f} ms, "
                  f"{summary.get('decodeTokensPerSecond')} tok/s]")
            print()
        return 0
    finally:
        session.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
