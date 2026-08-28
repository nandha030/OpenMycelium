"""`openmycelium serve` -- an OpenAI-compatible endpoint over both GPUs.

    GET  /health
    GET  /v1/models
    POST /v1/chat/completions        (streaming and non-streaming)

Applications change `base_url` and the model name; nothing else. That is the
whole point of speaking this protocol rather than inventing one.

Three restrictions are enforced rather than papered over, because an API that
quietly does something other than what it was asked is worse than one that
refuses:

* **Only `temperature: 0`.** Sampling is not validated across the vendor
  boundary -- the cached and recomputed distributions agree on the argmax but
  not on the ordering of lower-ranked candidates. Accepting `temperature: 0.7`
  and decoding greedily would return plausible output that does not match what
  the caller asked for, in a way no client could detect. Unsupported sampling
  fields are rejected by name.
* **One active request.** There is no batching and no queue; a second request
  would interleave into the first request's KV cache. Concurrent callers get
  `429` with `Retry-After`.
* **Loopback by default.** Binding to a non-loopback address requires a token,
  because this endpoint runs arbitrary prompts against a loaded model and has
  no other access control.

The port default is 11500, not 11434: that belongs to Ollama, and two servers
fighting over one port is a bad first experience.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from chat import ChatSession  # noqa: E402
from control import remove_if_matches, update_state  # noqa: E402
from coordinator import prepare_placement  # noqa: E402
from health import READY  # noqa: E402
from paths import runtime_root  # noqa: E402

DEFAULT_PORT = 11500
OLLAMA_PORT = 11434

#: Sampling fields this build cannot honour, each with the values that are
#: semantically no-ops. Real clients send defaults constantly -- LM Studio and
#: the OpenAI SDK both do -- and `top_p: 1.0` truncates nothing, `n: 1` asks for
#: one completion, `presence_penalty: 0` penalises nothing. Refusing those would
#: reject a request that asked for exactly what greedy decoding already does.
#:
#: Anything outside its no-op set is still refused by name. The rule is that no
#: caller is ever silently given something other than what it asked for -- not
#: that every field must be absent.
NO_OP_VALUES = {
    "top_p": (None, 1, 1.0),
    "top_k": (None, 0, -1),
    "presence_penalty": (None, 0, 0.0),
    "frequency_penalty": (None, 0, 0.0),
    "repetition_penalty": (None, 1, 1.0),
    "logit_bias": (None, {}, []),
    "n": (None, 1),
    "best_of": (None, 1),
    "seed": (None,),
    "logprobs": (None, False, 0),
    "top_logprobs": (None, 0),
    "response_format": (None, {}, {"type": "text"}),
    "tools": (None, []),
    "tool_choice": (None, "none", "auto"),
    "stop": (None, [], ""),
}


class ServeState:
    """Everything the handler needs, and the lock that serialises requests."""

    def __init__(self, session: ChatSession, model_name: str, args):
        self.session = session
        self.model_name = model_name
        self.args = args
        self.lock = threading.Lock()
        self.served = 0
        self.rejected = 0
        self.started = time.time()


def _port_free(host: str, port: int) -> Tuple[bool, str]:
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
        return True, ""
    except OSError as error:
        if error.errno in (errno.EADDRINUSE, errno.EACCES):
            return False, f"{host}:{port} is already in use"
        return False, str(error)
    finally:
        probe.close()


def _reject(request: Dict[str, Any]) -> Optional[str]:
    """Why this request cannot be served, or None."""
    temperature = request.get("temperature", 0)
    if temperature not in (0, 0.0, None):
        return (f"temperature={temperature} is not supported. Sampling is not "
                "validated across the vendor boundary in this build: the "
                "cached and recomputed logit distributions agree on the argmax "
                "but not on lower-ranked ordering. Use temperature=0.")
    options = request.get("stream_options") or {}
    unknown = [k for k in options if k != "include_usage"]
    if unknown:
        return f"unsupported stream_options: {', '.join(unknown)}"
    active = []
    for name, harmless in NO_OP_VALUES.items():
        if name not in request:
            continue
        value = request[name]
        if any(value == allowed and type(value) is type(allowed)
               or value == allowed for allowed in harmless):
            continue
        active.append(f"{name}={value!r}")
    if active:
        return (f"unsupported sampling parameters: {', '.join(active)}. This "
                "build decodes greedily; a value that would change the result "
                "is refused rather than ignored, so a caller is never silently "
                "given something else. Default values that change nothing "
                "(top_p=1, n=1, penalties=0) are accepted.")
    if not request.get("messages"):
        return "messages is required"
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: ServeState = None                      # type: ignore[assignment]

    def log_message(self, *args: Any) -> None:    # noqa: D102
        pass

    # ----------------------------------------------------------------- utils
    def _json(self, code: int, payload: Dict[str, Any],
              headers: Optional[Dict[str, str]] = None) -> None:
        blob = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(blob)

    def _error(self, code: int, message: str, kind: str = "invalid_request_error",
               headers: Optional[Dict[str, str]] = None) -> None:
        self._json(code, {"error": {"message": message, "type": kind,
                                    "code": code}}, headers)

    def _authorised(self) -> bool:
        token = self.state.args.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header.strip() == f"Bearer {token}"

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:                     # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            summary = self.state.session.health.summary()
            self._json(200, {
                "status": "ok" if summary["state"] == READY else "not_ready",
                "state": summary["state"],
                "workers": summary["workers"],
                "model": self.state.model_name,
                "servedRequests": self.state.served,
                "rejectedRequests": self.state.rejected,
                "uptimeSeconds": round(time.time() - self.state.started, 1),
                "decoding": "greedy (temperature 0 only)",
            })
            return
        if path in ("/v1/models", "/models"):
            if not self._authorised():
                self._error(401, "invalid token", "authentication_error")
                return
            self._json(200, {"object": "list", "data": [{
                "id": self.state.model_name, "object": "model",
                "created": int(self.state.started),
                "owned_by": "openmycelium"}]})
            return
        self._error(404, f"no route for {path}")

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:                    # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._error(404, f"no route for {path}")
            return
        if not self._authorised():
            self._error(401, "invalid token", "authentication_error")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as error:
            self._error(400, f"malformed JSON: {error}")
            return

        refusal = _reject(request)
        if refusal:
            self.state.rejected += 1
            self._error(400, refusal)
            return

        # One at a time. A second request would interleave into the first
        # request's KV cache, so it is refused rather than corrupted.
        if not self.state.lock.acquire(blocking=False):
            self.state.rejected += 1
            self._error(429, "a request is already in flight; this build "
                             "serves one at a time", "rate_limit_error",
                        {"Retry-After": "5"})
            return
        try:
            self._complete(request)
        finally:
            self.state.lock.release()

    def _complete(self, request: Dict[str, Any]) -> None:
        session = self.state.session
        messages = [{"role": str(m.get("role", "user")),
                     "content": str(m.get("content", ""))}
                    for m in request["messages"]]
        limit = int(request.get("max_tokens")
                    or request.get("max_completion_tokens")
                    or self.state.args.max_new_tokens)
        identifier = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        streaming = bool(request.get("stream"))

        if streaming:
            include_usage = bool((request.get("stream_options") or {})
                                 .get("include_usage"))
            self._stream(session, messages, limit, identifier, created,
                         include_usage)
            return

        ok = session.ask(messages, limit)
        if not ok:
            self._error(503, "the runtime failed during generation",
                        "server_error")
            return
        text = "".join(session.current)
        summary = session.last_summary or {}
        self.state.served += 1
        self._json(200, {
            "id": identifier, "object": "chat.completion", "created": created,
            "model": self.state.model_name,
            "choices": [{"index": 0, "finish_reason":
                         "stop" if summary.get("stopReason") == "eos" else "length",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": summary.get("promptTokens", 0),
                      "completion_tokens": summary.get("generatedTokens", 0),
                      "total_tokens": summary.get("promptTokens", 0)
                      + summary.get("generatedTokens", 0)},
        })

    def _stream(self, session, messages, limit, identifier, created,
                include_usage: bool = False) -> None:
        """Server-sent events in the OpenAI delta format."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(payload: Dict[str, Any]) -> bool:
            """One SSE event, in one HTTP chunk.

            A client that hung up mid-stream stops receiving here. Generation
            still runs to completion, because the worker has no abort path and
            half-cancelling would leave its KV cache in a state nothing else
            could reason about.
            """
            body = f"data: {json.dumps(payload)}\n\n".encode("utf-8")
            try:
                self.wfile.write(f"{len(body):X}\r\n".encode())
                self.wfile.write(body + b"\r\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def chunk(delta: Dict[str, Any], finish: Optional[str] = None) -> bool:
            return send({"id": identifier, "object": "chat.completion.chunk",
                         "created": created, "model": self.state.model_name,
                         "choices": [{"index": 0, "delta": delta,
                                      "finish_reason": finish}]})

        chunk({"role": "assistant", "content": ""})
        emitted = {"count": 0, "alive": True}

        def on_token(piece: str) -> None:
            if emitted["alive"] and piece:
                emitted["alive"] = chunk({"content": piece})
                emitted["count"] += 1

        session.stream_callback = on_token
        try:
            ok = session.ask(messages, limit)
        finally:
            session.stream_callback = None

        summary = session.last_summary or {}
        finish = "stop" if summary.get("stopReason") == "eos" else "length"
        if emitted["alive"]:
            chunk({}, finish if ok else "length")
            if include_usage:
                prompt_tokens = summary.get("promptTokens", 0)
                completion = summary.get("generatedTokens", 0)
                send({"id": identifier, "object": "chat.completion.chunk",
                      "created": created, "model": self.state.model_name,
                      "choices": [],
                      "usage": {"prompt_tokens": prompt_tokens,
                                "completion_tokens": completion,
                                "total_tokens": prompt_tokens + completion}})
            try:
                tail = b"data: [DONE]\n\n"
                self.wfile.write(f"{len(tail):X}\r\n".encode())
                self.wfile.write(tail + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.state.served += 1



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
        description="OpenAI-compatible endpoint across a CUDA and a ROCm GPU")
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", default=os.environ.get("OPENMYCELIUM_TOKEN", ""))
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--cuda-budget", default="14GiB")
    parser.add_argument("--rocm-budget", default="14GiB")
    parser.add_argument("--root", default=runtime_root())
    parser.add_argument("--work-dir", default="/opt/openmycelium/serve")
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--load-timeout", type=float, default=1800.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--worker-port", type=int, default=31995)
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _apply_config(args)

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not args.token:
        print("\n  refusing to bind to a non-loopback address without a token.")
        print("  This endpoint runs arbitrary prompts against a loaded model")
        print("  and has no other access control.")
        print("  Set OPENMYCELIUM_TOKEN, or pass --token.\n")
        return 78

    free, reason = _port_free(args.host, args.port)
    if not free:
        print(f"\n  cannot start: {reason}")
        if args.port == OLLAMA_PORT:
            print("  (11434 is Ollama's default; OpenMycelium uses 11500)")
        print(f"  choose another with:  openmycelium serve --port <n>\n")
        return 98

    from models import resolve_verbose  # noqa: PLC0415
    resolved = resolve_verbose(args.model, sys.stderr)
    if resolved is None:
        print(f"  no model found for {args.model!r}", file=sys.stderr)
        return 66
    model_name = os.path.basename(resolved.rstrip("/"))
    args.model = resolved
    args.prompt = "warmup"
    args.invoked_as = "serve"
    args.port_http = args.port
    args.port = args.worker_port
    os.makedirs(args.work_dir, exist_ok=True)

    try:
        prepare_placement(args)
    except Exception as error:                                # noqa: BLE001
        print(f"  placement refused: {error}", file=sys.stderr)
        return 2

    session = ChatSession(args)
    # The record carries the HTTP port, so `ps` shows where the API is.
    session.control.port = args.port_http
    session.control.write()

    print(f"  loading {model_name} across both GPUs ...")
    if not session.wait_ready():
        session.shutdown()
        return 3

    state = ServeState(session, model_name, args)
    Handler.state = state
    server = ThreadingHTTPServer((args.host, args.port_http), Handler)
    server.daemon_threads = True

    base = f"http://{args.host}:{args.port_http}/v1"
    workers = session.health.summary()["workers"]
    print()
    print(f"  OpenMycelium serving {model_name}")
    print(f"    CUDA  {workers['cuda']['residentMiB']:.0f} MiB   "
          f"ROCm  {workers['rocm']['residentMiB']:.0f} MiB")
    print(f"    base_url   {base}")
    print(f"    model      {model_name}")
    print(f"    auth       {'bearer token required' if args.token else 'none (loopback)'}")
    print(f"    decoding   greedy only; temperature must be 0")
    print(f"    concurrency one request at a time; others get 429")
    print()
    print(f"    curl {base}/models")
    print(f"    openmycelium ps          # shows this server")
    print(f"    openmycelium stop        # unloads both GPUs")
    print()

    update_state(session.control_path, READY, httpPort=args.port_http,
                 baseUrl=base)

    def shutdown(signum, frame):                              # noqa: ARG001
        print("\n  stopping; unloading both GPU workers ...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    import signal as signal_module
    signal_module.signal(signal_module.SIGTERM, shutdown)
    signal_module.signal(signal_module.SIGINT, shutdown)

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        session.shutdown()
        remove_if_matches(session.control_path, args.run_id)
        print("  both workers stopped; VRAM released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
