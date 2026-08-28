set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=$D/runtime/cli:$D/runtime/serving:$D/runtime/scheduler:$D/runtime/fabric
W=/opt/openmycelium/chat
echo "########## two-GPU run under the audit gate ##########"
printf 'Explain cross-vendor GPU inference in two sentences.\nName one limitation.\n/bye\n' \
  | /opt/hetenv/bin/python $D/runtime/cli/chat.py --model mistral-nemo \
      --max-new-tokens 40 --port 32041
echo
echo "########## audit the trail it produced ##########"
/opt/hetenv/bin/python - "$W" <<'PY'
import json, sys, os
sys.path.insert(0,"/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli")
sys.path.insert(0,"/mnt/c/Users/User/Documents/Open_Mycelium/runtime/serving")
from event_gate import EventGate
from audit import MINIMAL_TUPLE
work=sys.argv[1]
placement=json.load(open(os.path.join(work,"placement.json")))
events=[json.loads(l) for l in open(os.path.join(work,"events.jsonl")) if l.strip()]
run_ids={e.get("runId") for e in events}
print(f"  events: {len(events)}   distinct runIds: {len(run_ids)}")
gate=EventGate(run_id=sorted(r for r in run_ids if r)[0]); gate.expect_placement(placement)
for e in events: gate.admit(e)
s=gate.summary()
print(f"  admitted {s['admitted']}  rejected {s['rejected']}  roles {s['rolesSeen']}")
for r in s["rejections"]: print("    reject:", r)
print(f"  last sequence per role: {s['lastSequence']}")
print(f"  bootId stable: {s['bootId'] is not None}")
missing=[i for i,e in enumerate(events) if e.get("placementValidation")!="failed"
         and [n for n in MINIMAL_TUPLE if n not in e]]
print(f"  validated events missing any tuple field: {len(missing)}")
summ=[e for e in events if e.get("event")=="placement_validated"]
print(f"  placement_validated events: {len(summ)} (expect 2, one per worker)")
for e in summ:
    print(f"    {e['workerRole']:<5} {e['deviceIdentity']:<48} "
          f"tensors={e['assignedTensorCount']} layers "
          f"{e['cudaLayerStart']}-{e['cudaLayerEnd']}/{e['rocmLayerStart']}-{e['rocmLayerEnd']}"
          f" manifest={os.path.basename(e['manifestPath'])}")
tot=sum(e["assignedTensorCount"] for e in summ)
print(f"  total assigned tensors across workers: {tot}")
print(f"  digests agree across workers: {len({e['manifestDigest'] for e in summ})==1}")
print(f"  RESULT: {'PASS' if s['rejected']==0 and len(summ)==2 and tot==363 else 'FAIL'}"
      " - every accepted event independently attributable")
PY
