"""Where the runtime's own files live, wherever it was installed.

The CLI spawns worker processes by path and builds a `PYTHONPATH` for them, so
it has to know its own location. Hard-coding the development checkout made the
installed package import from the source tree -- which means a wheel could pass
its tests while being missing files, because the checkout silently supplied
them.

`runtime_root()` derives the answer from this module's position instead:

    <root>/runtime/cli/paths.py   ->   <root>

In the checkout that is the repository. Installed, it is the package directory
under `site-packages`. Neither is written down anywhere.
"""

from __future__ import annotations

import os
from typing import List

#: Environment override, for an unusual layout or a test harness.
ENV_ROOT = "OPENMYCELIUM_ROOT"


def runtime_root() -> str:
    override = os.environ.get(ENV_ROOT)
    if override:
        return os.path.abspath(override)
    here = os.path.dirname(os.path.abspath(__file__))          # .../runtime/cli
    return os.path.dirname(os.path.dirname(here))              # ...


def runtime_dir(*parts: str) -> str:
    return os.path.join(runtime_root(), "runtime", *parts)


def worker_driver() -> str:
    return runtime_dir("serving", "pipeline_run.py")


def worker_pythonpath(existing: str = "") -> str:
    """What a spawned worker needs on its path.

    `mccl` is an installed distribution rather than part of this tree, so its
    checkout location is included only when it exists. A missing directory on
    `PYTHONPATH` is harmless; importing from the checkout after installation
    would not be.
    """
    parts: List[str] = [runtime_dir("serving"), runtime_dir("scheduler"),
                        runtime_dir("fabric"), runtime_dir("cli")]
    vendored = runtime_dir("mccl", "src")
    if os.path.isdir(vendored):
        parts.append(vendored)
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)
