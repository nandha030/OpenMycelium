"""Rotation must bound the file without discarding failure evidence."""
import os, sys
sys.path.insert(0, "/mnt/c/Users/User/Documents/Open_Mycelium/runtime/serving")
import audit

class Stage:
    role = runtime = "cuda"
    device_identity = "nvidia:x"
    tensors = ["a"]
    layers = [0]
    weight_bytes = budget_bytes = 1

MANIFEST = {"placementId": "p", "manifestDigest": "d" * 64,
            "model": {"fingerprint": "f" * 64, "layerCount": 2},
            "pipeline": {"boundaryAfterLayer": 0,
                         "stageOrientation": "cuda-first"},
            "stages": [{"role": "cuda", "deviceIdentity": "nvidia:x"}],
            "fabric": {}}

target = "/tmp/rot"
os.makedirs(target, exist_ok=True)
path = os.path.join(target, "events.jsonl")
writer = audit.EventWriter(open(path, "a", encoding="utf-8"),
                           audit.new_run_id(), "cuda")
writer.bind_placement(MANIFEST, Stage())

for i in range(40):
    writer.emit("token", index=i, text="x" * 90)
writer.emit("failed", detail="deliberate, to be preserved")
for i in range(300):
    writer.emit("token", index=i, text="y" * 90)

print(f"  MAX_EVENT_BYTES={audit.MAX_EVENT_BYTES}  "
      f"EVENT_RETENTION={audit.EVENT_RETENTION}")
failed = kept = 0
for name in sorted(os.listdir(target)):
    size = os.path.getsize(os.path.join(target, name))
    tag = ""
    if name.endswith(".failed"):
        failed += 1
        tag = "   <- failure evidence, exempt from retention"
    elif name != "events.jsonl":
        kept += 1
    print(f"    {name:<40} {size:>7} bytes{tag}")
live = os.path.getsize(path)
print(f"\n  live file bounded: {live} < {audit.MAX_EVENT_BYTES}  "
      f"-> {live < audit.MAX_EVENT_BYTES}")
print(f"  rotated segments kept: {kept} (limit {audit.EVENT_RETENTION})")
print(f"  failure segments preserved: {failed}")
ok = (live < audit.MAX_EVENT_BYTES and kept <= audit.EVENT_RETENTION
      and failed >= 1)
print(f"\n  RESULT: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
