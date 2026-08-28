"""Fingerprint a provisioned environment, so a rerun can be shown to change it.

The fingerprint is deliberately sensitive to reinstallation, not just to version
drift: a `pip install` that reinstalls an identical version still rewrites the
files, so the newest modification time and the set of dist-info directories move
even when `pip freeze` does not. Comparing only versions would let a silent
reinstall pass as idempotent.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys


def main(env: str, label: str) -> int:
    python = os.path.join(env, "bin", "python")
    freeze = subprocess.run([python, "-m", "pip", "freeze", "--all"],
                            capture_output=True, text=True).stdout
    packages = sorted(line.strip() for line in freeze.splitlines() if line.strip())

    site = ""
    for root, dirs, _ in os.walk(os.path.join(env, "lib")):
        if os.path.basename(root) == "site-packages":
            site = root
            dirs[:] = []
            break

    newest, count, dist_infos = 0.0, 0, []
    if site:
        for entry in os.listdir(site):
            if entry.endswith(".dist-info"):
                dist_infos.append(entry)
        for root, _, files in os.walk(site):
            for name in files:
                path = os.path.join(root, name)
                try:
                    newest = max(newest, os.path.getmtime(path))
                    count += 1
                except OSError:
                    pass

    digest = hashlib.sha256("\n".join(packages).encode()).hexdigest()
    report = {
        "label": label,
        "environment": env,
        "packageCount": len(packages),
        "packages": packages,
        "packageListSha256": digest,
        "sitePackages": site,
        "fileCount": count,
        "newestMtime": round(newest, 3),
        "distInfoCount": len(dist_infos),
        "distInfo": sorted(dist_infos),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
