"""Step 9 without the UI: what OpenMycelium does while a generation is running.

Open WebUI's Stop button closes the client's stream. It does not cancel the
worker -- OpenMycelium has no worker-side cancellation in this build, and the
report must not claim otherwise. What can be shown is that the runtime drains
the in-flight generation safely, refuses concurrent work with 429 and a
Retry-After while it does, and serves the next request cleanly with no state
carried over from the abandoned one.

Run alongside the UI test, or on its own.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11500"
MODEL = "Mistral-Nemo-Instruct-2407"
TOKEN = os.environ.get("OM_TOKEN", "")
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def request(payload: dict, timeout: float = 600):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {TOKEN}"},
                                 method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def abandon_midway() -> None:
    """Start a long generation and walk away from it, as Stop does."""
    handle = request({"model": MODEL,
                      "messages": [{"role": "user",
                                    "content": "Write a long, detailed essay."}],
                      "max_tokens": 400, "stream": True})
    read = 0
    for raw in handle:
        if raw.strip().startswith(b"data:"):
            read += 1
        if read > 6:
            break
    handle.close()          # client goes away; the worker keeps generating
    print(f"    abandoned the stream after {read} chunks")


def main() -> int:
    print("  == a second request while one is in flight ==")
    codes: dict[str, object] = {}

    def long_one() -> None:
        try:
            handle = request({"model": MODEL,
                              "messages": [{"role": "user",
                                            "content": "Write a long paragraph."}],
                              "max_tokens": 300})
            handle.read()
            codes["first"] = 200
        except urllib.error.HTTPError as error:
            codes["first"] = error.code

    worker = threading.Thread(target=long_one)
    worker.start()
    time.sleep(3)
    try:
        request({"model": MODEL,
                 "messages": [{"role": "user", "content": "hello"}],
                 "max_tokens": 8}, timeout=30).read()
        codes["second"] = 200
        codes["retryAfter"] = None
    except urllib.error.HTTPError as error:
        codes["second"] = error.code
        codes["retryAfter"] = error.headers.get("Retry-After")
    worker.join()

    check("a concurrent request is refused with 429", codes.get("second") == 429,
          f"first={codes.get('first')} second={codes.get('second')}")
    check("the refusal carries Retry-After",
          codes.get("retryAfter") is not None, f"Retry-After: {codes.get('retryAfter')}")

    print()
    print("  == abandoning a stream, as the UI Stop button does ==")
    abandon_midway()
    print("    (the worker is NOT cancelled; it finishes the generation)")

    print()
    print("  == the runtime drains and accepts the next request ==")
    started = time.time()
    served = False
    for _ in range(60):
        try:
            body = json.loads(request(
                {"model": MODEL,
                 "messages": [{"role": "user",
                               "content": "Reply with exactly: banana"}],
                 "max_tokens": 12}, timeout=120).read())
            answer = body["choices"][0]["message"]["content"]
            served = True
            break
        except urllib.error.HTTPError as error:
            if error.code != 429:
                check("the next request was served", False, f"HTTP {error.code}")
                return 1
            time.sleep(2)
    waited = time.time() - started
    check("the next request is served once the drain completes", served,
          f"after {waited:.1f} s")
    if served:
        print(f"    answer: {answer.strip()[:60]!r}")
        check("no context carried over from the abandoned generation",
              "banana" in answer.lower() and "essay" not in answer.lower(),
              "the reply answers the new prompt only")

    print()
    print(f"  {len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
