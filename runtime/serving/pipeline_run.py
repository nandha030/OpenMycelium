"""Prefill and cached decode across the vendor boundary, over one bridge.

    CUDA   embedding + layers 0-19  ->  MCCL  ->  ROCm  layers 20-39 + head

Two milestones share this driver because they must share the transport, the
split, and the selection policy; running them through separate code would leave
open whether a decode result depended on something prefill did not do.

    prefill   one forward per sequence length, no cache, timing and memory
              decomposed by stage
    oracle    prefill with a cache, then N decode steps, each compared against
              recomputing the whole prefix with no cache at all

The oracle comparison happens inside the ROCm process, between two tensors that
its own kernels produced moments apart. That is deliberate: a CPU reference
would reintroduce cross-backend arithmetic differences and blur exactly the
off-by-one, RoPE-offset and mask-growth faults this is meant to catch.

Only the hidden state crosses the boundary. Keys and values stay on the GPU
that produced them, which the per-step cache reports check rather than assume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "mccl", "src"))
sys.path.insert(0, os.path.join(_HERE, "..", "scheduler"))

from greedy import greedy_token, ranked_candidates  # noqa: E402
from audit import (AuditError, EventWriter, prompt_ids_hash,  # noqa: E402
                   valid_run_id)
from profiler import METHODS, OFF, Profiler  # noqa: E402
from model_inspect import GIB, MIB, inspect_model  # noqa: E402
from placement import (ManifestError, load_placement, stage_assignment,  # noqa: E402
                       validate_for_worker)
from prompt_tokens import DEFAULT_PROMPT, ids_of_length, load_tokenizer  # noqa: E402
from stage_loader import StageLoader, validate_ownership  # noqa: E402
from stage_model import BoundaryMeta, MistralStage, StageSpec  # noqa: E402

try:
    from mccl.xvendor import ActivationHeader
except ImportError:
    ActivationHeader = None                                  # type: ignore


# --------------------------------------------------------------------- wire

def _send(sock: socket.socket, blob: bytes) -> None:
    sock.sendall(struct.pack("<I", len(blob)) + blob)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    parts, left = [], count
    while left:
        chunk = sock.recv(min(left, 1 << 20))
        if not chunk:
            raise RuntimeError(f"peer closed with {left}/{count} bytes outstanding")
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def _recv(sock: socket.socket) -> bytes:
    return _recv_exact(sock, struct.unpack("<I", _recv_exact(sock, 4))[0])


def _send_json(sock: socket.socket, payload: Dict[str, Any]) -> None:
    _send(sock, json.dumps(payload).encode("utf-8"))


def _recv_json(sock: socket.socket) -> Dict[str, Any]:
    return json.loads(_recv(sock).decode("utf-8"))


def mono() -> float:
    """A timestamp both processes can compare directly.

    `CLOCK_MONOTONIC` is system-wide on Linux, so a stamp taken in the CUDA
    process and one taken in the ROCm process lie on the same timeline. That is
    what makes a true one-way measurement possible: the earlier `wireMs` figure
    subtracted one process's total from the other's round trip, which double
    counted the receiver's own read of the payload and reported roughly a
    constant regardless of size -- a sure sign it was not measuring transfer.
    """
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def exact_bytes(host: Any, torch: Any) -> bytes:
    """The tensor's own bytes, reinterpreted rather than converted.

    `view(uint8)` aliases the same storage, so this is the payload itself. It
    goes through NumPy's buffer rather than a Python list because a 20 MiB
    prefill activation is 20 million list elements, which would dominate the
    measurement it is supposed to be checking.
    """
    return host.contiguous().view(torch.uint8).numpy().tobytes()


# ---------------------------------------------------------------- reporting

def contract(host: Any, torch: Any, qualify: bool) -> Dict[str, Any]:
    """Shape, dtype, layout and size; the full digest only when qualifying.

    Hashing 20 MiB per transfer is right when the point is to prove byte
    equality and wrong when the point is to measure time to first token, so the
    caller says which run this is.
    """
    record = {
        "shape": [int(d) for d in host.shape],
        "dtype": str(host.dtype),
        "contiguous": bool(host.is_contiguous()),
        "strides": [int(s) for s in host.stride()],
        "elements": int(host.numel()),
        "bytes": int(host.numel() * host.element_size()),
    }
    if qualify:
        record["sha256"] = hashlib.sha256(exact_bytes(host, torch)).hexdigest()
    return record


def logit_summary(logits: Any, torch: Any) -> Dict[str, Any]:
    flat = logits.reshape(-1)
    return {
        "shape": [int(d) for d in logits.shape],
        "dtype": str(logits.dtype),
        "allFinite": bool(torch.isfinite(flat).all().item()),
        "greedyToken": greedy_token(flat, torch),
        "candidates": ranked_candidates(flat, torch, 5),
    }


# -------------------------------------------------------------------- stages

def _failure_phase(message: str) -> str:
    """Classify a manifest rejection so the audit trail says what went wrong."""
    text = message.lower()
    for needle, phase in (("digest", "digest"), ("schema", "schema"),
                          ("fingerprint", "fingerprint"),
                          ("ownership", "ownership"), ("role", "role"),
                          ("runtime assignment", "role")):
        if needle in text:
            return phase
    return "read"


def build_stage(model_path: str, vendor: str, layer_indices, holds_embedding: bool,
                holds_head: bool, device: str, torch: Any, tensor_names
                ) -> Tuple[MistralStage, Dict[str, Any]]:
    spec = StageSpec(vendor, tuple(layer_indices), holds_embedding, holds_head, device)
    stage = MistralStage(os.path.join(model_path, "config.json"), spec, torch)
    loader = StageLoader(model_path, vendor, tensor_names, device, torch)
    metrics = loader.load()
    stage.install(loader.tensors)
    loader.tensors.clear()
    return stage, metrics.to_dict()


class DeviceMetrics:
    """Timing and memory probes that degrade to no-ops off the GPU.

    The smoke test runs both stages on the CPU, where these calls do not exist;
    guarding them here keeps the driver a single code path rather than two that
    could drift apart.
    """

    def __init__(self, torch: Any, device: str):
        self.torch = torch
        self.on_gpu = device.startswith("cuda")

    def sync(self) -> None:
        if self.on_gpu:
            self.torch.cuda.synchronize()

    def reset_peak(self) -> None:
        if self.on_gpu:
            self.torch.cuda.reset_peak_memory_stats()

    def allocated(self) -> int:
        return self.torch.cuda.memory_allocated() if self.on_gpu else 0

    def peak(self) -> int:
        return self.torch.cuda.max_memory_allocated() if self.on_gpu else 0


def resolve_device(vendor: str, torch: Any, allow_cpu: bool = False) -> Tuple[str, str]:
    if allow_cpu:
        # Only for smoke-testing the protocol on a tiny checkpoint. It proves
        # the driver sequences correctly; it proves nothing about the vendors.
        return "cpu", "cpu (smoke test)"
    if not torch.cuda.is_available():
        raise RuntimeError(f"stage {vendor}: no GPU runtime available")
    is_rocm = bool(getattr(torch.version, "hip", None))
    if (vendor == "rocm") != is_rocm:
        build = "ROCm" if is_rocm else "CUDA"
        raise RuntimeError(
            f"stage {vendor} requested but this is a {build} PyTorch build")
    return "cuda:0", torch.cuda.get_device_name(0)


# -------------------------------------------------------------- sender side

def run_sender(args, torch, model, assignment, first) -> Dict[str, Any]:
    device, gpu = resolve_device("cuda", torch, args.allow_cpu)
    probe = DeviceMetrics(torch, device)
    events = getattr(args, "audit", None)
    if events:
        events.emit("loading", device=device, gpu=gpu,
             layers=[int(i) for i in first.layers])
    load_started = time.perf_counter()
    stage, loader_metrics = build_stage(args.model, "cuda", first.layers, True,
                                        False, device, torch, assignment["cuda"])
    if events:
        events.emit("loaded", device=device, gpu=gpu,
             residentMiB=round(probe.allocated() / MIB, 1),
             loadSeconds=round(time.perf_counter() - load_started, 1))
    report: Dict[str, Any] = {
        "role": "cuda", "device": device, "gpu": gpu, "loader": loader_metrics,
        "layers": [int(i) for i in first.layers],
        "loadSeconds": round(time.perf_counter() - load_started, 1),
        "weightsMiB": round(probe.allocated() / MIB, 1),
    }

    tokenizer = load_tokenizer(args.model)
    hidden_size = int(model.config["hidden_size"])
    profiler = Profiler(torch, device, args.timing_method)
    report["timingMethod"] = profiler.method
    reconciliation: List[Dict[str, Any]] = []
    #: Running total of resolved sample time, so each step contributes a delta
    #: rather than a reset that would discard the run's own profile.
    profiler_seen = [0.0]

    if args.ready_file:
        deadline = time.time() + args.accept_timeout
        while not os.path.exists(args.ready_file):
            if time.time() > deadline:
                report["error"] = "receiver never signalled readiness"
                return report
            time.sleep(0.5)
    sock = socket.create_connection((args.peer, args.port), timeout=args.accept_timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if events:
        # Connected is not ready. In generate mode readiness is announced only
        # after a warm-up forward has crossed the boundary, so READY means both
        # stages have executed kernels rather than merely allocated memory.
        events.emit("connected" if args.mode in ("generate", "serve") else "ready",
             device=device, residentMiB=round(probe.allocated() / MIB, 1),
             peer=args.peer)

    def step(ids: List[int], positions: List[int], past: int, cache: Optional[Any],
             tag: Dict[str, Any], qualify: Optional[bool] = None) -> Dict[str, Any]:
        """One boundary crossing: embed, run layers 0-19, ship the hidden state."""
        qualify = args.qualify if qualify is None else qualify
        meta = BoundaryMeta(1, len(ids), hidden_size, "bf16", list(positions),
                            list(positions), use_cache=cache is not None,
                            past_length=past)
        tensor_ids = torch.tensor([ids], dtype=torch.long, device=device)
        probe.sync()
        probe.reset_peak()
        resident = probe.allocated()
        t0 = mono()
        with torch.inference_mode():
            hidden = stage.forward(tensor_ids, meta, past_key_values=cache,
                                   profiler=profiler)
        # Synchronised, so the stamp is when the producer's kernels have
        # actually finished, not when they were queued.
        probe.sync()
        t_producer_done = mono()
        # Stage-level end to end, measured the existing way. The per-operation
        # sum must reconcile with this or the profiler is measuring something
        # other than the work.
        stage_ms = (t_producer_done - t0) * 1000.0
        profiler.collect()

        host = hidden.detach().to("cpu")
        probe.sync()
        t_d2h_done = mono()
        payload = exact_bytes(host, torch)
        expected = 1 * len(ids) * hidden_size * 2
        if len(payload) != expected:
            raise RuntimeError(
                f"boundary payload is {len(payload)} bytes, expected {expected}; "
                "only the hidden state may cross")

        control = dict(tag)
        control.update({
            "op": "step",
            "meta": meta.to_dict(),
            "qualify": bool(qualify),
            "sentContract": contract(host, torch, qualify),
            "maskShape": stage.last_mask_shape,
            "positionSpan": [positions[0], positions[-1]],
            "warmState": tag.get("warmState", "steady-state"),
            "senderCache": stage.cache_report(cache),
            "senderComputeMs": round((t_producer_done - t0) * 1000, 3),
            "senderDeviceToHostMs": round((t_d2h_done - t_producer_done) * 1000, 3),
            "senderWorkspaceMiB": round(
                (probe.peak() - resident) / MIB, 2),
        })
        if profiler.enabled:
            total = sum(sum(v) for v in profiler.samples.values())
            summed = total - profiler_seen[0]
            profiler_seen[0] = total
            reconciliation.append({
                "label": tag.get("label", ""),
                "warmState": tag.get("warmState", "steady-state"),
                "stageEndToEndMs": round(stage_ms, 4),
                "summedOperationsMs": round(summed, 4),
                "unaccountedMs": round(stage_ms - summed, 4),
                "coverage": round(summed / stage_ms, 4) if stage_ms > 0 else None,
            })
        _send_json(sock, control)
        if ActivationHeader is not None:
            header = ActivationHeader("bf16", (1, len(ids), hidden_size))
            header.validate()
            if header.byte_size != len(payload):
                raise RuntimeError("framing disagrees with the payload size")
            _send(sock, header.pack())
        t_wire_start = mono()
        _send(sock, payload)
        t_sendall_done = mono()
        reply = _recv_json(sock)
        t_reply = mono()
        if "error" in reply:
            raise RuntimeError(f"receiver reported: {reply['error']}")

        # The full boundary crossing, measured across both processes on one
        # shared monotonic clock:
        #   producer kernels done -> D2H done -> last payload byte received
        #   -> H2D done -> consumer tensor resident and synchronised
        received_at = reply["tPayloadComplete"]
        # "Consumer ready" is the instant the host-to-device copy has been
        # synchronised and the consumer's first kernel could run. In this
        # synchronous design that is the same stamp; nothing is hidden between
        # them, and the consumer's own compute is reported separately rather
        # than folded into the crossing.
        ready_at = reply["tHostToDeviceComplete"]
        crossing = {
            "deviceToHostMs": round((t_d2h_done - t_producer_done) * 1000, 3),
            "framingMs": round((t_wire_start - t_d2h_done) * 1000, 3),
            "wireMs": round((received_at - t_wire_start) * 1000, 3),
            "hostToDeviceMs": round((ready_at - received_at) * 1000, 3),
            "producerDoneToConsumerReadyMs": round(
                (ready_at - t_producer_done) * 1000, 3),
            "consumerComputeMs": reply["receiverComputeMs"],
            "sendallReturnedBeforeLastByte": bool(t_sendall_done < received_at),
            "bytes": len(payload),
        }
        wire_seconds = received_at - t_wire_start
        crossing["wireMBps"] = round(
            len(payload) / wire_seconds / 1e6, 1) if wire_seconds > 0 else None
        end_to_end = ready_at - t_producer_done
        crossing["endToEndMBps"] = round(
            len(payload) / end_to_end / 1e6, 1) if end_to_end > 0 else None
        # If the two clocks did not share a timeline these orderings would not
        # hold, and every figure above would be fiction. Checked, not assumed.
        crossing["clockOrderingHolds"] = bool(
            t_wire_start <= received_at <= ready_at
            <= reply["tConsumerReady"] <= t_reply)
        reply["crossing"] = crossing
        reply["senderRoundTripMs"] = round((t_reply - t_wire_start) * 1000, 3)
        for key in ("senderComputeMs", "senderDeviceToHostMs",
                    "senderWorkspaceMiB", "maskShape", "positionSpan"):
            reply[key] = control[key]
        return reply

    def reset() -> None:
        _send_json(sock, {"op": "reset"})
        _recv_json(sock)

    try:
        if args.mode == "prefill":
            report["prefill"] = _sender_prefill(args, tokenizer, step, reset,
                                               profiler, profiler_seen)
        elif args.mode == "generate":
            report["generate"] = _sender_generate(args, tokenizer, stage, step,
                                                  reset, events, profiler,
                                                  profiler_seen)
        elif args.mode == "serve":
            report["serve"] = _sender_serve(args, tokenizer, stage, step,
                                            reset, events, profiler,
                                            profiler_seen)
        else:
            report["oracle"] = _sender_oracle(args, tokenizer, stage, step, reset)
    finally:
        try:
            _send_json(sock, {"op": "done"})
            _recv_json(sock)
        except OSError:
            pass
        sock.close()
    if profiler.enabled:
        report["profile"] = profiler.report()
        report["profile"]["layerTotals"] = {
            phase: profiler.layer_totals(f"layer.{phase}.")
            for phase in ("prefill", "decode")}
        report["reconciliation"] = reconciliation
    return report


def _sender_prefill(args, tokenizer, step, reset, profiler=None,
                    profiler_seen=None) -> List[Dict[str, Any]]:
    """Each length twice: once hashed to prove byte equality, once to be timed.

    Hashing 20 MiB on both sides is the right thing to do when the claim is
    byte equality and the wrong thing to do when the claim is time to first
    token -- it would land inside the interval being measured. Two passes keep
    each claim clean, and the timing pass is the second one, so it is not
    measuring a cold allocator.
    """
    rows = []
    for length in args.seq_lens:
        ids, provenance = ids_of_length(tokenizer, args.prompt, length,
                                        not args.no_chat_template)
        positions = list(range(length))

        reset()
        qualified = step(ids, positions, 0, None,
                         {"path": "fresh", "step": 0, "label": f"qualify{length}",
                          "warmState": "first-touch"},
                         qualify=True)

        # The first touch of a shape selects kernels: measured at 113x the
        # repeat cost on ROCm at sequence 1, with the extra time spent outside
        # any timed region. It is run, recorded, and then excluded rather than
        # pooled with steady state.
        repeats = max(1, int(getattr(args, "prefill_repeats", 1)))
        samples: List[float] = []
        compute_samples: List[float] = []
        consumer_samples: List[float] = []
        timed = None
        for attempt in range(repeats):
            first_touch = attempt == 0 and repeats > 1
            reset()
            started = time.perf_counter()
            timed = step(ids, positions, 0, None,
                         {"path": "fresh", "step": 0,
                          "label": f"timed{length}.{attempt}",
                          "warmState": "first-touch" if first_touch
                          else "steady-state"},
                         qualify=False)
            elapsed = (time.perf_counter() - started) * 1000
            if not first_touch:
                compute_samples.append(timed["senderComputeMs"])
                consumer_samples.append(timed["receiverComputeMs"])
            if first_touch and profiler is not None:
                # Drop the first-touch samples so the published profile and its
                # per-layer statistics describe steady state only.
                profiler.reset()
                profiler_seen[0] = 0.0
            else:
                samples.append(elapsed)
        elapsed = sorted(samples)[len(samples) // 2] if samples else elapsed

        rows.append({
            "sequenceLength": length,
            "tokenizer": provenance,
            "boundaryShape": qualified["receivedContract"]["shape"],
            "boundaryBytes": qualified["receivedContract"]["bytes"],
            "byteExact": qualified.get("byteExact"),
            "boundarySha256": qualified["receivedContract"].get("sha256"),
            "checks": qualified.get("checks"),
            "maskShape": qualified["maskShape"],
            "positionSpan": qualified["positionSpan"],
            "logits": qualified["logits"],
            "logitsAgreeAcrossPasses":
                qualified["logits"]["greedyToken"] == timed["logits"]["greedyToken"],
            "ttftMs": round(elapsed, 2),
            "ttftSamplesMs": [round(v, 2) for v in samples],
            "cudaComputeSamplesMs": [round(v, 3) for v in compute_samples],
            "rocmComputeSamplesMs": [round(v, 3) for v in consumer_samples],
            "decomposition": {
                "cudaComputeMs": timed["senderComputeMs"],
                "cudaDeviceToHostMs": timed["senderDeviceToHostMs"],
                "rocmComputeMs": timed["receiverComputeMs"],
            },
            # Measured across both processes on one monotonic clock, from the
            # producer's kernels finishing to the tensor being resident and
            # synchronised on the consumer.
            "crossing": timed["crossing"],
            "memory": {
                "cudaWorkspaceMiB": timed["senderWorkspaceMiB"],
                "rocmWorkspaceMiB": timed["receiverWorkspaceMiB"],
            },
        })
    return rows


def _sender_generate(args, tokenizer, stage, step, reset, events,
                     profiler=None, profiler_seen=None) -> Dict[str, Any]:
    """Prompt in, tokens out, streamed as they are produced.

    Decoding is greedy and nothing else: `sampling.DecodeRequest` refuses a
    stochastic request because the oracle validated the argmax, not the ranking
    of lower candidates.

    Detokenisation decodes the whole id list each step and emits the difference
    rather than decoding each id alone. A byte-level BPE token is often half a
    character, so per-token decoding produces replacement characters exactly
    where the text gets interesting.
    """
    from sampling import DecodeRequest  # noqa: PLC0415

    request = DecodeRequest(max_new_tokens=args.max_new_tokens).validate()
    # The prompt at its natural tokenised length, unless a length is forced for
    # a measurement run.
    pad_to = int(getattr(args, "pad_prompt_to", 0) or 0)
    ids, provenance = ids_of_length(tokenizer, args.prompt, pad_to,
                                    not args.no_chat_template) if pad_to > 0         else _tokenize_prompt(tokenizer, args)

    # Warm both runtimes before anything is timed. The first BF16 GEMM on a
    # cold context triggers kernel selection on each vendor, measured at 62
    # seconds on this machine with both runtimes cold at once -- while steady
    # state is around 100 ms per token. Charging that to the user's first
    # request, and calling it time-to-first-token, would misreport the runtime
    # by a factor of six hundred. It happens during QUALIFYING instead, which is
    # what that state is for: READY then means both stages have executed
    # kernels, not merely allocated memory.
    reset()
    warm_started = time.perf_counter()
    step([ids[0]], [0], 0, None,
         {"path": "fresh", "step": 0, "label": "warmup"}, qualify=False)
    warmup_ms = (time.perf_counter() - warm_started) * 1000
    if events:
        events.emit("warmed", warmupMs=round(warmup_ms, 1))
        # Earned now: the warm-up traversed both stages, so this asserts that
        # CUDA and ROCm have each run real kernels on real weights.
        events.emit("ready", warmupMs=round(warmup_ms, 1))

    reset()
    cache = stage.new_cache()
    started = time.perf_counter()
    prefill = step(ids, list(range(len(ids))), 0, cache,
                   {"path": "cached", "step": 0, "label": "prefill"},
                   qualify=False)
    ttft_ms = (time.perf_counter() - started) * 1000
    token = prefill["logits"]["greedyToken"]

    generated: List[int] = []
    step_ms: List[float] = []
    text_so_far = ""
    eos = _eos_ids(tokenizer)
    stopped = ""

    for index in range(request.max_new_tokens):
        generated.append(token)
        whole = tokenizer.decode(generated, skip_special_tokens=True)
        delta, text_so_far = whole[len(text_so_far):], whole
        if events:
            events.emit("token", index=index, tokenId=int(token),
                 text=delta)
        if token in eos:
            stopped = "eos"
            break
        if index + 1 >= request.max_new_tokens:
            stopped = "length"
            break
        position = len(ids) + index
        began = time.perf_counter()
        result = step([token], [position], position, cache,
                      {"path": "cached", "step": index + 1,
                       "label": f"decode{index + 1}"}, qualify=False)
        step_ms.append((time.perf_counter() - began) * 1000)
        token = result["logits"]["greedyToken"]

    elapsed = time.perf_counter() - started
    warm = step_ms[1:] or step_ms
    return {
        "prompt": args.prompt,
        "promptTokens": len(ids),
        "tokenizer": provenance,
        "decode": request.to_dict(),
        "generatedTokens": generated,
        "text": text_so_far,
        "stopReason": stopped or "length",
        "ttftMs": round(ttft_ms, 1),
        "warmupMs": round(warmup_ms, 1),
        "prefillTokensPerSecond": round(len(ids) / (ttft_ms / 1000), 1)
        if ttft_ms > 0 else None,
        "decodeTokensPerSecond": round(1000.0 / (sum(warm) / len(warm)), 2)
        if warm else None,
        "interTokenMs": {
            "p50": round(_percentile(step_ms, 50), 2),
            "p95": round(_percentile(step_ms, 95), 2),
            "p99": round(_percentile(step_ms, 99), 2),
        } if step_ms else {},
        "totalSeconds": round(elapsed, 2),
        "cacheAtEnd": stage.cache_report(cache),
        "decomposition": _decomposition(profiler, ttft_ms),
    }


def _full_breakdown(sender: Dict[str, Any], reply: Dict[str, Any],
                    ttft_ms: float) -> Dict[str, Any]:
    """Every term of one prefill, end to end, with the residual named.

    TTFT = CUDA embedding + CUDA layers
         + device-to-host + wire + host-to-device
         + ROCm layers + ROCm norm + ROCm head
         + unprofiledRuntimeOverhead

    The residual is what none of the instrumented regions covered -- position
    ids, mask construction, rotary tables, dtype checks, framing, Python
    dispatch. It stays a separate term: dividing it across layers would charge
    time to layers that did not spend it, and per-layer cost is exactly what a
    boundary decision reads.
    """
    receiver = reply.get("receiverDecomposition") or {}
    crossing = reply.get("crossing") or {}
    terms = {
        "cudaEmbeddingMs": sender.get("embeddingMs", 0.0),
        "cudaLayerSumMs": sender.get("layerSumMs", 0.0),
        "deviceToHostMs": crossing.get("deviceToHostMs", 0.0),
        "framingMs": crossing.get("framingMs", 0.0),
        "wireMs": crossing.get("wireMs", 0.0),
        "hostToDeviceMs": crossing.get("hostToDeviceMs", 0.0),
        "rocmLayerSumMs": receiver.get("layerSumMs", 0.0),
        "rocmFinalNormMs": receiver.get("finalNormMs", 0.0),
        "rocmLmHeadMs": receiver.get("lmHeadMs", 0.0),
    }
    accounted = sum(v for v in terms.values() if isinstance(v, (int, float)))
    terms["accountedMs"] = round(accounted, 4)
    terms["ttftMs"] = round(ttft_ms, 4)
    terms["unprofiledRuntimeOverheadMs"] = round(ttft_ms - accounted, 4)
    terms["coverage"] = round(accounted / ttft_ms, 4) if ttft_ms > 0 else None
    terms["residualIsNegative"] = bool(accounted > ttft_ms)
    terms["timingMethod"] = sender.get("timingMethod")
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in terms.items()}


def _decomposition_from(samples: Dict[str, float], method: str,
                        stage_ms: float) -> Dict[str, Any]:
    """Build a decomposition from already-summed per-label times."""
    embedding = samples.get("embedding", 0.0)
    layers = sum(v for k, v in samples.items() if k.startswith("layer."))
    norm = samples.get("final_norm", 0.0)
    head = samples.get("lm_head", 0.0)
    accounted = embedding + layers + norm + head
    return {
        "timingMethod": method,
        "embeddingMs": round(embedding, 4),
        "layerSumMs": round(layers, 4),
        "finalNormMs": round(norm, 4),
        "lmHeadMs": round(head, 4),
        "accountedMs": round(accounted, 4),
        "stageEndToEndMs": round(stage_ms, 4),
        "unprofiledRuntimeOverheadMs": round(stage_ms - accounted, 4),
        "coverage": round(accounted / stage_ms, 4) if stage_ms > 0 else None,
        "residualIsNegative": bool(accounted > stage_ms),
        "perLabelMs": {k: round(v, 4) for k, v in sorted(samples.items())},
    }


def _decomposition(profiler, stage_ms: float) -> Dict[str, Any]:
    """Where this request's CUDA-side time went.

    The part the profiler does not cover is reported as its own term. It is
    real work -- position ids, mask construction, rotary tables, dtype checks --
    and dividing it across layers would attribute time to layers that did not
    spend it, corrupting exactly the per-layer costs a boundary decision uses.
    """
    if profiler is None or not profiler.enabled:
        return {}
    profiler.collect()
    samples = profiler.samples
    embedding = sum(samples.get("embedding", []))
    layers = sum(sum(v) for k, v in samples.items() if k.startswith("layer."))
    norm = sum(samples.get("final_norm", []))
    head = sum(samples.get("lm_head", []))
    accounted = embedding + layers + norm + head
    return {
        "timingMethod": profiler.method,
        "embeddingMs": round(embedding, 4),
        "layerSumMs": round(layers, 4),
        "finalNormMs": round(norm, 4),
        "lmHeadMs": round(head, 4),
        "accountedMs": round(accounted, 4),
        "stageEndToEndMs": round(stage_ms, 4),
        "unprofiledRuntimeOverheadMs": round(stage_ms - accounted, 4),
        "coverage": round(accounted / stage_ms, 4) if stage_ms > 0 else None,
        # A negative residual is impossible -- the parts cannot exceed the
        # whole -- so it is flagged rather than reported as a small number.
        "residualIsNegative": bool(accounted > stage_ms),
        "perLabelMs": {k: round(sum(v), 4) for k, v in sorted(samples.items())},
    }


def _sender_serve(args, tokenizer, stage, step, reset, events, profiler=None,
                  profiler_seen=None) -> Dict[str, Any]:
    """Stay loaded and answer prompts as they arrive on stdin.

    This is the difference between a demonstration and something usable. The
    22.8 GiB of weights are loaded once; each request then costs only prompt
    processing and decode, instead of the ~208 s reload the one-shot path pays
    every time.

    Requests arrive as JSON Lines so the supervisor can pipe them in without a
    second socket, and each one starts from a fresh KV cache -- prompts are
    independent, and carrying a cache between them would leak one user's
    context into the next.
    """
    from sampling import DecodeRequest  # noqa: PLC0415

    # Warm both runtimes before announcing readiness, exactly as generate does:
    # the first BF16 kernels take tens of seconds to select and must not be
    # charged to the first request.
    reset()
    warm_started = time.perf_counter()
    warm_ids, _ = _tokenize_prompt(tokenizer, args)
    step([warm_ids[0]], [0], 0, None,
         {"path": "fresh", "step": 0, "label": "warmup"}, qualify=False)
    warmup_ms = (time.perf_counter() - warm_started) * 1000
    if events:
        events.emit("warmed", warmupMs=round(warmup_ms, 1))
        events.emit("ready", warmupMs=round(warmup_ms, 1))

    served, failures = 0, 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            if events:
                events.emit("request_failed", detail=f"bad JSON: {error}")
            failures += 1
            continue
        if request.get("op") == "shutdown":
            break
        identifier = str(request.get("id", served))
        try:
            result = _serve_one(args, tokenizer, stage, step, reset, events,
                                request, identifier, DecodeRequest,
                                profiler, profiler_seen)
            served += 1
        except Exception as error:                            # noqa: BLE001
            failures += 1
            if events:
                events.emit("request_failed", requestId=identifier,
                     detail=f"{type(error).__name__}: {error}")
            continue
        if events:
            events.emit("request_done", requestId=identifier, **result)

    return {"servedRequests": served, "failedRequests": failures,
            "warmupMs": round(warmup_ms, 1)}


def _serve_one(args, tokenizer, stage, step, reset, events, request, identifier,
               DecodeRequest, profiler=None, profiler_seen=None) -> Dict[str, Any]:
    """One prompt, from a clean cache, streamed token by token."""
    decode = DecodeRequest(
        max_new_tokens=int(request.get("maxNewTokens", args.max_new_tokens)),
        temperature=float(request.get("temperature", 0.0)),
        do_sample=bool(request.get("doSample", False)),
    ).validate()

    # A conversation, when the client sends one. Without this each turn is
    # templated as a lone user message and the model cannot see what was already
    # said -- which is not chat, it is a sequence of unrelated questions.
    # Explicit ids: the baseline campaign must send byte-identical work to every
    # session, and re-tokenising per session would let a tokenizer difference
    # masquerade as a performance difference.
    explicit = request.get("tokenIds")
    if explicit:
        ids = [int(i) for i in explicit]
        messages = None
    else:
        messages = request.get("messages")
    if messages:
        ids = _template_ids(tokenizer, messages)
    elif not explicit:
        prompt = str(request.get("prompt", ""))
        if not prompt:
            raise ValueError("request has no prompt, messages or tokenIds")
        saved, args.prompt = args.prompt, prompt
        try:
            ids, _ = _tokenize_prompt(tokenizer, args)
        finally:
            args.prompt = saved

    reset()
    cache = stage.new_cache()
    if profiler is not None and profiler.enabled:
        # Each request is its own measurement; samples must not leak across.
        profiler.reset()
        profiler_seen[0] = 0.0
    if events:
        events.emit("request_start", requestId=identifier,
             promptTokens=len(ids), maxNewTokens=decode.max_new_tokens)

    started = time.perf_counter()
    prefill = step(ids, list(range(len(ids))), 0, cache,
                   {"path": "cached", "step": 0, "label": f"prefill:{identifier}"},
                   qualify=False)
    ttft_ms = (time.perf_counter() - started) * 1000
    prefill_decomposition = _decomposition(profiler, ttft_ms)
    if profiler is not None and profiler.enabled:
        profiler.reset()
        if profiler_seen is not None:
            profiler_seen[0] = 0.0
    token = prefill["logits"]["greedyToken"]

    generated: List[int] = []
    step_ms: List[float] = []
    text_so_far = ""
    eos = _eos_ids(tokenizer)
    stopped = "length"
    decode_began = time.perf_counter()

    for index in range(decode.max_new_tokens):
        generated.append(token)
        whole = tokenizer.decode(generated, skip_special_tokens=True)
        delta, text_so_far = whole[len(text_so_far):], whole
        if events:
            events.emit("token", requestId=identifier, index=index,
                 tokenId=int(token), text=delta)
        if token in eos:
            stopped = "eos"
            break
        if index + 1 >= decode.max_new_tokens:
            break
        position = len(ids) + index
        began = time.perf_counter()
        result = step([token], [position], position, cache,
                      {"path": "cached", "step": index + 1,
                       "label": f"decode:{identifier}"}, qualify=False)
        step_ms.append((time.perf_counter() - began) * 1000)
        token = result["logits"]["greedyToken"]

    warm = step_ms[1:] or step_ms
    # The cache is dropped here so the next request starts clean and the memory
    # comes back; the weights stay exactly where they are.
    report = {
        "promptTokens": len(ids),
        "generatedTokens": len(generated),
        "text": text_so_far,
        "stopReason": stopped,
        "ttftMs": round(ttft_ms, 1),
        "decodeTokensPerSecond": round(1000.0 / (sum(warm) / len(warm)), 2)
        if warm else None,
        "interTokenMs": {"p50": round(_percentile(step_ms, 50), 2),
                         "p95": round(_percentile(step_ms, 95), 2),
                         "p99": round(_percentile(step_ms, 99), 2)}
        if step_ms else {},
        "totalSeconds": round(time.perf_counter() - started, 2),
        "promptIdsHash": prompt_ids_hash(ids),
        "cacheAtEnd": stage.cache_report(cache),
        "decomposition": prefill_decomposition,
        "breakdown": _full_breakdown(prefill_decomposition, prefill, ttft_ms),
        "decodeDecomposition": _decomposition(
            profiler, (time.perf_counter() - decode_began) * 1000),
        "decodeSteps": len(step_ms),
        "kvLengthAtEnd": stage.cache_report(cache).get("uniformLength"),
    }
    del cache
    return report


def _template_ids(tokenizer, messages) -> List[int]:
    """Token ids for a conversation, whatever shape the tokenizer returns.

    `apply_chat_template(tokenize=True)` returns a BatchEncoding on transformers
    5.x and a plain list on older ones, and may or may not add a batch
    dimension. Iterating the wrong one yields the string "input_ids", which is
    exactly the failure this unwraps -- so the shape is checked rather than
    assumed.
    """
    encoded = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True)
    if hasattr(encoded, "input_ids"):
        encoded = encoded.input_ids
    elif isinstance(encoded, dict):
        encoded = encoded["input_ids"]
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if encoded and isinstance(encoded[0], (list, tuple)):
        encoded = encoded[0]
    ids = [int(i) for i in encoded]
    if not ids:
        raise ValueError("the chat template produced no tokens")
    return ids


def _tokenize_prompt(args_tokenizer, args):
    """The prompt exactly as given, at whatever length it tokenises to."""
    from prompt_tokens import templated_ids  # noqa: PLC0415
    ids, text = templated_ids(args_tokenizer, args.prompt,
                              not args.no_chat_template)
    return ids, {"requestedLength": len(ids), "baseTemplatedLength": len(ids),
                 "promptRepeats": 1, "truncated": False,
                 "usedChatTemplate": not args.no_chat_template,
                 "templatePreview": text[:160], "firstIds": ids[:8],
                 "lastIds": ids[-4:]}


def _eos_ids(tokenizer) -> set:
    ids = set()
    for value in (getattr(tokenizer, "eos_token_id", None),
                  getattr(tokenizer, "pad_token_id", None)):
        if isinstance(value, int):
            ids.add(value)
        elif isinstance(value, (list, tuple)):
            ids.update(int(v) for v in value)
    return ids


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _sender_oracle(args, tokenizer, stage, step, reset) -> Dict[str, Any]:
    """Prefill once with a cache, then decode; recompute the prefix every step."""
    ids, provenance = ids_of_length(tokenizer, args.prompt, args.prompt_len,
                                    not args.no_chat_template)
    reset()
    cache = stage.new_cache()
    prefill = step(ids, list(range(len(ids))), 0, cache,
                   {"path": "cached", "step": 0, "label": "prefill"})
    prefix = list(ids)
    token = prefill["logits"]["greedyToken"]
    steps: List[Dict[str, Any]] = []

    for index in range(1, args.decode_steps + 1):
        position = len(prefix)
        # Cached: one token, reading the keys and values already on each GPU.
        cached = step([token], [position], position, cache,
                      {"path": "cached", "step": index, "label": f"decode{index}"})
        # Baseline: the whole prefix again with no cache anywhere, so a cache
        # indexing, RoPE offset or mask growth error shows up as a difference.
        fresh_ids = prefix + [token]
        fresh = step(fresh_ids, list(range(len(fresh_ids))), 0, None,
                     {"path": "fresh", "step": index, "label": f"recompute{index}"})
        comparison = fresh.get("comparison", {})
        steps.append({
            "step": index,
            "inputToken": token,
            "prefixLength": len(fresh_ids),
            "cachedToken": cached["logits"]["greedyToken"],
            "recomputedToken": fresh["logits"]["greedyToken"],
            "tokensAgree": comparison.get("tokensAgree"),
            "bitIdentical": comparison.get("bitIdentical"),
            "cacheChecks": cached.get("cacheChecks"),
            "metrics": comparison.get("metrics"),
            "topKOverlap": comparison.get("topKOverlap"),
            "crossing": cached.get("crossing"),
            "cachedDecodeMs": round(cached["senderComputeMs"]
                                    + cached["receiverComputeMs"], 2),
            "recomputeMs": round(fresh["senderComputeMs"]
                                 + fresh["receiverComputeMs"], 2),
        })
        prefix.append(token)
        token = cached["logits"]["greedyToken"]
        if args.progress:
            row = steps[-1]
            print(f"  step {index:>2}  token {row['cachedToken']:>7}  "
                  f"agree={row['tokensAgree']}  bitIdentical={row['bitIdentical']}",
                  file=sys.stderr, flush=True)

    generated = [row["cachedToken"] for row in steps]
    try:
        text = tokenizer.decode(generated, skip_special_tokens=True)
    except Exception as error:                                # noqa: BLE001
        text = f"<decode failed: {error}>"
    return {
        "tokenizer": provenance,
        "promptLength": len(ids),
        "prefillLogits": prefill["logits"],
        "senderCacheAfterPrefill": stage.cache_report(cache),
        "senderCacheFinal": stage.cache_report(cache),
        "steps": steps,
        "generatedTokens": generated,
        "generatedText": text,
        "allTokensAgree": all(row["tokensAgree"] for row in steps),
    }


# ------------------------------------------------------------ receiver side

def run_receiver(args, torch, model, assignment, second) -> Dict[str, Any]:
    device, gpu = resolve_device("rocm", torch, args.allow_cpu)
    probe = DeviceMetrics(torch, device)
    events = getattr(args, "audit", None)
    if events:
        events.emit("loading", device=device, gpu=gpu,
             layers=[int(i) for i in second.layers])
    load_started = time.perf_counter()
    stage, loader_metrics = build_stage(args.model, "rocm", second.layers, False,
                                        True, device, torch, assignment["rocm"])
    if events:
        events.emit("loaded", device=device, gpu=gpu,
             residentMiB=round(probe.allocated() / MIB, 1),
             loadSeconds=round(time.perf_counter() - load_started, 1))
    report: Dict[str, Any] = {
        "role": "rocm", "device": device, "gpu": gpu, "loader": loader_metrics,
        "layers": [int(i) for i in second.layers],
        "loadSeconds": round(time.perf_counter() - load_started, 1),
        "weightsMiB": round(probe.allocated() / MIB, 1),
    }
    from logit_metrics import compare_logits  # noqa: PLC0415

    owned = sorted(int(i) for i in second.layers)
    owned_set = set(owned)
    profiler = Profiler(torch, device, args.timing_method)
    report["timingMethod"] = profiler.method
    reconciliation: List[Dict[str, Any]] = []
    #: Running total of resolved sample time, so each step contributes a delta
    #: rather than a reset that would discard the run's own profile.
    profiler_seen = [0.0]

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", args.port))
    listener.listen(1)
    listener.settimeout(args.accept_timeout)
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as handle:
            handle.write(str(args.port))
    if events:
        events.emit("ready", device=device,
             residentMiB=round(probe.allocated() / MIB, 1), port=args.port)
    try:
        sock, _ = listener.accept()
    except socket.timeout:
        report["error"] = f"no peer within {args.accept_timeout}s"
        return report
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    cache: Optional[Any] = None
    pending: Dict[int, Any] = {}
    transfers, mismatches = 0, []

    while True:
        control = _recv_json(sock)
        op = control.get("op")
        if op == "done":
            _send_json(sock, {"ok": True})
            break
        if op == "reset":
            cache, pending = None, {}
            _send_json(sock, {"ok": True})
            continue

        meta = BoundaryMeta.from_dict(control["meta"])
        if meta.use_cache and cache is None:
            cache = stage.new_cache()
        t0 = mono()
        if ActivationHeader is not None:
            header = ActivationHeader.unpack(_recv(sock))
            shape, expected = header.shape, header.byte_size
        else:
            shape = (meta.batch, meta.sequence, meta.hidden)
            expected = meta.batch * meta.sequence * meta.hidden * 2
        payload = _recv(sock)
        t_payload_complete = mono()
        if len(payload) != expected:
            _send_json(sock, {"error": f"{len(payload)} bytes, expected {expected}"})
            continue

        host = torch.frombuffer(bytearray(payload), dtype=torch.uint8) \
                    .view(torch.bfloat16).reshape(shape)
        # Straight to the device: nothing that is not part of the crossing may
        # sit between the last byte arriving and the tensor being resident.
        # Hashing used to happen here and inflated the interval by tens of
        # milliseconds at 20 MiB.
        hidden = host.to(device)
        probe.sync()
        t_h2d_complete = mono()

        probe.reset_peak()
        resident = probe.allocated()
        use = cache if meta.use_cache else None
        step_mark = profiler.mark() if profiler.enabled else {}
        with torch.inference_mode():
            logits = stage.forward(hidden, meta, past_key_values=use,
                                   last_position_only=True,
                                   profiler=profiler)
        probe.sync()
        t_consumer_done = mono()
        consumer_ms = (t_consumer_done - t_h2d_complete) * 1000.0
        profiler.collect()
        if profiler.enabled:
            total = sum(sum(v) for v in profiler.samples.values())
            summed = total - profiler_seen[0]
            profiler_seen[0] = total
            reconciliation.append({
                "label": control.get("label", ""),
                "warmState": control.get("warmState", "steady-state"),
                "stageEndToEndMs": round(consumer_ms, 4),
                "summedOperationsMs": round(summed, 4),
                "unaccountedMs": round(consumer_ms - summed, 4),
                "coverage": round(summed / consumer_ms, 4)
                if consumer_ms > 0 else None,
            })

        received = contract(host, torch, control.get("qualify", False))
        sent = control["sentContract"]
        checks = {key: sent[key] == received[key] for key in
                  ("shape", "dtype", "strides", "elements", "bytes")}
        if control.get("qualify"):
            checks["sha256"] = sent.get("sha256") == received.get("sha256")
        byte_exact = all(checks.values())
        if not byte_exact:
            mismatches.append({"label": control.get("label"),
                               "failed": [k for k, v in checks.items() if not v]})
        transfers += 1

        last = logits.reshape(-1)
        reply: Dict[str, Any] = {
            "receivedContract": received,
            "byteExact": byte_exact,
            "checks": checks,
            "logits": logit_summary(logits, torch),
            "receiverHostToDeviceMs": round(
                (t_h2d_complete - t_payload_complete) * 1000, 3),
            "receiverComputeMs": round(
                (t_consumer_done - t_h2d_complete) * 1000, 3),
            "receiverTotalMs": round((t_consumer_done - t0) * 1000, 3),
            "receiverWorkspaceMiB": round(
                (probe.peak() - resident) / MIB, 2),
            "receiverMaskShape": stage.last_mask_shape,
            "receiverDecomposition": _decomposition_from(
                profiler.since(step_mark) if profiler.enabled else {},
                profiler.method, consumer_ms),
            # Absolute stamps on the shared monotonic clock, so the sender can
            # compute true one-way times instead of inferring them by
            # subtracting one process's total from the other's round trip.
            "tPayloadComplete": t_payload_complete,
            "tHostToDeviceComplete": t_h2d_complete,
            "tConsumerReady": t_consumer_done,
        }

        if meta.use_cache:
            mine = stage.cache_report(cache)
            theirs = control.get("senderCache", {})
            their_layers = set(theirs.get("populatedLayers", []))
            reply["cacheChecks"] = {
                "receiverHoldsExactlyItsLayers": mine["populatedLayers"] == owned,
                "senderHoldsNoneOfThem": bool(their_layers)
                and not (their_layers & owned_set),
                "lengthsMatch": mine["uniformLength"] == theirs.get("uniformLength"),
                "receiverLength": mine["uniformLength"],
                "senderLength": theirs.get("uniformLength"),
                "receiverCacheMiB": round(mine["bytes"] / MIB, 1),
            }

        # Both paths for a step land in the same process, so the comparison is
        # between tensors this GPU produced; no cross-backend difference enters.
        key = int(control.get("step", 0))
        if control.get("path") == "cached":
            pending[key] = last.detach().clone()
        elif key in pending:
            baseline = pending.pop(key)
            metrics = compare_logits(baseline, last, torch)
            cached_token = greedy_token(baseline, torch)
            fresh_token = greedy_token(last, torch)
            reply["comparison"] = {
                # The whole envelope, per step. A pass/fail on the selected
                # token alone would hide a distribution drifting underneath it.
                "metrics": {name: metrics[name] for name in
                            ("maxAbsError", "meanAbsError", "rmse",
                             "normalizedRmse", "cosineSimilarity",
                             "klDivergenceFp32", "spearmanOnCandidates")},
                "topKOverlap": {name: metrics["topKOverlap"][name]["fraction"]
                                for name in ("top1", "top5", "top20")},
                "cachedToken": cached_token,
                "recomputedToken": fresh_token,
                "tokensAgree": cached_token == fresh_token,
                "bitIdentical": bool(
                    (baseline.view(torch.uint16) == last.view(torch.uint16))
                    .all().item()),
            }
        _send_json(sock, reply)

    if profiler.enabled or reconciliation:
        report["profile"] = profiler.report()
        report["profile"]["layerTotals"] = {
            phase: profiler.layer_totals(f"layer.{phase}.")
            for phase in ("prefill", "decode")}
        report["reconciliation"] = reconciliation
    report["transfers"] = transfers
    report["boundaryMismatches"] = mismatches
    report["allTransfersByteExact"] = not mismatches
    if cache is not None:
        report["finalCache"] = stage.cache_report(cache)
    sock.close()
    listener.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-vendor prefill and cached decode")
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", required=True, choices=("cuda", "rocm"))
    parser.add_argument("--mode", default="prefill",
                        choices=("prefill", "oracle", "generate", "serve"))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--no-chat-template", action="store_true")
    parser.add_argument("--seq-lens", default="1,8,64,256,2048")
    parser.add_argument("--prompt-len", type=int, default=16)
    parser.add_argument("--decode-steps", type=int, default=32)
    parser.add_argument("--qualify", action="store_true",
                        help="hash every boundary tensor; off for timing runs")
    parser.add_argument("--placement", required=True,
                        help="immutable Scheduler PlacementManifest JSON")
    parser.add_argument("--run-id", required=True,
                        help="canonical UUIDv4 for one coordinator attempt; "
                             "correlation metadata, not authentication")
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--port", type=int, default=31900)
    parser.add_argument("--peer", default="127.0.0.1")
    parser.add_argument("--ready-file", default="")
    parser.add_argument("--accept-timeout", type=float, default=300.0)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true",
                        help="smoke-test the protocol without GPUs")
    parser.add_argument("--events", default="",
                        help="write JSONL lifecycle events here for the supervisor")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--pad-prompt-to", type=int, default=0,
                        help="force the prompt to this token count")
    parser.add_argument("--heartbeat", type=float, default=15.0)
    parser.add_argument("--prefill-repeats", type=int, default=1,
                        help="timed passes per length; the first is first-touch "
                             "and is excluded from steady-state statistics")
    parser.add_argument("--timing-method", default=OFF, choices=list(METHODS),
                        help="off | stream-event | global-sync-control")
    args = parser.parse_args()
    args.seq_lens = [int(v) for v in args.seq_lens.split(",") if v.strip()]

    if not valid_run_id(args.run_id):
        print(json.dumps({"role": args.role,
                          "error": f"run id {args.run_id!r} is not a canonical UUID"}))
        return 64

    import torch  # noqa: PLC0415
    torch.manual_seed(0)

    # The writer exists before the manifest is read, so a failure during
    # validation is still attributable to a run -- which is the whole point of
    # the pre-validation failure record.
    stream = open(args.events, "a", encoding="utf-8") if args.events else None
    args.audit = EventWriter(stream, args.run_id, args.role) if stream else None

    def reject(phase: str, reason: str, code: int) -> int:
        if args.audit is not None:
            args.audit.emit_pre_validation_failure(args.placement, phase, reason)
            args.audit.close()
        print(json.dumps({"role": args.role, "error": reason,
                          "failurePhase": phase, "runId": args.run_id}))
        return code

    try:
        model = inspect_model(args.model)
    except Exception as error:                                # noqa: BLE001
        return reject("read", f"{type(error).__name__}: {error}", 65)
    try:
        placement = load_placement(args.placement)
        assigned_stage = validate_for_worker(placement, model, args.role)
    except ManifestError as error:
        phase = _failure_phase(str(error))
        return reject(phase, str(error), 66)
    except OSError as error:
        return reject("read", str(error), 65)

    stages = {role: stage_assignment(placement, role)
              for role in ("cuda", "rocm")}
    assignment = {role: list(stage.tensors) for role, stage in stages.items()}
    ownership = validate_ownership((t.name for t in model.tensors), assignment)
    if not ownership.valid:
        return reject("ownership", "ownership not exclusive", 3)
    first, second = stages["cuda"], stages["rocm"]

    if args.audit is not None:
        # Identity is adopted only now, from the verified manifest.
        args.audit.bind_placement(placement, assigned_stage)
        args.audit.emit_placement_summary(
            placement, assigned_stage, args.placement,
            runtime_version=f"torch {torch.__version__}")
    args.event_stream = stream
    started = time.perf_counter()
    try:
        if args.role == "cuda":
            report = run_sender(args, torch, model, assignment, first)
        else:
            report = run_receiver(args, torch, model, assignment, second)
    except Exception as error:                                # noqa: BLE001
        # A worker that dies must say so. The supervisor must never have to
        # infer a failure from output that simply stopped.
        if args.audit is not None:
            args.audit.emit("failed",
                            detail=f"{type(error).__name__}: {error}")
            args.audit.close()
        print(json.dumps({"role": args.role, "error": str(error)}))
        raise
    report["mode"] = args.mode
    report["placementId"] = placement["placementId"]
    report["manifestDigest"] = placement["manifestDigest"]
    report["deviceIdentity"] = assigned_stage.device_identity
    report["boundary"] = placement["pipeline"]["boundaryAfterLayer"]
    report["seconds"] = round(time.perf_counter() - started, 2)
    if args.audit is not None:
        args.audit.emit("failed" if "error" in report else "finished",
                        detail=report.get("error", ""))
        args.audit.close()
    print(json.dumps(report, sort_keys=True))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
