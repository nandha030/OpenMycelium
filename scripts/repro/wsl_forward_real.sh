#!/usr/bin/env bash
# Steps 2-3: one real token across both cards, repeated for determinism.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
FP="$S/runtime/serving/forward_pass.py"
TOKEN=${TOKEN:-1234}
REPEAT=${REPEAT:-2}
PORT=${PORT:-31200}

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "loading both stages and running $REPEAT forward passes for token $TOKEN ..."
/opt/rocmenv/bin/python "$FP" --model "$MODEL" --role rocm --token $TOKEN \
    --repeat $REPEAT --port $PORT >/opt/rf_rocm.json 2>/opt/rf_rocm.err &
R=$!
sleep 8
/opt/hetenv/bin/python "$FP" --model "$MODEL" --role cuda --token $TOKEN \
    --repeat $REPEAT --port $PORT --peer 127.0.0.1 >/opt/rf_cuda.json 2>/opt/rf_cuda.err
CRC=$?
wait $R 2>/dev/null; RRC=$?

for f in /opt/rf_cuda.err /opt/rf_rocm.err; do
  if [ -s "$f" ] && clean <"$f" | grep -qiE 'error|traceback'; then
    echo "--- $(basename $f) ---"; clean <"$f" | tail -12
  fi
done

/opt/hetenv/bin/python - "$CRC" "$RRC" <<'PY'
import json, sys
crc, rrc = int(sys.argv[1]), int(sys.argv[2])
def load(p):
    try:
        return json.loads(open(p).read().strip().splitlines()[-1])
    except Exception as e:
        return {"error": str(e)}
cuda, rocm = load("/opt/rf_cuda.json"), load("/opt/rf_rocm.json")
if "error" in cuda or "error" in rocm:
    print("  cuda:", cuda.get("error", "ok"))
    print("  rocm:", rocm.get("error", "ok"))
    raise SystemExit(1)

cl, rl = cuda.get("loader", {}), rocm.get("loader", {})
lg = rocm.get("logits", {})
print()
print("Model:               Mistral-Nemo-Instruct-2407")
print(f"CUDA:                embedding + layers 0-{cuda['boundary']}, "
      f"{cl.get('gpuAllocatedGiB')} GiB on {cuda.get('gpu')}")
print(f"ROCm:                layers {cuda['boundary']+1}-39 + norm/head, "
      f"{rl.get('gpuAllocatedGiB')} GiB on {rocm.get('gpu')}")
print(f"Boundary:            {cuda.get('boundaryShape')} {cuda.get('boundaryDtype')}, "
      f"{cuda.get('boundaryBytes')} B ({cuda.get('boundaryBytes',0)/1024:.0f} KiB)")
print(f"CUDA workspace:      {cuda.get('workspaceMiB')} MiB transient")
print(f"ROCm workspace:      {rocm.get('workspaceMiB')} MiB transient")
print(f"CUDA kernels observed: {'yes' if cuda.get('kernelsObserved') else 'no'}")
print(f"ROCm kernels observed: {'yes' if rocm.get('kernelsObserved') else 'no'}")
print(f"Logits:              {lg.get('shape')} {lg.get('dtype')}, "
      f"{'finite' if lg.get('allFinite') else 'NOT FINITE'}")
print(f"Top-5 token ids:     {lg.get('topTokenIds')}")
print(f"Top-5 values:        {lg.get('topValues')}")
print(f"Logits digest:       {lg.get('rawSha256')} (raw BF16 bytes)")
print(f"Deterministic:       {'yes' if rocm.get('deterministic') else 'NO'} "
      f"over {len(rocm.get('repeats', []))} runs")
print(f"Generated token:     {rocm.get('generatedToken')}")
print(f"Combined weights:    "
      f"{cl.get('gpuAllocatedGiB',0) + rl.get('gpuAllocatedGiB',0):.3f} GiB across two 16 GiB cards")

ok = (crc == 0 and rrc == 0 and lg.get("allFinite")
      and lg.get("shape") == [1, 1, 131072]
      and lg.get("dtype") == "torch.bfloat16"
      and rocm.get("deterministic")
      and cuda.get("kernelsObserved") and rocm.get("kernelsObserved"))
print()
print("RESULT:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
PY
