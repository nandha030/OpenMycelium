"""Open WebUI -> OpenMycelium integration smoke, driven through Open WebUI.

Every request below goes to Open WebUI, which forwards it to OpenMycelium using
the provider connection configured in its own settings store. Nothing here talks
to OpenMycelium directly, so a failure in Open WebUI's forwarding shows up as a
failure here rather than being bypassed.

The browser pane could not be displayed in this environment, so the settings
page was not driven by clicking and visual rendering is not verified. What is
verified is the request path: discovery, streaming, history, stop, restart and
persistence, all through Open WebUI's backend.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

UI = "http://localhost:3000"
JWT = open("/tmp/webui.jwt").read().strip()
MODEL_HINT = "Mistral-Nemo"
failures: list[str] = []
model_id = ""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def call(path: str, payload=None, timeout=600, stream=False):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        UI + path, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {JWT}"},
        method="POST" if data else "GET")
    handle = urllib.request.urlopen(request, timeout=timeout)
    return handle if stream else json.loads(handle.read() or b"{}")


# ------------------------------------------------------- 3. model discovery
print("  == 3. model discovery and selection ==")
try:
    models = call("/api/models")
    ids = [m["id"] for m in models.get("data", [])]
    model_id = next((i for i in ids if MODEL_HINT in i), "")
    check("Open WebUI discovered the OpenMycelium model", bool(model_id),
          f"{ids}")
    owners = {m["id"]: m.get("owned_by") for m in models.get("data", [])}
    check("only OpenMycelium models are present (Ollama disabled)",
          all(o != "ollama" for o in owners.values()), str(owners))
except Exception as error:                                    # noqa: BLE001
    check("Open WebUI discovered the OpenMycelium model", False, str(error)[:150])

if not model_id:
    print("\n  cannot continue without a model")
    raise SystemExit(1)


# ------------------------------------------- 4/5. streaming, incrementally
print("\n  == 4/5. streaming request, delivered incrementally ==")
try:
    start = time.perf_counter()
    handle = call("/api/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user",
                      "content": "Explain cross-vendor GPU inference briefly."}],
        "stream": True,
    }, stream=True)
    chunks, first_at, pieces, finish = 0, None, [], None
    for raw in handle:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        event = json.loads(payload)
        for choice in event.get("choices", []):
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            piece = choice.get("delta", {}).get("content")
            if piece:
                if first_at is None:
                    first_at = time.perf_counter()
                chunks += 1
                pieces.append(piece)
    total = time.perf_counter() - start
    text = "".join(pieces)
    check("the stream produced many chunks", chunks > 5, f"{chunks} chunks")
    check("text began well before the response completed",
          first_at is not None and (first_at - start) < total * 0.5,
          f"first token at {(first_at - start) * 1000:.0f} ms of {total * 1000:.0f} ms")
    check("streamed text is non-empty", bool(text.strip()), repr(text[:70]))
except Exception as error:                                    # noqa: BLE001
    check("the stream produced many chunks", False, str(error)[:150])


# --------------------------------------------------- 6/7. two-turn history
print("\n  == 6/7. two-turn conversation, turn two depends on turn one ==")
try:
    first = call("/api/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user",
                      "content": "Remember this number: 8317. Reply with just: noted"}],
    })
    reply_one = first["choices"][0]["message"]["content"]
    print(f"    turn 1 reply: {reply_one.strip()[:60]!r}")

    second = call("/api/chat/completions", {
        "model": model_id,
        "messages": [
            {"role": "user",
             "content": "Remember this number: 8317. Reply with just: noted"},
            {"role": "assistant", "content": reply_one},
            {"role": "user", "content": "What number did I ask you to remember?"},
        ],
    })
    reply_two = second["choices"][0]["message"]["content"]
    print(f"    turn 2 reply: {reply_two.strip()[:70]!r}")
    check("the second turn used the first turn's context",
          "8317" in reply_two)
    check("prompt token count grew with the history",
          second.get("usage", {}).get("prompt_tokens", 0)
          > first.get("usage", {}).get("prompt_tokens", 0),
          f"{first.get('usage', {}).get('prompt_tokens')} -> "
          f"{second.get('usage', {}).get('prompt_tokens')}")
except Exception as error:                                    # noqa: BLE001
    check("the second turn used the first turn's context", False, str(error)[:150])


# ------------------------------------------------------ 8. EOS and length
print("\n  == 8. EOS versus length-limited completion ==")
try:
    short = call("/api/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with exactly one word: ready"}],
        "max_tokens": 200,
    })
    reason_eos = short["choices"][0].get("finish_reason")
    used = short.get("usage", {}).get("completion_tokens")
    print(f"    short answer: {short['choices'][0]['message']['content'].strip()[:40]!r}")
    check("a short answer ends by EOS, not by the limit",
          reason_eos == "stop" and used is not None and used < 200,
          f"finish_reason={reason_eos}, {used} tokens of 200")

    capped = call("/api/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": "Write a long detailed essay."}],
        "max_tokens": 24,
    })
    reason_len = capped["choices"][0].get("finish_reason")
    used_len = capped.get("usage", {}).get("completion_tokens")
    check("a capped answer ends by length",
          reason_len == "length" and used_len == 24,
          f"finish_reason={reason_len}, {used_len} tokens of 24")
except Exception as error:                                    # noqa: BLE001
    check("a short answer ends by EOS, not by the limit", False, str(error)[:150])

print()
print(f"  {len(failures)} check(s) failed")
with open("/tmp/webui_model_id", "w") as handle:
    handle.write(model_id)
sys.exit(1 if failures else 0)
