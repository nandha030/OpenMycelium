"""`hetccl` command line: diagnose, qualify, smoke-test.

Three entry points, matching the three questions an operator has:

    hetccl diagnose                     what hardware and runtimes are here?
    hetccl qualify --direction ...      is this direction byte-verified?
    hetccl smoke-test                   does the installed package work?

Everything fails closed. An unqualified direction is refused rather than
attempted, and a missing runtime is reported rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional

from . import __version__
from .collective_frame import (
    AllGatherAssembler,
    CollectiveError,
    CollectiveFrame,
    ConnectionFenced,
    ConnectionMultiplexer,
)
from .protocol_version import ProtocolIdentity, ProtocolMismatch, negotiate
from .stream import FrameStream, encode_frame
from .qualification import qualify, run_probe
from .xvendor import (
    DEFAULT_CHUNK,
    DEFAULT_SLOTS,
    ActivationHeader,
    QualificationLedger,
    TransportError,
    describe,
)

LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")
BRIDGE = os.environ.get("OM_BRIDGE_BIN", "/opt/bin/bridge")
PROBE = os.environ.get("OM_PROBE_BIN", "/opt/bin/host_access_probe")


def _run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _gpu(vendor: str) -> str:
    if vendor == "cuda":
        out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        return out.stdout.strip().splitlines()[0] if out and out.stdout.strip() else "absent"
    rocminfo = shutil.which("rocminfo") or "/opt/rocm/bin/rocminfo"
    out = _run([rocminfo])
    if out and out.stdout:
        for line in out.stdout.splitlines():
            if "Marketing Name" in line and "Ryzen" not in line and "CPU" not in line:
                return line.split(":", 1)[1].strip()
    return "absent"


def cmd_diagnose(args) -> int:
    probe = run_probe(PROBE, search_paths=[PROBE])
    decisions = qualify(probe, {"cuda": True, "rocm": False})
    ledger = QualificationLedger.load(args.ledger)
    report = {
        "package": {"name": "openmycelium-hetccl", "version": __version__},
        "protocol": ProtocolIdentity().to_dict(),
        "host": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine()},
        "gpus": {"cuda": _gpu("cuda"), "rocm": _gpu("rocm")},
        "hostAccess": probe or "probe unavailable",
        "receivePolicy": {v: d.to_dict() for v, d in decisions.items()},
        "transport": describe(),
        "qualifiedDirections": list(ledger.directions()),
        "knownLimitations": [
            "UCX direct receive into ROCm device memory is DISABLED on the WSL "
            "DXG path: uct_rocm_copy stores from the CPU into memory that is "
            "not host-accessible and faults. Host staging is the only qualified "
            "receive path for ROCm here.",
            "Vendor-local NCCL/RCCL groups of a single rank are degenerate; "
            "collective results validate orchestration and cross-vendor routing "
            "only, not multi-rank vendor-local behaviour.",
            "A ROCm PyTorch wheel on WSL may need a provisional HSA runtime "
            "patch (see runtime/bridge/rocm_torch_patch.sh). That configuration "
            "is unsupported by AMD and PyTorch.",
            "No unified VRAM. The two cards remain separate memory domains.",
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_qualify(args) -> int:
    if args.direction not in ("cuda-to-rocm", "rocm-to-cuda"):
        print(json.dumps({"error": f"unknown direction {args.direction}"}))
        return 2
    send, recv = args.direction.split("-to-")
    ledger = QualificationLedger.load(args.ledger)
    try:
        record = ledger.assert_permitted(send, recv)
    except TransportError as error:
        print(json.dumps({"direction": args.direction, "qualified": False,
                          "error": str(error)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"direction": args.direction, "qualified": True,
                      "record": record.to_dict()}, indent=2, sort_keys=True))
    return 0


# ------------------------------------------------------------------ smoke test

def _smoke_host_framing() -> tuple[bool, str]:
    """Framing and validation with no GPU involved at all."""
    try:
        header = ActivationHeader("f32", (4, 8), sequence=3)
        if ActivationHeader.unpack(header.pack()).shape != (4, 8):
            return False, "activation header did not round-trip"
        frame = CollectiveFrame("all-gather", 1, 0, "cuda", 2, 0, "i64", (3, 3))
        back = CollectiveFrame.unpack(frame.pack())
        if back.dtype != "i64" or back.root_rank != 2:
            return False, "collective frame did not round-trip"
        for bad in (CollectiveFrame("broadcast", 1, 0, "cuda", 0, 0, "f32", (-1,)),
                    CollectiveFrame("broadcast", 1, 0, "tpu", 0, 0, "f32", (2,))):
            try:
                bad.validate()
                return False, "invalid frame was accepted"
            except CollectiveError:
                pass
        negotiate(ProtocolIdentity(), ProtocolIdentity())
        try:
            negotiate(ProtocolIdentity(), ProtocolIdentity(activation=99))
            return False, "protocol mismatch was not refused"
        except ProtocolMismatch:
            pass
        return True, "framing, validation and protocol negotiation"
    except Exception as error:                      # noqa: BLE001 - smoke test
        return False, f"{type(error).__name__}: {error}"


def _smoke_assembler_and_mux() -> tuple[bool, str]:
    try:
        a = AllGatherAssembler(3, 1, 0)
        for rank in (2, 0, 1):                      # deliberately out of order
            f = CollectiveFrame("all-gather", 1, 0, "cuda", rank, 0, "u8", (4,))
            a.accept(f, bytes([rank]) * 4)
        if a.result() != bytes([0]) * 4 + bytes([1]) * 4 + bytes([2]) * 4:
            return False, "all-gather output was not rank-ordered"
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(CollectiveFrame("broadcast", 1, 0, "cuda", 0, 0, "f32", (2,)))
        try:
            m.accept(CollectiveFrame("broadcast", 1, 0, "cuda", 0, 9, "f32", (2,)))
            return False, "out-of-order frame was accepted"
        except ConnectionFenced:
            pass
        if not m.gates[2].failed:
            return False, "connection fencing did not reach the second collective"
        return True, "rank ordering and connection fencing"
    except Exception as error:                      # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"


def _smoke_bridge(send: str, recv: str, ledger_path: str) -> tuple[bool, str]:
    ledger = QualificationLedger.load(ledger_path)
    try:
        ledger.assert_permitted(send, recv)
    except TransportError as error:
        return False, f"unqualified: {str(error)[:70]}"
    if not os.path.exists(BRIDGE):
        return False, f"bridge binary not found at {BRIDGE}"
    port = 30500 + (7 if send == "cuda" else 9)
    receiver = subprocess.Popen(
        [BRIDGE, "--role", "recv", "--vendor", recv, "--bytes", str(8 << 20),
         "--chunk", str(DEFAULT_CHUNK), "--slots", str(DEFAULT_SLOTS),
         "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    import time
    time.sleep(0.8)
    sender = _run([BRIDGE, "--role", "send", "--vendor", send, "--bytes", str(8 << 20),
                   "--chunk", str(DEFAULT_CHUNK), "--slots", str(DEFAULT_SLOTS),
                   "--window", str(DEFAULT_SLOTS), "--port", str(port),
                   "--peer", "127.0.0.1"], timeout=180)
    try:
        out, _ = receiver.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        receiver.kill()
        return False, "receiver timed out"
    if sender is None or '"verified":true' not in (out or "").replace(" ", ""):
        return False, "device round trip was not byte-verified"
    return True, "8 MiB device round trip, byte-verified"


def _smoke_live_collectives() -> tuple[bool, str]:
    """Broadcast, all-gather and multiplexing over a real socket.

    Uses a socketpair and a background thread rather than the cross-vendor link,
    so the check runs in a clean environment with no GPU and no torch. It
    exercises the parts that in-process tests cannot: real segmentation, the
    buffered reader, and fencing driven by an actual short read.
    """
    import socket as _socket
    import threading

    try:
        left, right = _socket.socketpair()
        collectives = {
            1: ("broadcast", "f32", (4, 8), 3),
            2: ("all-gather", "u8", (16,), 3),
        }
        blobs: list[bytes] = []
        expected: dict[int, list[bytes]] = {1: [], 2: []}
        for idx in range(3):
            for cid, (op, dtype, shape, _n) in collectives.items():
                origin = idx if op == "all-gather" else 0
                frame = CollectiveFrame(op, cid, 0, "cuda", origin, idx, dtype, shape)
                payload = bytes([(cid * 31 + idx * 7 + i) & 0xFF
                                 for i in range(frame.byte_size)])
                expected[cid].append(payload)
                blobs.append(encode_frame(frame, payload))

        # Write in 5-byte slices: headers and payloads land split across reads.
        def writer():
            joined = b"".join(blobs)
            for i in range(0, len(joined), 5):
                left.sendall(joined[i:i + 5])
            left.shutdown(_socket.SHUT_WR)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()

        mux = ConnectionMultiplexer()
        for cid in collectives:
            mux.open(cid)
        gather = AllGatherAssembler(3, 2, 0)
        stream = FrameStream(right)
        got: dict[int, list[bytes]] = {1: [], 2: []}
        for _ in range(len(blobs)):
            frame, payload = stream.next_frame()
            mux.accept(frame)
            if frame.operation == "all-gather":
                gather.accept(frame, payload)
            got[frame.collective_id].append(payload)
        thread.join(timeout=5)
        left.close(); right.close()

        if got != expected:
            return False, "frames did not survive segmented delivery"
        if not gather.is_complete:
            return False, "all-gather did not complete"
        if gather.result() != b"".join(expected[2]):
            return False, "all-gather output was not rank-ordered"
        return True, (f"broadcast+all-gather interleaved, {stream.frames} frames "
                      f"in {stream.reads} reads, rank-ordered")
    except Exception as error:                      # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"


def _smoke_live_fencing() -> tuple[bool, str]:
    """A truncated payload must fence the whole connection, not half-succeed."""
    import socket as _socket
    try:
        left, right = _socket.socketpair()
        frame = CollectiveFrame("broadcast", 1, 0, "cuda", 0, 0, "f32", (64,))
        blob = encode_frame(frame, b"\x00" * frame.byte_size)
        left.sendall(blob[:len(blob) // 2])         # cut mid-payload
        left.close()

        mux = ConnectionMultiplexer()
        mux.open(1); mux.open(2)
        stream = FrameStream(right)
        try:
            f, payload = stream.next_frame()
            mux.accept(f)
            right.close()
            return False, "truncated frame was accepted"
        except CollectiveError as error:
            mux.fence(1, str(error))
        right.close()
        if not mux.fenced or not mux.gates[2].failed:
            return False, "fencing did not reach the whole connection"
        return True, "truncated payload fenced every collective on the link"
    except Exception as error:                      # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"


def _smoke_fail_closed(ledger_path: str) -> tuple[bool, str]:
    """An unrecorded direction must be refused, not attempted."""
    empty = QualificationLedger()
    try:
        empty.assert_permitted("cuda", "rocm")
        return False, "unqualified direction was permitted"
    except TransportError:
        return True, "unqualified direction refused"


def cmd_smoke_test(args) -> int:
    checks: list[tuple[str, bool, str]] = []

    ok, detail = _smoke_host_framing()
    checks.append(("host-only framing (no GPU required)", ok, detail))
    ok, detail = _smoke_assembler_and_mux()
    checks.append(("rank ordering and multiplexer fencing", ok, detail))
    ok, detail = _smoke_live_collectives()
    checks.append(("live broadcast + all-gather over a socket", ok, detail))
    ok, detail = _smoke_live_fencing()
    checks.append(("live connection fencing on truncation", ok, detail))
    ok, detail = _smoke_fail_closed(args.ledger)
    checks.append(("fail-closed without a qualification record", ok, detail))

    for send, recv in (("cuda", "rocm"), ("rocm", "cuda")):
        ok, detail = _smoke_bridge(send, recv, args.ledger)
        checks.append((f"{send} -> {recv} device round trip", ok, detail))

    for name, passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<46} {detail}")
    failed = [n for n, p, _ in checks if not p]
    print()
    print(f"smoke test: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hetccl",
        description="OpenMycelium HetCCL cross-vendor communication runtime")
    parser.add_argument("--version", action="version",
                        version=f"openmycelium-hetccl {__version__}")
    parser.add_argument("--ledger", default=LEDGER,
                        help="qualification ledger path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diagnose", help="report hardware, runtimes and receive policy")
    q = sub.add_parser("qualify", help="check a direction's qualification record")
    q.add_argument("--direction", required=True,
                   choices=("cuda-to-rocm", "rocm-to-cuda"))
    sub.add_parser("smoke-test", help="verify the installed package works")

    args = parser.parse_args(argv)
    return {"diagnose": cmd_diagnose, "qualify": cmd_qualify,
            "smoke-test": cmd_smoke_test}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
