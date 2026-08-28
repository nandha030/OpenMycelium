"""Replace real device identities with stable placeholders in UI fixtures.

Frozen release evidence under release/ is NOT touched. Those files are
provenance: rewriting them would invalidate the hashes that make them worth
keeping, and the identities there are needed to attribute a measurement to a
specific card.

What is rewritten is the fixture set the console UI is developed against, plus
any non-frozen documentation. A fixture needs the shape of an identity, not
somebody's actual serial number.

    nvidia:GPU-<uuid>  ->  nvidia:device-0
    amd:pci-<bdf>      ->  amd:device-0
    GPU-<uuid>         ->  device-0
    <bdf>              ->  0000:00:00.0

Idempotent: a second run changes nothing. The scanned-file count is reported
separately from the change count, so a run that finds the fixtures and has
nothing to do is distinguishable from one that found no fixtures at all.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: Derived from this file, never hard-coded: scripts/repro/ -> repository root.
#: An absolute path baked in here would leak whoever's machine wrote it and
#: break for every other clone location.
ROOT = Path(__file__).resolve().parents[2]
TARGETS = [ROOT / "docs" / "ui" / "contracts"]

NVIDIA_QUALIFIED = re.compile(r"nvidia:GPU-[0-9a-fA-F-]{8,}")
AMD_QUALIFIED = re.compile(r"amd:pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d")
BARE_UUID = re.compile(r"GPU-[0-9a-fA-F]{8}-[0-9a-fA-F-]{4,}")
LONG_BDF = re.compile(r"\b[0-9a-fA-F]{8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d\b")
SHORT_BDF = re.compile(r"\b0000:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d\b")

REPLACEMENTS = (
    (NVIDIA_QUALIFIED, "nvidia:device-0"),
    (AMD_QUALIFIED, "amd:device-0"),
    (BARE_UUID, "device-0"),
    (LONG_BDF, "00000000:00:00.0"),
    (SHORT_BDF, "0000:00:00.0"),
)


def sanitize(text: str) -> tuple[str, int]:
    changes = 0
    for pattern, replacement in REPLACEMENTS:
        text, count = pattern.subn(replacement, text)
        changes += count
    return text, changes


def main() -> int:
    scanned = 0
    total = 0
    missing = []
    for target in TARGETS:
        if not target.is_dir():
            missing.append(target)
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            scanned += 1
            cleaned, changes = sanitize(original)
            # Gate on the text actually differing, not on the substitution
            # count. Several placeholders match their own patterns -- the
            # replacement 0000:00:00.0 is itself a valid short BDF -- so a hit
            # count stays non-zero forever and the file is rewritten on every
            # run. Comparing the text is what makes this idempotent.
            if cleaned == original:
                continue
            if path.suffix == ".json":
                json.loads(cleaned)          # must still parse before writing
            path.write_text(cleaned, encoding="utf-8")
            print(f"  {changes:3d} replacement(s)  {path.relative_to(ROOT)}")
            total += changes

    print(f"  root     {ROOT}")
    print(f"  scanned  {scanned} file(s)")
    print(f"  changed  {total} identity value(s)")
    for target in missing:
        print(f"  MISSING  {target}")
    return 1 if missing or scanned == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
