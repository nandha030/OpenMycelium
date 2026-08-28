"""Exercise the OpenAI-compatible endpoint using only the standard library.

The fresh distribution has no `openai` or `requests` package, and installing one
to test the server would weaken the point of a clean bootstrap: the endpoint has
to be correct on the wire, not merely correct through one vendor's client.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11500"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
MODEL = "Mistral-Nemo-Instruct-2407"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def post(path: str, payload: dict, token: str | None = None, stream: bool = False):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    key = TOKEN if token is None else token
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    handle = urllib.request.urlopen(request, timeout=600)
    if stream:
        return handle
    return json.loads(handle.read())


def get(path: str, token: str | None = None):
    request = urllib.request.Request(BASE + path)
    key = TOKEN if token is None else token
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    return json.loads(urllib.request.urlopen(request, timeout=120).read())


# ---------------------------------------------------------------- discovery
try:
    models = get("/v1/models")
    names = [m["id"] for m in models.get("data", [])]
    check("GET /v1/models lists the served model", MODEL in names, str(names))
except Exception as error:                                        # noqa: BLE001
    check("GET /v1/models lists the served model", False, str(error)[:120])

# ----------------------------------------------------------- non-streaming
try:
    body = post("/v1/chat/completions", {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say the single word: ready"}],
        "max_tokens": 16,
    })
    text = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {})
    check("non-streaming completion returns content", bool(text.strip()),
          repr(text[:60]))
    check("usage carries all three token counts",
          all(usage.get(k) is not None for k in
              ("prompt_tokens", "completion_tokens", "total_tokens")), str(usage))
    check("finish_reason is set", bool(body["choices"][0].get("finish_reason")),
          str(body["choices"][0].get("finish_reason")))
except Exception as error:                                        # noqa: BLE001
    check("non-streaming completion returns content", False, str(error)[:160])

# --------------------------------------------------------------- streaming
try:
    handle = post("/v1/chat/completions", {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Count: one two three"}],
        "max_tokens": 24,
        "stream": True,
        "stream_options": {"include_usage": True},
    }, stream=True)
    chunks, saw_done, saw_usage, pieces = 0, False, False, []
    for raw in handle:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            saw_done = True
            break
        event = json.loads(payload)
        chunks += 1
        if event.get("usage"):
            saw_usage = True
        for choice in event.get("choices", []):
            pieces.append(choice.get("delta", {}).get("content") or "")
    check("stream delivered multiple chunks", chunks > 1, f"{chunks} chunks")
    check("stream terminated with [DONE]", saw_done)
    check("stream_options.include_usage produced a usage chunk", saw_usage)
    check("streamed text is non-empty", bool("".join(pieces).strip()),
          repr("".join(pieces)[:60]))
except Exception as error:                                        # noqa: BLE001
    check("stream delivered multiple chunks", False, str(error)[:160])

# -------------------------------------------------------------- multi-turn
try:
    body = post("/v1/chat/completions", {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "My favourite colour is green."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "What is my favourite colour?"},
        ],
        "max_tokens": 16,
    })
    answer = body["choices"][0]["message"]["content"]
    check("multi-turn context is carried", "green" in answer.lower(),
          repr(answer[:60]))
except Exception as error:                                        # noqa: BLE001
    check("multi-turn context is carried", False, str(error)[:160])

# ------------------------------------------------------ sampling is refused
try:
    post("/v1/chat/completions", {
        "model": MODEL, "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8, "temperature": 0.9,
    })
    check("a sampling request is refused rather than silently ignored", False,
          "accepted temperature 0.9")
except urllib.error.HTTPError as error:
    check("a sampling request is refused rather than silently ignored",
          error.code == 400, f"HTTP {error.code}")
except Exception as error:                                        # noqa: BLE001
    check("a sampling request is refused rather than silently ignored", False,
          str(error)[:120])

# ----------------------------------------------------- one request at a time
if "--concurrency" in sys.argv:
    codes: list[int] = []

    def fire() -> None:
        try:
            post("/v1/chat/completions", {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Write a long paragraph."}],
                "max_tokens": 400,
            })
            codes.append(200)
        except urllib.error.HTTPError as error:
            codes.append(error.code)
        except Exception:                                         # noqa: BLE001
            codes.append(0)

    first = threading.Thread(target=fire)
    first.start()
    time.sleep(2.0)
    second = threading.Thread(target=fire)
    second.start()
    first.join()
    second.join()
    check("a second concurrent request is rejected with 429",
          429 in codes, str(codes))

print()
print(f"  {len(failures)} API check(s) failed")
sys.exit(1 if failures else 0)
