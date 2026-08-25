#!/usr/bin/env bash
# Backpressure stress for host-staged-xvendor.
#
#   ci       100 transfers   -- runs on every change
#   stress   10000 transfers -- explicit hardware run
#
# Checks per run: every sequence number accounted for, every payload verified,
# periodic device round trip, bounded pinned memory, and no global device sync
# (enforced by LD_PRELOAD, not by timing).
set -uo pipefail
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
BIN=/opt/bin/bridge
GUARD=/opt/bin/sync_guard.so

PROFILE="${1:-ci}"
case "$PROFILE" in
  ci)     TRANSFERS=100   ;;
  stress) TRANSFERS=10000 ;;
  *) echo "usage: $0 [ci|stress]"; exit 2 ;;
esac

echo "=== building sync guard ==="
gcc -shared -fPIC "$S/runtime/bridge/sync_guard.c" -o "$GUARD" -ldl 2>&1 | head -5
[ -f "$GUARD" ] || { echo "guard build failed"; exit 1; }

echo "=== proving the guard actually fires ==="
SYNC_GUARD_SELFTEST=1 LD_PRELOAD="$GUARD" "$BIN" --help >/dev/null 2>/tmp/guard.log
if [ $? -eq 97 ] && grep -q "SYNC-GUARD VIOLATION" /tmp/guard.log; then
  echo "  guard self-test: FIRES correctly (exit 97)"
else
  echo "  guard self-test: FAILED -- guard is not effective, aborting"
  exit 1
fi

PORT=23000
run_stream() {
  local sv=$1 rv=$2
  PORT=$((PORT + 1))
  LD_PRELOAD="$GUARD" "$BIN" --role recv --vendor "$rv" --mode stream \
      --chunk $((4*1024*1024)) --slots 2 --port $PORT --device-check-every 32 \
      >/opt/s_recv.json 2>/opt/s_recv.err &
  local r=$!
  sleep 0.6
  LD_PRELOAD="$GUARD" timeout 900 "$BIN" --role send --vendor "$sv" --mode stream \
      --transfers "$TRANSFERS" --chunk $((4*1024*1024)) --slots 2 --window 2 \
      --port $PORT --peer 127.0.0.1 >/opt/s_send.json 2>/opt/s_send.err
  local src=$?
  wait $r 2>/dev/null
  local rrc=$?

  echo "  --- $sv -> $rv ($TRANSFERS transfers) ---"
  echo "    send: $(head -1 /opt/s_send.json 2>/dev/null)"
  echo "    recv: $(head -1 /opt/s_recv.json 2>/dev/null)"
  if grep -q "SYNC-GUARD VIOLATION" /opt/s_send.err /opt/s_recv.err 2>/dev/null; then
    echo "    VERDICT: FAIL (global device synchronisation detected)"
    return 1
  fi
  local verified
  verified=$(grep -oE '"verified":(true|false)' /opt/s_recv.json 2>/dev/null | cut -d: -f2)
  local got
  got=$(grep -oE '"transfers":[0-9]+' /opt/s_recv.json 2>/dev/null | head -1 | cut -d: -f2)
  local pinned
  pinned=$(grep -oE '"pinnedBytes":[0-9]+' /opt/s_recv.json 2>/dev/null | cut -d: -f2)
  local allocs
  allocs=$(grep -oE '"hostAllocs":[0-9]+' /opt/s_recv.json 2>/dev/null | cut -d: -f2)

  local ok=1
  [ "$verified" = "true" ] || { echo "    payload verification: FAIL"; ok=0; }
  [ "$got" = "$TRANSFERS" ] || { echo "    sequence completeness: FAIL (saw ${got:-0}/$TRANSFERS)"; ok=0; }
  [ "${allocs:-0}" = "2" ] || { echo "    pinned pool bounded: FAIL (${allocs:-?} allocations, expected 2)"; ok=0; }
  [ "$src" -eq 0 ] || { echo "    sender exit: FAIL ($src)"; ok=0; }
  [ "$rrc" -eq 0 ] || { echo "    receiver exit: FAIL ($rrc)"; ok=0; }

  if [ $ok -eq 1 ]; then
    echo "    VERDICT: PASS  (all $TRANSFERS sequences, pinned=${pinned} bytes in $allocs allocations, no global sync)"
  else
    echo "    VERDICT: FAIL"
    head -3 /opt/s_recv.err 2>/dev/null | sed 's/^/      /'
  fi
  return $((1 - ok))
}

echo
echo "############ backpressure stress: profile=$PROFILE ############"
FAILED=0
run_stream cuda rocm || FAILED=1
run_stream rocm cuda || FAILED=1

echo
if [ $FAILED -eq 0 ]; then echo "STRESS RESULT: PASS"; else echo "STRESS RESULT: FAIL"; fi
exit $FAILED
