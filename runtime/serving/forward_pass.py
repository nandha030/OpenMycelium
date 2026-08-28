"""One-token forward pass across the vendor boundary.

    token IDs -> CUDA embedding + layers 0-19
              -> MCCL boundary activation [1, 1, 5120] BF16
              -> ROCm layers 20-39 + norm + lm_head
              -> logits [1, 1, 131072]

Sampling happens in one place, the ROCm worker, because it owns the head. Two
processes sampling independently could disagree the moment anything stochastic
is introduced.

Logits are never materialised in FP32 wholesale: a [1, 1, 131072] BF16 tensor
promoted to FP32 costs half a gigabyte for a checksum. Validation is a finite
check, shape and dtype, top-k, a bounded FP32 slice, and a digest over the raw
BF16 bytes.
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

from model_inspect import GIB, MIB, inspect_model  # noqa: E402
from partition import assign_tensors, plan_pipeline  # noqa: E402
from stage_loader import StageLoader, validate_ownership  # noqa: E402
from stage_model import BoundaryMeta, MistralStage, StageSpec  # noqa: E402

try:
    from mccl.xvendor import ActivationHeader, QualificationLedger, TransportError
except ImportError:                                    # package not installed
    ActivationHeader = None                            # type: ignore
    QualificationLedger = None                         # type: ignore
    TransportError = RuntimeError                      # type: ignore


# --------------------------------------------------------------------- wire

def _send(sock: socket.socket, blob: bytes) -> None:
    sock.sendall(struct.pack("<I", len(blob)) + blob)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    parts, left = [], count
    while left:
        chunk = sock.recv(left)
        if not chunk:
            raise RuntimeError(f"peer closed with {left}/{count} bytes outstanding")
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def _recv(sock: socket.socket) -> bytes:
    return _recv_exact(sock, struct.unpack("<I", _recv_exact(sock, 4))[0])


def send_activation(sock: socket.socket, tensor: Any, meta: BoundaryMeta,
                    torch: Any) -> int:
    """Stage the hidden state to host and send it with its metadata.

    The hidden state is all that crosses. Masks and rotary tables are rebuilt on
    the far side from the metadata, which is a few hundred bytes.
    """
    host = tensor.detach().to("cpu").contiguous()
    payload = host.view(torch.uint8).numpy().tobytes()
    _send(sock, json.dumps(meta.to_dict()).encode("utf-8"))
    if ActivationHeader is not None:
        header = ActivationHeader("bf16", tuple(int(d) for d in tensor.shape))
        header.validate()
        if header.byte_size != len(payload):
            raise RuntimeError(
                f"framing mismatch: header {header.byte_size} vs payload {len(payload)}")
        _send(sock, header.pack())
    _send(sock, payload)
    return len(payload)


def recv_activation(sock: socket.socket, device: str, torch: Any
                    ) -> Tuple[Any, BoundaryMeta, int]:
    meta = BoundaryMeta.from_dict(json.loads(_recv(sock).decode("utf-8")))
    if ActivationHeader is not None:
        header = ActivationHeader.unpack(_recv(sock))
        shape = header.shape
        expected = header.byte_size
    else:
        shape = (meta.batch, meta.sequence, meta.hidden)
        expected = meta.batch * meta.sequence * meta.hidden * 2
    payload = _recv(sock)
    if len(payload) != expected:
        raise RuntimeError(f"activation is {len(payload)} bytes, expected {expected}")
    flat = torch.frombuffer(bytearray(payload), dtype=torch.uint8)
    tensor = flat.view(torch.bfloat16).reshape(shape).to(device)
    return tensor, meta, len(payload)


# ---------------------------------------------------------------- validation

def raw_bytes(tensor: Any, torch: Any) -> bytes:
    """Exact bytes of a tensor, with no NumPy step and no FP32 promotion.

    `view(torch.uint8)` reinterprets the same storage, so what is hashed is
    literally what would travel on the wire.
    """
    host = tensor.detach().to("cpu").contiguous()
    return bytes(host.view(torch.uint8).reshape(-1).tolist())


def tensor_contract(tensor: Any, torch: Any) -> Dict[str, Any]:
    """Shape, dtype, contiguity, byte count and a digest over the whole tensor."""
    host = tensor.detach().to("cpu")
    blob = raw_bytes(tensor, torch)
    return {
        "shape": [int(d) for d in tensor.shape],
        "dtype": str(tensor.dtype),
        "contiguous": bool(host.is_contiguous()),
        "strides": [int(s) for s in host.stride()],
        "elements": int(tensor.numel()),
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }

def describe_logits(logits: Any, torch: Any, top_k: int = 5) -> Dict[str, Any]:
    """Validate without promoting 131072 BF16 values to FP32."""
    finite = bool(torch.isfinite(logits).all().item())
    flat = logits.reshape(-1)
    values, indices = torch.topk(flat.float() if flat.numel() <= 4096 else flat,
                                 k=min(top_k, flat.numel()))
    head_slice = flat[:16].float().tolist()
    raw = flat.contiguous().view(torch.uint8).cpu().numpy().tobytes()
    return {
        "shape": list(logits.shape),
        "dtype": str(logits.dtype),
        "allFinite": finite,
        "topTokenIds": [int(i) for i in indices.tolist()],
        "topValues": [round(float(v), 4) for v in values.float().tolist()],
        "first16Fp32": [round(v, 4) for v in head_slice],
        "rawSha256": hashlib.sha256(raw).hexdigest()[:16],
    }


# -------------------------------------------------------------------- stages

def build_stage(model_path: str, vendor: str, layer_indices, holds_embedding: bool,
                holds_head: bool, device: str, torch: Any,
                tensor_names) -> Tuple[MistralStage, Dict[str, Any]]:
    spec = StageSpec(vendor, tuple(layer_indices), holds_embedding, holds_head, device)
    stage = MistralStage(os.path.join(model_path, "config.json"), spec, torch)
    loader = StageLoader(model_path, vendor, tensor_names, device, torch)
    metrics = loader.load()
    stage.install(loader.tensors)
    loader.tensors.clear()          # the modules own the tensors now
    return stage, metrics.to_dict()


def resolve_device(vendor: str, torch: Any) -> Tuple[str, str]:
    if not torch.cuda.is_available():
        raise RuntimeError(f"stage {vendor}: no GPU runtime available")
    is_rocm = bool(getattr(torch.version, "hip", None))
    if (vendor == "rocm") != is_rocm:
        build = "ROCm" if is_rocm else "CUDA"
        raise RuntimeError(
            f"stage {vendor} requested but this is a {build} PyTorch build; "
            "run each stage in its own environment")
    return "cuda:0", torch.cuda.get_device_name(0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-vendor one-token forward pass")
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", required=True, choices=("cuda", "rocm", "reference"))
    parser.add_argument("--token", type=int, default=1234)
    parser.add_argument("--position", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the same token N times to check determinism")
    parser.add_argument("--cuda-budget", default="14GiB")
    parser.add_argument("--rocm-budget", default="14GiB")
    parser.add_argument("--context-length", type=int, default=2048)
    parser.add_argument("--port", type=int, default=31000)
    parser.add_argument("--peer", default="127.0.0.1")
    parser.add_argument("--ready-file", default="",
                        help="receiver touches this once bound; sender waits for it")
    parser.add_argument("--accept-timeout", type=float, default=180.0)
    parser.add_argument("--save-logits", default="",
                        help="write raw BF16 logits here for offline comparison")
    args = parser.parse_args()

    import torch  # noqa: PLC0415
    from model_inspect import parse_size  # noqa: PLC0415

    torch.manual_seed(0)
    model = inspect_model(args.model)
    plan = plan_pipeline(model, parse_size(args.cuda_budget),
                         parse_size(args.rocm_budget), args.context_length)
    assignment = assign_tensors(model, plan)
    ownership = validate_ownership((t.name for t in model.tensors), assignment)
    if not ownership.valid:
        print(json.dumps({"error": "ownership not exclusive",
                          "detail": ownership.to_dict()}))
        return 3

    first, second = plan.stages
    report: Dict[str, Any] = {"role": args.role, "boundary": plan.boundary}
    started = time.perf_counter()

    if args.role == "reference":
        # Whole model on one device. Only viable for a small checkpoint; the
        # real one will not fit, which is the entire point of the split.
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        names = sorted(t.name for t in model.tensors)
        stage, metrics = build_stage(args.model, "reference",
                                     range(model.layer_count), True, True,
                                     device, torch, names)
        meta = BoundaryMeta(1, 1, int(model.config["hidden_size"]), "bf16",
                            [args.position], [args.position])
        ids = torch.tensor([[args.token]], dtype=torch.long, device=device)
        with torch.inference_mode():
            logits = stage.forward(ids, meta)
        report["device"] = device
        report["loader"] = metrics
        report["logits"] = describe_logits(logits, torch)

    elif args.role == "cuda":
        device, name = resolve_device("cuda", torch)
        stage, metrics = build_stage(args.model, "cuda", first.layers, True, False,
                                     device, torch, assignment["cuda"])
        report["device"], report["gpu"] = device, name
        report["loader"] = metrics

        if args.ready_file:
            deadline = time.time() + args.accept_timeout
            while not os.path.exists(args.ready_file):
                if time.time() > deadline:
                    print(json.dumps({"role": "cuda",
                                      "error": "receiver never signalled readiness"}))
                    return 6
                time.sleep(0.5)
        sock = socket.create_connection((args.peer, args.port), timeout=args.accept_timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        hidden_size = int(model.config["hidden_size"])
        sent, digests = 0, []
        for _ in range(args.repeat):
            meta = BoundaryMeta(1, 1, hidden_size, "bf16",
                                [args.position], [args.position])
            ids = torch.tensor([[args.token]], dtype=torch.long, device=device)
            torch.cuda.reset_peak_memory_stats()
            before = torch.cuda.memory_allocated()
            with torch.inference_mode():
                hidden = stage.forward(ids, meta)
            report["workspaceMiB"] = round(
                (torch.cuda.max_memory_allocated() - before) / MIB, 2)
            report["boundaryShape"] = list(hidden.shape)
            report["boundaryDtype"] = str(hidden.dtype)
            raw = hidden.detach().to("cpu").contiguous().view(torch.uint8).numpy().tobytes()
            digests.append(hashlib.sha256(raw).hexdigest()[:16])
            report["boundaryFirst16"] = [round(float(v), 5) for v in
                                         hidden.reshape(-1)[:16].float().tolist()]
            # The contract of the tensor as it stands immediately before send.
            report.setdefault("sentContract", []).append(tensor_contract(hidden, torch))
            sent = send_activation(sock, hidden, meta, torch)
        report["boundaryBytes"] = sent
        report["boundaryDigests"] = digests
        report["kernelsObserved"] = torch.cuda.memory_allocated() > 0
        _recv_exact(sock, 1)
        sock.close()

    else:
        device, name = resolve_device("rocm", torch)
        stage, metrics = build_stage(args.model, "rocm", second.layers, False, True,
                                     device, torch, assignment["rocm"])
        report["device"], report["gpu"] = device, name
        report["loader"] = metrics

        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", args.port))
        listener.listen(1)
        listener.settimeout(args.accept_timeout)
        if args.ready_file:
            # Signal only after bind+listen, so the sender cannot connect early
            # and cannot wait forever on a receiver that never came up.
            with open(args.ready_file, "w", encoding="utf-8") as handle:
                handle.write(str(args.port))
        try:
            sock, _ = listener.accept()
        except socket.timeout:
            print(json.dumps({"role": "rocm",
                              "error": f"no peer within {args.accept_timeout}s"}))
            return 6
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        outputs = []
        for _ in range(args.repeat):
            hidden, meta, received = recv_activation(sock, device, torch)
            # The contract of the tensor as it arrived, before any further work.
            # Only an end-to-end comparison of these two proves the integrated
            # bridge preserves the boundary; a generic transport test cannot.
            report.setdefault("receivedContract", []).append(tensor_contract(hidden, torch))
            torch.cuda.reset_peak_memory_stats()
            before = torch.cuda.memory_allocated()
            with torch.inference_mode():
                logits = stage.forward(hidden, meta)
            report["workspaceMiB"] = round(
                (torch.cuda.max_memory_allocated() - before) / MIB, 2)
            described = describe_logits(logits, torch)
            if args.save_logits:
                # Raw BF16 bytes, no FP32 promotion and no NumPy on the way out.
                with open(args.save_logits, "wb") as handle:
                    handle.write(raw_bytes(logits, torch))
                described["savedTo"] = args.save_logits
                # Bit patterns for the top candidates, so a tie can be checked
                # rather than inferred from printed decimals.
                from logit_metrics import bf16_bits  # noqa: PLC0415
                described["topBits"] = bf16_bits(logits, described["topTokenIds"], torch)
            described["receivedBytes"] = received
            outputs.append(described)
        report["logits"] = outputs[0]
        report["repeats"] = outputs
        report["deterministic"] = len({o["rawSha256"] for o in outputs}) == 1
        report["generatedToken"] = outputs[0]["topTokenIds"][0]
        report["kernelsObserved"] = torch.cuda.memory_allocated() > 0
        sock.sendall(b"\x01")
        sock.close()
        listener.close()

    report["seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
