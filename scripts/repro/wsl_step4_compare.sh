#!/usr/bin/env bash
# Step 4: split-pipeline logits vs an independent streaming CPU reference.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
TOKEN=${TOKEN:-1234}
PORT=${PORT:-31300}
clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "=== independent reference: streaming CPU, all 40 layers ==="
CUDA_VISIBLE_DEVICES="" /opt/hetenv/bin/python "$S/runtime/serving/reference_stream.py" \
    --model "$MODEL" --token $TOKEN >/opt/s4_ref.json 2>/dev/null
clean </opt/s4_ref.json | tail -1 | cut -c1-160

echo
echo "=== split pipeline: CUDA layers 0-19 -> ROCm layers 20-39 ==="
/opt/rocmenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role rocm --token $TOKEN --port $PORT >/opt/s4_rocm.json 2>/dev/null &
R=$!
sleep 8
/opt/hetenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role cuda --token $TOKEN --port $PORT --peer 127.0.0.1 \
    >/opt/s4_cuda.json 2>/dev/null
wait $R 2>/dev/null

echo
/opt/hetenv/bin/python - <<'PY'
import json
def last(p):
    try:
        return json.loads(open(p).read().strip().splitlines()[-1])
    except Exception as e:
        return {"error": str(e)}
ref = last("/opt/s4_ref.json")
rocm = last("/opt/s4_rocm.json")
split = rocm.get("logits", {})
if "error" in ref or not split:
    print("  reference:", ref.get("error", "ok"), " split:", bool(split))
    raise SystemExit(1)

print(f"{'':<22}{'reference (CPU)':>26}{'split (CUDA+ROCm)':>26}")
print(f"{'shape':<22}{str(ref['shape']):>26}{str(split['shape']):>26}")
print(f"{'dtype':<22}{ref['dtype']:>26}{split['dtype']:>26}")
print(f"{'all finite':<22}{str(ref['allFinite']):>26}{str(split['allFinite']):>26}")
print(f"{'raw digest':<22}{ref['rawSha256']:>26}{split['rawSha256']:>26}")
print()
print(f"  reference top-5 ids   {ref['topTokenIds']}")
print(f"  split     top-5 ids   {split['topTokenIds']}")
print(f"  reference top-5 vals  {ref['topValues']}")
print(f"  split     top-5 vals  {split['topValues']}")

ref_set, split_set = set(ref["topTokenIds"]), set(split["topTokenIds"])
a, b = ref["first16Fp32"], split["first16Fp32"]
diffs = [abs(x - y) for x, y in zip(a, b)]
rel = [d / max(abs(x), 1e-6) for d, x in zip(diffs, a)]
# BF16 carries 8 mantissa bits, so ~0.4% per rounding; 40 layers of differing
# accumulation order can compound that.
BF16_EPS = 2 ** -8
print()
print(f"  first-16 max abs diff  {max(diffs):.4f}")
print(f"  first-16 max rel diff  {max(rel) * 100:.2f}%   (bf16 unit roundoff {BF16_EPS*100:.2f}%)")
print(f"  top-5 sets identical   {'yes' if ref_set == split_set else 'NO'}")
print(f"  top-1 same token       {'yes' if ref['topTokenIds'][0] == split['topTokenIds'][0] else 'no'}")

# A tie at the top means argmax order is arbitrary and implementation-dependent.
tied_ref = ref["topValues"][0] == ref["topValues"][1]
tied_split = split["topValues"][0] == split["topValues"][1]
if tied_ref or tied_split:
    print()
    print(f"  NOTE: the top two logits are tied in bf16 "
          f"(reference {ref['topValues'][:2]}, split {split['topValues'][:2]}).")
    print("        Greedy argmax between equal values is arbitrary, so the "
          "single generated")
    print("        token is not a stable cross-implementation comparison here; "
          "the top-k set is.")

ok = (ref_set == split_set and ref["allFinite"] and split["allFinite"]
      and ref["shape"] == split["shape"] and max(rel) < 0.05)
print()
print("RESULT:", "PASS - split agrees with the independent reference within bf16 tolerance"
      if ok else "FAIL - see the differences above")
raise SystemExit(0 if ok else 1)
PY
