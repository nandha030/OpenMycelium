"""CPU acceptance for `openmycelium serve`, before any GPU cycle is spent.

Everything here is protocol and lifecycle. It runs against the tiny checkpoint
on the CPU path, so a defect in SSE framing, concurrency, authentication or
shutdown is found in seconds rather than after a ninety-second model load.

The hand-written SSE implementation is deliberately not rewritten to use a
framework. Whether it is correct is decided by the official OpenAI SDK parsing
it and by a client disconnecting mid-stream -- not by whether it looks
conventional.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ROOT = "/mnt/c/Users/User/Documents/Open_Mycelium"
PY = "/opt/hetenv/bin/python"
MODEL = "tiny-mistral"

results: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def environment() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([
        os.path.join(ROOT, "runtime", "serving"),
        os.path.join(ROOT, "runtime", "scheduler"),
        os.path.join(ROOT, "runtime", "fabric"),
        os.path.join(ROOT, "runtime", "cli"),
        os.path.join(ROOT, "runtime", "mccl", "src")])
    return env


def start_server(port: int, host: str = "127.0.0.1", token: str = "",
                 work: str = "/opt/openmycelium/acc", extra: Optional[List] = None
                 ) -> subprocess.Popen:
    command = [PY, os.path.join(ROOT, "runtime", "cli", "serve.py"),
               "--model", MODEL, "--allow-cpu", "--host", host,
               "--port", str(port), "--worker-port", str(port + 400),
               "--context-length", "512", "--max-new-tokens", "12",
               "--work-dir", work] + (extra or [])
    if token:
        command += ["--token", token]
    return subprocess.Popen(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            env=environment(), start_new_session=True)


def wait_ready(port: int, process: subprocess.Popen, timeout: float = 180
               ) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if json.loads(response.read())["state"] == "READY":
                    return True
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
    return False


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
    try:
        process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        process.kill()


# --------------------------------------------------------------- 1, 2, 8
def sdk_tests(port: int) -> None:
    from openai import OpenAI  # noqa: PLC0415
    client = OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="unused")

    models = client.models.list()
    check("SDK: /v1/models", any(m.id == MODEL for m in models.data),
          f"{[m.id for m in models.data]}")

    reply = client.chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "user", "content": "hello"}])
    text = reply.choices[0].message.content
    check("SDK: non-streaming completion",
          bool(text) and reply.object == "chat.completion",
          f"{len(text or '')} chars, finish={reply.choices[0].finish_reason}")
    check("SDK: usage reported",
          bool(reply.usage and reply.usage.total_tokens),
          f"prompt={getattr(reply.usage, 'prompt_tokens', None)} "
          f"completion={getattr(reply.usage, 'completion_tokens', None)}")

    # Streaming: the SDK parses the framing, so this is the real test of it.
    pieces: List[str] = []
    usage = None
    finish = None
    stream = client.chat.completions.create(
        model=MODEL, temperature=0, stream=True,
        stream_options={"include_usage": True},
        messages=[{"role": "user", "content": "hello"}])
    for event in stream:
        if event.choices and event.choices[0].delta.content:
            pieces.append(event.choices[0].delta.content)
        if event.choices and event.choices[0].finish_reason:
            finish = event.choices[0].finish_reason
        if getattr(event, "usage", None):
            usage = event.usage
    check("SDK: streaming parsed by the SDK", bool(pieces),
          f"{len(pieces)} deltas, finish={finish}")
    check("SDK: final usage chunk", usage is not None and
          getattr(usage, "total_tokens", 0) > 0,
          f"total={getattr(usage, 'total_tokens', None)}")
    check("SSE: stream terminated cleanly", finish is not None,
          f"finish_reason={finish}")

    # Multi-turn: the second answer must depend on the first exchange.
    conversation = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "".join(pieces) or "hi"},
        {"role": "user", "content": "and again"},
    ]
    multi = client.chat.completions.create(
        model=MODEL, temperature=0, messages=conversation)
    check("SDK: multi-turn accepted",
          bool(multi.choices[0].message.content),
          f"prompt_tokens={multi.usage.prompt_tokens} "
          f"(vs {reply.usage.prompt_tokens} single-turn)")
    check("multi-turn grows the prompt",
          multi.usage.prompt_tokens > reply.usage.prompt_tokens,
          "history reaches the model")


# --------------------------------------------------------------------- 3
def concurrency_test(port: int) -> None:
    """One request proceeds; a simultaneous second gets 429 + Retry-After."""
    outcomes: List[Tuple[int, str]] = []
    barrier = threading.Barrier(2)

    def fire() -> None:
        payload = json.dumps({"model": MODEL, "temperature": 0,
                              "messages": [{"role": "user",
                                            "content": "count slowly"}]}
                             ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json"})
        barrier.wait()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                outcomes.append((response.status, ""))
        except urllib.error.HTTPError as error:
            outcomes.append((error.code, error.headers.get("Retry-After", "")))
        except urllib.error.URLError as error:
            outcomes.append((0, str(error.reason)))

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    codes = sorted(code for code, _ in outcomes)
    retry = [value for code, value in outcomes if code == 429]
    check("concurrency: one 200 and one 429", codes == [200, 429], f"{codes}")
    check("concurrency: 429 carries Retry-After",
          bool(retry and retry[0]), f"Retry-After: {retry[0] if retry else None}")


# --------------------------------------------------------------------- 4
def disconnect_test(port: int) -> None:
    """Hang up mid-stream, then confirm the server still serves."""
    connection = socket.create_connection(("127.0.0.1", port), timeout=10)
    payload = json.dumps({"model": MODEL, "temperature": 0, "stream": True,
                          "messages": [{"role": "user",
                                        "content": "hello there"}]})
    connection.sendall(
        f"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n{payload}".encode())
    time.sleep(0.4)
    got_something = False
    try:
        connection.settimeout(3)
        got_something = bool(connection.recv(256))
    except OSError:
        pass
    connection.close()                       # hang up mid-stream

    # The server must survive it and accept the next request.
    time.sleep(1.0)
    deadline = time.monotonic() + 120
    recovered = False
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({"model": MODEL, "temperature": 0,
                                 "messages": [{"role": "user",
                                               "content": "still there?"}]}
                                ).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                recovered = response.status == 200
            break
        except urllib.error.HTTPError as error:
            if error.code == 429:            # previous generation finishing
                time.sleep(1.0)
                continue
            break
        except urllib.error.URLError:
            break
    check("disconnect: server survives a client hangup", recovered,
          f"received-before-hangup={got_something}; "
          "generation completes, the lock is released")


# ------------------------------------------------------------ 4 (continued)
def drain_test(port: int) -> None:
    """A disconnected request keeps the lock until its generation drains.

    Generation cannot be aborted mid-flight, so the guarantee that matters is
    that the abandoned work finishes at a clean boundary while the lock is
    still held. If the lock were released early, a second request would enter
    while the first was still writing into the runtime, and the two would
    interleave in a way neither caller could detect.
    """
    reference = _plain_completion(port, "reference prompt")
    if reference is None:
        check("drain: reference completion", False, "could not establish one")
        return
    check("drain: reference completion", True, f"{len(reference)} chars")

    # Start a stream and hang up almost immediately.
    connection = socket.create_connection(("127.0.0.1", port), timeout=10)
    # The abandoned request must still be generating when the next one arrives,
    # or the test proves nothing: a first attempt used the server's 12-token
    # default, which finished inside the 0.25 s before the probe and correctly
    # returned 200. A long generation creates a drain window to observe.
    payload = json.dumps({"model": MODEL, "temperature": 0, "stream": True,
                          "max_tokens": 400,
                          "messages": [{"role": "user",
                                        "content": "abandoned prompt"}]})
    connection.sendall(
        f"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n{payload}".encode())
    time.sleep(0.25)
    connection.close()

    # Immediately: the lock must still be held by the draining request.
    code, retry = _status_only(port, "during drain")
    check("drain: second request during drain gets 429", code == 429,
          f"got {code}")
    check("drain: that 429 carries Retry-After", bool(retry),
          f"Retry-After: {retry}")

    # The drain must end, and the next request must be uncontaminated: the same
    # prompt as the reference must produce the same text, because every request
    # builds a fresh KV cache.
    deadline = time.monotonic() + 180
    after = None
    while time.monotonic() < deadline:
        after = _plain_completion(port, "reference prompt")
        if after is not None:
            break
        time.sleep(0.5)
    check("drain: abandoned generation reaches a clean boundary",
          after is not None, "the lock was released again")
    check("drain: next request is uncontaminated", after == reference,
          "same prompt reproduces the reference exactly"
          if after == reference else f"differs: {(after or '')[:60]!r}")


def _plain_completion(port: int, prompt: str) -> Optional[str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"model": MODEL, "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}
                        ).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read())
        return body["choices"][0]["message"]["content"]
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError):
        return None


def _status_only(port: int, prompt: str) -> Tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"model": MODEL, "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}
                        ).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Retry-After", "")
    except urllib.error.URLError as error:
        return 0, str(error.reason)


def stop_during_drain(port: int) -> None:
    """`stop` must end a runtime whose request was abandoned mid-generation."""
    process = start_server(port, work="/opt/openmycelium/acc-drain")
    if not wait_ready(port, process):
        check("stop-during-drain: server ready", False, "never became ready")
        stop_server(process)
        return
    connection = socket.create_connection(("127.0.0.1", port), timeout=10)
    payload = json.dumps({"model": MODEL, "temperature": 0, "stream": True,
                          "messages": [{"role": "user",
                                        "content": "abandoned then stopped"}]})
    connection.sendall(
        f"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n{payload}".encode())
    time.sleep(0.25)
    connection.close()

    stopper = subprocess.run(
        [PY, os.path.join(ROOT, "runtime", "cli", "lifecycle.py"), "stop"],
        capture_output=True, text=True, env=environment(), timeout=180)
    try:
        process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        pass
    outcome = (stopper.stdout.strip().splitlines() or [""])[-1].strip()
    check("stop-during-drain: runtime terminated", process.poll() is not None,
          f"stop said: {outcome}")
    leftovers = subprocess.run(
        ["bash", "-c", "ps -eo args | grep pipeline_run | grep -v grep | wc -l"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    check("stop-during-drain: no orphan workers", leftovers == "0",
          f"{leftovers} remain")
    if process.poll() is None:
        stop_server(process)


# --------------------------------------------------------------------- 6, 7
def security_and_port_tests(port: int) -> None:
    # Non-loopback without a token must be refused before anything loads.
    process = subprocess.run(
        [PY, os.path.join(ROOT, "runtime", "cli", "serve.py"),
         "--model", MODEL, "--allow-cpu", "--host", "0.0.0.0",
         "--port", str(port + 3)],
        capture_output=True, text=True, env=environment(), timeout=120)
    check("security: non-loopback without a token is refused",
          process.returncode == 78 and "token" in process.stdout.lower(),
          f"exit {process.returncode}")

    # Port collision must be a clear refusal, not a traceback.
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", port + 5))
    holder.listen(1)
    try:
        process = subprocess.run(
            [PY, os.path.join(ROOT, "runtime", "cli", "serve.py"),
             "--model", MODEL, "--allow-cpu", "--port", str(port + 5)],
            capture_output=True, text=True, env=environment(), timeout=120)
        check("port collision: clear refusal",
              process.returncode == 98 and "in use" in process.stdout,
              f"exit {process.returncode}")
    finally:
        holder.close()


def token_tests(port: int) -> None:
    """A tokened server: missing, wrong and correct bearer tokens."""
    token = "acceptance-token-1234"
    process = start_server(port, host="127.0.0.1", token=token,
                           work="/opt/openmycelium/acc-token")
    try:
        if not wait_ready(port, process):
            check("security: tokened server starts", False, "never became ready")
            return
        check("security: tokened server starts", True)

        def models_call(header: Optional[str]) -> int:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models")
            if header:
                request.add_header("Authorization", header)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        check("security: missing token gets 401", models_call(None) == 401)
        check("security: wrong token gets 401",
              models_call("Bearer wrong-token") == 401)
        check("security: correct token accepted",
              models_call(f"Bearer {token}") == 200)
    finally:
        stop_server(process)


# --------------------------------------------------------------------- 5
def stop_during_stream(port: int) -> None:
    """`stop` while a stream is open: server and workers both go."""
    process = start_server(port, work="/opt/openmycelium/acc-stop")
    if not wait_ready(port, process):
        check("stop-during-stream: server ready", False, "never became ready")
        stop_server(process)
        return

    opened = threading.Event()

    def stream() -> None:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({"model": MODEL, "temperature": 0,
                                 "stream": True,
                                 "messages": [{"role": "user",
                                               "content": "hello"}]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=60) as response:
                opened.set()
                for _ in response:
                    time.sleep(0.05)
        except Exception:                                     # noqa: BLE001
            opened.set()

    thread = threading.Thread(target=stream, daemon=True)
    thread.start()
    opened.wait(timeout=30)
    time.sleep(0.3)

    stopper = subprocess.run(
        [PY, os.path.join(ROOT, "runtime", "cli", "lifecycle.py"), "stop"],
        capture_output=True, text=True, env=environment(), timeout=120)
    thread.join(timeout=60)
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pass

    outcome = stopper.stdout.strip().splitlines()[-1:] or [""]
    check("stop-during-stream: server process exited",
          process.poll() is not None, f"stop said: {outcome[0].strip()}")
    # The report must agree with reality. A stop that succeeded while claiming
    # the process survived SIGKILL is a false failure, and the first run of this
    # suite produced exactly that.
    check("stop-during-stream: stop reports the truth",
          process.poll() is not None and "stopped" in outcome[0],
          outcome[0].strip())

    free = True
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        free = False
    finally:
        probe.close()
    check("stop-during-stream: port released", free)

    listing = subprocess.run(
        [PY, os.path.join(ROOT, "runtime", "cli", "lifecycle.py"), "ps",
         "--json"], capture_output=True, text=True, env=environment(),
        timeout=60)
    try:
        state = json.loads(listing.stdout)
        check("stop-during-stream: no live record remains",
              not state.get("running"), f"{len(state.get('running', []))} live")
    except ValueError:
        check("stop-during-stream: no live record remains", False,
              "ps --json unparseable")

    leftovers = subprocess.run(
        ["bash", "-c", "ps -eo args | grep pipeline_run | grep -v grep | wc -l"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    check("stop-during-stream: no orphan workers", leftovers == "0",
          f"{leftovers} pipeline_run processes remain")


def main() -> int:
    port = 11510
    print("\n  CPU acceptance for openmycelium serve\n")
    process = start_server(port)
    try:
        if not wait_ready(port, process):
            output = (process.stdout.read() if process.stdout else "")[-800:]
            print(f"  server never became ready:\n{output}")
            return 2
        print("  server ready\n")
        sdk_tests(port)
        concurrency_test(port)
        disconnect_test(port)
        drain_test(port)
    finally:
        stop_server(process)

    print()
    security_and_port_tests(port)
    token_tests(port + 10)
    stop_during_stream(port + 20)
    stop_during_drain(port + 30)

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"  {passed}/{len(results)} checks passed")
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print("  FAILED:")
        for name in failed:
            print(f"    - {name}")
        return 1
    print("  RESULT: PASS - protocol and lifecycle qualified on the CPU path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
