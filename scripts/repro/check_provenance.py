"""Assert that the freshly installed package is byte-for-byte the frozen build.

Kept in its own file rather than a heredoc so the bootstrap script stays
parseable and the assertion is reviewable on its own.
"""

import json
import sys

FROZEN_CONTENT = "e5f06b44439925943fe1c5c36bd953102213332d8559a190d32421f8ab6e2210"
FROZEN_VERSION = "0.1.0a7"


def main(path: str) -> int:
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    content = report.get("installedContentSha256")
    version = report.get("openmycelium")
    print(f"  version       {version}")
    print(f"  content       {content}")
    print(f"  frozen match  {content == FROZEN_CONTENT and version == FROZEN_VERSION}")
    if content != FROZEN_CONTENT:
        print(f"  expected      {FROZEN_CONTENT}")
        return 1
    return 0 if version == FROZEN_VERSION else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
