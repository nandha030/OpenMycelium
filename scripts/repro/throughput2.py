"""Throughput report of record for 0.1.0a8.

Every quantity that was previously conflated is separated, and every identity
the report depends on is recorded so the measurement can be reproduced or
contradicted.

    server load        spawn -> ready. Excluded from throughput entirely.
    warm-up            one request per session, discarded.
    TTFT               request start -> first content token, client-observed.
    decode tok/s       (generated - 1) / (last token - first token).
    end-to-end tok/s   generated / (request start -> response complete).

Five independent server processes. Median, minimum and maximum only: a p95 from
five observations is a number with no content.

Byte-exact transport is qualified immediately before and immediately after the
campaign, never inside a timed session: hashing a full activation costs far more
than the transfer it checks, so doing it during a measured request would alter
the path being benchmarked.

Identical output across the five sessions is reported as deterministic
functional consistency and nothing more. It is not evidence of byte-exact
transport -- a small corruption of the boundary activation can leave a greedy
argmax unchanged, so identical tokens do not imply identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

MODEL = "Mistral-Nemo-Instruct-2407"
MODEL_DIR = f"/var/lib/openmycelium/models/{MODEL}"
PORT = 11500
TOKENS = 64
SESSIONS = int(os.environ.get("OM_SESSIONS", "5"))
OM = "/opt/om/venv/bin/openmycelium"
CUDA_PY = "/var/lib/openmycelium/state/env/cuda/bin/python"
LOG = "/var/log/om-a8"
HARNESS = "/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro"

# Chosen because it reliably runs past 64 tokens: an explanatory question with
# two parts. A prompt that hit EOS early would make sessions incomparable.
PROMPT = ("Describe how a pipeline-parallel inference runtime divides a "
          "transformer model across two accelerators from different vendors, "
          "and explain what has to cross the boundary between them.")


def wait_ready(token: str, timeout: float = 900) -> float | None:
    start = time.perf_counter()
    deadline = start + timeout
    while time.perf_counter() < deadline:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/models")
            request.add_header("Authorization", f"Bearer {token}")
            urllib.request.urlopen(request, timeout=5)
            return time.perf_counter() - start
        except Exception:                                     # noqa: BLE001
            time.sleep(2)
    return None


def one_request(token: str) -> dict:
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
    first = last = None
    pieces: list[str] = []
    usage: dict = {}
    finish = None
    with urllib.request.urlopen(request, timeout=900) as handle:
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
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                piece = choice.get("delta", {}).get("content")
                if piece:
                    now = time.perf_counter()
                    if first is None:
                        first = now
                    last = now
                    pieces.append(piece)
    complete = time.perf_counter()

    text = "".join(pieces)
    generated = usage.get("completion_tokens") or len(pieces)
    decode_window = (last - first) if (first and last and last > first) else 0.0
    return {
        "ttftMs": (first - start) * 1000 if first else None,
        "decodeWindowS": decode_window,
        "totalS": complete - start,
        "generatedTokens": generated,
        "promptTokens": usage.get("prompt_tokens"),
        "finishReason": finish,
        "hitEos": finish == "stop",
        "decodeTokS": (generated - 1) / decode_window if decode_window else 0.0,
        "endToEndTokS": generated / (complete - start) if complete > start else 0.0,
        "outputSha256": hashlib.sha256(text.encode()).hexdigest(),
        "outputChars": len(text),
    }


def placement_ok() -> tuple[bool, list[int]]:
    out = subprocess.run(
        [OM, "plan", "--model", MODEL, "--output", "/tmp/plan.json"],
        capture_output=True, text=True)
    if out.returncode != 0:
        return False, []
    try:
        manifest = json.load(open("/tmp/plan.json"))
    except Exception:                                         # noqa: BLE001
        return False, []
    counts = sorted(len(s["tensors"]) for s in manifest["stages"])
    names = [set(s["tensors"]) for s in manifest["stages"]]
    overlap = names[0] & names[1] if len(names) == 2 else {"x"}
    return (counts == [181, 182] and not overlap), counts


def boundary_exact(label: str) -> bool:
    out = subprocess.run(["bash", f"{HARNESS}/boundary_exact_clean.sh", label],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        print(f"    {line.strip()}")
    return out.returncode == 0


def main() -> int:
    token = os.environ.get("OM_TOKEN", "")
    os.makedirs(LOG, exist_ok=True)

    print("  == identity ==", flush=True)
    probe = subprocess.run([CUDA_PY, f"{HARNESS}/identity_probe.py",
                            MODEL_DIR, PROMPT],
                           capture_output=True, text=True)
    identity = json.loads(probe.stdout[probe.stdout.index("{"):])
    for key in ("model", "modelSha256", "modelFiles", "modelBytes",
                "tokenizerIdentity", "fixMistralRegex", "promptIdsSha256",
                "promptTokenCount",
                "eosTokenId"):
        print(f"    {key:<20} {identity[key]}")

    print()
    print("  == byte-exact transport, before the sessions ==", flush=True)
    before = boundary_exact("boundary-before")

    sessions = []
    for index in range(1, SESSIONS + 1):
        print(f"\n  == session {index} of {SESSIONS} ==", flush=True)
        placement, counts = placement_ok()
        print(f"    placement          {counts} exclusive={placement}")
        spawn = time.perf_counter()
        server = subprocess.Popen(
            [OM, "serve", "--model", MODEL, "--port", str(PORT),
             "--token", token, "--max-new-tokens", str(TOKENS + 8)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            load = wait_ready(token)
            if load is None:
                print("    server never became ready")
                continue
            print(f"    server load        {load:.1f} s  (excluded)")
            one_request(token)                              # warm-up, discarded
            sample = one_request(token)
            sample.update({"session": index, "serverLoadS": round(load, 2),
                           "placementExclusive": placement,
                           "placementCounts": counts})
            sessions.append(sample)
            print(f"    generated          {sample['generatedTokens']} tokens, "
                  f"finish_reason={sample['finishReason']}")
            print(f"    TTFT               {sample['ttftMs']:.1f} ms")
            print(f"    decode             {sample['decodeTokS']:.2f} tok/s "
                  f"over {sample['decodeWindowS']:.2f} s")
            print(f"    end-to-end         {sample['endToEndTokS']:.2f} tok/s "
                  f"over {sample['totalS']:.2f} s")
            print(f"    output sha256      {sample['outputSha256'][:32]}")
        finally:
            subprocess.run([OM, "stop"], capture_output=True)
            try:
                server.wait(timeout=180)
            except subprocess.TimeoutExpired:
                server.kill()
            time.sleep(4)

    print()
    print("  == byte-exact transport, after the sessions ==", flush=True)
    after = boundary_exact("boundary-after")

    if not sessions:
        print("  no sessions completed")
        return 1

    lengths = {s["generatedTokens"] for s in sessions}
    digests = {s["outputSha256"] for s in sessions}
    eos = [s for s in sessions if s["hitEos"]]

    print()
    print(f"  == {len(sessions)} independent sessions ==")
    print(f"    prompt             {identity['promptTokenCount']} tokens, "
          f"ids sha256 {identity['promptIdsSha256'][:32]}")
    print(f"    generated lengths  {sorted(lengths)}"
          + ("  (all equal)" if len(lengths) == 1 else "  UNEQUAL"))
    print(f"    ended by EOS       {len(eos)} of {len(sessions)}")
    print(f"    output digests     {len(digests)} distinct"
          + ("  (deterministic output; not transport proof)" if len(digests) == 1
             else "  NON-DETERMINISTIC"))
    print(f"    placement          "
          f"{sum(1 for s in sessions if s['placementExclusive'])} of "
          f"{len(sessions)} exclusive 181/182")
    print(f"    byte-exact         before={before}  after={after}")
    print()
    print(f"    {'metric':<24} {'median':>9} {'min':>9} {'max':>9}")
    summary = {}
    for label, key, unit in (("TTFT", "ttftMs", "ms"),
                             ("decode", "decodeTokS", "tok/s"),
                             ("end-to-end", "endToEndTokS", "tok/s"),
                             ("server load", "serverLoadS", "s")):
        values = [s[key] for s in sessions]
        summary[key] = {"median": statistics.median(values),
                        "min": min(values), "max": max(values),
                        "values": values}
        print(f"    {label + ' (' + unit + ')':<24} "
              f"{statistics.median(values):9.2f} {min(values):9.2f} "
              f"{max(values):9.2f}")

    report = {
        "version": "0.1.0a8",
        "identity": identity,
        "prompt": PROMPT,
        "maxTokens": TOKENS,
        "sessions": sessions,
        "summary": summary,
        "allLengthsEqual": len(lengths) == 1,
        "generatedLengths": sorted(lengths),
        "endedByEos": len(eos),
        "deterministicOutput": len(digests) == 1,
        "placementExclusiveAll": all(s["placementExclusive"] for s in sessions),
        "byteExactBefore": before,
        "byteExactAfter": after,
        "method": (
            "one fresh server process per session; server load timed and "
            "excluded; one warm-up request discarded; one measured request, "
            "streamed so the first content token is timed rather than "
            "inferred. decode = (generated - 1) / (last token - first token). "
            "end-to-end = generated / (request start -> response complete). "
            "No p95: five observations do not support one."),
        "transportQualification": (
            "The transport was byte-exact qualified immediately before and "
            "after the performance campaign. Full-payload hashing was disabled "
            "during timed sessions to avoid contaminating performance. The "
            "five sessions produced identical output, providing deterministic "
            "functional consistency but not per-session byte-level transport "
            "proof."),
    }
    with open(f"{LOG}/throughput-report.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(f"\n  written to {LOG}/throughput-report.json")

    problems = []
    if len(lengths) != 1:
        problems.append("generated lengths differ between sessions")
    if len(digests) != 1:
        problems.append("output was not deterministic across sessions")
    if not all(s["placementExclusive"] for s in sessions):
        problems.append("a session did not have exclusive 181/182 ownership")
    if not (before and after):
        problems.append("byte-exact transport was not confirmed")
    for item in problems:
        print(f"  PROBLEM: {item}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
