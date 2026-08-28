"""Replay the audit trail through the installed package's own gate.

`run`, `chat` and `serve` do not emit an event stream by default, so this
exercises the machinery that would carry one: it takes the placement manifest
that was actually issued for this model, binds a writer to it, emits a real
event sequence, and replays every record through the coordinator's gate.

The point is not that events can be written. It is that the gate still refuses
what it is supposed to refuse. A trail that admits everything records nothing.
"""

from __future__ import annotations

import io
import json
import os
import sys
import uuid

PACKAGE = "/opt/om/venv/lib/python3.12/site-packages/openmycelium"
sys.path.insert(0, os.path.join(PACKAGE, "runtime", "serving"))
sys.path.insert(0, os.path.join(PACKAGE, "runtime", "cli"))

import audit          # noqa: E402
import event_gate     # noqa: E402

MANIFEST = sys.argv[1] if len(sys.argv) > 1 else "/root/rc1-placement.json"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


class Stage:
    """The minimum a writer needs to describe which half it is."""
    def __init__(self, placement, index):
        stage = placement["stages"][index]
        self.role = stage.get("role") or stage.get("vendor") or ("cuda" if index == 0 else "rocm")
        self.index = index
        self.device_identity = stage.get("deviceIdentity") or f"device-{index}"
        self.orientation = stage.get("orientation") or ("first" if index == 0 else "last")


placement = json.load(open(MANIFEST))
run_id = str(uuid.uuid4())

print(f"  manifest      {MANIFEST}")
print(f"  placementId   {placement.get('placementId')}")
print(f"  boundary      after layer {placement['pipeline']['boundaryAfterLayer']}")
counts = sorted(len(s["tensors"]) for s in placement["stages"])
print(f"  ownership     {counts}, overlap "
      f"{len(set(placement['stages'][0]['tensors']) & set(placement['stages'][1]['tensors']))}")

gate = event_gate.EventGate(run_id=run_id)
gate.expect_placement(placement)

# -------------------------------------------------- a legitimate trail
records: list[dict] = []
for index in range(2):
    stream = io.StringIO()
    writer = audit.EventWriter(stream, run_id, Stage(placement, index).role)
    try:
        writer.bind_placement(placement, Stage(placement, index))
    except Exception as error:                                # noqa: BLE001
        check("writer binds to the issued manifest", False, str(error)[:120])
        break
    writer.emit("stageReady")
    writer.emit("boundaryTransfer", bytesTransferred=10240)
    writer.emit("stageComplete")
    for line in stream.getvalue().splitlines():
        if line.strip():
            records.append(json.loads(line))

check("writers bound to the manifest and emitted events", len(records) == 6,
      f"{len(records)} records")

if records:
    missing = [f for f in audit.MINIMAL_TUPLE if f not in records[0]]
    check("every record carries the full minimal tuple", not missing,
          f"missing {missing}" if missing else f"{len(audit.MINIMAL_TUPLE)} fields")

    admitted = 0
    for record in records:
        rejection = gate.admit(record)
        if rejection is None:
            admitted += 1
        else:
            print(f"    unexpected rejection: {rejection}")
    check("the gate admits a legitimate trail", admitted == len(records),
          f"{admitted} of {len(records)}")

# --------------------------------------- the gate must refuse these
if records:
    refusals = 0
    trials = 0

    tampered = dict(records[0])
    tampered["manifestDigest"] = "0" * 64
    trials += 1
    refusals += gate.admit(tampered) is not None

    wrong_run = dict(records[0])
    wrong_run["runId"] = str(uuid.uuid4())
    trials += 1
    refusals += gate.admit(wrong_run) is not None

    replayed = dict(records[-1])
    replayed["eventSequence"] = 0
    trials += 1
    refusals += gate.admit(replayed) is not None

    impostor = dict(records[-1])
    impostor["writerId"] = str(uuid.uuid4())
    impostor["eventSequence"] = 99
    trials += 1
    refusals += gate.admit(impostor) is not None

    check("the gate refuses tampering, wrong run, replay and impostor writers",
          refusals == trials, f"{refusals} of {trials} refused")

print()
print(f"{len(failures)} audit check(s) failed")
sys.exit(1 if failures else 0)
