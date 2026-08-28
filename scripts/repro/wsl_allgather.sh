#!/usr/bin/env bash
# Hierarchical cross-vendor all-gather, verified byte-for-byte.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
CUDA_PY=/opt/hetenv/bin/python
ROCM_PY=/opt/rocmenv/bin/python
export PYTHONPATH="$S/runtime"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
AG=$S/runtime/mycelium/hier_allgather.py
PORT=28100
PASS=0; FAIL=0

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

run_ag() {  # label, local_ranks, shape, dtype, cuda_extra, rocm_extra, expect
  local label=$1 L=$2 shape=$3 dtype=$4 cx=${5:-} rx=${6:-} expect=${7:-pass}
  PORT=$((PORT+1))
  local common="--local-ranks $L --shape $shape --dtype $dtype --collective-id 1 --port $PORT"

  $CUDA_PY "$AG" --vendor cuda --role listen $common $cx >/opt/ag_c.json 2>/opt/ag_c.err &
  local c=$!
  sleep 2.5
  timeout 120 $ROCM_PY "$AG" --vendor rocm --role connect $common --peer 127.0.0.1 $rx \
      >/opt/ag_r.json 2>/opt/ag_r.err
  local rrc=$?
  wait $c 2>/dev/null; local crc=$?

  $CUDA_PY - "$label" "$(clean </opt/ag_c.json | tail -1)" "$(clean </opt/ag_r.json | tail -1)" \
            "$expect" "$crc" "$rrc" <<'PY'
import json, sys
label, expect, crc, rrc = sys.argv[1], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
def load(x):
    try: return json.loads(x)
    except Exception: return {}
cu, ro = load(sys.argv[2]), load(sys.argv[3])

if expect == "pass":
    # Byte-for-byte on BOTH sides, and both must agree with each other.
    ok = (crc == 0 and rrc == 0
          and cu.get("byteExact") is True and ro.get("byteExact") is True
          and cu.get("sha256") == ro.get("sha256") == cu.get("expectedSha256"))
    t = ro.get("timing", {})
    detail = (f"sha {cu.get('sha256')} both sides  out {cu.get('outputShape')}  "
              f"local {t.get('localCollectiveMs')}ms border {t.get('bridgeMs')}ms "
              f"dist {t.get('localDistributionMs')}ms total {t.get('totalMs')}ms")
else:
    ok = (crc != 0 or rrc != 0) and ("fencedAtEpoch" in cu or "fencedAtEpoch" in ro)
    err = cu.get("error") or ro.get("error") or ""
    detail = f"fenced :: {str(err)[:66]}"
print(f"  {label:<40} {'PASS' if ok else 'FAIL'}  {detail}")
sys.exit(0 if ok else 1)
PY
  if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

echo "############ all-gather: rank-ordered output, byte verified ############"
run_ag "2 ranks (1 per vendor) f32"        1 "2,3"   f32
run_ag "4 ranks (2 per vendor) f32"        2 "2,3"   f32
run_ag "4 ranks f16"                       2 "4,4"   f16
run_ag "4 ranks i64"                       2 "8"     i64
run_ag "4 ranks larger (128,256) f32"      2 "128,256" f32
echo
echo "############ ordering comes from rank metadata, not arrival ############"
run_ag "CUDA sends its ranks reversed"     2 "2,3"   f32 --reverse-send
run_ag "ROCm sends its ranks reversed"     2 "2,3"   f32 "" --reverse-send
run_ag "both sides send reversed"          2 "2,3"   f32 --reverse-send --reverse-send
echo
echo "############ contract violations must fence, not half-succeed ############"
run_ag "a rank omits its contribution"     2 "2,3"   f32 "" "--drop-rank 3" fail

echo
echo "ALL-GATHER: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1
