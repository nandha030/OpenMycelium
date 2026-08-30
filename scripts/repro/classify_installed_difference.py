"""Classify every difference between the installed wheel and the checkout.

`verify_installed_matches_checkout.py` answers yes or no. When the answer is no,
the next question is which kind of no: a line-ending difference means the
artifact is the same program built from a differently-encoded tree, and a
content difference means the artifact is not this source at all. They call for
completely different responses, and a summary that does not separate them
invites treating the second as if it were the first.
"""

from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SITE = (sys.argv[1] if len(sys.argv) > 1
        else "/opt/om/venv/lib/python3.12/site-packages/openmycelium")
INCLUDE = ("cli", "serving", "scheduler", "fabric", "safety")

identical, endings_only, content = [], [], []

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
                content.append(f"{part}/{relative} (absent from the checkout)")
                continue
            with open(os.path.join(root, name), "rb") as handle:
                installed = handle.read()
            with open(source, "rb") as handle:
                checked_out = handle.read()
            label = f"{part}/{relative}"
            if installed == checked_out:
                identical.append(label)
            elif installed.replace(b"\r\n", b"\n") == checked_out.replace(b"\r\n", b"\n"):
                endings_only.append(label)
            else:
                content.append(label)

print(f"\n  installed: {SITE}")
print(f"  checkout:  {REPO}\n")
print(f"  identical                    {len(identical)}")
print(f"  differ in line endings only  {len(endings_only)}")
print(f"  differ in content            {len(content)}")

for label, entries in (("line endings only", endings_only),
                       ("CONTENT", content)):
    if entries:
        print(f"\n  {label}:")
        for entry in entries:
            print(f"    {entry}")

print()
if content:
    print("  == the installed wheel is NOT built from this source ==")
    sys.exit(1)
if endings_only:
    print("  == same program, different line endings ==")
    print("  The wheel was built from a working tree whose line endings differ")
    print("  from what is committed. Rebuilding from the commit produces a")
    print("  different installed-content digest, so the measured digest does not")
    print("  identify the committed source and must not be sealed as if it did.")
    sys.exit(2)
print("  == the installed wheel is this checkout ==")
