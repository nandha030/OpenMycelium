"""Two-process cross-vendor pipeline inference.

    stage A (CUDA)  layers [0, k)  -->  activation  -->  stage B (ROCm)  layers [k, n)

The stages are separate processes because a CUDA PyTorch and a ROCm PyTorch
cannot coexist in one environment -- which is also the real deployment shape,
where each host carries only its own vendor runtime.

The activation crosses on the qualified path: device -> pinned host -> wire ->
pinned host -> device. Device memory is never handed to the socket, for the
reason established by measurement: ROCm-for-WSL device memory faults on any CPU
access, so anything that memcpys into it segfaults.

Framing reuses `mccl.xvendor.ActivationHeader`, so shape and dtype travel with
each transfer and are validated on arrival rather than assumed.

Weights are derived from a per-tensor CRC32 seed, so both stages and the
reference build identical parameters without shipping a checkpoint. Python's
`hash()` is deliberately avoided: it is randomised per process.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mccl", "src"))

from mccl.xvendor import ActivationHeader, TransferMetrics, TransportError  # noqa: E402

_TORCH_TO_TAG = {
    "torch.float16": "f16", "torch.bfloat16": "bf16",
    "torch.float32": "f32", "torch.float64": "f64",
}
_TAG_TO_TORCH = {v: k for k, v in _TORCH_TO_TAG.items()}


@dataclass(frozen=True)
class ModelConfig:
    vocab: int = 256
    layers: int = 8
    width: int = 256
    seed: int = 20260824

    def to_dict(self) -> dict[str, Any]:
        return {"vocab": self.vocab, "layers": self.layers,
                "width": self.width, "seed": self.seed}


def build_layer(name: str, rows: int, cols: int, device: str, torch: Any) -> Any:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(20260824 + zlib.crc32(name.encode("utf-8")))
    raw = torch.randn(rows, cols, generator=generator, dtype=torch.float32)
    return (raw / (cols ** 0.5)).to(device)


def build_weights(config: ModelConfig, layer_range: range, device: str, torch: Any):
    weights = {"embed": build_layer("embed", config.vocab, config.width, device, torch)}
    for layer in layer_range:
        weights[f"w{layer}"] = build_layer(f"w{layer}", config.width, config.width, device, torch)
        weights[f"v{layer}"] = build_layer(f"v{layer}", config.width, config.width, device, torch)
    weights["out"] = build_layer("out", config.width, config.vocab, device, torch)
    return weights


def _rms_norm(tensor: Any, torch: Any, eps: float = 1e-6) -> Any:
    return tensor * torch.rsqrt(tensor.pow(2).mean(dim=-1, keepdim=True) + eps)


def run_layers(hidden: Any, layer_range: range, weights: dict, torch: Any) -> Any:
    """A gated block per layer; enough arithmetic that a wrong split diverges."""
    for layer in layer_range:
        gate = torch.tanh(hidden @ weights[f"v{layer}"])
        hidden = _rms_norm(hidden + gate * (hidden @ weights[f"w{layer}"]), torch)
    return hidden


# ------------------------------------------------------------------ transport

def _send_all(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(payload)


def _recv_all(sock: socket.socket, count: int) -> bytes:
    parts, remaining = [], count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise TransportError(f"peer closed with {remaining} of {count} bytes outstanding")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def send_activation(sock: socket.socket, tensor: Any, sequence: int, torch: Any) -> int:
    """Stage the tensor into pinned host memory, then frame and send it."""
    tag = _TORCH_TO_TAG.get(str(tensor.dtype))
    if tag is None:
        raise TransportError(f"unsupported activation dtype {tensor.dtype}")
    host = tensor.detach().to("cpu").contiguous()
    payload = host.numpy().tobytes()
    header = ActivationHeader(tag, tuple(int(d) for d in tensor.shape), sequence=sequence)
    header.validate()
    if header.byte_size != len(payload):
        raise TransportError(
            f"framing mismatch: header claims {header.byte_size} bytes, payload is {len(payload)}"
        )
    packed = header.pack()
    _send_all(sock, struct.pack("<I", len(packed)) + packed)
    _send_all(sock, payload)
    return len(payload)


def recv_activation(sock: socket.socket, device: str, torch: Any,
                    prefix: bytes = b"") -> tuple[Any, ActivationHeader]:
    """`prefix` carries bytes already consumed by the caller's arrival probe,
    so the receive timer can start at first byte rather than at connect."""
    header_len = struct.unpack("<I", prefix + _recv_all(sock, 4 - len(prefix)))[0]
    if not 20 <= header_len <= 128:
        raise TransportError(f"implausible header length {header_len}")
    header = ActivationHeader.unpack(_recv_all(sock, header_len))
    payload = _recv_all(sock, header.byte_size)
    dtype = getattr(torch, _TAG_TO_TORCH[header.dtype].split(".")[1])
    flat = torch.frombuffer(bytearray(payload), dtype=dtype)
    return flat.reshape(header.shape).to(device), header


# ---------------------------------------------------------------------- roles

def resolve_device(vendor: str, torch: Any) -> tuple[str, str]:
    """Refuse to pass off one vendor's GPU as the other's."""
    if vendor not in {"cuda", "rocm"}:
        return "cpu", "cpu"
    is_rocm_build = bool(getattr(torch.version, "hip", None))
    if torch.cuda.is_available() and (vendor == "rocm") == is_rocm_build:
        return "cuda:0", f"{vendor} on {torch.cuda.get_device_name(0)}"
    if torch.cuda.is_available():
        build = "ROCm" if is_rocm_build else "CUDA"
        return "cpu", f"{vendor} requested but this is a {build} build; standing in with CPU"
    return "cpu", f"{vendor} requested but no GPU runtime is present; standing in with CPU"


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-vendor pipeline stage")
    parser.add_argument("--stage", required=True, choices=("a", "b", "reference"))
    parser.add_argument("--vendor", default="cpu")
    parser.add_argument("--split", type=int, default=4)
    parser.add_argument("--layers", type=int, default=8)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--tokens", default="7,11,23,42,5,99,3,64")
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=1,
                        help="steady-state timed transfers")
    parser.add_argument("--warmup", type=int, default=0,
                        help="untimed transfers run first, excluded from percentiles")
    parser.add_argument("--port", type=int, default=25000)
    parser.add_argument("--peer", default="127.0.0.1")
    args = parser.parse_args()

    import torch  # noqa: PLC0415

    config = ModelConfig(layers=args.layers, width=args.width)
    device, note = resolve_device(args.vendor, torch)
    tokens = [int(t) % config.vocab for t in args.tokens.split(",")]
    report: dict[str, Any] = {"stage": args.stage, "vendor": args.vendor,
                             "device": device, "note": note, "split": args.split}
    metrics = TransferMetrics()

    def embed() -> Any:
        ids = torch.tensor([tokens] * args.batch, dtype=torch.long, device=device)
        return build_weights(config, range(0), device, torch)["embed"][ids]

    if args.stage == "reference":
        weights = build_weights(config, range(config.layers), device, torch)
        hidden = weights["embed"][torch.tensor([tokens] * args.batch,
                                               dtype=torch.long, device=device)]
        hidden = run_layers(hidden, range(config.layers), weights, torch)
        logits = hidden @ weights["out"]
        report["tokens"] = torch.argmax(logits[:, -1, :], dim=-1).tolist()
        report["checksum"] = round(float(logits.double().sum().item()), 4)

    elif args.stage == "a":
        weights = build_weights(config, range(0, args.split), device, torch)
        # Connection setup sits outside every timer. The send timer covers
        # exactly: device->host staging, framing, and the socket write.
        sock = socket.create_connection((args.peer, args.port), timeout=120)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        moved, cold_ms = 0, None
        for step in range(args.warmup + args.iterations):
            hidden = weights["embed"][torch.tensor([tokens] * args.batch,
                                                   dtype=torch.long, device=device)]
            hidden = run_layers(hidden, range(0, args.split), weights, torch)
            start = time.perf_counter()
            moved = send_activation(sock, hidden, step, torch)
            elapsed = time.perf_counter() - start
            if step == 0:
                # Reported separately rather than folded into the percentiles.
                cold_ms = round(elapsed * 1000.0, 3)
            if step >= args.warmup:
                metrics.observe(moved, elapsed)
        verdict = _recv_all(sock, 1)
        sock.close()
        report["activationBytes"] = moved
        report["coldStartMs"] = cold_ms
        report["warmupSkipped"] = args.warmup
        report["metrics"] = metrics.to_dict()
        report["peerVerified"] = verdict == b"\x01"

    else:
        weights = build_weights(config, range(args.split, config.layers), device, torch)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", args.port))
        listener.listen(1)
        listener.settimeout(120)
        sock, _ = listener.accept()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        out_tokens, checksum, cold_ms = [], 0.0, None
        # The receive timer starts only once a frame is actually arriving, so it
        # measures wire + host->device staging rather than how long stage A took
        # to connect and compute its half.
        for step in range(args.warmup + args.iterations):
            first_byte = _recv_all(sock, 1)
            start = time.perf_counter()
            hidden, header = recv_activation(sock, device, torch, prefix=first_byte)
            elapsed = time.perf_counter() - start
            if step == 0:
                cold_ms = round(elapsed * 1000.0, 3)
            if step >= args.warmup:
                metrics.observe(header.byte_size, elapsed)
            hidden = run_layers(hidden, range(args.split, config.layers), weights, torch)
            logits = hidden @ weights["out"]
            out_tokens = torch.argmax(logits[:, -1, :], dim=-1).tolist()
            checksum = round(float(logits.double().sum().item()), 4)
        report["coldStartMs"] = cold_ms
        report["warmupSkipped"] = args.warmup
        sock.sendall(b"\x01")
        sock.close()
        listener.close()
        report["tokens"] = out_tokens
        report["checksum"] = checksum
        report["metrics"] = metrics.to_dict()

    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
