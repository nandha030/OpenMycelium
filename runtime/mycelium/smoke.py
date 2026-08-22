"""Executable CPU/Gloo correctness smoke test for a Mycelium plan."""

from __future__ import annotations

import json

from .adapter import CPUForwardedCollective, RuntimeConfig


def main() -> int:
    config = RuntimeConfig.from_environment()
    collective = CPUForwardedCollective(config)
    torch = collective.torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tensor = torch.tensor([float(config.rank + 1)], device=device)
    collective.all_reduce(tensor)
    actual = float(tensor.cpu().item())
    expected = float(config.world_size * (config.world_size + 1) // 2)
    passed = actual == expected
    print(
        json.dumps(
            {
                "algorithm": config.algorithm,
                "version": config.algorithm_version,
                "planId": config.plan_id,
                "transport": config.transport,
                "rank": config.rank,
                "worldSize": config.world_size,
                "device": device,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    collective.barrier()
    collective.close()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
