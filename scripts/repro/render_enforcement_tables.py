"""Render the enforcement tables from the canonical data.

The tables in `docs/SAFETY_ENFORCEMENT.md` are generated from
`runtime/safety/enforcement.py`, not typed alongside it. A table maintained in
two places drifts, and the drift stays invisible until someone reads both
carefully at the same moment.

`test_enforcement_contract.py` asserts both directions, so a stale paste is a
test failure rather than a discrepancy nobody notices.

    render_enforcement_tables.py            print every table
    render_enforcement_tables.py --check    exit 1 if the document disagrees
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "runtime", "safety"))

from enforcement import (ACTION_REQUIRES, AUTHORITY_ORDER,  # noqa: E402
                         CANARY_SEQUENCE, FAULTS, ROLLBACK, Action)

DOC = os.path.join(_REPO, "docs", "SAFETY_ENFORCEMENT.md")
TICK = chr(96)


def code(value: str) -> str:
    return f"{TICK}{value}{TICK}"


def render_authority() -> str:
    lines = ["| Rung | Authority | Actions it adds |", "|---|---|---|"]
    for index, authority in enumerate(AUTHORITY_ORDER):
        adds = [a.value for a in Action
                if ACTION_REQUIRES[a] is authority and a is not Action.NONE]
        lines.append(f"| {index} | {code(authority.value)} "
                     f"| {', '.join(code(a) for a in adds) if adds else '—'} |")
    return "\n".join(lines)


def render_faults() -> str:
    lines = [
        "| Fault | Detected by | Confidence | Action | Trigger | Rollback to |",
        "|---|---|---|---|---|---|",
    ]
    for entry in FAULTS:
        lines.append(
            f"| {code(entry.name)} "
            f"| {entry.detected_by} "
            f"| {code(entry.requires_confidence.value)} "
            f"| {code(entry.permitted_action.value)} "
            f"| {code(entry.contract_trigger) if entry.contract_trigger else '—'} "
            f"| {entry.rollback} |")
    return "\n".join(lines)


def render_canaries() -> str:
    lines = ["| # | Authority | Accept when | Reject when |", "|---|---|---|---|"]
    for entry in CANARY_SEQUENCE:
        lines.append(f"| {entry.order} | {code(entry.authority.value)} "
                     f"| {entry.accept_when} | {entry.reject_when} |")
    return "\n".join(lines)


def render_rollback() -> str:
    lines = ["| Mechanism | Scope | Restart | Survives crash |", "|---|---|---|---|"]
    for entry in ROLLBACK:
        lines.append(f"| {entry.mechanism} | {entry.scope} "
                     f"| {'yes' if entry.requires_restart else 'no'} "
                     f"| {'yes' if entry.survives_crash else 'no'} |")
    return "\n".join(lines)


TABLES = (("authority ladder", render_authority),
          ("fault matrix", render_faults),
          ("canary sequence", render_canaries),
          ("rollback", render_rollback))


def main() -> int:
    if "--check" not in sys.argv:
        for name, render in TABLES:
            print(f"\n== {name} ==\n")
            print(render())
        return 0

    with open(DOC, "r", encoding="utf-8") as handle:
        text = handle.read()
    failed = 0
    for name, render in TABLES:
        missing = [line for line in render().splitlines()
                   if line.startswith("| `") and line not in text]
        if missing:
            failed += len(missing)
            print(f"  {len(missing)} generated row(s) of the {name} are not in {DOC}:")
            for line in missing[:6]:
                print(f"    {line}")
        else:
            print(f"  {name}: present and identical")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
