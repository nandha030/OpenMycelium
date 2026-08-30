"""`openmycelium ps`, `stop`, `version` -- finding and ending running runtimes.

`ps` reads the control records a coordinator publishes and reports only what it
can verify: a record whose process is gone is shown as stale rather than as a
running model. `stop` acts on the same records, and refuses to signal anything
whose PID, process start time and runId do not all still agree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from control import (CONTROL_DIR, list_records,  # noqa: E402
                     remove_if_matches, request_stop)

VERSION = "0.3.0a11"


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def cmd_ps(args) -> int:
    records = list_records(args.control_dir)
    live = [r for r in records if r["_alive"]]
    stale = [r for r in records if not r["_alive"]]

    if args.json:
        print(json.dumps({"running": live, "stale": stale}, indent=2))
        return 0

    print()
    if not records:
        print("  no OpenMycelium runtimes have published a control record")
        print()
        return 0
    if live:
        print(f"  {'RUN':<10}{'MODEL':<30}{'STATE':<12}{'PID':>8}"
              f"{'PORT':>7}{'UP':>7}  BOUNDARY")
        for record in live:
            print(f"  {record['run_id'][:8]:<10}"
                  f"{record.get('model_name', '?')[:29]:<30}"
                  f"{record.get('state', '?'):<12}{record.get('pid', 0):>8}"
                  f"{record.get('port', 0):>7}{_age(record['_ageSeconds']):>7}  "
                  f"{record.get('placement_id', '?')[:16]}")
    else:
        print("  nothing running")
    if stale:
        print()
        print(f"  {len(stale)} stale record(s) -- the process is gone:")
        for record in stale:
            print(f"    {record['run_id'][:8]}  {record.get('model_name', '?')}"
                  f"  pid {record.get('pid')} (not running)")
        print("    clear them with:  openmycelium stop --prune")
    print()
    return 0


def cmd_stop(args) -> int:
    records = list_records(args.control_dir)
    if args.prune:
        removed = 0
        for record in records:
            if not record["_alive"] and remove_if_matches(record["_path"],
                                                          record["run_id"]):
                removed += 1
        print(f"  removed {removed} stale record(s)")
        return 0

    live = [r for r in records if r["_alive"]]
    if args.run_id:
        live = [r for r in live if r["run_id"].startswith(args.run_id)]
        if not live:
            print(f"  no running runtime matches {args.run_id!r}")
            return 1
    if not live:
        print("  nothing running")
        stale = [r for r in records if not r["_alive"]]
        if stale:
            print(f"  ({len(stale)} stale record(s); "
                  f"clear with: openmycelium stop --prune)")
        return 0
    if len(live) > 1 and not args.all and not args.run_id:
        print(f"  {len(live)} runtimes are running; name one or pass --all")
        for record in live:
            print(f"    {record['run_id'][:8]}  "
                  f"{record.get('model_name', '?')}  pid {record.get('pid')}")
        return 2

    failures = 0
    for record in live:
        print(f"  stopping {record['run_id'][:8]} "
              f"({record.get('model_name', '?')}, pid {record.get('pid')}) ...")
        result = request_stop(record, timeout=args.timeout)
        outcome = result.get("outcome")
        detail = result.get("detail", "")
        print(f"    {outcome}" + (f": {detail}" if detail else ""))
        if outcome in ("stopped", "killed", "stale"):
            # Removed only if the record still names this run: a newer attempt
            # may have rewritten it between the read and the delete.
            remove_if_matches(record["_path"], record["run_id"])
        else:
            failures += 1
    return 1 if failures else 0


def cmd_version(args) -> int:
    from provenance import collect, lines  # noqa: PLC0415

    record = collect(deep=args.verbose)
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    print()
    if args.verbose:
        for line in lines(record):
            print(line)
    else:
        print(f"  openmycelium            {record.get('openmycelium')}")
        print(f"  mccl                    {record.get('mcclVersion', '?')}")
        print(f"  transport               {record.get('transport')}")
        print(f"  event schema            v{record.get('eventSchemaVersion')}")
        print()
        print("  full detail:  openmycelium version --verbose")
    print()
    print("  decoding is deterministic greedy only in this build")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime lifecycle")
    parser.add_argument("command", nargs="?", default="ps",
                        choices=("ps", "stop", "version"))
    parser.add_argument("run_id", nargs="?", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--prune", action="store_true",
                        help="remove records whose process is gone")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--control-dir", default=CONTROL_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    return {"ps": cmd_ps, "stop": cmd_stop, "version": cmd_version}[
        args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
