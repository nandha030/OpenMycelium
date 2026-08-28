#!/usr/bin/env bash
# Prove the two defects are gone: no traceback when a client disconnects
# mid-request, and readiness no longer spawns interpreters on every poll.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli
PY=/opt/om/venv/bin/python
PORT=11502
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

"$PY" "$SRC/console.py" --port "$PORT" > /tmp/f.out 2> /tmp/f.err &
CONSOLE=$!
for _ in $(seq 1 30); do
  sleep 1
  curl -sf -m 3 "http://127.0.0.1:$PORT/api/workloads" >/dev/null 2>&1 && break
done

echo "  == readiness caching =="
S=$(date +%s%N)
curl -s -m 300 -o /tmp/r1.json "http://127.0.0.1:$PORT/api/readiness"
M=$(date +%s%N)
curl -s -m 60 -o /tmp/r2.json "http://127.0.0.1:$PORT/api/readiness"
E=$(date +%s%N)
first=$(( (M - S) / 1000000 )); second=$(( (E - M) / 1000000 ))
printf '    first  %5s ms  cached=%s\n' "$first" \
  "$("$PY" -c "import json;print(json.load(open('/tmp/r1.json')).get('cached'))")"
printf '    second %5s ms  cached=%s age=%ss\n' "$second" \
  "$("$PY" -c "import json;print(json.load(open('/tmp/r2.json')).get('cached'))")" \
  "$("$PY" -c "import json;print(json.load(open('/tmp/r2.json')).get('cacheAgeSeconds'))")"
[ "$second" -lt 200 ]
check "a repeat readiness call is served from cache in under 200 ms" $?
"$PY" -c "
import json,sys
a=json.load(open('/tmp/r1.json')); b=json.load(open('/tmp/r2.json'))
sys.exit(0 if a['ready']==b['ready'] and b.get('cached') is True else 1)"
check "the cached answer is the same answer, marked as cached" $?

echo
echo "  == a client that disconnects mid-request =="
before=$(grep -c "Traceback" /tmp/f.err || true)
for _ in 1 2 3 4 5; do
  curl -s -m 0.05 "http://127.0.0.1:$PORT/api/readiness" >/dev/null 2>&1 || true
  curl -s -m 0.05 "http://127.0.0.1:$PORT/api/fabric" >/dev/null 2>&1 || true
done
sleep 3
after=$(grep -c "Traceback" /tmp/f.err || true)
printf '    tracebacks before %s, after %s\n' "$before" "$after"
[ "$before" = "$after" ]
check "ten aborted requests produced no new traceback" $?
if grep -q "BrokenPipeError" /tmp/f.err; then false; else true; fi
check "no BrokenPipeError reached the log" $?

echo
echo "  == favicon no longer 404s =="
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT/favicon.ico")
printf '    /favicon.ico -> HTTP %s\n' "$code"
[ "$code" = "200" ]; check "favicon is served" $?

echo
echo "  == the logo is the one that ships =="
curl -s -m 20 -o /tmp/logo.png "http://127.0.0.1:$PORT/assets/openmycelium-logo.png"
printf '    served %s bytes, sha %s\n' "$(stat -c%s /tmp/logo.png)" \
  "$(sha256sum /tmp/logo.png | cut -c1-16)"
printf '    ondisk %s bytes, sha %s\n' "$(stat -c%s "$SRC/console_assets/openmycelium-logo.png")" \
  "$(sha256sum "$SRC/console_assets/openmycelium-logo.png" | cut -c1-16)"
cmp -s /tmp/logo.png "$SRC/console_assets/openmycelium-logo.png"
check "the served logo is byte-identical to the packaged one" $?

kill "$CONSOLE" 2>/dev/null; wait "$CONSOLE" 2>/dev/null
echo
echo "  stderr tail:"
tail -4 /tmp/f.err | sed 's/^/    /'
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
