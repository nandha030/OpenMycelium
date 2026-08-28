"""The `openmycelium` console entry point.

Dispatches a subcommand to the module that implements it. The Windows `.cmd`
file is a thin wrapper that calls this through WSL; every decision about what a
command does lives here, in the installed package, rather than in a batch file
that cannot be tested or versioned with the code.
"""

from __future__ import annotations

import os
import runpy
import sys
from typing import Dict, List, Tuple

VERSION = "0.2.0a4"

#: subcommand -> (module file under runtime/, implicit first argument)
COMMANDS: Dict[str, Tuple[str, str]] = {
    "doctor":  ("cli/doctor.py", ""),
    "fabric":  ("cli/fabric_cli.py", ""),
    "model":   ("cli/models.py", ""),
    "plan":    ("cli/scheduler_cli.py", ""),
    "run":     ("cli/coordinator.py", ""),
    "chat":    ("cli/chat.py", ""),
    "serve":   ("cli/serve.py", ""),
    "pool":    ("cli/pool.py", ""),
    "stats":   ("cli/stats.py", ""),
    "ps":      ("cli/lifecycle.py", "ps"),
    "stop":    ("cli/lifecycle.py", "stop"),
    "version": ("cli/lifecycle.py", "version"),
    "provision": ("cli/provision.py", ""),
    "config":  ("cli/config_cli.py", ""),
    "console": ("cli/console.py", ""),
}

#: Convenience spellings, expanded before dispatch.
ALIASES: Dict[str, List[str]] = {
    "pull":   ["model", "pull"],
    "models": ["model", "list"],
    "rm":     ["model", "remove"],
    "quit":   ["stop"],
    "exit":   ["stop"],
}

USAGE = """
  openmycelium - one model across an NVIDIA and an AMD GPU

  Setup and inspection
    openmycelium provision                 build the CUDA and ROCm environments
    openmycelium config                    resolved paths and where each came from
    openmycelium doctor                    check every precondition
    openmycelium fabric list               discovered accelerators
    openmycelium version                   versions and protocols

  Models
    openmycelium model list                what is loadable
    openmycelium model inspect MODEL       size, layers, origin
    openmycelium model pull REPO           fetch a Safetensors repository
    openmycelium model import PATH         copy into the fast store
    openmycelium model verify MODEL        check shards are complete
    openmycelium model remove MODEL --yes  delete from the store

  Running
    openmycelium plan --model MODEL        show the placement
    openmycelium run --model MODEL --prompt "..."
    openmycelium chat --model MODEL        loads once, then prompt freely
    openmycelium serve --model MODEL       OpenAI-compatible API on 11500
    openmycelium console                   local operator console on 11501

  Lifecycle
    openmycelium ps                        running runtimes
    openmycelium stop [RUN]                graceful shutdown
    openmycelium stop --prune              clear stale records

  Aliases:  pull, models, rm, quit, exit

  Decoding is deterministic greedy only in this build.
"""


def package_root() -> str:
    """The installed package directory: the parent of `runtime/`."""
    return os.path.dirname(os.path.abspath(__file__))


def prepare_path() -> None:
    """Put the runtime's own directories on `sys.path`, and nothing else.

    The modules import each other by bare name. Rewriting twenty files into a
    package hierarchy would be a larger change than this alpha warrants, so the
    directories are added explicitly -- from the installed location, never from
    a checkout.
    """
    root = os.path.join(package_root(), "runtime")
    for part in ("cli", "serving", "scheduler", "fabric"):
        directory = os.path.join(root, part)
        if directory not in sys.path:
            sys.path.insert(0, directory)


def main(argv: List[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if argv else 64
    if argv[0] == "--version":
        argv = ["version"]

    command = argv[0]
    if command in ALIASES:
        argv = ALIASES[command] + argv[1:]
        command = argv[0]
    if command not in COMMANDS:
        print(f"unknown command: {command}")
        print(USAGE)
        return 64

    relative, implicit = COMMANDS[command]
    target = os.path.join(package_root(), "runtime", *relative.split("/"))
    if not os.path.isfile(target):
        print(f"  the installed package is missing {relative}")
        return 70

    prepare_path()
    rest = argv[1:]
    sys.argv = [target] + ([implicit] if implicit else []) + rest
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as exit_code:
        return int(exit_code.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
