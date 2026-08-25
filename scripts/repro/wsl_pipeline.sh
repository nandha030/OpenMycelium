#!/usr/bin/env bash
# Two-process cross-vendor pipeline inference:
#   stage A on CUDA (layers 0..split)  ->  activation  ->  stage B on ROCm (split..N)
# compared against a single-device reference.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
CUDA_PY=/opt/hetenv/bin/python      # CUDA torch build
ROCM_PY=/opt/rocmenv/bin/python     # ROCm torch build
export PYTHONPATH="$S/runtime"

LAYERS=${LAYERS:-8}
SPLIT=${SPLIT:-4}
WIDTH=${WIDTH:-256}
ITER=${ITER:-1}
PORT=${PORT:-25001}

STAGE=$S/runtime/mycelium/pipeline_stage.py
WARMUP=${WARMUP:-5}
COMMON="--layers $LAYERS --width $WIDTH --split $SPLIT --iterations $ITER --warmup $WARMUP"

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "=== reference: all $LAYERS layers on one device (CUDA) ==="
REF=$($CUDA_PY "$STAGE" --stage reference --vendor cuda $COMMON 2>&1 | clean | tail -1)
echo "  $REF"

echo
echo "=== pipeline: layers 0-$((SPLIT-1)) on CUDA, $SPLIT-$((LAYERS-1)) on ROCm ==="
$ROCM_PY "$STAGE" --stage b --vendor rocm $COMMON --port $PORT >/opt/pipe_b.json 2>/opt/pipe_b.err &
B=$!
sleep 3
$CUDA_PY "$STAGE" --stage a --vendor cuda $COMMON --port $PORT --peer 127.0.0.1 \
    >/opt/pipe_a.json 2>/opt/pipe_a.err
ARC=$?
wait $B 2>/dev/null; BRC=$?

A_OUT=$(cat /opt/pipe_a.json 2>/dev/null | clean | tail -1)
B_OUT=$(cat /opt/pipe_b.json 2>/dev/null | clean | tail -1)
echo "  stage A: $A_OUT"
echo "  stage B: $B_OUT"
[ -s /opt/pipe_a.err ] && echo "  A stderr: $(clean </opt/pipe_a.err | head -2)"
[ -s /opt/pipe_b.err ] && echo "  B stderr: $(clean </opt/pipe_b.err | head -2)"

echo
echo "=== verdict ==="
$CUDA_PY - "$REF" "$B_OUT" <<'PY'
import json, sys
try:
    ref = json.loads(sys.argv[1]); got = json.loads(sys.argv[2])
except Exception as e:
    print(f"  could not parse results: {e}"); raise SystemExit(1)

rt, gt = ref.get("tokens"), got.get("tokens")
rc, gc = ref.get("checksum"), got.get("checksum")
print(f"  reference        : {ref.get('note')}")
print(f"  pipeline stage B : {got.get('note')}")
print(f"  reference tokens : {rt}")
print(f"  pipeline  tokens : {gt}")
tol = max(1e-3, abs(rc) * 1e-5) if isinstance(rc, float) else 1e-3
drift = abs(rc - gc) if isinstance(rc, float) and isinstance(gc, float) else float("inf")
print(f"  logit checksum   : ref={rc} pipeline={gc} drift={drift:.6f} tol={tol:.6f}")
if isinstance(got.get("metrics"), dict):
    m = got["metrics"]
    print(f"  activation       : {m['bytesMoved']} B over {m['transfers']} steady-state transfers")
    print(f"  steady-state     : p50 {m['p50LatencyMs']} ms  p95 {m['p95LatencyMs']} ms  "
          f"p99 {m['p99LatencyMs']} ms  {m['throughputMBps']} MB/s")
    cold = got.get("coldStartMs")
    if cold is not None:
        print(f"  cold start       : {cold} ms (excluded, {got.get('warmupSkipped',0)} warmup skipped)")
ok = rt == gt and drift <= tol
print("  RESULT:", "PASS - pipeline output matches the single-device reference"
      if ok else "FAIL - pipeline output diverged")
raise SystemExit(0 if ok else 1)
PY
