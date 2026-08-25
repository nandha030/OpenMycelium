#!/usr/bin/env bash
# Hierarchical cross-vendor broadcast, both root directions.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
CUDA_PY=/opt/hetenv/bin/python
ROCM_PY=/opt/rocmenv/bin/python
export PYTHONPATH="$S/runtime"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
BC=$S/runtime/mycelium/hier_broadcast.py
PORT=27100
PASS=0; FAIL=0

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }
py_for() { [ "$1" = "cuda" ] && echo "$CUDA_PY" || echo "$ROCM_PY"; }

run_bcast() {   # label, root_vendor, shape, dtype, extra_root, expect
  local label=$1 rootv=$2 shape=$3 dtype=$4 extra=${5:-} expect=${6:-pass}
  local destv; [ "$rootv" = "cuda" ] && destv=rocm || destv=cuda
  PORT=$((PORT+1))
  local rpy dpy; rpy=$(py_for "$rootv"); dpy=$(py_for "$destv")
  local common="--root-vendor $rootv --shape $shape --dtype $dtype --collective-id 1"

  $rpy "$BC" --vendor "$rootv" --role root $common --port $PORT $extra \
      >/opt/bc_root.json 2>/opt/bc_root.err &
  local r=$!
  sleep 2.5
  timeout 120 $dpy "$BC" --vendor "$destv" --role peer $common --port $PORT \
      --peer 127.0.0.1 >/opt/bc_peer.json 2>/opt/bc_peer.err
  local prc=$?
  wait $r 2>/dev/null; local rrc=$?

  local ROOT PEER
  ROOT=$(clean </opt/bc_root.json | tail -1)
  PEER=$(clean </opt/bc_peer.json | tail -1)

  $CUDA_PY - "$label" "$ROOT" "$PEER" "$expect" "$rrc" "$prc" <<'PY'
import json, sys
label, expect, rrc, prc = sys.argv[1], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
def load(x):
    try: return json.loads(x)
    except Exception: return {}
root, peer = load(sys.argv[2]), load(sys.argv[3])

if expect == "pass":
    rc, pc = root.get("checksum"), peer.get("checksum")
    ok = (rrc == 0 and prc == 0
          and peer.get("byteExact") is True
          and root.get("sha256") is not None
          and root.get("sha256") == peer.get("sha256")
          and root.get("peerVerified") is True
          and peer.get("rootVendorFromFrame") == root.get("direction","").split("->")[0])
    t = peer.get("timing", {})
    detail = (f"sha {root.get('sha256')} byteExact={peer.get('byteExact')}  local {t.get('localCollectiveMs')}ms "
              f"bridge {t.get('bridgeMs')}ms total {t.get('totalMs')}ms"
              f"{' [degenerate local]' if t.get('localDegenerate') else ''}")
else:
    # Failure injection: the peer must fence, not hang and not consume.
    ok = prc != 0 and "fencedAtEpoch" in peer
    detail = f"peer rc={prc} fenced at epoch {peer.get('fencedAtEpoch')} :: {str(peer.get('error'))[:60]}"
print(f"  {label:<38} {'PASS' if ok else 'FAIL'}  {detail}")
sys.exit(0 if ok else 1)
PY
  if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

echo "############ hierarchical broadcast: root on each vendor ############"
run_bcast "CUDA root  -> ROCm  (4x8 f32)"   cuda "4,8"    f32
run_bcast "ROCm root  -> CUDA  (4x8 f32)"   rocm "4,8"    f32
echo
echo "############ dtypes ############"
run_bcast "CUDA root  f16"                  cuda "16,16"  f16
run_bcast "ROCm root  f64"                  rocm "8,8"    f64
run_bcast "CUDA root  i64"                  cuda "32"     i64
echo
echo "############ edge cases ############"
run_bcast "zero-length tensor (shape 0)"    cuda "0"      f32
run_bcast "zero dim among others (4,0,8)"   rocm "4,0,8"  f32
run_bcast "non-contiguous root input"       cuda "8,16"   f32 --non-contiguous
run_bcast "larger payload (256x1024 f32)"   rocm "256,1024" f32
echo
echo "############ failure propagation and fencing ############"
run_bcast "root aborts mid-collective"      cuda "4,8"    f32 --fail-after-frame fail

echo
echo "BROADCAST: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1
