"""Report the identity of what is actually installed, not what a label claims.

Identity is asserted on installed content. Three rebuilds of one committed
source produce three wheel byte streams at identical size -- ZIP timestamps and
packaging metadata differ -- and install to one content digest. So the wheel
hash identifies a file and the content digest identifies the product; release
evidence needs both, and must never present two wheel byte streams under one
version.

MCCL is digested for the same reason and with more force: it owns the wire
protocol and the transport, so a change there moves the boundary bytes every
byte-exactness claim rests on. A version label alone cannot distinguish two
builds of it.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import distribution


def digest(name: str) -> dict:
    dist = distribution(name)
    files = sorted(str(f) for f in (dist.files or []) if str(f).endswith(".py"))
    running = hashlib.sha256()
    counted = 0
    for entry in files:
        try:
            with open(dist.locate_file(entry), "rb") as handle:
                running.update(entry.encode("utf-8"))
                running.update(handle.read())
            counted += 1
        except OSError:
            continue
    return {"version": dist.version, "installedContentSha256": running.hexdigest(),
            "pythonFiles": counted}


if __name__ == "__main__":
    print(json.dumps({name: digest(name) for name in
                      ("openmycelium", "openmycelium-mccl")}, indent=2))
