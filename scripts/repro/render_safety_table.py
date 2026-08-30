"""Render the Safety Governor transition table from the canonical data.

The document's table is generated from `runtime/safety/contract.py`, not typed
alongside it. A table maintained in two places drifts, and the drift stays
invisible until someone reads both carefully at the same moment.

`test_contract_data.py` asserts both directions -- every row in the data appears
in the document, and every row in the document exists in the data -- so a stale
paste is a test failure rather than a discrepancy nobody notices.

    render_safety_table.py            print the table
    render_safety_table.py --check    exit 1 if the document disagrees
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "runtime", "safety"))

from contract import TRANSITIONS  # noqa: E402

DOC = os.path.join(_REPO, "docs", "SAFETY_GOVERNOR.md")
TICK = chr(96)


def code(value: str) -> str:
    return f"{TICK}{value}{TICK}"


def render() -> str:
    lines = [
        "| From | To | Trigger | Source | Timeout | Drain | Audit event |",
        "|---|---|---|---|---|---|---|",
    ]
    for transition in TRANSITIONS:
        lines.append(
            f"| {code(transition.source.value)} "
            f"| {code(transition.target.value)} "
            f"| {code(transition.trigger)} "
            f"| {transition.trigger_source.value} "
            f"| {code(transition.timeout_key) if transition.timeout_key else '—'} "
            f"| {code(transition.drain_mode.value) if transition.drain_mode else '—'} "
            f"| {code(transition.audit_event)} |")
    return "\n".join(lines)


def main() -> int:
    table = render()
    if "--check" not in sys.argv:
        print(table)
        return 0

    with open(DOC, "r", encoding="utf-8") as handle:
        text = handle.read()
    missing = [line for line in table.splitlines()
               if line.startswith("| `") and line not in text]
    if missing:
        print(f"  {len(missing)} generated row(s) are not in {DOC}:")
        for line in missing:
            print(f"    {line}")
        return 1
    print(f"  all {len(TRANSITIONS)} transition rows present and identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
