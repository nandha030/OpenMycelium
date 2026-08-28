"""Render the two GPU probes and decide whether the pair is acceptable.

The expected cards are named explicitly. A run that provisioned two CUDA
environments, or that fell back to one card driving both stages, would still
produce two "qualified" reports -- so the identities are checked against what
this machine is supposed to have, and against each other.
"""

from __future__ import annotations

import json
import sys

EXPECT = {
    "cuda": ("NVIDIA", "5060"),
    "rocm": ("AMD", "9060"),
}
GIB = 1 << 30


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as error:                                    # noqa: BLE001
        return {"error": f"unreadable: {error}", "qualified": False}


def main(cuda_path: str, rocm_path: str) -> int:
    reports = {"cuda": load(cuda_path), "rocm": load(rocm_path)}
    problems: list[str] = []

    for vendor, report in reports.items():
        name = report.get("deviceName", "")
        print(f"    {vendor}")
        if report.get("error"):
            print(f"      error         {report['error']}")
            problems.append(f"{vendor}: {report['error']}")
            continue
        print(f"      torch         {report.get('torchVersion')}")
        print(f"      build         {report.get('buildVendor')} "
              f"(hip {report.get('hipVersion')}, cuda {report.get('cudaVersion')})")
        print(f"      device        {name}")
        print(f"      memory        {report.get('totalBytes', 0) / GIB:.2f} GiB total, "
              f"{report.get('freeBytes', 0) / GIB:.2f} GiB free")
        print(f"      bf16 matmul   finite={report.get('matmulFinite')} "
              f"on {report.get('resultDevice')} "
              f"mean|x|={report.get('matmulMeanAbs', 0):.3f}")
        print(f"      allocated     {report.get('allocatedBytes', 0)} bytes")

        want_vendor, want_model = EXPECT[vendor]
        if want_vendor.lower() not in name.lower():
            problems.append(f"{vendor}: expected an {want_vendor} card, got {name!r}")
        if want_model not in name:
            problems.append(f"{vendor}: expected model {want_model}, got {name!r}")
        if report.get("buildVendor") != vendor:
            problems.append(f"{vendor}: torch is a {report.get('buildVendor')} build")
        if not report.get("ranOnDevice"):
            problems.append(f"{vendor}: the matmul did not run on the device")
        if not report.get("matmulFinite"):
            problems.append(f"{vendor}: the bf16 matmul was not finite")

    names = [reports[v].get("deviceName", "") for v in ("cuda", "rocm")]
    if names[0] and names[0] == names[1]:
        problems.append("both environments report the same device; "
                        "this is one card, not two")

    print()
    if problems:
        for item in problems:
            print(f"      PROBLEM: {item}")
        return 1
    print("      both environments drive distinct, expected cards with no fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
