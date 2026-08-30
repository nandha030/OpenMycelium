"""Rebuild the wheel and prove the installed content digest is reproducible.

The wheel file itself is not reproducible and is not expected to be: three
rebuilds of one source give three byte streams at identical size, differing in
ZIP timestamps and packaging metadata. The *content* must be, because that is
what identity is asserted on.

Computes the same digest `provenance` computes -- sorted `.py` entry name plus
bytes -- from the wheel archive rather than from an installation, so it can
compare two builds without installing either.

Usage:  verify_rebuild_reproducible.py <wheel-a> <wheel-b>
"""

from __future__ import annotations

import hashlib
import sys
import zipfile


def content_digest(wheel: str) -> tuple[str, int]:
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith(".py"))
        running = hashlib.sha256()
        for name in names:
            running.update(name.encode("utf-8"))
            running.update(archive.read(name))
    return running.hexdigest(), len(names)


def python_files(wheel: str) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        return {n: archive.read(n) for n in archive.namelist() if n.endswith(".py")}


first, second = sys.argv[1], sys.argv[2]
digest_a, count_a = content_digest(first)
digest_b, count_b = content_digest(second)

print(f"\n  {first}\n    {count_a} python files, content {digest_a}")
print(f"  {second}\n    {count_b} python files, content {digest_b}\n")

files_a, files_b = python_files(first), python_files(second)
only_a = sorted(set(files_a) - set(files_b))
only_b = sorted(set(files_b) - set(files_a))
differ = sorted(n for n in set(files_a) & set(files_b) if files_a[n] != files_b[n])

for label, entries in (("only in the first", only_a),
                       ("only in the second", only_b),
                       ("differing", differ)):
    if entries:
        print(f"  {len(entries)} {label}:")
        for entry in entries[:10]:
            print(f"    {entry}")

if digest_a == digest_b and not (only_a or only_b or differ):
    print("  == the installed content is reproducible ==")
    print("  The wheel files may still differ byte for byte; ZIP timestamps and")
    print("  packaging metadata are not product content.")
    sys.exit(0)
print("  == the installed content is NOT reproducible ==")
sys.exit(1)
