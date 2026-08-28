"""Spawn one local process per global rank for a hierarchical collective run.

    python -m mycelium.launch_hierarchy --topology cuda:2,rocm:2 -- \
        python -m mycelium.hierarchy_check

Each child inherits RANK, WORLD_SIZE, and the shared rendezvous and coordinator
settings. The launcher exits non-zero if any rank does, so it can gate CI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .hierarchical import Topology


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch every rank of a hierarchical collective")
    parser.add_argument("--topology", required=True, help="vendor:count[,vendor:count...]")
    parser.add_argument("--master-addr", default=os.environ.get("MASTER_ADDR", "127.0.0.1"))
    parser.add_argument("--master-port", type=int, default=int(os.environ.get("MASTER_PORT", "29400")))
    parser.add_argument("--coordinator-host", default=os.environ.get("MCCL_COORDINATOR_HOST", "127.0.0.1"))
    parser.add_argument("--coordinator-port", type=int, default=int(os.environ.get("MCCL_COORDINATOR_PORT", "29500")))
    parser.add_argument("--group", default=os.environ.get("MCCL_GROUP", "mycelium-hierarchy"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a worker command is required after --")

    topology = Topology.from_spec(args.topology)
    shared = os.environ.copy()
    shared.update(
        {
            "MYCELIUM_TOPOLOGY": args.topology,
            "WORLD_SIZE": str(topology.world_size),
            "MASTER_ADDR": args.master_addr,
            "MASTER_PORT": str(args.master_port),
            "MCCL_COORDINATOR_HOST": args.coordinator_host,
            "MCCL_COORDINATOR_PORT": str(args.coordinator_port),
            "MCCL_GROUP": args.group,
        }
    )

    processes = []
    for group in topology.groups:
        for local_rank, rank in enumerate(group.ranks):
            environment = dict(shared)
            environment.update(
                {
                    "RANK": str(rank),
                    "LOCAL_RANK": str(local_rank),
                    "MYCELIUM_VENDOR": group.vendor,
                }
            )
            processes.append(subprocess.Popen(command, env=environment))

    exit_codes = [process.wait() for process in processes]
    for rank, code in enumerate(exit_codes):
        if code != 0:
            print(f"rank {rank} exited with {code}", file=sys.stderr)
    return 0 if all(code == 0 for code in exit_codes) else 1


if __name__ == "__main__":
    sys.exit(main())
