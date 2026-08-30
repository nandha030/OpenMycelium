"""Build the `openmycelium` wheel from a staged copy of the runtime tree.

The repository is not laid out as a Python package: the runtime modules import
each other by bare name and live under `runtime/`. Rewriting them into a package
hierarchy is a larger change than this alpha needs, so the tree is staged into
`openmycelium/runtime/` and the installed launcher adds those directories to
`sys.path` itself -- from its own location, never from a checkout.

Staging is deliberate rather than building in place. It makes the wheel's
contents an explicit list, so a file that only ever worked because the checkout
supplied it shows up as missing here instead of at a customer's first run.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "0.3.0a10"
MCCL_PIN = "openmycelium-mccl==0.2.0a3"

#: Runtime subtrees the CLI needs. `mccl` is deliberately absent: it ships as
#: its own distribution and is depended on by version, not vendored.
INCLUDE = ("cli", "serving", "scheduler", "fabric", "safety")

#: Never shipped: caches, tests that import development-only fixtures, and the
#: checkout's own build outputs.
EXCLUDE_DIRS = ("__pycache__", ".pytest_cache", "tests", "native", "k8s")
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".json.partial")

PYPROJECT = f"""[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "openmycelium"
version = "{VERSION}"
description = "Run one model across an NVIDIA CUDA and an AMD ROCm GPU"
readme = "README.md"
requires-python = ">=3.10"
license = {{text = "Apache-2.0"}}
authors = [{{name = "OpenMycelium contributors"}}]
dependencies = ["{MCCL_PIN}"]

[project.scripts]
openmycelium = "openmycelium.launcher:main"

[tool.setuptools]
packages = ["openmycelium"]

[tool.setuptools.package-data]
openmycelium = ["runtime/**/*.py", "runtime/**/*.json", "runtime/**/*.md",
                "runtime/**/*.html", "runtime/**/*.css", "runtime/**/*.js",
                "runtime/**/*.png"]
"""

README = """# openmycelium

Runs a single model across an NVIDIA (CUDA) and an AMD (ROCm) GPU, so a model
too large for either card can be served by both.

    openmycelium doctor
    openmycelium model import /path/to/model
    openmycelium serve --model my-model --port 11500

Decoding is deterministic greedy only in this build: sampling is not yet
validated across the vendor boundary, and unsupported sampling parameters are
refused rather than ignored.
"""


#: Every file that declares the version. They must agree, and this is checked
#: before a wheel is produced rather than noticed afterwards.
VERSION_SOURCES = (
    os.path.join("packaging", "launcher.py"),
    os.path.join("runtime", "cli", "lifecycle.py"),
)


def check_versions() -> None:
    """Refuse to build when the version constants disagree.

    They are bumped by hand in three places, and a `git add` scoped to the wrong
    directories once committed `launcher.py` at 0.3.0a7 with `lifecycle.py` still
    at 0.3.0a5 -- a wheel whose `openmycelium version` would have reported an
    artifact that does not exist. Cheap to check here, and the only place that
    sees all three at once.
    """
    found = {"scripts/build_openmycelium_wheel.py": VERSION}
    for relative in VERSION_SOURCES:
        path = os.path.join(REPO, relative)
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VERSION = "):
                    found[relative] = line.split("=", 1)[1].strip().strip('"')
                    break
            else:
                raise SystemExit(f"  {relative} declares no VERSION")

    if len(set(found.values())) != 1:
        listing = "\n".join(f"    {name:<42} {value}"
                            for name, value in sorted(found.items()))
        raise SystemExit(
            f"  version constants disagree; refusing to build:\n{listing}")


def stage(destination: str, verbose: bool = True) -> List[str]:
    package = os.path.join(destination, "openmycelium")
    os.makedirs(package, exist_ok=True)
    shipped: List[str] = []

    shutil.copy2(os.path.join(REPO, "packaging", "launcher.py"),
                 os.path.join(package, "launcher.py"))
    shipped.append("openmycelium/launcher.py")
    with open(os.path.join(package, "__init__.py"), "w", encoding="utf-8") as h:
        h.write(f'"""OpenMycelium: one model across two GPU vendors."""\n\n'
                f'__version__ = "{VERSION}"\n')
    shipped.append("openmycelium/__init__.py")

    for part in INCLUDE:
        source = os.path.join(REPO, "runtime", part)
        target = os.path.join(package, "runtime", part)
        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in sorted(files):
                if name.endswith(EXCLUDE_SUFFIX):
                    continue
                relative = os.path.relpath(os.path.join(root, name), source)
                final = os.path.join(target, relative)
                os.makedirs(os.path.dirname(final), exist_ok=True)
                shutil.copy2(os.path.join(root, name), final)
                shipped.append(f"openmycelium/runtime/{part}/{relative}")

    with open(os.path.join(destination, "pyproject.toml"), "w",
              encoding="utf-8") as handle:
        handle.write(PYPROJECT)
    with open(os.path.join(destination, "README.md"), "w",
              encoding="utf-8") as handle:
        handle.write(README)
    if verbose:
        print(f"  staged {len(shipped)} files")
    return shipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the openmycelium wheel")
    parser.add_argument("--out", default=os.path.join(REPO, "dist"))
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    check_versions()

    staging = tempfile.mkdtemp(prefix="om-wheel-")
    shipped = stage(staging)
    if args.list_only:
        for name in shipped:
            print(f"    {name}")
        return 0

    # Existing release artifacts are never touched: the wheel is built into a
    # temporary directory and only the new file is copied across.
    os.makedirs(args.out, exist_ok=True)
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir",
         os.path.join(staging, "dist"), staging],
        capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stdout[-2000:])
        print(build.stderr[-2000:])
        return 1

    produced = [f for f in os.listdir(os.path.join(staging, "dist"))
                if f.endswith(".whl")]
    if not produced:
        print("  no wheel was produced")
        return 1
    wheel = produced[0]
    source = os.path.join(staging, "dist", wheel)
    target = os.path.join(args.out, wheel)
    if os.path.exists(target):
        print(f"  refusing to overwrite the existing artifact {target}")
        return 3
    shutil.copy2(source, target)

    with open(target, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    print(f"  built {wheel}")
    print(f"  {os.path.getsize(target)} bytes")
    print(f"  sha256 {digest}")
    print(f"  {target}")

    # Appended, never rewritten: earlier artifacts keep their recorded hashes.
    sums = os.path.join(args.out, "SHA256SUMS")
    existing = ""
    if os.path.exists(sums):
        with open(sums, "r", encoding="utf-8") as handle:
            existing = handle.read()
    if wheel not in existing:
        with open(sums, "a", encoding="utf-8") as handle:
            handle.write(f"{digest} *{wheel}\n")
        print(f"  appended to {sums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
