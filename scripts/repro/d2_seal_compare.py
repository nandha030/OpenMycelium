"""Compare the two arms of the Gate D.2 seal smoke, and check the observation.

Two questions, and they are different questions:

  1. Did shadow mode change the run? Every correctness invariant must be equal
     across the arms -- the frozen token sequence, ownership, tensor count,
     overlap, boundary layer, fingerprint. TTFT is reported and is *not* judged:
     one observation per arm cannot bound an overhead.

  2. Can the observation be correlated? Every field the schema requires must be
     present, `placementId` must be the placement the run actually used, and
     `runId` must be the run the audit trail attributes its events to. An
     observation that cannot be matched to its run is an anecdote.
"""

from __future__ import annotations

import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/var/log/om-d2-seal"

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "runtime", "safety"))
from shadow import REQUIRED_FIELDS, SHADOW_SCHEMA_VERSION  # noqa: E402

failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def load(name: str):
    with open(os.path.join(OUT, name), "r", encoding="utf-8") as handle:
        text = handle.read()
    # The runner prints the decoded text before the JSON document.
    return json.loads(text[text.index("{"):])


def dig(document, *path, default=None):
    node = document
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


off, shadow = load("run-off.json"), load("run-shadow.json")

print("\nGate D.2 seal -- paired 24-token smoke, 0.3.0a10\n")
print("  correctness across the arms")

INVARIANTS = (
    ("token sequence", ("result", "generatedTokens")),
    ("decoded text", ("result", "text")),
    ("prompt tokens", ("result", "promptTokens")),
    ("stop reason", ("result", "stopReason")),
    ("tensor count", ("placement", "model", "tensorCount")),
    ("model fingerprint", ("placement", "model", "fingerprint")),
    ("boundary layer", ("placement", "pipeline", "boundaryAfterLayer")),
    ("transport", ("placement", "pipeline", "transport")),
    ("adapter identity", ("placement", "adapterId")),
    ("adapter config digest", ("placement", "adapterConfigDigest")),
)
# `manifestDigest` is deliberately *not* compared across arms. `_digest` hashes
# the whole manifest minus itself, and the manifest carries `placementId` -- a
# fresh uuid -- and `createdAt`. Two separate runs must produce two digests;
# equal ones would mean the placement identity had stopped being unique. What
# is worth checking is that the observation names the digest of the manifest its
# own run used, which the correlation section does.
for label, path in INVARIANTS:
    a, b = dig(off, *path, default="<absent>"), dig(shadow, *path, default="<absent>")
    check(f"{label} equal", a == b and a != "<absent>",
          str(a)[:60] if a == b else f"off={str(a)[:40]} shadow={str(b)[:40]}")

# Ownership and overlap are per-stage, and the interesting property is not that
# two lists match but that they partition the model: every tensor owned once.
def ownership(document):
    stages = dig(document, "placement", "stages", default=[])
    return [(s.get("role"), s.get("deviceIdentity"),
             tuple(sorted(s.get("tensors") or [])), tuple(s.get("layers") or []),
             s.get("holdsEmbedding"), s.get("holdsLMHead")) for s in stages]


own_off, own_shadow = ownership(off), ownership(shadow)
check("stage ownership equal", own_off == own_shadow,
      "; ".join(f"{r} {len(ts)} tensors"
                + (f", layers {ly[0]}-{ly[-1]}" if ly else "")
                for r, _, ts, ly, _, _ in own_off))
layer_sets = [set(ly) for _, _, _, ly, _, _ in own_off]
tensor_sets = [set(ts) for _, _, ts, _, _, _ in own_off]
check("layer overlap is zero",
      not set.intersection(*layer_sets) if len(layer_sets) > 1 else True)
# The stronger statement: not merely that the layer ranges are disjoint, but
# that every weight is owned exactly once. Disjoint layers with a duplicated
# embedding would pass the first check and still be two copies on two devices.
check("tensor overlap is zero",
      not set.intersection(*tensor_sets) if len(tensor_sets) > 1 else True,
      f"shared {sorted(set.intersection(*tensor_sets))[:3]}"
      if len(tensor_sets) > 1 and set.intersection(*tensor_sets) else "")
owned = set().union(*tensor_sets) if tensor_sets else set()
check("every model tensor is owned exactly once",
      len(owned) == sum(len(s) for s in tensor_sets)
      == dig(off, "placement", "model", "tensorCount"),
      f"{len(owned)} owned, model reports "
      f"{dig(off, 'placement', 'model', 'tensorCount')}")
