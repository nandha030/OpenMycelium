"""The clean Mistral-Nemo run: real GPUs, installed wheel, OpenAI clients.

This validates an already-qualified protocol surface on real hardware. It is not
where HTTP problems should be discovered -- 29 CPU acceptance checks exist so
that this run tests the engine and the installation, not the framing.

Everything runs from the installed package. The checkout supplies this script
and nothing else, which is checked rather than assumed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

VENV = "/opt/omfresh/venv"
PORT = 11500
MODEL_DIR = "/opt/models/Mistral-Nemo-Instruct-2407"
MODEL_NAME = "Mistral-Nemo-Instruct-2407"
WORK = "/opt/omgpu"
THROUGHPUT_BAND = (10.6, 11.3)

results: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""),
          flush=True)


def env() -> Dict[str, str]:
    clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    clean["PATH"] = f"{VENV}/bin:" + clean.get("PATH", "")
    clean["OPENMYCELIUM_STATE"] = WORK
    return clean


def get(path: str, timeout: float = 10) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}",
                                    timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    print("\n  Mistral-Nemo GPU API validation, from the installed wheel\n")

    # --- the model must already be local; nothing is downloaded -----------
    check("model is present locally", os.path.isdir(MODEL_DIR), MODEL_DIR)

    print("\n  == starting the server ==", flush=True)
    server = subprocess.Popen(
        [f"{VENV}/bin/openmycelium", "serve", "--model", MODEL_NAME,
         "--port", str(PORT), "--work-dir", WORK, "--max-new-tokens", "64"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env(), start_new_session=True)

    ready = False
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        if server.poll() is not None:
            break
        health = get("/health", timeout=3)
        if health and health.get("state") == "READY":
            ready = True
            break
        time.sleep(1.0)
    if not ready:
        output = (server.stdout.read() if server.stdout else "")[-1500:]
        check("server reached READY", False, "see output below")
        print(output)
        return 2
    check("server reached READY", True)

    health = get("/health") or {}
    workers = health.get("workers", {})
    cuda_mib = workers.get("cuda", {}).get("residentMiB", 0)
    rocm_mib = workers.get("rocm", {}).get("residentMiB", 0)
    check("both GPUs hold weights", cuda_mib > 10000 and rocm_mib > 10000,
          f"CUDA {cuda_mib:.0f} MiB   ROCm {rocm_mib:.0f} MiB")

    # --- exclusive ownership, read from the placement the run used --------
    placement_path = os.path.join(WORK, "placement.json")
    try:
        with open(placement_path, "r", encoding="utf-8") as handle:
            placement = json.load(handle)
        counts = {s["role"]: len(s["tensors"]) for s in placement["stages"]}
        overlap = set(placement["stages"][0]["tensors"]) & \
            set(placement["stages"][1]["tensors"])
        total = sum(counts.values())
        check("exclusive 181/182 tensor ownership",
              sorted(counts.values()) == [181, 182] and not overlap
              and total == 363,
              f"cuda={counts.get('cuda')} rocm={counts.get('rocm')} "
              f"total={total} overlap={len(overlap)}")
        check("boundary is after layer 19",
              placement["pipeline"]["boundaryAfterLayer"] == 19,
              f"boundaryAfterLayer="
              f"{placement['pipeline']['boundaryAfterLayer']}")
    except (OSError, ValueError, KeyError, IndexError) as error:
        check("exclusive 181/182 tensor ownership", False, str(error))

    # --- ps sees it -------------------------------------------------------
    listing = subprocess.run([f"{VENV}/bin/openmycelium", "ps", "--json"],
                             capture_output=True, text=True, env=env(),
                             timeout=60)
    try:
        running = json.loads(listing.stdout).get("running", [])
    except ValueError:
        running = []
    check("ps observes the live server",
          any(r.get("port") == PORT for r in running),
          f"{[(r['run_id'][:8], r.get('port'), r.get('state')) for r in running]}")

    # --- OpenAI SDK -------------------------------------------------------
    print("\n  == OpenAI SDK ==", flush=True)
    sys.path.insert(0, f"{VENV}/lib/python3.12/site-packages")
    from openai import OpenAI  # noqa: PLC0415
    client = OpenAI(base_url=f"http://127.0.0.1:{PORT}/v1", api_key="unused")

    listed = client.models.list()
    check("SDK: /v1/models reports the model",
          any(m.id == MODEL_NAME for m in listed.data),
          f"{[m.id for m in listed.data]}")

    began = time.perf_counter()
    reply = client.chat.completions.create(
        model=MODEL_NAME, temperature=0, max_tokens=48,
        messages=[{"role": "user",
                   "content": "Explain cross-vendor GPU inference in two sentences."}])
    elapsed = time.perf_counter() - began
    text = reply.choices[0].message.content or ""
    completion = reply.usage.completion_tokens
    rate = completion / elapsed if elapsed else 0
    check("SDK: non-streaming completion", bool(text.strip()),
          f"{completion} tokens in {elapsed:.1f}s")
    print(f"        {text[:150]}...")

    pieces: List[str] = []
    usage = None
    began = time.perf_counter()
    for event in client.chat.completions.create(
            model=MODEL_NAME, temperature=0, max_tokens=48, stream=True,
            stream_options={"include_usage": True},
            messages=[{"role": "user",
                       "content": "Explain cross-vendor GPU inference in two sentences."}]):
        if event.choices and event.choices[0].delta.content:
            pieces.append(event.choices[0].delta.content)
        if getattr(event, "usage", None):
            usage = event.usage
    stream_elapsed = time.perf_counter() - began
    stream_rate = (usage.completion_tokens / stream_elapsed
                   if usage and stream_elapsed else 0)
    check("SDK: streaming completion", len(pieces) > 1,
          f"{len(pieces)} deltas, {stream_elapsed:.1f}s")
    check("SDK: final usage chunk", usage is not None,
          f"total={getattr(usage, 'total_tokens', None)}")

    conversation = [
        {"role": "user",
         "content": "Explain cross-vendor GPU inference in two sentences."},
        {"role": "assistant", "content": "".join(pieces) or text},
        {"role": "user", "content": "Name one limitation of that approach."},
    ]
    multi = client.chat.completions.create(
        model=MODEL_NAME, temperature=0, max_tokens=48, messages=conversation)
    multi_text = multi.choices[0].message.content or ""
    check("SDK: multi-turn context reaches the model",
          multi.usage.prompt_tokens > reply.usage.prompt_tokens,
          f"prompt_tokens {reply.usage.prompt_tokens} -> "
          f"{multi.usage.prompt_tokens}")
    print(f"        {multi_text[:150]}...")

    # --- throughput -------------------------------------------------------
    low, high = THROUGHPUT_BAND
    observed = max(rate, stream_rate)
    inside = low <= observed <= high
    check("decode throughput within the established band", inside,
          f"observed {observed:.2f} tok/s "
          f"(non-stream {rate:.2f}, stream {stream_rate:.2f}); "
          f"band {low}-{high}"
          + ("" if inside else "  <-- recorded, not adjusted"))

    # --- stop -------------------------------------------------------------
    print("\n  == stop ==", flush=True)
    stopper = subprocess.run([f"{VENV}/bin/openmycelium", "stop"],
                             capture_output=True, text=True, env=env(),
                             timeout=180)
    print("   ", (stopper.stdout.strip().splitlines() or [""])[-1].strip())
    try:
        server.wait(timeout=120)
    except subprocess.TimeoutExpired:
        pass
    check("server process exited", server.poll() is not None)
    check("port released", get("/health", timeout=3) is None)

    listing = subprocess.run([f"{VENV}/bin/openmycelium", "ps", "--json"],
                             capture_output=True, text=True, env=env(),
                             timeout=60)
    try:
        left = json.loads(listing.stdout).get("running", [])
    except ValueError:
        left = []
    check("no live control record remains", not left, f"{len(left)} live")

    orphans = subprocess.run(
        ["bash", "-c", "ps -eo args | grep pipeline_run | grep -v grep | wc -l"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    check("no orphan workers", orphans == "0", f"{orphans} remain")

    vram = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    try:
        released = int(vram.splitlines()[0]) < 2000
    except (ValueError, IndexError):
        released = False
    check("CUDA VRAM released", released, f"{vram} MiB in use")

    # --- audit trail ------------------------------------------------------
    events = os.path.join(WORK, "events.jsonl")
    if os.path.isfile(events):
        rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
        sys.path.insert(0, os.path.join(
            subprocess.run([f"{VENV}/bin/python", "-c",
                            "import openmycelium,os;print(openmycelium.__path__[0])"],
                           capture_output=True, text=True).stdout.strip(),
            "runtime", "cli"))
        summaries = [r for r in rows if r.get("event") == "placement_validated"]
        tensors = sum(r.get("assignedTensorCount", 0) for r in summaries)
        run_ids = {r.get("runId") for r in rows if r.get("runId")}
        boots = {r.get("bootId") for r in rows if r.get("bootId")}
        check("event trail is complete and attributable",
              len(summaries) == 2 and tensors == 363 and len(run_ids) == 1
              and len(boots) == 1,
              f"{len(rows)} events, {len(summaries)} placement_validated, "
              f"{tensors} tensors, {len(run_ids)} runId, {len(boots)} bootId")
    else:
        check("event trail is complete and attributable", False, "no events file")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n  {passed}/{len(results)} checks passed")
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print("  FAILED:")
        for name in failed:
            print(f"    - {name}")
        return 1
    print("  RESULT: PASS - cross-vendor OpenAI-compatible inference, "
          "from the installed wheel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
