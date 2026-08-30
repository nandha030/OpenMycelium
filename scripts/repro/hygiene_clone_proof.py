"""Prove a fresh clone is identical, clean and buildable under either autocrlf.

`0.3.0a10` was built from a tree mixing CRLF and LF because `.gitattributes`
declared no rule for `.py`, so the checked-out bytes followed the cloner's
`core.autocrlf`. Every file was byte-for-byte the same program and the artifact
still could not be reproduced from its commit.

A narrow `*.py` rule fixed the digest and left a stock Windows clone dirty on
arrival and therefore unbuildable under the dirty-tree guard. This checks the
whole policy instead: clone the same ref twice, once with `core.autocrlf=true`
and once unset, and require both clones to be clean, identical where it matters,
executable on Linux, still CRLF where Windows needs it, buildable, green, and
byte-for-byte unchanged in frozen evidence.

Written in Python rather than shell because every check here is a comparison of
exact bytes, and shell quoting is how those get corrupted.

Usage:  hygiene_clone_proof.py <ref> [workdir]
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
REF = sys.argv[1] if len(sys.argv) > 1 else "chore/py-eol-lf"
WORK = sys.argv[2] if len(sys.argv) > 2 else "/tmp/om-hygiene-proof"
PYTEST_PY = os.environ.get("PYTEST_PY", "/opt/hetenv/bin/python3")

#: Subtrees the wheel ships, mirroring INCLUDE in the build script.
SHIPPED = ("runtime/cli", "runtime/serving", "runtime/scheduler",
           "runtime/fabric", "runtime/safety")

failures: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    line = f"{'ok  ' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else "")
    (notes if ok else failures).append(line)
    print(f"    {line}")
    return ok


def run(args, cwd=None, env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          env=merged)


def tree_digest(root: str, relative_dirs, suffix=".py") -> tuple[str, int]:
    """Digest of every matching file: sorted relative path, then bytes."""
    entries = []
    for relative in relative_dirs:
        base = os.path.join(root, relative)
        for directory, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for name in sorted(files):
                if suffix and not name.endswith(suffix):
                    continue
                path = os.path.join(directory, name)
                entries.append((os.path.relpath(path, root).replace("\\", "/"),
                                path))
    running = hashlib.sha256()
    for name, path in sorted(entries):
        running.update(name.encode("utf-8"))
        with open(path, "rb") as handle:
            running.update(handle.read())
    return running.hexdigest(), len(entries)


def clone(setting: str) -> str:
    destination = os.path.join(WORK, f"autocrlf-{setting}")
    # --branch clones the ref directly, so no ref switch is needed. Without it
    # the clone lands on the source repo's current branch and the switch aborts
    # on a dirty autocrlf=true tree -- which once left a proof measuring the very
    # branch it was meant to contrast with.
    run(["git", "-c", f"core.autocrlf={setting}", "clone", "--quiet",
         "--no-local", "--branch", REF, "--single-branch", REPO, destination])
    return destination


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    want = run(["git", "rev-parse", REF], cwd=REPO).stdout.strip()
    print(f"\nHygiene clone proof -- ref {REF} ({want[:12]})\n")

    results = {}
    for setting in ("true", "false"):
        print(f"  core.autocrlf={setting}")
        clone_path = clone(setting)

        head = run(["git", "rev-parse", "HEAD"], cwd=clone_path).stdout.strip()
        if not check(f"[{setting}] the clone is on {REF}", head == want,
                     f"on {head[:12]}, wanted {want[:12]}"):
            continue

        # 1. Clean status. The whole point: a clone is not dirty on arrival.
        status = run(["git", "status", "--porcelain", "--untracked-files=no"],
                     cwd=clone_path).stdout.strip()
        dirty = [line for line in status.splitlines() if line.strip()]
        check(f"[{setting}] git status is clean", not dirty,
              f"{len(dirty)} dirty path(s): " + ", ".join(d[3:] for d in dirty[:4]))

        # 2. Shipped Python content digest.
        digest, count = tree_digest(clone_path, SHIPPED)
        results[setting] = digest
        check(f"[{setting}] shipped python digested", count > 0,
              f"{count} files, {digest[:16]}...")

        # 3. Linux scripts execute directly -- a CR in a shebang makes the
        #    kernel look for an interpreter whose name ends in \r.
        bad_shebang, total_sh = [], 0
        for directory, dirs, files in os.walk(clone_path):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                if not name.endswith(".sh"):
                    continue
                total_sh += 1
                with open(os.path.join(directory, name), "rb") as handle:
                    first = handle.readline()
                if first.rstrip(b"\n").endswith(b"\r"):
                    bad_shebang.append(name)
        check(f"[{setting}] no .sh has a CR in its shebang", not bad_shebang,
              f"{total_sh} scripts; bad: {bad_shebang[:3]}")

        probe = os.path.join(clone_path, "scripts", "repro", "check_orphans.sh")
        if os.path.isfile(probe):
            os.chmod(probe, 0o755)
            executed = run([probe])
            check(f"[{setting}] a .sh executes directly via its shebang",
                  executed.returncode == 0,
                  (executed.stdout + executed.stderr).strip()[:60])

        # 4. Windows launchers keep CRLF.
        for launcher in ("openmycelium.cmd", "install.cmd"):
            path = os.path.join(clone_path, launcher)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    body = handle.read()
                check(f"[{setting}] {launcher} retains CRLF",
                      b"\r\n" in body, f"{body.count(bytes([13, 10]))} CRLF")

        # 5. The dirty-tree build guard permits this clone, and it builds.
        out = os.path.join(clone_path, "dist")
        built = run([PYTEST_PY, "scripts/build_openmycelium_wheel.py",
                     "--out", out], cwd=clone_path)
        refused = "refusing to build" in (built.stdout + built.stderr)
        check(f"[{setting}] the dirty-tree guard permits the clone", not refused,
              "guard refused" if refused else "permitted")
        wheels = [f for f in os.listdir(out)] if os.path.isdir(out) else []
        wheel = [w for w in wheels if w.endswith(".whl")]
        check(f"[{setting}] the clone builds a wheel", bool(wheel),
              wheel[0] if wheel else (built.stdout + built.stderr)[-120:])

        # 6. Tests.
        suite = run(["bash", "scripts/repro/run_unit_suite.sh"], cwd=clone_path,
                    env={"PYTEST_PY": PYTEST_PY})
        text = suite.stdout + suite.stderr
        passed = "unit suite PASSED" in text
        total = ""
        for line in text.splitlines():
            if "unit suite" in line:
                total = line.strip()
        check(f"[{setting}] the unit suite passes", passed, total)

        # 7. Frozen evidence, byte for byte.
        frozen, frozen_count = tree_digest(clone_path, ("release",), suffix="")
        results[f"frozen-{setting}"] = frozen
        check(f"[{setting}] frozen evidence digested", frozen_count > 0,
              f"{frozen_count} files, {frozen[:16]}...")
        print()

    # Cross-clone comparisons.
    print("  across the two clones")
    check("the shipped python content is identical",
          results.get("true") and results.get("true") == results.get("false"),
          results.get("true", "")[:32])

    canonical, _ = tree_digest(REPO, ("release",), suffix="")
    check("frozen evidence is unchanged in both clones",
          results.get("frozen-true") == results.get("frozen-false")
          == canonical,
          f"{canonical[:32]}...")

    print()
    if failures:
        print(f"  == hygiene clone proof FAILED: {len(failures)} check(s) ==")
        return 1
    print(f"  == hygiene clone proof PASSED: {len(notes)} checks ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
