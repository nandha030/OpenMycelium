#!/usr/bin/env bash
# Step 1: split vs unsplit on the tiny checkpoint, logits compared byte-for-byte.
#
# The synthetic model is small enough to run whole on one device, so the split
# result has something exact to be checked against. On the real checkpoint no
# such reference exists -- it does not fit on one card, which is the point.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
TINY=${TINY:-/opt/tiny-nemo}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
FP="$S/runtime/serving/forward_pass.py"
TOKEN=${TOKEN:-137}
PORT=${PORT:-31100}
BUD="--cuda-budget 1GiB --rocm-budget 1GiB --context-length 512"

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "=== unsplit reference (whole model on the NVIDIA card) ==="
/opt/hetenv/bin/python "$FP" --model "$TINY" --role reference --token $TOKEN $BUD \
    >/opt/fp_ref.json 2>/opt/fp_ref.err
clean </opt/fp_ref.json | tail -1
[ -s /opt/fp_ref.err ] && clean </opt/fp_ref.err | tail -3

echo
echo "=== split: CUDA stage -> ROCm stage ==="
/opt/rocmenv/bin/python "$FP" --model "$TINY" --role rocm --token $TOKEN $BUD \
    --port $PORT >/opt/fp_rocm.json 2>/opt/fp_rocm.err &
R=$!
sleep 4
/opt/hetenv/bin/python "$FP" --model "$TINY" --role cuda --token $TOKEN $BUD \
    --port $PORT --peer 127.0.0.1 >/opt/fp_cuda.json 2>/opt/fp_cuda.err
wait $R 2>/dev/null
clean </opt/fp_cuda.json | tail -1
clean </opt/fp_rocm.json | tail -1
for f in /opt/fp_cuda.err /opt/fp_rocm.err; do
  [ -s "$f" ] && { echo "--- $(basename $f) ---"; clean <"$f" | tail -4; }
done

echo
echo "=== verdict ==="
/opt/hetenv/bin/python - <<'PY'
import json
def load(p):
    try:
        return json.loads(open(p).read().strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"{p}: {e}"}
ref, cuda, rocm = (load(p) for p in
                   ("/opt/fp_ref.json", "/opt/fp_cuda.json", "/opt/fp_rocm.json"))
for name, r in (("reference", ref), ("cuda", cuda), ("rocm", rocm)):
    if "error" in r:
        print(f"  {name}: {r['error']}")
rl, sl = ref.get("logits", {}), rocm.get("logits", {})
print(f"  reference device   {ref.get('device')}")
print(f"  cuda stage         {cuda.get('gpu')}  boundary {cuda.get('boundaryShape')} "
      f"{cuda.get('boundaryDtype')} = {cuda.get('boundaryBytes')} B")
print(f"  rocm stage         {rocm.get('gpu')}")
print(f"  reference logits   {rl.get('shape')} {rl.get('dtype')} sha {rl.get('rawSha256')}")
print(f"  split logits       {sl.get('shape')} {sl.get('dtype')} sha {sl.get('rawSha256')}")
print(f"  reference top ids  {rl.get('topTokenIds')}")
print(f"  split top ids      {sl.get('topTokenIds')}")
same_bytes = rl.get("rawSha256") == sl.get("rawSha256") and rl.get("rawSha256")
same_top = rl.get("topTokenIds") == sl.get("topTokenIds")
finite = rl.get("allFinite") and sl.get("allFinite")
print()
if same_bytes:
    print("  RESULT: PASS - split logits are byte-identical to the unsplit reference")
elif same_top and finite:
    print("  RESULT: PARTIAL - top tokens agree but raw bytes differ")
else:
    print("  RESULT: FAIL")
raise SystemExit(0 if same_bytes else 1)
PY
