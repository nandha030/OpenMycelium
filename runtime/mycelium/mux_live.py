"""Live multiplexing over the real CUDA<->ROCm bridge.

The in-memory multiplexer tests prove the state machine. They cannot prove that
frame parsing, socket buffering, concurrency, and fencing behave when a real
stream fragments a header across three reads or coalesces four frames into one.
This drives `ConnectionMultiplexer` over an actual socket between a CUDA process
and a ROCm process, with payloads round-tripping through device memory.

Wire format per frame:  [u32 frame_len][frame][payload]

A note on empty tensors: every empty payload has the same SHA-256, so the digest
alone cannot distinguish a correct empty result from a wrong one. Correctness
there is established jointly by the validated shape and dtype metadata and the
zero-length payload -- reported as `emptyByMetadata` rather than as a digest
match.
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
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hetccl", "src"))

from hetccl.collective_frame import (  # noqa: E402
    AllGatherAssembler,
    CollectiveError,
    CollectiveFrame,
    ConnectionFenced,
    ConnectionMultiplexer,
    StaleFrameError,
)
from hetccl.xvendor import QualificationLedger, TransportError  # noqa: E402

LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")
_TORCH_NAME = {"f16": "float16", "bf16": "bfloat16", "f32": "float32",
               "f64": "float64", "i32": "int32", "i64": "int64", "u8": "uint8"}


class FrameStream:
    """Length-delimited reader that tolerates arbitrary socket segmentation.

    Everything arrives through one buffer, so a header split across three reads
    and four frames delivered in one read take the same path.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = bytearray()
        self.reads = 0

    def _fill(self, need: int) -> None:
        while len(self.buf) < need:
            chunk = self.sock.recv(65536)
            self.reads += 1
            if not chunk:
                raise CollectiveError(
                    f"peer closed with {need - len(self.buf)} of {need} bytes outstanding")
            self.buf.extend(chunk)

    def take(self, count: int) -> bytes:
        if count == 0:
            return b""
        self._fill(count)
        out = bytes(self.buf[:count])
        del self.buf[:count]
        return out

    def next_frame(self) -> tuple[CollectiveFrame, bytes]:
        length = struct.unpack("<I", self.take(4))[0]
        if not 8 <= length <= 4096:
            raise CollectiveError(f"implausible frame length {length}")
        frame = CollectiveFrame.unpack(self.take(length))
        return frame, self.take(frame.byte_size)


def encode(frame: CollectiveFrame, payload: bytes) -> bytes:
    blob = frame.pack()
    return struct.pack("<I", len(blob)) + blob + payload


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


def make_payload(seed: int, shape, dtype_tag: str, device: str, torch: Any) -> bytes:
    """Build on device, stage to host -- the real path, not a host-only shortcut."""
    numel = 1
    for d in shape:
        numel *= d
    dtype = getattr(torch, _TORCH_NAME[dtype_tag])
    if numel == 0:
        return b""
    g = torch.Generator(device="cpu")
    g.manual_seed(seed)
    if dtype_tag in ("i32", "i64", "u8"):
        flat = torch.randint(0, 90, (numel,), generator=g, dtype=dtype)
    else:
        flat = torch.randn(numel, generator=g, dtype=torch.float32).to(dtype)
    t = flat.reshape(tuple(shape)).to(device)
    return t.detach().to("cpu").contiguous().numpy().tobytes()


def land_on_device(payload: bytes, shape, dtype_tag: str, device: str, torch: Any) -> bytes:
    """Push the received bytes onto the device and read them back."""
    if not payload:
        return b""
    dtype = getattr(torch, _TORCH_NAME[dtype_tag])
    t = torch.frombuffer(bytearray(payload), dtype=dtype).reshape(tuple(shape)).to(device)
    return t.detach().to("cpu").contiguous().numpy().tobytes()


# Two concurrent collectives with deliberately different dtypes and sizes.
PLAN = {
    11: {"op": "broadcast",  "dtype": "f32", "shape": (4, 8),   "frames": 3},
    22: {"op": "all-gather", "dtype": "f16", "shape": (64, 32), "frames": 3},
}


