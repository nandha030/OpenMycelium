"""Prove a provisioned environment really drives the GPU it claims.

Run inside the environment under test. Emits one JSON object.

The checks that matter are the negative ones. `torch.cuda.is_available()` being
true is not evidence: a ROCm build reports the same attribute name, and a build
that silently fell back would still import. So this asserts the vendor of the
build, the identity of the device, that the operands actually lived on the
device, and that the result came back finite -- and it refuses to accept a
result computed on the CPU.
"""

from __future__ import annotations

import json
import sys

report: dict = {"expect": sys.argv[1] if len(sys.argv) > 1 else "unknown"}

try:
    import torch

    report["torchVersion"] = torch.__version__
    report["hipVersion"] = getattr(torch.version, "hip", None)
    report["cudaVersion"] = getattr(torch.version, "cuda", None)
    report["buildVendor"] = "rocm" if report["hipVersion"] else "cuda"
    report["available"] = bool(torch.cuda.is_available())
    report["deviceCount"] = torch.cuda.device_count() if report["available"] else 0

    if report["available"]:
        report["deviceName"] = torch.cuda.get_device_name(0)
        free, total = torch.cuda.mem_get_info(0)
        report["totalBytes"] = total
        report["freeBytes"] = free
        capability = torch.cuda.get_device_capability(0)
        report["capability"] = f"{capability[0]}.{capability[1]}"

        # A real operation, on the device, in the dtype the runtime uses.
        left = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
        right = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
        product = left @ right
        torch.cuda.synchronize()
        report["operandDevice"] = str(left.device)
        report["resultDevice"] = str(product.device)
        report["resultDtype"] = str(product.dtype)
        report["matmulFinite"] = bool(torch.isfinite(product).all().item())
        report["matmulMeanAbs"] = float(product.abs().mean().item())
        # A CPU fallback would show up here as a device type of "cpu".
        report["ranOnDevice"] = product.device.type == "cuda"
        report["allocatedBytes"] = torch.cuda.memory_allocated(0)
    else:
        report["deviceName"] = ""
        report["matmulFinite"] = False
        report["ranOnDevice"] = False

    report["vendorMatchesExpectation"] = report["buildVendor"] == report["expect"]
    report["qualified"] = bool(
        report["available"]
        and report["matmulFinite"]
        and report["ranOnDevice"]
        and report["vendorMatchesExpectation"]
        and report.get("allocatedBytes", 0) > 0
    )
except Exception as error:                                        # noqa: BLE001
    report["error"] = f"{type(error).__name__}: {error}"
    report["qualified"] = False

print(json.dumps(report, indent=2, sort_keys=True))
sys.exit(0 if report.get("qualified") else 1)
