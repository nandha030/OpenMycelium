"""Hierarchical cross-vendor all-gather.

    local gather (NCCL/RCCL)  ->  border exchange (host-staged-xvendor)
                              ->  local distribution (NCCL/RCCL)

There is no root. Every rank contributes and every rank ends holding the same
rank-ordered concatenation. Ordering comes from the contributing rank carried in
each frame, never from arrival order -- `--reverse-send` deliberately transmits
contributions backwards to prove that.

Correctness is byte-for-byte against an independently constructed expected
buffer. A numeric checksum would agree for two tensors holding the same values
in the wrong rank order, so it cannot decide this.

Each side owns a contiguous block of global ranks, so a two-process run can
still exercise multi-rank ordering: with `--local-ranks 2`, CUDA owns global
ranks 0-1 and ROCm owns 2-3.
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
    AllGatherAssembler,
    CollectiveError,
    CollectiveFrame,
    CollectiveTiming,
    StaleFrameError,
    degeneracy_note,
)
from mccl.xvendor import QualificationLedger, TransportError  # noqa: E402

LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")
_TORCH_NAME = {"f16": "float16", "bf16": "bfloat16", "f32": "float32",
               "f64": "float64", "i32": "int32", "i64": "int64", "u8": "uint8"}


def _send(sock, blob: bytes) -> None:
    sock.sendall(struct.pack("<I", len(blob)) + blob)


def _recv_exact(sock, count: int) -> bytes:
    parts, left = [], count
    while left:
        chunk = sock.recv(left)
        if not chunk:
            raise CollectiveError(f"border peer closed with {left}/{count} bytes outstanding")
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def _recv(sock) -> bytes:
    size = struct.unpack("<I", _recv_exact(sock, 4))[0]
    if size > (1 << 30):
        raise CollectiveError(f"implausible frame length {size}")
    return _recv_exact(sock, size)


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


def rank_bytes(rank: int, shape, dtype_tag: str, torch: Any) -> bytes:
    """Deterministic contribution for a global rank, derived on both sides."""
    numel = 1
    for d in shape:
        numel *= d
    dtype = getattr(torch, _TORCH_NAME[dtype_tag])
    generator = torch.Generator(device="cpu")
    generator.manual_seed(90000 + rank)
    if numel == 0:
        return b""
    if dtype_tag in ("i32", "i64", "u8"):
        flat = torch.randint(0, 90, (numel,), generator=generator, dtype=dtype)
    else:
        flat = torch.randn(numel, generator=generator, dtype=torch.float32).to(dtype)
    return flat.reshape(tuple(shape)).contiguous().numpy().tobytes()


def main() -> int:
    p = argparse.ArgumentParser(description="Hierarchical cross-vendor all-gather")
    p.add_argument("--vendor", required=True, choices=("cuda", "rocm"))
    p.add_argument("--role", required=True, choices=("listen", "connect"),
                   help="transport role only; all-gather has no root")
    p.add_argument("--local-ranks", type=int, default=1)
    p.add_argument("--shape", default="2,3")
    p.add_argument("--dtype", default="f32")
    p.add_argument("--collective-id", type=int, default=1)
    p.add_argument("--port", type=int, default=28000)
    p.add_argument("--peer", default="127.0.0.1")
    p.add_argument("--reverse-send", action="store_true",
                   help="transmit local contributions in reverse rank order")
    p.add_argument("--drop-rank", type=int, default=-1,
                   help="omit one contribution, to prove incompleteness fails closed")
    p.add_argument("--ledger", default=LEDGER)
    args = p.parse_args()

    import torch  # noqa: PLC0415

    shape = tuple(int(x) for x in args.shape.split(",") if x != "")
    device, note = resolve_device(args.vendor, torch)
    L = args.local_ranks
    world = 2 * L
    # CUDA owns the low block, ROCm the high block. Neither is a root.
    base = 0 if args.vendor == "cuda" else L
    my_ranks = list(range(base, base + L))
    peer_vendor = "rocm" if args.vendor == "cuda" else "cuda"

    report: dict[str, Any] = {
        "vendor": args.vendor, "device": device, "note": note,
        "collectiveId": args.collective_id, "worldSize": world,
        "myRanks": my_ranks, "shape": list(shape), "dtype": args.dtype,
    }
    timing = CollectiveTiming()
    started = time.perf_counter()

    try:
        ledger = QualificationLedger.load(args.ledger)
        for a, b in ((args.vendor, peer_vendor), (peer_vendor, args.vendor)):
            ledger.assert_permitted(a, b)
        report["qualified"] = f"{args.vendor}<->{peer_vendor}"
    except (TransportError, ValueError) as error:
        report["error"] = f"qualification refused: {error}"
        print(json.dumps(report, sort_keys=True)); return 3

    assembler = AllGatherAssembler(world, args.collective_id, 0)

    try:
        # --- local gather: contributions from this vendor's ranks -------------
        t_local = time.perf_counter()
        local: dict[int, bytes] = {}
        for r in my_ranks:
            if r == args.drop_rank:
                continue
            payload = rank_bytes(r, shape, args.dtype, torch)
            # Round-trip through the device so the path is real, not host-only.
            dtype = getattr(torch, _TORCH_NAME[args.dtype])
            if payload:
                t = torch.frombuffer(bytearray(payload), dtype=dtype).reshape(shape).to(device)
                payload = t.detach().to("cpu").contiguous().numpy().tobytes()
            local[r] = payload
            assembler.accept(
                CollectiveFrame("all-gather", args.collective_id, 0,
                                args.vendor, r, 0, args.dtype, shape), payload)
        timing.local_ms = (time.perf_counter() - t_local) * 1000.0
        timing.local_degenerate = L <= 1

        # --- border exchange --------------------------------------------------
        if args.role == "listen":
            lst = socket.socket()
            lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            lst.bind(("0.0.0.0", args.port)); lst.listen(1); lst.settimeout(120)
            sock, _ = lst.accept()
        else:
            lst = None
            sock = socket.create_connection((args.peer, args.port), timeout=120)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        t_border = time.perf_counter()
        order = sorted(local, reverse=args.reverse_send)
        report["sendOrder"] = order
        _send(sock, struct.pack("<H", len(order)))
        for r in order:
            frame = CollectiveFrame("all-gather", args.collective_id, 0,
                                    args.vendor, r, 0, args.dtype, shape)
            _send(sock, frame.pack())
            if frame.byte_size:
                sock.sendall(local[r])

        expected_count = struct.unpack("<H", _recv(sock))[0]
        for _ in range(expected_count):
            frame = CollectiveFrame.unpack(_recv(sock))
            payload = _recv_exact(sock, frame.byte_size) if frame.byte_size else b""
            assembler.accept(frame, payload)
        timing.bridge_ms = (time.perf_counter() - t_border) * 1000.0

        # --- local distribution ----------------------------------------------
        t_dist = time.perf_counter()
        if not assembler.is_complete:
            raise CollectiveError(
                f"all-gather incomplete; missing ranks {assembler.missing}")
        assembled = assembler.result()
        out_shape = assembler.output_shape()
        dtype = getattr(torch, _TORCH_NAME[args.dtype])
        if assembled:
            output = torch.frombuffer(bytearray(assembled), dtype=dtype).reshape(out_shape).to(device)
            landed = output.detach().to("cpu").contiguous().numpy().tobytes()
        else:
            output = torch.empty(out_shape, dtype=dtype, device=device)
            landed = b""
        distribution_ms = (time.perf_counter() - t_dist) * 1000.0

        # --- byte-for-byte verification --------------------------------------
        expected = b"".join(rank_bytes(r, shape, args.dtype, torch) for r in range(world))
        report["outputShape"] = list(out_shape)
        report["bytes"] = len(landed)
        report["byteExact"] = landed == expected
        report["sha256"] = hashlib.sha256(landed).hexdigest()[:16]
        report["expectedSha256"] = hashlib.sha256(expected).hexdigest()[:16]
        if landed != expected:
            for i in range(min(len(landed), len(expected))):
                if landed[i] != expected[i]:
                    report["firstByteMismatch"] = i
                    break

        sock.sendall(b"\x01")
        _recv_exact(sock, 1)
        sock.close()
        if lst:
            lst.close()

    except (CollectiveError, StaleFrameError, OSError, ValueError) as error:
        epoch = assembler.fail()
        report["error"] = f"{type(error).__name__}: {error}"
        report["fencedAtEpoch"] = epoch
        report["timing"] = timing.to_dict()
        print(json.dumps(report, sort_keys=True)); return 5

    timing.total_ms = (time.perf_counter() - started) * 1000.0
    payload = timing.to_dict()
    payload["localDistributionMs"] = round(distribution_ms, 3)
    report["timing"] = payload
    dnote = degeneracy_note({args.vendor: L, peer_vendor: L})
    if dnote:
        report["degeneracy"] = dnote
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("byteExact") else 1


if __name__ == "__main__":
    raise SystemExit(main())
