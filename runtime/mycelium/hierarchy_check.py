"""Correctness harness for the hierarchical cross-vendor collective.

Every rank contributes a known vector, so the expected result is analytic and
never derived from the collective under test. Rank r contributes (r+1), which
makes the global sum N(N+1)/2, the minimum 1, the maximum N, and the mean
(N+1)/2 in every element.

Run one process per global rank with a shared MYCELIUM_TOPOLOGY, for example:

    MYCELIUM_TOPOLOGY=cuda:2,rocm:2 RANK=0 python -m mycelium.hierarchy_check

Each rank prints a JSON verdict and exits non-zero if any reduction is wrong.
"""

from __future__ import annotations

import json
import os

from .hierarchical import HierarchicalCollective, HierarchyConfig, expected_all_reduce

# Float addition is not associative, so a hierarchical sum will not always be
# bitwise equal to a flat one. Integer reductions are checked exactly.
FLOAT_TOLERANCE = 1e-6


def main() -> int:
    config = HierarchyConfig.from_environment()
    collective = HierarchicalCollective(config)
    torch = collective.torch
    width = int(os.environ.get("MYCELIUM_CHECK_WIDTH", "8"))
    device = _device_for(collective)
    contributions = [float(rank + 1) for rank in range(config.topology.world_size)]

    results = []
    for reduction in ("sum", "min", "max", "avg"):
        tensor = torch.full((width,), float(config.rank + 1), dtype=torch.float32, device=device)
        collective.all_reduce(tensor, reduction=reduction)
        expected = expected_all_reduce(contributions, reduction)
        actual = [float(value) for value in tensor.detach().to("cpu").tolist()]
        deviation = max(abs(value - expected) for value in actual)
        results.append(
            {
                "reduction": reduction,
                "expected": expected,
                "actual": actual[0],
                "uniform": len(set(actual)) == 1,
                "maxDeviation": deviation,
                "passed": deviation <= FLOAT_TOLERANCE and len(set(actual)) == 1,
            }
        )

    passed = all(item["passed"] for item in results)
    verdict = dict(collective.describe())
    verdict.update({"device": device, "width": width, "checks": results, "passed": passed})
    print(json.dumps(verdict, sort_keys=True), flush=True)
    collective.barrier()
    collective.close()
    return 0 if passed else 1


def _device_for(collective: HierarchicalCollective) -> str:
    torch = collective.torch
    if collective.group.vendor in {"cuda", "rocm"} and torch.cuda.is_available():
        # One visible device per rank is the usual container contract; fall back
        # to round-robin when several ranks share a host.
        return f"cuda:{collective.config.rank % torch.cuda.device_count()}"
    return "cpu"


if __name__ == "__main__":
    raise SystemExit(main())
