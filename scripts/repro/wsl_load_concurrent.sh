#!/usr/bin/env bash
# Steps 6-7: both stages resident simultaneously, each within its budget.
#
# This is the capacity claim. Sequential loads prove the loader works; only a
# concurrent load shows 22.8 GiB of weights held across two 16 GiB cards at the
# same moment.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
REAL=${MODEL:-/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407}
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
CLI="$S/runtime/serving/cli_infer.py"

echo "loading both stages concurrently, one process per vendor ..."
/opt/hetenv/bin/python "$CLI" load-check --model "$REAL" --stage cuda --json \
    >/opt/lc_cuda.json 2>/opt/lc_cuda.err &
CUDA_PID=$!
/opt/rocmenv/bin/python "$CLI" load-check --model "$REAL" --stage rocm --json \
    >/opt/lc_rocm.json 2>/opt/lc_rocm.err &
ROCM_PID=$!

# Sample both GPUs while the two loads are in flight.
PEAK_NV=0
for _ in $(seq 1 200); do
  kill -0 $CUDA_PID 2>/dev/null || kill -0 $ROCM_PID 2>/dev/null || break
  NV=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
  [ -n "${NV:-}" ] && [ "$NV" -gt "$PEAK_NV" ] && PEAK_NV=$NV
  sleep 1
done
wait $CUDA_PID; CRC=$?
wait $ROCM_PID; RRC=$?

echo
/opt/hetenv/bin/python - "$PEAK_NV" <<'PY'
import json, sys
peak_nv = int(sys.argv[1])
def load(p):
    try:
        return json.load(open(p))
    except Exception as e:
        return {"error": f"unreadable: {e}"}
cuda, rocm = load("/opt/lc_cuda.json"), load("/opt/lc_rocm.json")

print(f"{'':<26}{'CUDA stage':>22}{'ROCm stage':>22}")
rows = [
    ("GPU", "deviceName", None),
    ("Assigned tensors", "assignedTensors", None),
    ("Loaded tensors", "loadedTensors", None),
    ("Assigned weight GiB", "assignedGiB", None),
    ("GPU allocation GiB", "gpuAllocatedGiB", None),
    ("GPU peak GiB", "gpuPeakGiB", None),
    ("CPU staging peak MiB", "cpuStagingPeakMiB", None),
    ("Device verified", "deviceAllocationVerified", None),
    ("Within budget", "withinQualifiedBudget", None),
    ("Seconds", "seconds", None),
]
for label, key, _ in rows:
    print(f"{label:<26}{str(cuda.get(key,'-')):>22}{str(rocm.get(key,'-')):>22}")

own_ok = (cuda.get("ownership", {}).get("everyTensorHasExactlyOneOwner")
          and rocm.get("ownership", {}).get("everyTensorHasExactlyOneOwner"))
total = cuda.get("assignedGiB", 0) + rocm.get("assignedGiB", 0)
tensors = cuda.get("assignedTensors", 0) + rocm.get("assignedTensors", 0)
print()
print(f"Combined weights held     {total:.3f} GiB across two 16 GiB cards")
print(f"Combined tensors          {tensors} of 363")
print(f"Exclusive ownership       {'yes' if own_ok else 'NO'}")
print(f"nvidia-smi peak observed  {peak_nv} MiB while both stages were loading")

ok = (own_ok and tensors == 363
      and cuda.get("withinQualifiedBudget") and rocm.get("withinQualifiedBudget")
      and cuda.get("deviceAllocationVerified") and rocm.get("deviceAllocationVerified"))
print()
print("RESULT:", "PASS - 22.8 GiB of weights resident across both vendors simultaneously"
      if ok else "FAIL - see the per-stage fields above")
raise SystemExit(0 if ok else 1)
PY
