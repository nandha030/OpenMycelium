"""Equal-length throughput measurement, five independent sessions.

Every earlier number came from a run whose prompt and generation lengths varied,
which makes them incomparable: TTFT scales with prompt length and end-to-end
rate depends on how many tokens were generated. So this fixes both, separates
the three quantities that were previously conflated, and reports median and
range rather than a single figure.

    TTFT              prompt submitted -> first token returned
    decode tok/s      generated tokens / (total - TTFT)
    end-to-end tok/s  generated tokens / total, TTFT included

Each session is a fresh server process: weights reloaded, caches cold, nothing
carried over. A number measured across one warm process would describe a
best case that a user starting the tool never sees.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

MODEL = "Mistral-Nemo-Instruct-2407"
PORT = 11500
TOKENS = 64
SESSIONS = int(os.environ.get("OM_SESSIONS", "5"))
OM = "/opt/om/venv/bin/openmycelium"

# One fixed prompt for every session and every request. Its token count is
# reported so the measurement can be reproduced.
PROMPT = ("Describe how a pipeline-parallel inference runtime divides a "
          "transformer model across two accelerators from different vendors, "
          "and explain what has to cross the boundary between them.")


def wait_ready(token: str, timeout: float = 900) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{PORT}/v1/models")
            request.add_header("Authorization", f"Bearer {token}")
            urllib.request.urlopen(request, timeout=5)
            return True
        except Exception:                                     # noqa: BLE001
            time.sleep(3)
    return False


def one_request(token: str) -> dict:
    """Stream, so the first token can be timed rather than inferred."""
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"}, method="POST")

    start = time.perf_counter()
    first: float | None = None
    produced = 0
    usage: dict = {}
    with urllib.request.urlopen(request, timeout=600) as handle:
        for raw in handle:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            event = json.loads(payload)
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices", []):
                piece = choice.get("delta", {}).get("content")
                if piece:
                    if first is None:
                        first = time.perf_counter()
                    produced += 1
    total = time.perf_counter() - start
    ttft = (first - start) if first else float("nan")
    generated = usage.get("completion_tokens") or produced
    return {
        "ttftMs": ttft * 1000,
        "totalS": total,
        "generated": generated,
        "promptTokens": usage.get("prompt_tokens"),
        "decodeTokS": (generated - 1) / (total - ttft) if total > ttft else 0.0,
        "endToEndTokS": generated / total if total else 0.0,
    }


def main() -> int:
    token = os.environ.get("OM_TOKEN", "")
    sessions = []
    for index in range(1, SESSIONS + 1):
        print(f"  session {index} of {SESSIONS}: starting a fresh server",
              flush=True)
        server = subprocess.Popen(
            [OM, "serve", "--model", MODEL, "--port", str(PORT),
             "--token", token, "--max-new-tokens", str(TOKENS + 8)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not wait_ready(token):
                print("    server never became ready")
                continue
            one_request(token)                       # discard: warms nothing
            sample = one_request(token)              # measured
            sample["session"] = index
            sessions.append(sample)
            print(f"    prompt {sample['promptTokens']} tokens, "
                  f"generated {sample['generated']}, "
                  f"TTFT {sample['ttftMs']:.0f} ms, "
                  f"decode {sample['decodeTokS']:.2f} tok/s, "
                  f"end-to-end {sample['endToEndTokS']:.2f} tok/s", flush=True)
        finally:
            subprocess.run([OM, "stop"], capture_output=True)
            try:
                server.wait(timeout=120)
            except subprocess.TimeoutExpired:
                server.kill()
            time.sleep(5)

    if not sessions:
        print("  no sessions completed")
        return 1

    print()
    print(f"  {len(sessions)} independent sessions, "
          f"prompt fixed at {sessions[0]['promptTokens']} tokens, "
          f"{TOKENS} generated")
    print()
    print(f"    {'metric':<22} {'median':>9} {'min':>9} {'max':>9}")
    summary = {}
    for label, key, unit in (("TTFT", "ttftMs", "ms"),
                             ("decode", "decodeTokS", "tok/s"),
                             ("end-to-end", "endToEndTokS", "tok/s")):
        values = [s[key] for s in sessions]
        summary[key] = {"median": statistics.median(values),
                        "min": min(values), "max": max(values),
                        "values": values}
        print(f"    {label + ' (' + unit + ')':<22} "
              f"{statistics.median(values):9.2f} "
              f"{min(values):9.2f} {max(values):9.2f}")

    out = {"model": MODEL, "sessions": sessions, "summary": summary,
           "promptTokens": sessions[0]["promptTokens"],
           "generatedTokens": TOKENS,
           "method": ("one fresh server process per session; one discarded "
                      "request then one measured request; streaming, so the "
                      "first token is timed rather than inferred")}
    with open("/var/log/om-a8/throughput.json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=True)
    print()
    print("  written to /var/log/om-a8/throughput.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
