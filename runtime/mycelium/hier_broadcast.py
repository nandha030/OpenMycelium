"""Hierarchical cross-vendor broadcast.

    root rank
      -> vendor-local broadcast (NCCL or RCCL)
      -> source border rank
      -> host-staged-xvendor bridge
      -> destination border rank
      -> destination vendor-local broadcast

Broadcast first, deliberately: it exercises routing, framing, ordering, and
failure fencing without opening any question about cross-vendor reduction
semantics. Every rank ends holding the root's bytes, so correctness is a
comparison rather than a numerical tolerance argument.

On a machine with one GPU per vendor the local groups hold a single rank, so
the NCCL and RCCL broadcasts are degenerate. That is recorded rather than
glossed: the run validates orchestration and cross-vendor routing, not
multi-rank vendor-local behaviour.
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
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mccl", "src"))

from mccl.collective_frame import (  # noqa: E402
    CollectiveError,
    CollectiveFrame,
    CollectiveTiming,
    FrameGate,
    StaleFrameError,
    degeneracy_note,
)
from mccl.xvendor import QualificationLedger, TransportError  # noqa: E402

LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")

_TAGS = {"torch.float16": "f16", "torch.bfloat16": "bf16",
         "torch.float32": "f32", "torch.float64": "f64",
         "torch.int32": "i32", "torch.int64": "i64", "torch.uint8": "u8"}
_TORCH_NAME = {v: k.split(".")[1] for k, v in _TAGS.items()}


# --------------------------------------------------------------------- wire

def _send(sock: socket.socket, blob: bytes) -> None:
    sock.sendall(struct.pack("<I", len(blob)) + blob)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    parts, left = [], count
    while left:
        chunk = sock.recv(left)
        if not chunk:
            raise CollectiveError(f"border peer closed with {left}/{count} bytes outstanding")
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def _recv(sock: socket.socket) -> bytes:
    size = struct.unpack("<I", _recv_exact(sock, 4))[0]
    if size > (1 << 30):
        raise CollectiveError(f"implausible frame length {size}")
    return _recv_exact(sock, size)


# ---------------------------------------------------------------- collective

def local_broadcast(tensor: Any, group_size: int, torch: Any) -> tuple[Any, bool]:
    """Vendor-local broadcast. Degenerate when the group holds one rank."""
    if group_size <= 1:
        return tensor, True
    torch.distributed.broadcast(tensor, src=0)
    return tensor, False


def stage_to_host(tensor: Any, torch: Any) -> bytes:
    """Device -> pinned host. Non-contiguous input is made contiguous first.

    A non-contiguous tensor has no single byte range to send; silently sending
    its storage would transmit the wrong elements.
    """
    detached = tensor.detach()
    if not detached.is_contiguous():
        detached = detached.contiguous()
    return detached.to("cpu").numpy().tobytes()


def resolve_device(vendor: str, torch: Any) -> tuple[str, str]:
    if vendor not in {"cuda", "rocm"}:
        return "cpu", "cpu"
    is_rocm = bool(getattr(torch.version, "hip", None))
    if torch.cuda.is_available() and (vendor == "rocm") == is_rocm:
        return "cuda:0", f"{vendor} on {torch.cuda.get_device_name(0)}"
    if torch.cuda.is_available():
        return "cpu", f"{vendor} requested but this is a " \
                      f"{'ROCm' if is_rocm else 'CUDA'} build; standing in with CPU"
    return "cpu", f"{vendor} requested but no GPU runtime is present"


def reference_tensor(shape, dtype_tag: str, seed: int, device: str, torch: Any) -> Any:
    """Deterministic root payload, identical on both sides without shipping it."""
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    numel = 1
    for d in shape:
        numel *= d
    dtype = getattr(torch, _TORCH_NAME[dtype_tag])
    if numel == 0:
        return torch.empty(tuple(shape), dtype=dtype, device=device)
    if dtype_tag in ("i32", "i64", "u8"):
        flat = torch.randint(0, 100, (numel,), generator=generator, dtype=dtype)
    else:
        flat = torch.randn(numel, generator=generator, dtype=torch.float32).to(dtype)
    return flat.reshape(tuple(shape)).to(device)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hierarchical cross-vendor broadcast")
    parser.add_argument("--vendor", required=True, choices=("cuda", "rocm"))
    parser.add_argument("--role", required=True, choices=("root", "peer"),
                        help="root holds the source tensor; peer receives it")
    parser.add_argument("--root-vendor", required=True, choices=("cuda", "rocm"))
    parser.add_argument("--shape", default="4,8")
    parser.add_argument("--dtype", default="f32")
    parser.add_argument("--collective-id", type=int, default=1)
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--group-size", type=int, default=1)
    parser.add_argument("--port", type=int, default=27000)
    parser.add_argument("--peer", default="127.0.0.1")
    parser.add_argument("--non-contiguous", action="store_true",
                        help="broadcast a transposed view, to prove staging handles it")
    parser.add_argument("--fail-after-frame", action="store_true",
                        help="border rank aborts mid-collective, to test fencing")
    parser.add_argument("--ledger", default=LEDGER)
    args = parser.parse_args()

    import torch  # noqa: PLC0415

    shape = tuple(int(x) for x in args.shape.split(",") if x != "")
    device, note = resolve_device(args.vendor, torch)
    dest_vendor = "rocm" if args.root_vendor == "cuda" else "cuda"
    direction = f"{args.root_vendor}->{dest_vendor}"
    report: dict[str, Any] = {
        "vendor": args.vendor, "role": args.role, "device": device, "note": note,
        "direction": direction, "collectiveId": args.collective_id,
        "shape": list(shape), "dtype": args.dtype,
    }
    timing = CollectiveTiming()
    started = time.perf_counter()

    # Per-direction gating is preserved: the collective rides the transport, so
    # it inherits the transport's qualification and may not bypass it.
    try:
        ledger = QualificationLedger.load(args.ledger)
        record = ledger.assert_permitted(args.root_vendor, dest_vendor)
        report["qualifiedBy"] = {"direction": record.direction,
                                 "throughputMBps": record.throughput_mbps}
    except (TransportError, ValueError) as error:
        report["error"] = f"qualification refused: {error}"
        print(json.dumps(report, sort_keys=True), flush=True)
        return 3

    gate = FrameGate(collective_id=args.collective_id, epoch=0)

    try:
        if args.role == "root":
            tensor = reference_tensor(shape, args.dtype, args.seed, device, torch)
            if args.non_contiguous and tensor.dim() >= 2:
                tensor = tensor.transpose(0, 1)
                report["nonContiguousInput"] = not tensor.is_contiguous()

            t0 = time.perf_counter()
            tensor, degenerate = local_broadcast(tensor, args.group_size, torch)
            timing.local_ms = (time.perf_counter() - t0) * 1000.0
            timing.local_degenerate = degenerate

            payload = stage_to_host(tensor, torch)
            frame = CollectiveFrame("broadcast", args.collective_id, gate.epoch,
                                    args.root_vendor, 0, 0, args.dtype,
                                    tuple(int(d) for d in tensor.shape))
            frame.validate()
            if frame.byte_size != len(payload):
                raise CollectiveError(
                    f"payload {len(payload)} disagrees with frame {frame.byte_size}")

            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", args.port))
            listener.listen(1)
            listener.settimeout(120)
            sock, _ = listener.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            t1 = time.perf_counter()
            _send(sock, frame.pack())
            if args.fail_after_frame:
                # Abort between header and payload: the peer must fence this
                # collective rather than wait, and must not consume the frame.
                sock.close(); listener.close()
                report["injectedFailure"] = "aborted after frame, before payload"
                report["timing"] = timing.to_dict()
                print(json.dumps(report, sort_keys=True), flush=True)
                return 4
            sock.sendall(payload)
            ack = _recv_exact(sock, 1)
            timing.bridge_ms = (time.perf_counter() - t1) * 1000.0
            sock.close(); listener.close()

            report["bytes"] = len(payload)
            report["peerVerified"] = ack == b"\x01"
            # Byte identity is the oracle; the checksum is only a diagnostic,
            # since two different tensors can share a sum.
            report["sha256"] = hashlib.sha256(payload).hexdigest()[:16]
            report["checksum"] = _checksum(tensor, torch)

        else:
            sock = socket.create_connection((args.peer, args.port), timeout=120)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            t1 = time.perf_counter()
            frame = gate.accept(CollectiveFrame.unpack(_recv(sock)))
            payload = _recv_exact(sock, frame.byte_size)
            timing.bridge_ms = (time.perf_counter() - t1) * 1000.0
            if len(payload) != frame.byte_size:
                raise CollectiveError("payload length disagrees with the accepted frame")

            dtype = getattr(torch, _TORCH_NAME[frame.dtype])
            if frame.is_empty:
                tensor = torch.empty(frame.shape, dtype=dtype, device=device)
            else:
                flat = torch.frombuffer(bytearray(payload), dtype=dtype)
                tensor = flat.reshape(frame.shape).to(device)

            t0 = time.perf_counter()
            tensor, degenerate = local_broadcast(tensor, args.group_size, torch)
            timing.local_ms = (time.perf_counter() - t0) * 1000.0
            timing.local_degenerate = degenerate

            sock.sendall(b"\x01")
            sock.close()
            landed = tensor.detach().to("cpu").contiguous().numpy().tobytes() if tensor.numel() else b""
            report["bytes"] = len(payload)
            report["rootVendorFromFrame"] = frame.root_vendor
            # Byte identity, not numeric agreement: equal sums do not imply
            # equal tensors.
            report["sha256"] = hashlib.sha256(landed).hexdigest()[:16]
            report["byteExact"] = landed == payload
            report["checksum"] = _checksum(tensor, torch)

    except (CollectiveError, StaleFrameError, OSError) as error:
        epoch = gate.fail()
        report["error"] = f"{type(error).__name__}: {error}"
        report["fencedAtEpoch"] = epoch
        report["timing"] = timing.to_dict()
        print(json.dumps(report, sort_keys=True), flush=True)
        return 5

    timing.total_ms = (time.perf_counter() - started) * 1000.0
    report["timing"] = timing.to_dict()
    dnote = degeneracy_note({args.root_vendor: args.group_size,
                             dest_vendor: args.group_size})
    if dnote:
        report["degeneracy"] = dnote
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


def _checksum(tensor: Any, torch: Any) -> float:
    if tensor.numel() == 0:
        return 0.0
    return round(float(tensor.detach().double().sum().item()), 6)


if __name__ == "__main__":
    raise SystemExit(main())