def sender(sock, scenario: str, epoch: int, device: str, torch: Any,
           report: dict) -> None:
    frames: list[tuple[int, bytes]] = []
    for idx in range(3):
        for cid, spec in PLAN.items():
            use_epoch = epoch
            if scenario == "stale-epoch" and cid == 22 and idx == 1:
                use_epoch = epoch - 1 if epoch else 7      # deliberately wrong
            # For broadcast the field is the root and stays 0; for all-gather it
            # identifies the contributing rank, so each frame carries a
            # different one. Reusing 0 makes the assembler reject duplicates.
            origin_rank = idx if spec["op"] == "all-gather" else 0
            frame = CollectiveFrame(spec["op"], cid, use_epoch, "cuda",
                                    origin_rank, idx, spec["dtype"], spec["shape"])
            payload = make_payload(1000 + cid + idx, spec["shape"],
                                   spec["dtype"], device, torch)
            frames.append((cid, encode(frame, payload)))

    if scenario == "reverse-completion":
        # Finish collective 22 entirely, then 11 -- the opposite of interleaved.
        frames.sort(key=lambda item: (0 if item[0] == 22 else 1))
    report["sendOrder"] = [cid for cid, _ in frames]

    if scenario == "coalesced":
        # Several complete frames in a single write.
        blob = b"".join(b for _, b in frames)
        sock.sendall(blob)
        report["writes"] = 1
    elif scenario == "fragmented":
        # Headers and payloads deliberately split across many small writes.
        blob = b"".join(b for _, b in frames)
        step, writes = 7, 0
        for i in range(0, len(blob), step):
            sock.sendall(blob[i:i + step])
            writes += 1
        report["writes"] = writes
    elif scenario == "disconnect-mid-payload":
        # Collective 11 is fine; 22 is cut in the middle of a payload.
        blob = b"".join(b for _, b in frames)
        cut = len(blob) - (len(frames[-1][1]) // 2)
        sock.sendall(blob[:cut])
        report["writes"] = 1
        report["truncatedAt"] = cut
        sock.close()
        return
    else:
        for _, b in frames:
            sock.sendall(b)
        report["writes"] = len(frames)
    sock.shutdown(socket.SHUT_WR)


def receiver(sock, expect_fence: bool, epoch: int, device: str, torch: Any,
             report: dict) -> bool:
    mux = ConnectionMultiplexer()
    for cid in PLAN:
        mux.open(cid, epoch=epoch)
    stream = FrameStream(sock)
    gathers = {cid: AllGatherAssembler(3, cid, epoch)
               for cid, s in PLAN.items() if s["op"] == "all-gather"}
    seen: dict[int, list[str]] = {cid: [] for cid in PLAN}
    empty_by_metadata = 0

    try:
        while True:
            try:
                frame, payload = stream.next_frame()
            except CollectiveError as error:
                if "peer closed" in str(error) and not stream.buf and all(
                        len(v) == PLAN[k]["frames"] for k, v in seen.items()):
                    break                                  # clean end of stream
                raise
            mux.accept(frame)
            landed = land_on_device(payload, frame.shape, frame.dtype, device, torch)
            if frame.is_empty:
                # Digest is meaningless here; metadata plus a zero-length
                # payload is what establishes correctness.
                empty_by_metadata += 1
            if landed != payload:
                raise CollectiveError(
                    f"collective {frame.collective_id} seq {frame.sequence} "
                    "did not survive the device round trip")
            if frame.collective_id in gathers:
                gathers[frame.collective_id].accept(frame, payload)
            seen[frame.collective_id].append(hashlib.sha256(payload).hexdigest()[:12])
            if all(len(v) == PLAN[k]["frames"] for k, v in seen.items()):
                break
    except (ConnectionFenced, StaleFrameError, CollectiveError, OSError) as error:
        if not mux.fenced:
            mux.fence(-1, str(error))
        report["error"] = f"{type(error).__name__}: {str(error)[:110]}"
        report["mux"] = mux.state()
        report["framesBefore"] = {k: len(v) for k, v in seen.items()}
        # No collective may report success out of a fenced connection.
        report["partialSuccessExposed"] = any(
            g.is_complete for g in gathers.values())
        return expect_fence and not report["partialSuccessExposed"]

    report["mux"] = mux.state()
    report["digests"] = seen
    report["emptyByMetadata"] = empty_by_metadata
    report["socketReads"] = stream.reads
    report["gathersComplete"] = {cid: g.is_complete for cid, g in gathers.items()}
    return not expect_fence


def main() -> int:
    p = argparse.ArgumentParser(description="Live multiplexing over the bridge")
    p.add_argument("--vendor", required=True, choices=("cuda", "rocm"))
    p.add_argument("--side", required=True, choices=("send", "recv"))
    p.add_argument("--scenario", default="interleave")
    p.add_argument("--epoch", type=int, default=0)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--port", type=int, default=29000)
    p.add_argument("--peer", default="127.0.0.1")
    p.add_argument("--ledger", default=LEDGER)
    args = p.parse_args()

    import torch  # noqa: PLC0415

    device, note = resolve_device(args.vendor, torch)
    peer_vendor = "rocm" if args.vendor == "cuda" else "cuda"
    report: dict[str, Any] = {"vendor": args.vendor, "side": args.side,
                              "scenario": args.scenario, "device": device,
                              "note": note, "epoch": args.epoch}
    expect_fence = args.scenario in ("stale-epoch", "disconnect-mid-payload")

    try:
        ledger = QualificationLedger.load(args.ledger)
        ledger.assert_permitted(args.vendor, peer_vendor)
    except (TransportError, ValueError) as error:
        report["error"] = f"qualification refused: {error}"
        print(json.dumps(report, sort_keys=True)); return 3

    ok = True
    started = time.perf_counter()
    try:
        if args.side == "recv":
            lst = socket.socket()
            lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            lst.bind(("0.0.0.0", args.port)); lst.listen(4); lst.settimeout(120)
        for round_index in range(args.rounds):
            epoch = args.epoch + round_index
            if args.side == "recv":
                sock, _ = lst.accept()
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                ok = receiver(sock, expect_fence, epoch, device, torch, report) and ok
                sock.close()
            else:
                sock = socket.create_connection((args.peer, args.port), timeout=120)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sender(sock, args.scenario, epoch, device, torch, report)
                try:
                    sock.close()
                except OSError:
                    pass
            if args.rounds > 1:
                time.sleep(0.05)
        if args.side == "recv":
            lst.close()
    except OSError as error:
        report["error"] = f"OSError: {error}"
        ok = expect_fence

    report["seconds"] = round(time.perf_counter() - started, 3)
    report["ok"] = ok
    print(json.dumps(report, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
