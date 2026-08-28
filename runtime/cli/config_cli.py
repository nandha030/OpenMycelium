"""`openmycelium config` -- what every path resolved to, and why.

A surprising path should be traceable rather than guessed at, so each setting
prints the source that produced it: a flag, an environment variable, the
configuration file, auto-discovery, or the documented default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from config import (ConfigError, add_arguments, config_paths,  # noqa: E402
                    from_args, require_runtimes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show resolved configuration")
    add_arguments(parser)
    parser.add_argument("--check", action="store_true",
                        help="also verify both GPU runtimes actually work")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    config = from_args(args)

    if args.json:
        print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        return 0

    print()
    print("  resolved configuration")
    for line in config.describe():
        print(line)
    print()
    print("  configuration files are looked for in this order:")
    for path in config_paths():
        if not path:
            continue
        mark = "  <- in use" if path == config.config_file else ""
        exists = "exists" if os.path.isfile(path) else "absent"
        print(f"    {path:<52} {exists}{mark}")
    print()
    print("  precedence: command line > environment > file > discovery > default")

    if args.check:
        print()
        try:
            for name, detail in require_runtimes(config).items():
                print(f"  [OK] {name}: {detail}")
        except ConfigError as error:
            print(f"  {error}")
            return 1
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
