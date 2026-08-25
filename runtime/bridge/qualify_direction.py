"""Qualify one `host-staged-xvendor` direction and record the exact platform tuple.

Runs the bridge with payload CRC enabled, verifies the result byte-for-byte,
and appends a `QualificationRecord` only if it passed. A direction that has not
been through this is refused at transfer time -- the transport fails closed.

The recorded tuple includes GPU models, Windows build, WSL kernel, and driver
and runtime versions, so a driver or WSL upgrade invalidates the qualification
instead of silently inheriting it.

    python qualify_direction.py --send cuda --recv rocm --bytes 268435456
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hetccl", "src"))

from hetccl.xvendor import (  # noqa: E402
    DEFAULT_CHUNK,
    DEFAULT_SLOTS,
    QualificationLedger,
    QualificationRecord,
    TransportError,
)

BRIDGE = os.environ.get("OM_BRIDGE_BIN", "/opt/bin/bridge")
LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")


def _run(command, timeout=300):
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_model(vendor: str) -> str:
    if vendor == "cuda":
        out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        return out.stdout.strip().splitlines()[0] if out and out.stdout.strip() else "unknown"
    rocminfo = shutil.which("rocminfo") or "/opt/rocm/bin/rocminfo"
    out = _run([rocminfo])
    if out and out.stdout:
        for line in out.stdout.splitlines():
            if "Marketing Name" in line and "CPU" not in line:
                name = line.split(":", 1)[1].strip()
                if name and "AMD Ryzen" not in name:
                    return name
    return "unknown"


def cuda_driver() -> str:
    out = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    return out.stdout.strip().splitlines()[0] if out and out.stdout.strip() else "unknown"


def rocm_version() -> str:
    for path in ("/opt/rocm/.info/version", "/opt/rocm/.info/version-dev"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read().strip().split("-")[0]
        except OSError:
            continue
    match = re.search(r"rocm-(\d+\.\d+\.\d+)", os.path.realpath("/opt/rocm") or "")
    return match.group(1) if match else "unknown"


def windows_version() -> str:
    """Read the Windows build through the WSL interop path."""
    for source in ("/proc/sys/kernel/osrelease",):
        try:
            with open(source, "r", encoding="utf-8") as handle:
                text = handle.read().strip()
            match = re.search(r"(\d+\.\d+\.\d+)", text)
            if match:
                break
        except OSError:
            pass
    out = _run(["/mnt/c/Windows/System32/cmd.exe", "/c", "ver"], timeout=30)
    if out and out.stdout:
        match = re.search(r"\[Version ([\d.]+)\]", out.stdout)
        if match:
            return match.group(1)
    return "unknown"


def run_bridge(send: str, recv: str, total: int, chunk: int, slots: int, port: int):
    """Run one verified transfer and return (ok, throughput_mbps, detail)."""
    receiver = subprocess.Popen(
        [BRIDGE, "--role", "recv", "--vendor", recv, "--bytes", str(total),
         "--chunk", str(chunk), "--slots", str(slots), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(0.8)
    sender = subprocess.run(
        [BRIDGE, "--role", "send", "--vendor", send, "--bytes", str(total),
         "--chunk", str(chunk), "--slots", str(slots), "--window", str(slots),
         "--port", str(port), "--peer", "127.0.0.1"],
        capture_output=True, text=True, timeout=600,
    )
    try:
        r_out, _ = receiver.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        receiver.kill()
        return False, 0.0, "receiver timed out"

    try:
        recv_json = json.loads(r_out.strip().splitlines()[-1])
        send_json = json.loads(sender.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, 0.0, f"unparseable bridge output (rc={receiver.returncode})"

    ok = bool(recv_json.get("verified")) and recv_json.get("badCrc", 1) == 0
    return ok, float(send_json.get("MBps", 0.0)), recv_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify one cross-vendor direction")
    parser.add_argument("--send", required=True, choices=("cuda", "rocm"))
    parser.add_argument("--recv", required=True, choices=("cuda", "rocm"))
    parser.add_argument("--bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--port", type=int, default=22000)
    parser.add_argument("--ledger", default=LEDGER)
    args = parser.parse_args()

    direction = f"{args.send}->{args.recv}"
    print(f"qualifying {direction} with payload CRC enabled ...")
    ok, mbps, detail = run_bridge(args.send, args.recv, args.bytes,
                                  args.chunk, args.slots, args.port)
    print(f"  byte verified: {ok}   throughput: {mbps:.1f} MB/s")
    if not ok:
        print(f"  detail: {detail}")
        print(f"REFUSED: {direction} is not qualified")
        return 1

    record = QualificationRecord(
        direction=direction,
        send_gpu=gpu_model(args.send),
        recv_gpu=gpu_model(args.recv),
        windows_version=windows_version(),
        wsl_kernel=platform.release(),
        cuda_driver=cuda_driver(),
        rocm_version=rocm_version(),
        chunk_bytes=args.chunk,
        slots=args.slots,
        verified_bytes=args.bytes,
        throughput_mbps=round(mbps, 1),
        byte_verified=True,
        recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    ledger = QualificationLedger.load(args.ledger)
    try:
        ledger.record(record)
    except TransportError as error:
        print(f"REFUSED: {error}")
        return 1
    with open(args.ledger, "w", encoding="utf-8") as handle:
        handle.write(ledger.to_json())

    print(f"QUALIFIED {direction}")
    print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
