"""Compare environment fingerprints taken before and after a provision rerun.

Takes pairs of files: before, after, before, after. Any difference in the
package list, the file count, the dist-info set or the newest modification time
means the rerun touched the environment.
"""

from __future__ import annotations

import json
import sys


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def compare(before: dict, after: dict) -> list[str]:
    label = before["label"].rsplit("-", 1)[0]
    differences: list[str] = []
    for field in ("packageListSha256", "packageCount", "fileCount",
                  "distInfoCount", "newestMtime"):
        if before[field] != after[field]:
            differences.append(f"{label}: {field} changed "
                               f"{before[field]} -> {after[field]}")
    added = set(after["packages"]) - set(before["packages"])
    removed = set(before["packages"]) - set(after["packages"])
    for item in sorted(added):
        differences.append(f"{label}: package added {item}")
    for item in sorted(removed):
        differences.append(f"{label}: package removed {item}")
    if not differences:
        print(f"    {label:<5} unchanged: {before['packageCount']} packages, "
              f"{before['fileCount']} files, "
              f"list sha {before['packageListSha256'][:16]}, "
              f"newest mtime {before['newestMtime']}")
    return differences


def main(paths: list[str]) -> int:
    problems: list[str] = []
    for index in range(0, len(paths), 2):
        problems += compare(load(paths[index]), load(paths[index + 1]))
    if problems:
        print()
        for item in problems:
            print(f"    CHANGED: {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
