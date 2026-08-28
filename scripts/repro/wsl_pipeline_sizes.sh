#!/usr/bin/env bash
# Activation-size sweep for the cross-vendor pipeline.
# The 16 KiB case is kept as the original reference point; the rest are sizes a
# real transformer boundary would actually produce.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
CUDA_PY=/opt/hetenv/bin/python
ROCM_PY=/opt/rocmenv/bin/python
export PYTHONPATH="$S/runtime"
STAGE=$S/runtime/mycelium/pipeline_stage.py
PORT=25100

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

seq_tokens() {   # build a comma list of N pseudo-token ids
  $CUDA_PY -c "print(','.join(str((i*37+11)%256) for i in range($1)))"
}

run_size() {  # label, batch, seq, width, iterations
  local label=$1 batch=$2 seqlen=$3 width=$4 iters=$5
  PORT=$((PORT+1))
  local toks; toks=$(seq_tokens "$seqlen")
  local common="--layers 8 --split 4 --width $width --batch $batch --tokens $toks --iterations $iters --warmup 5"

  local ref; ref=$($CUDA_PY "$STAGE" --stage reference --vendor cuda $common 2>&1 | clean | tail -1)
  $ROCM_PY "$STAGE" --stage b --vendor rocm $common --port $PORT >/opt/ps_b.json 2>/dev/null &
  local b=$!
  sleep 3
  $CUDA_PY "$STAGE" --stage a --vendor cuda $common --port $PORT --peer 127.0.0.1 \
      >/opt/ps_a.json 2>/dev/null
  wait $b 2>/dev/null

  $CUDA_PY - "$label" "$ref" "$(cat /opt/ps_a.json | clean | tail -1)" "$(cat /opt/ps_b.json | clean | tail -1)" <<'PY'
import json, sys
label = sys.argv[1]
try:
    ref, a, b = (json.loads(x) for x in sys.argv[2:5])
except Exception:
    print(f"  {label:<26} FAILED to parse"); raise SystemExit(0)
ok = ref.get("tokens") == b.get("tokens") and abs(ref.get("checksum",0) - b.get("checksum",1)) <= max(1e-3, abs(ref.get("checksum",0))*1e-5)
am, bm = a.get("metrics", {}), b.get("metrics", {})
print(f"  {label:<26} {a.get('activationBytes',0)/1048576:7.3f} MiB  "
      f"send p50 {am.get('p50LatencyMs',0):7.3f} p95 {am.get('p95LatencyMs',0):7.3f}  "
      f"recv p50 {bm.get('p50LatencyMs',0):7.3f} p95 {bm.get('p95LatencyMs',0):7.3f}  "
      f"{bm.get('throughputMBps',0):8.1f} MB/s  {'PASS' if ok else 'FAIL'}")
PY
}

echo "activation size sweep: layers 0-3 on CUDA -> layers 4-7 on ROCm"
echo "  (send timer = device->host stage + frame + write; recv timer starts at first byte)"
echo
printf '  %-26s %11s  %-26s  %-26s  %13s  %s\n' "case" "size" "sender" "receiver" "throughput" ""
run_size "16 KiB (original)"      2   8  256 50
run_size "batch 8 seq 128 w512"   8 128  512 30
run_size "batch 8 seq 512 w1024"  8 512 1024 15
run_size "batch 16 seq 512 w1024" 16 512 1024 10
