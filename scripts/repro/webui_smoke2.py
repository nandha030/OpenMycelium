"""Open WebUI smoke, steps 9 to 15: stop, drain, restart, reconnect, persist.

Step 9 deserves care. Closing the stream is what Open WebUI's Stop button does
to the connection. It does not cancel the worker: this build has no worker-side
cancellation, and the generation continues to completion on the GPUs. What is
demonstrated is that the runtime drains that generation safely, refuses
concurrent work with 429 and a Retry-After while draining, and then serves the
next request with nothing carried over. None of that is backend cancellation and
it is not described as such.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

UI = "http://localhost:3000"
JWT = open("/tmp/webui.jwt").read().strip()
MODEL = open("/tmp/webui_model_id").read().strip()
OM = "/opt/om/venv/bin/openmycelium"
SECRET = "/run/openmycelium/openwebui.token"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def ui_request(payload, timeout=600, stream=False):
    request = urllib.request.Request(
        f"{UI}/api/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {JWT}"}, method="POST")
    handle = urllib.request.urlopen(request, timeout=timeout)
    return handle if stream else json.loads(handle.read() or b"{}")


def direct(payload, timeout=60):
    """Straight to OpenMycelium, to observe status codes Open WebUI hides."""
    token = open(SECRET).read().strip()
    request = urllib.request.Request(
        "http://127.0.0.1:11500/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"}, method="POST")
    return urllib.request.urlopen(request, timeout=timeout)


# ------------------------------------------------------------ 9. stop/drain
print("  == 9. Stop during generation, then drain ==")
handle = ui_request({"model": MODEL,
                     "messages": [{"role": "user",
                                   "content": "Write a very long detailed essay."}],
                     "stream": True, "max_tokens": 400}, stream=True)
read = 0
for raw in handle:
    if raw.strip().startswith(b"data:"):
        read += 1
    if read > 8:
        break
handle.close()
print(f"    closed the stream after {read} chunks, as Stop does")
print("    the worker is NOT cancelled; it finishes the generation")

time.sleep(2)
code, retry_after = None, None
try:
    direct({"model": "Mistral-Nemo-Instruct-2407",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8}, timeout=25).read()
    code = 200
except urllib.error.HTTPError as error:
    code, retry_after = error.code, error.headers.get("Retry-After")
except Exception as error:                                    # noqa: BLE001
    code = f"error: {type(error).__name__}"
check("a request during the drain is refused with 429", code == 429, f"HTTP {code}")
check("the refusal carries Retry-After", retry_after is not None,
      f"Retry-After: {retry_after}")

print("    waiting for the drain to finish")
served, waited = False, time.time()
for _ in range(90):
    try:
        body = json.loads(direct({"model": "Mistral-Nemo-Instruct-2407",
                                  "messages": [{"role": "user",
                                                "content": "Reply with exactly: banana"}],
                                  "max_tokens": 12}, timeout=120).read())
        answer = body["choices"][0]["message"]["content"]
        served = True
        break
    except urllib.error.HTTPError as error:
        if error.code != 429:
            break
        time.sleep(2)
check("the next request is served once the drain completes", served,
      f"after {time.time() - waited:.0f} s")
if served:
    print(f"    answer: {answer.strip()[:50]!r}")
    check("no context carried over from the abandoned generation",
          "banana" in answer.lower() and "essay" not in answer.lower())


# ------------------------------------------ 10-13. outage and reconnection
print("\n  == 10-12. stop OpenMycelium, observe the outage ==")
subprocess.run([OM, "stop"], capture_output=True)
time.sleep(6)
outage = None
try:
    ui_request({"model": MODEL,
                "messages": [{"role": "user", "content": "are you there?"}],
                "max_tokens": 16}, timeout=90)
    outage = 200
except urllib.error.HTTPError as error:
    outage = error.code
except Exception as error:                                    # noqa: BLE001
    outage = type(error).__name__
check("Open WebUI reports the outage rather than inventing a reply",
      outage != 200, f"HTTP/exception: {outage}")

print("\n  == 13. restart OpenMycelium and reconnect with the same config ==")
token = open(SECRET).read().strip()
server = subprocess.Popen(
    [OM, "serve", "--model", "Mistral-Nemo-Instruct-2407", "--host", "0.0.0.0",
     "--port", "11500", "--max-new-tokens", "512"],
    env={**__import__("os").environ, "OPENMYCELIUM_TOKEN": token,
         "PATH": "/opt/om/venv/bin:/usr/bin:/bin"},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ready = False
for _ in range(150):
    time.sleep(4)
    try:
        request = urllib.request.Request("http://127.0.0.1:11500/v1/models")
        request.add_header("Authorization", f"Bearer {token}")
        urllib.request.urlopen(request, timeout=5)
        ready = True
        break
    except Exception:                                         # noqa: BLE001
        pass
check("OpenMycelium came back up", ready)

if ready:
    try:
        body = ui_request({"model": MODEL,
                           "messages": [{"role": "user",
                                         "content": "Reply with exactly: reconnected"}],
                           "max_tokens": 12}, timeout=180)
        text = body["choices"][0]["message"]["content"]
        check("Open WebUI reconnected using the existing provider configuration",
              bool(text.strip()), repr(text.strip()[:50]))
    except Exception as error:                                # noqa: BLE001
        check("Open WebUI reconnected using the existing provider configuration",
              False, str(error)[:150])

print()
print(f"  {len(failures)} check(s) failed")
sys.exit(1 if failures else 0)
