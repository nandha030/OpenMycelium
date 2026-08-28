#!/usr/bin/env bash
# Where does the split diverge from the CPU reference: at the boundary, or after?
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
TOKEN=${TOKEN:-1234}; PORT=${PORT:-31400}

CUDA_VISIBLE_DEVICES="" /opt/hetenv/bin/python "$S/runtime/serving/reference_stream.py" \
    --model "$MODEL" --token $TOKEN >/opt/d_ref.json 2>/dev/null
/opt/rocmenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role rocm --token $TOKEN --port $PORT >/opt/d_rocm.json 2>/dev/null &
R=$!; sleep 8
/opt/hetenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role cuda --token $TOKEN --port $PORT --peer 127.0.0.1 >/opt/d_cuda.json 2>/dev/null
wait $R 2>/dev/null

/opt/hetenv/bin/python - <<'PY'
import json
def last(p): return json.loads(open(p).read().strip().splitlines()[-1])
ref, cuda, rocm = last("/opt/d_ref.json"), last("/opt/d_cuda.json"), last("/opt/d_rocm.json")

def stats(a, b, label):
    d = [abs(x - y) for x, y in zip(a, b)]
    r = [abs(x - y) / max(abs(x), 1e-6) for x, y in zip(a, b)]
    exact = sum(1 for x, y in zip(a, b) if x == y)
    print(f"  {label:<34} max|d| {max(d):.5f}   max rel {max(r)*100:6.2f}%   "
          f"exact {exact}/{len(a)}")
    return max(r)

print("Divergence located by stage")
print()
rb, cb = ref.get("boundaryFirst16", []), cuda.get("boundaryFirst16", [])
if rb and cb:
    print(f"  reference hidden@19  {[round(v,4) for v in rb[:6]]}")
    print(f"  CUDA hidden@19       {[round(v,4) for v in cb[:6]]}")
    first_half = stats(rb, cb, "after layers 0-19 (CUDA half)")
else:
    print("  boundary slice unavailable"); first_half = None
print()
rl, sl = ref["first16Fp32"], rocm["logits"]["first16Fp32"]
full = stats(rl, sl, "after all 40 layers (final logits)")
print()
if first_half is not None:
    if first_half < 1e-6:
        print("  The CUDA half reproduces the CPU reference exactly; all divergence")
        print("  arises in the second half or the head.")
    else:
        print(f"  Divergence is already present at the boundary ({first_half*100:.2f}%),")
        print("  so it accumulates through both halves rather than originating in one.")
print()
print(f"  top-5 sets identical: "
      f"{set(ref['topTokenIds']) == set(rocm['logits']['topTokenIds'])}")
PY
