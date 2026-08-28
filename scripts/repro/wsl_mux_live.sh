#!/usr/bin/env bash
# Live multiplexing over the real CUDA<->ROCm bridge, both directions.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
CUDA_PY=/opt/hetenv/bin/python
ROCM_PY=/opt/rocmenv/bin/python
export PYTHONPATH="$S/runtime"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
MUX=$S/runtime/mycelium/mux_live.py
PORT=29100
PASS=0; FAIL=0

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }
py_for() { [ "$1" = "cuda" ] && echo "$CUDA_PY" || echo "$ROCM_PY"; }

run() {  # label, send_vendor, scenario, rounds
  local label=$1 sv=$2 scen=$3 rounds=${4:-1}
  local rv; [ "$sv" = "cuda" ] && rv=rocm || rv=cuda
  PORT=$((PORT+1))
  local spy rpy; spy=$(py_for "$sv"); rpy=$(py_for "$rv")

  $rpy "$MUX" --vendor "$rv" --side recv --scenario "$scen" --rounds "$rounds" \
      --port $PORT >/opt/mux_r.json 2>/opt/mux_r.err &
  local r=$!
  sleep 2.5
  timeout 180 $spy "$MUX" --vendor "$sv" --side send --scenario "$scen" --rounds "$rounds" \
      --port $PORT --peer 127.0.0.1 >/opt/mux_s.json 2>/opt/mux_s.err
  wait $r 2>/dev/null; local rrc=$?

  $CUDA_PY - "$label" "$(clean </opt/mux_r.json | tail -1)" "$(clean </opt/mux_s.json | tail -1)" "$rrc" <<'PY'
import json, sys
label, rrc = sys.argv[1], int(sys.argv[4])
def load(x):
    try: return json.loads(x)
    except Exception: return {}
recv, send = load(sys.argv[2]), load(sys.argv[3])
ok = rrc == 0 and recv.get("ok") is True
mux = recv.get("mux", {})
if recv.get("error"):
    detail = (f"fenced={mux.get('fenced')} by={mux.get('fencedBy')} "
              f"partialExposed={recv.get('partialSuccessExposed')} :: {str(recv.get('error'))[:58]}")
else:
    d = recv.get("digests", {})
    detail = (f"writes={send.get('writes')} reads={recv.get('socketReads')} "
              f"frames={{{', '.join(f'{k}:{len(v)}' for k,v in sorted(d.items()))}}} "
              f"gathers={recv.get('gathersComplete')} {recv.get('seconds')}s")
print(f"  {label:<44} {'PASS' if ok else 'FAIL'}  {detail}")
sys.exit(0 if ok else 1)
PY
  if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

for DIR in cuda rocm; do
  OTHER=$([ "$DIR" = "cuda" ] && echo rocm || echo cuda)
  echo "############ $DIR -> $OTHER ############"
  run "interleaved ids, mixed ops/dtypes"   "$DIR" interleave
  run "reversed completion order"           "$DIR" reverse-completion
  run "fragmented headers and payloads"     "$DIR" fragmented
  run "coalesced frames in one write"       "$DIR" coalesced
  run "stale epoch injected into one id"    "$DIR" stale-epoch
  run "disconnect mid-payload"              "$DIR" disconnect-mid-payload
  run "reconnect, fresh epoch, 3 rounds"    "$DIR" interleave 3
  echo
done

echo "############ sustained: parser-state and allocation leaks ############"
run "sustained 40 rounds cuda->rocm"        cuda interleave 40
run "sustained 40 rounds rocm->cuda"        rocm interleave 40

echo
echo "LIVE MULTIPLEXING: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1