check("both workers exited cleanly",
      set((off.get("exitCodes") or {}).values()) == {0}
      and set((shadow.get("exitCodes") or {}).values()) == {0},
      f"off {off.get('exitCodes')} shadow {shadow.get('exitCodes')}")

print("\n  observation")
observation_path = os.path.join(OUT, "safety-shadow-shadow.jsonl")
with open(observation_path, "r", encoding="utf-8") as handle:
    records = [json.loads(line) for line in handle if line.strip()]
check("the shadow arm produced exactly one observation", len(records) == 1,
      f"{len(records)} record(s)")
record = records[0]

missing = [name for name in REQUIRED_FIELDS if name not in record]
check("every required field is present", not missing, f"missing {missing}")
check("the schema version is the current one",
      record.get("schemaVersion") == SHADOW_SCHEMA_VERSION,
      str(record.get("schemaVersion")))

# Correlation, against the run's own records rather than against itself.
with open(os.path.join(OUT, "work-shadow", "placement.json"), "r",
          encoding="utf-8") as handle:
    placement = json.load(handle)
check("placementId is the placement the run used",
      record.get("placementId") == placement.get("placementId"),
      f"{record.get('placementId')} vs {placement.get('placementId')}")
check("manifestDigest is the manifest the run used",
      record.get("manifestDigest") == placement.get("manifestDigest"),
      str(record.get("manifestDigest"))[:24])

events_path = os.path.join(OUT, "work-shadow", "events.jsonl")
run_ids = set()
with open(events_path, "r", encoding="utf-8") as handle:
    for line in handle:
        if line.strip():
            value = json.loads(line).get("runId")
            if value:
                run_ids.add(value)
check("runId is the run the audit trail attributes its events to",
      record.get("runId") in run_ids,
      f"observation {record.get('runId')}; audit {sorted(run_ids)}")

with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as handle:
    boot = handle.read().strip()
check("bootId is this boot", record.get("bootId") == boot, boot)
check("wallTimeUtc and monotonicNs are both recorded",
      isinstance(record.get("wallTimeUtc"), float)
      and isinstance(record.get("monotonicNs"), int),
      f"{record.get('wallTimeUtc')} / {record.get('monotonicNs')}")
check("nothing was enforced",
      record.get("safetyEnforced") is False and record.get("safetySimulated") is True)

# AMD power must arrive as unavailable-expected, never as zero. This is the
# distinction most likely to be lost when telemetry passes through an adapter,
# and reporting a real card as drawing 0 W would be a false safety signal.
signals = record.get("safetySignals") or {}
amd = next((v for k, v in signals.items() if k.startswith("amd:")), {})
nvidia = next((v for k, v in signals.items() if k.startswith("nvidia:")), {})
check("AMD power is null with UNAVAILABLE_EXPECTED, not zero",
      amd.get("powerWatts") is None
      and "UNAVAILABLE_EXPECTED" in str(amd.get("powerConfidence")),
      f"{amd.get('powerWatts')} / {amd.get('powerConfidence')}")
check("NVIDIA power is a value with AVAILABLE",
      isinstance(nvidia.get("powerWatts"), (int, float))
      and "AVAILABLE" == str(nvidia.get("powerConfidence")).split(".")[-1],
      f"{nvidia.get('powerWatts')} W / {nvidia.get('powerConfidence')}")

print("\n  off arm")
check("the off arm produced no observation file",
      not os.path.exists(os.path.join(OUT, "work-off", "safety-shadow.jsonl")))
with open(os.path.join(OUT, "run-off.err"), "r", encoding="utf-8") as handle:
    off_err = handle.read()
check("the off arm printed no observation line",
      "[safety/shadow]" not in off_err)

# Reported, never judged. Two observations cannot bound an overhead, and the
# Gate A campaign put run-to-run TTFT spread at 44-47 ms within a single build.
print("\n  timing (reported, not judged)")
for name, document in (("off", off), ("shadow", shadow)):
    ttft = dig(document, "result", "ttftMs", default=dig(document, "result", "ttft_ms"))
    decode = dig(document, "result", "decodeTokensPerSecond",
                 default=dig(document, "result", "tokensPerSecond"))
    print(f"    {name:<7} TTFT {ttft}  decode {decode}")

print()
if failures:
    print(f"  == D.2 seal smoke FAILED: {len(failures)} check(s) ==")
    sys.exit(1)
print("  == D.2 seal smoke PASSED ==")
