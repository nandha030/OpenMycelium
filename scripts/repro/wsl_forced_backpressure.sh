#!/usr/bin/env bash
# Forced backpressure: make the credit window actually bind.
#
# The earlier stress runs never stalled -- the wire was slower than the copy
# engine, so slot reuse always found its event retired. That proved the path
# does not deadlock or corrupt, but not that throttling works. Delaying the
# receiver before it acks holds the sender's credits and forces the stall.
set -uo pipefail
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
BIN=/opt/bin/bridge
PASS=0; FAIL=0
PORT=26000

verdict() {
  if [ "$2" -eq 1 ]; then printf '  %-52s PASS  %s\n' "$1" "${3:-}"; PASS=$((PASS+1))
  else printf '  %-52s FAIL  %s\n' "$1" "${3:-}"; FAIL=$((FAIL+1)); fi
}

field() { grep -oE "\"$2\":[0-9]+" "$1" 2>/dev/null | head -1 | cut -d: -f2; }

# ------------------------------------------------ 1. the window genuinely binds
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT \
     --recv-delay-ms 20 --device-check-every 0 >/opt/bp_recv.json 2>/opt/bp_recv.err &
R=$!
sleep 0.6
timeout 300 $BIN --role send --vendor cuda --mode stream --transfers 60 --slots 2 \
     --window 2 --port $PORT --peer 127.0.0.1 >/opt/bp_send.json 2>/opt/bp_send.err
SRC=$?
wait $R 2>/dev/null; RRC=$?

WAITS=$(field /opt/bp_send.json creditWaits)
ALLOCS=$(field /opt/bp_send.json hostAllocs)
PINNED=$(field /opt/bp_send.json pinnedBytes)
VERIFIED=$(grep -oE '"verified":(true|false)' /opt/bp_recv.json | cut -d: -f2)

echo "  sender: $(head -1 /opt/bp_send.json)"
echo "  recv:   $(head -1 /opt/bp_recv.json)"
[ "${WAITS:-0}" -gt 0 ] 2>/dev/null && OK=1 || OK=0
verdict "credit window actually stalled the sender" $OK "creditWaits=${WAITS:-0}"
[ "${ALLOCS:-0}" = "2" ] && OK=1 || OK=0
verdict "sender pinned pool stayed at two allocations" $OK "hostAllocs=${ALLOCS:-?} pinned=${PINNED:-?}"
[ "$VERIFIED" = "true" ] && [ "$SRC" -eq 0 ] && [ "$RRC" -eq 0 ] && OK=1 || OK=0
verdict "payload still verified while throttled" $OK "verified=$VERIFIED"

# --------------------------- 2. receiver cancelled while the sender is blocked
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT \
     --recv-delay-ms 400 --device-check-every 0 >/dev/null 2>&1 &
R=$!
sleep 0.6
$BIN --role send --vendor cuda --mode stream --transfers 5000 --slots 2 \
     --window 2 --port $PORT --peer 127.0.0.1 >/dev/null 2>/opt/bp2_send.err &
Sp=$!
sleep 3                        # sender is now parked in the credit loop
kill -9 $R 2>/dev/null
DEADLINE=$((SECONDS + 30))
while kill -0 $Sp 2>/dev/null && [ $SECONDS -lt $DEADLINE ]; do sleep 0.5; done
if kill -0 $Sp 2>/dev/null; then kill -9 $Sp 2>/dev/null; OK=0; else OK=1; fi
wait $Sp 2>/dev/null
verdict "sender exits when receiver dies mid-stall" $OK "within 30s"

if grep -qE 'direction=send.*phase=credit.*seq=[0-9]+' /opt/bp2_send.err; then OK=1; else OK=0; fi
verdict "stall error names direction, phase and sequence" $OK \
        "$(grep -oE 'direction=send phase=credit seq=[0-9]+' /opt/bp2_send.err | head -1)"

# ------------------------------------------- 3. recovery on a fresh connection
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/opt/bp3.json 2>/dev/null &
R=$!
sleep 0.6
timeout 120 $BIN --role send --vendor cuda --mode stream --transfers 50 --slots 2 \
     --window 2 --port $PORT --peer 127.0.0.1 >/dev/null 2>&1
wait $R 2>/dev/null
grep -q '"verified":true' /opt/bp3.json && OK=1 || OK=0
verdict "fresh connection after the kill still verifies" $OK "$(field /opt/bp3.json transfers) transfers"

echo
echo "FORCED BACKPRESSURE: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1
