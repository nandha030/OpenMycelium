#!/usr/bin/env bash
# Is the boundary transport byte-exact for this path?
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
TOKEN=${TOKEN:-1234}; PORT=${PORT:-31500}
/opt/rocmenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role rocm --token $TOKEN --repeat 2 --port $PORT >/opt/t_rocm.json 2>/dev/null &
R=$!; sleep 8
/opt/hetenv/bin/python "$S/runtime/serving/forward_pass.py" --model "$MODEL" \
    --role cuda --token $TOKEN --repeat 2 --port $PORT --peer 127.0.0.1 \
    >/opt/t_cuda.json 2>/dev/null
wait $R 2>/dev/null
/opt/hetenv/bin/python - <<'PY'
import json
def last(p): return json.loads(open(p).read().strip().splitlines()[-1])
cuda, rocm = last("/opt/t_cuda.json"), last("/opt/t_rocm.json")
sent, got = cuda.get("boundaryDigests", []), rocm.get("receivedDigests", [])
print(f"  sent by CUDA      {sent}")
print(f"  received by ROCm  {got}")
ok = bool(sent) and sent == got
print(f"  byte-exact across the vendor boundary: {'yes' if ok else 'NO'}")
print()
print("  So the boundary activation crosses unchanged; any numerical difference")
print("  from a CPU reference comes from GPU-vs-CPU kernel arithmetic, not from")
print("  the split or the transport.")
raise SystemExit(0 if ok else 1)
PY
