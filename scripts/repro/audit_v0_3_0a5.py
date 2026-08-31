"""Read-only audit of v0.3.0a5: does its tagged source reproduce its artifact?

`0.3.0a10` was non-releasable because it was built from a working tree mixing
CRLF and LF, so its installed-content digest named a tree no checkout produces.
`0.3.0a5` was built on the same machine before that was understood and may share
the condition. Gate B did verify a rebuild reproduced `e2eccbbe…`, but that
rebuild ran on the same tree in the same session, so it cannot settle it.

This settles it without touching anything:

  - no rebuild. The preserved wheel is read as an archive; nothing is compiled,
    staged or installed.
  - no GPU work. Nothing here runs the model.
  - no tag modification. The tag is read, never written.

Three things are compared:

  1. the preserved wheel's bytes, against the canonical wheel SHA-256 recorded
     in the release evidence
  2. the content digest computed from that wheel, against the recorded
     `installedContentSha256`
  3. the `.py` bytes inside that wheel, against a clean checkout of the tagged
     commit -- under `core.autocrlf` both false and true, because which of the
     two reproduces the artifact is the whole question

A mismatch in (3) is a source-reproduction caveat, not a defect in the artifact.
The wheel is what was qualified on hardware, and that evidence stands whatever
this finds.

Usage:  audit_v0_3_0a5.py [workdir]
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
WORK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/om-a5-audit"

TAG = "v0.3.0a5"
PRESERVED = os.environ.get(
    "OM_PRESERVED",
    "/mnt/c/Users/User/Documents/OpenMycelium_artifacts/v0.3.0a5")
WHEEL = os.path.join(PRESERVED, "openmycelium-0.3.0a5-py3-none-any.whl")

#: As recorded in docs/NONRELEASABLE_BUILDS.md and the v0.3.0a5 tag message.
RECORDED_WHEEL = "3eeaed3e229226f4d1408826fc42934057878fa48a91fc7fbb89900a88a04871"
RECORDED_CONTENT = "e2eccbbe6de9aa8fdc342bbed015cede325a84161269dfa719c6f3f97c0011bf"

#: How the build staged the tree, so a wheel entry can be mapped back to the
#: source file it came from.
INCLUDE = ("cli", "serving", "scheduler", "fabric", "safety")

findings: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    line = f"{'ok  ' if ok else '!!  '}  {label}" + (f" -- {detail}" if detail else "")
    (notes if ok else findings).append(line)
    print(f"  {line}")
    return ok


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def wheel_python(path: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        return {n: archive.read(n)
                for n in archive.namelist() if n.endswith(".py")}


def content_digest(files: dict) -> str:
    running = hashlib.sha256()
    for name in sorted(files):
        running.update(name.encode("utf-8"))
        running.update(files[name])
    return running.hexdigest()


def source_python(root: str) -> dict:
    """The shipped .py of a checkout, keyed by the wheel entry name it becomes."""
    mapped = {}
    for part in INCLUDE:
        base = os.path.join(root, "runtime", part)
        for directory, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".pytest_cache", "tests",
                                    "native", "k8s")]
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(directory, name)
                relative = os.path.relpath(path, base).replace("\\", "/")
                with open(path, "rb") as handle:
                    mapped[f"openmycelium/runtime/{part}/{relative}"] = handle.read()
    launcher = os.path.join(root, "packaging", "launcher.py")
    if os.path.isfile(launcher):
        with open(launcher, "rb") as handle:
            mapped["openmycelium/launcher.py"] = handle.read()
    return mapped


def main() -> int:
    print(f"\nRead-only audit of {TAG}\n")
    print("  no rebuild, no GPU work, no tag modification\n")

    if not os.path.isfile(WHEEL):
        print(f"  the preserved wheel is missing: {WHEEL}")
        return 2

    # 1. The preserved bytes are the bytes the record names.
    with open(WHEEL, "rb") as handle:
        raw = handle.read()
    actual_wheel = hashlib.sha256(raw).hexdigest()
    check("the preserved wheel matches the recorded canonical SHA-256",
          actual_wheel == RECORDED_WHEEL, actual_wheel)

    # 2. The content digest the record names.
    wheel_files = wheel_python(WHEEL)
    actual_content = content_digest(wheel_files)
    check("the wheel reproduces the recorded installedContentSha256",
          actual_content == RECORDED_CONTENT,
          f"{actual_content} ({len(wheel_files)} .py)")

    carriage = sorted(n for n, b in wheel_files.items() if b"\r\n" in b)
    check("no .py inside the artifact carries CRLF", not carriage,
          f"{len(carriage)} of {len(wheel_files)} do, e.g. "
          + ", ".join(os.path.basename(n) for n in carriage[:3]))

    # 3. Against a clean checkout of the tagged commit, both ways.
    commit = run(["git", "rev-list", "-n1", TAG], cwd=REPO).stdout.strip()
    print(f"\n  tagged commit {commit}\n")
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    outcome = {}
    for setting in ("false", "true"):
        destination = os.path.join(WORK, f"autocrlf-{setting}")
        run(["git", "-c", f"core.autocrlf={setting}", "clone", "--quiet",
             "--no-local", "--branch", TAG, "--single-branch", REPO, destination])
        head = run(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip()
        if head != commit:
            check(f"[autocrlf={setting}] clean checkout of {TAG}", False,
                  f"on {head[:12]}")
            continue

        source = source_python(destination)
        shared = set(source) & set(wheel_files)
        differing = sorted(n for n in shared if source[n] != wheel_files[n])
        endings_only = [n for n in differing
                        if source[n].replace(b"\r\n", b"\n")
                        == wheel_files[n].replace(b"\r\n", b"\n")]
        substantive = [n for n in differing if n not in endings_only]
        outcome[setting] = (len(shared), differing, endings_only, substantive)

        check(f"[autocrlf={setting}] the checkout reproduces the artifact's .py",
              not differing,
              f"{len(shared)} shared; {len(differing)} differ "
              f"({len(endings_only)} line endings only, "
              f"{len(substantive)} substantive)")

    print()
    if outcome.get("false") and outcome.get("true"):
        _, diff_false, eol_false, sub_false = outcome["false"]
        _, diff_true, eol_true, sub_true = outcome["true"]
        check("no substantive difference under either setting",
              not sub_false and not sub_true,
              f"false {len(sub_false)}, true {len(sub_true)}")
        which = ("LF" if not diff_false else
                 "CRLF" if not diff_true else "neither")
        print(f"  the artifact's .py match a checkout written as: {which}")

    print()
    if findings:
        print(f"  == audit found {len(findings)} discrepancy/ies ==")
        return 1
    print(f"  == audit clean: {len(notes)} checks ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
