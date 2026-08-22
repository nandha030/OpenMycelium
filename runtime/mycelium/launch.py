"""Launch a command with normalized Mycelium distributed environment values."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .adapter import RuntimeConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch an OpenMycelium distributed worker")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a workload command is required after --")
    config = RuntimeConfig.from_environment()
    environment = os.environ.copy()
    environment.update({
        "RANK": str(config.rank),
        "WORLD_SIZE": str(config.world_size),
        "LOCAL_RANK": str(config.worker_index),
        "MASTER_ADDR": config.rendezvous_host,
        "MASTER_PORT": str(config.rendezvous_port),
    })
    return subprocess.call(command, env=environment)


if __name__ == "__main__":
    sys.exit(main())
