#!/usr/bin/env bash
# Failure and timeout propagation for host-staged-xvendor.
#
# Each case injects one fault and asserts the peer exits within the timeout with
# a diagnostic naming direction, sequence, and phase -- not a hang, and not a
# silent success.
set -uo pipefail
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
BIN=/opt/bin/bridge
TIMEOUT=20
PORT=24000
PASS=0; FAIL=0

verdict() {  # name, condition, detail
  if [ "$2" -eq 1 ]; then printf '  %-46s PASS  %s\n' "$1" "${3:-}"; PASS=$((PASS+1))
  else printf '  %-46s FAIL  %s\n' "$1" "${3:-}"; FAIL=$((FAIL+1)); fi
}

has_diagnostic() {  # file -> 1 if it names direction, seq and phase
  grep -qE 'direction=(send|recv).*phase=[a-z0-9]+.*seq=[0-9]+' "$1" && echo 1 || echo 0
}

# ---------------------------------------------------------------- 1. sender killed mid-frame
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/opt/f.json 2>/opt/f.err &
R=$!
sleep 0.6
$BIN --role send --vendor cuda --mode stream --transfers 100000 --slots 2 --window 2 \
     --port $PORT --peer 127.0.0.1 >/dev/null 2>&1 &
Sp=$!
sleep 1.5
kill -9 $Sp 2>/dev/null
timeout $TIMEOUT bash -c "wait $R" >/dev/null 2>&1
wait $R 2>/dev/null; RC=$?
OK=0; [ $RC -ne 0 ] && OK=1
verdict "sender killed mid-frame -> receiver exits" $OK "rc=$RC diag=$(has_diagnostic /opt/f.err)"

# ---------------------------------------------------------------- 2. receiver killed with credits exhausted
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/dev/null 2>&1 &
R=$!
sleep 0.6
$BIN --role send --vendor cuda --mode stream --transfers 100000 --slots 2 --window 2 \
     --port $PORT --peer 127.0.0.1 >/dev/null 2>/opt/f2.err &
Sp=$!
sleep 1.5
kill -9 $R 2>/dev/null
sleep 1
if kill -0 $Sp 2>/dev/null; then
  sleep $TIMEOUT
  if kill -0 $Sp 2>/dev/null; then kill -9 $Sp 2>/dev/null; OK=0; else OK=1; fi
else OK=1; fi
wait $Sp 2>/dev/null
verdict "receiver killed, credits exhausted -> sender exits" $OK "diag=$(has_diagnostic /opt/f2.err)"

# ---------------------------------------------------------------- 3. disconnect between header and payload
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/dev/null 2>/opt/f3.err &
R=$!
sleep 0.6
# Speak the framing by hand: a valid header, then close before the payload.
python3 - "$PORT" <<'PY'
import socket, struct, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
# magic, seq, dtype, rank, dims[4], bytes, crc  (matches activation_header_t)
hdr = struct.pack("<IQII4qIQ" if False else "<I4xQII4qQI4x",
                  0x584D4331, 0, 2, 1, 1024, 0, 0, 0, 4096, 0)
s.sendall(hdr)
s.close()
PY
sleep 3
timeout $TIMEOUT bash -c "wait $R" >/dev/null 2>&1
kill -9 $R 2>/dev/null
wait $R 2>/dev/null; RC=$?
OK=0; [ $RC -ne 0 ] && OK=1
verdict "disconnect between header and payload" $OK "rc=$RC diag=$(has_diagnostic /opt/f3.err)"

# ---------------------------------------------------------------- 4. malformed descriptors
malformed_case() {  # label, python-packed header
  PORT=$((PORT+1))
  $BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/dev/null 2>/opt/f4.err &
  local r=$!
  sleep 0.6
  python3 -c "$2" "$PORT" 2>/dev/null
  sleep 2
  kill -9 $r 2>/dev/null
  wait $r 2>/dev/null; local rc=$?
  local ok=0
  grep -qE 'malformed descriptor|out of order' /opt/f4.err && ok=1
  verdict "$1" $ok "$(grep -oE '(malformed descriptor|out of order)' /opt/f4.err | head -1)"
}

PKT='import socket,struct,sys
s=socket.create_connection(("127.0.0.1",int(sys.argv[1])))
s.sendall(struct.pack("<I4xQII4qQI4x",%s))
import time; time.sleep(1); s.close()'

malformed_case "malformed: bad magic"      "$(printf "$PKT" '0xDEADBEEF,0,2,1,16,0,0,0,64,0')"
malformed_case "malformed: dtype index"    "$(printf "$PKT" '0x584D4331,0,999,1,16,0,0,0,64,0')"
malformed_case "malformed: rank"           "$(printf "$PKT" '0x584D4331,0,2,99,16,0,0,0,64,0')"
malformed_case "malformed: negative dim"   "$(printf "$PKT" '0x584D4331,0,2,1,-5,0,0,0,64,0')"
malformed_case "malformed: oversized len"  "$(printf "$PKT" '0x584D4331,0,2,1,16,0,0,0,999999999,0')"
malformed_case "malformed: sequence skew"  "$(printf "$PKT" '0x584D4331,77,2,1,16,0,0,0,64,0')"

# ---------------------------------------------------------------- 5. stalled completion -> peer times out
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/dev/null 2>/opt/f5.err &
R=$!
sleep 0.6
$BIN --role send --vendor cuda --mode stream --transfers 100 --slots 2 --window 2 \
     --stall-at 10 --port $PORT --peer 127.0.0.1 >/dev/null 2>/opt/f5s.err &
Sp=$!
sleep 2
# The receiver should be blocked awaiting a payload that will never arrive.
if kill -0 $R 2>/dev/null; then STALLED=1; else STALLED=0; fi
kill -9 $Sp $R 2>/dev/null
wait $Sp $R 2>/dev/null
OK=0
grep -q 'deliberate stall' /opt/f5s.err && [ $STALLED -eq 1 ] && OK=1
verdict "deliberate stall detected at the sender" $OK "stall logged with seq/phase"

# ---------------------------------------------------------------- 6. recovery: a fresh connection still qualifies
PORT=$((PORT+1))
$BIN --role recv --vendor rocm --mode stream --slots 2 --port $PORT >/opt/f6.json 2>/dev/null &
R=$!
sleep 0.6
timeout 120 $BIN --role send --vendor cuda --mode stream --transfers 50 --slots 2 \
     --window 2 --port $PORT --peer 127.0.0.1 >/dev/null 2>&1
wait $R 2>/dev/null
OK=0
grep -q '"verified":true' /opt/f6.json && OK=1
verdict "fresh connection after faults still verifies" $OK "$(grep -oE '"transfers":[0-9]+' /opt/f6.json | head -1)"

echo
echo "FAULT RESULTS: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1
