"""Prove the installed wheel is this checkout, file by file.

Cheaper than a rebuild and answers the question a rebuild is usually asked to
answer: is the artifact that was measured the artifact this commit describes? A
checkout file edited between build and run would otherwise produce the same
passing output for the wrong reason.

Compares content, not the wheel: identity is asserted on installed content.
"""

from __future__ import annotations

import hashlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = (sys.argv[1] if len(sys.argv) > 1
        else "/opt/om/venv/lib/python3.12/site-packages/openmycelium")

#: Mirrors INCLUDE in scripts/build_openmycelium_wheel.py.
INCLUDE = ("cli", "serving", "scheduler", "fabric", "safety")


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


differ, missing, extra, same = [], [], [], 0

for part in INCLUDE:
    installed_root = os.path.join(SITE, "runtime", part)
    checkout_root = os.path.join(REPO, "runtime", part)
    for root, dirs, files in os.walk(installed_root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(root, name), installed_root)
            source = os.path.join(checkout_root, relative)
            if not os.path.isfile(source):
                extra.append(f"{part}/{relative}")
                continue
            if sha256(os.path.join(root, name)) != sha256(source):
                differ.append(f"{part}/{relative}")
            else:
                same += 1
    for root, dirs, files in os.walk(checkout_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests", "native")]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(root, name), checkout_root)
            if not os.path.isfile(os.path.join(installed_root, relative)):
                missing.append(f"{part}/{relative}")

print(f"\n  installed: {SITE}")
print(f"  checkout:  {REPO}\n")
print(f"  ok    {same} shipped module(s) identical")
for label, entries in (("differ from the checkout", differ),
                       ("in the checkout but not shipped", missing),
                       ("shipped but not in the checkout", extra)):
    if entries:
        print(f"  FAIL  {len(entries)} {label}")
        for entry in entries[:20]:
            print(f"          {entry}")

print()
if differ or extra:
    print("  == the installed wheel is NOT this checkout ==")
    sys.exit(1)
if missing:
    # Not a failure by itself: the build excludes tests directories and native
    # sources deliberately. Listed so the exclusion stays visible.
    print(f"  note: {len(missing)} checkout module(s) are deliberately not shipped")
print("  == the installed wheel is this checkout ==")
