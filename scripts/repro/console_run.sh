#!/usr/bin/env bash
# The operator loop the console exists for: gate, run on both GPUs, stream
# events, read metrics, stop, and confirm the machine is idle again.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli
PY=/opt/om/venv/bin/python
PORT=11501
MODEL=Mistral-Nemo-Instruct-2407
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

"$PY" "$SRC/console.py" --port "$PORT" > /tmp/con.out 2> /tmp/con.err &
CONSOLE=$!
for _ in $(seq 1 30); do
  sleep 1
  curl -sf -m 3 "http://127.0.0.1:$PORT/api/workloads" >/dev/null 2>&1 && break
done

echo "  == gate =="
curl -s -m 240 -o /tmp/g.json "http://127.0.0.1:$PORT/api/run/gate?model=$MODEL"
"$PY" -c "
import json; g=json.load(open('/tmp/g.json'))
print(f'    canRun={g[\"canRun\"]}')
for x in g['gates']: print(f'      {\"ok \" if x[\"ok\"] else \"NO \"}{x[\"label\"]}: {x[\"detail\"][:56]}')
import sys; sys.exit(0 if g['canRun'] else 1)"
check "the gate permits a run" $?

echo
echo "  == stream events while a run happens =="
( curl -sN -m 400 "http://127.0.0.1:$PORT/api/run/events" > /tmp/sse.txt ) &
SSE=$!
sleep 1

curl -s -m 30 -X POST "http://127.0.0.1:$PORT/api/run/start" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"Explain cross-vendor GPU inference in one sentence.\",\"maxNewTokens\":24}" \
  -o /tmp/start.json
"$PY" -c "
import json; d=json.load(open('/tmp/start.json'))
print(f'    runId {d.get(\"runId\",\"\")[:8]}  pid {d.get(\"pid\")}')
import sys; sys.exit(0 if d.get('runId') else 1)"
check "run started through the console" $?

echo "    waiting for the run to finish"
for _ in $(seq 1 120); do
  sleep 3
  grep -q '"kind": "finished"' /tmp/sse.txt 2>/dev/null && break
  grep -q '"kind":"finished"' /tmp/sse.txt 2>/dev/null && break
done
kill "$SSE" 2>/dev/null

echo
echo "  == what the stream carried =="
"$PY" - <<'PY'
import json
kinds = {}
result = None
for line in open("/tmp/sse.txt", errors="ignore"):
    if not line.startswith("data: "):
        continue
    try:
        e = json.loads(line[6:])
    except ValueError:
        continue
    kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    if e.get("kind") == "result":
        result = e.get("result")
print("    event kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
if result:
    r = result.get("result") or {}
    tokens = r.get("generatedTokens")
    print(f"    TTFT         {r.get('ttftMs')} ms")
    print(f"    decode       {r.get('decodeTokensPerSecond')} tok/s")
    print(f"    generated    {len(tokens) if isinstance(tokens, list) else tokens} tokens")
    print(f"    boundary     {(result.get('placement') or {}).get('boundaryAfterLayer', '')}")
    text = (r.get("text") or "")[:70]
    if text:
        print(f"    text         {text!r}")
else:
    print("    no result event was captured")
PY
grep -qE '"kind": ?"finished"' /tmp/sse.txt
check "the stream delivered lifecycle events through to finished" $?

echo
echo "  == the machine is idle again =="
sleep 4
curl -s -m 30 "http://127.0.0.1:$PORT/api/workloads" -o /tmp/w.json
"$PY" -c "
import json; w=json.load(open('/tmp/w.json'))
print(f'    running={len(w[\"running\"])}  stale={len(w[\"stale\"])}  busy={w[\"busy\"]}')
import sys; sys.exit(0 if not w['busy'] else 1)"
check "no workload remains active" $?
ORPHANS=$(pgrep -fc pipeline_run 2>/dev/null); ORPHANS=${ORPHANS:-0}
echo "    orphan workers: $ORPHANS"
[ "$ORPHANS" = "0" ]; check "zero orphan workers" $?
curl -s -m 60 "http://127.0.0.1:$PORT/api/residency" -o /tmp/res.json
"$PY" -c "
import json
for d in json.load(open('/tmp/res.json'))['devices']:
    print(f'    {d[\"vendor\"]:<5} {d[\"name\"][:28]:<30} {d[\"usedMiB\"]:>6} MiB of {d[\"totalMiB\"]}')"

kill "$CONSOLE" 2>/dev/null; wait "$CONSOLE" 2>/dev/null
echo "  console stdout bytes: $(stat -c%s /tmp/con.out)  (must be 0)"
[ "$(stat -c%s /tmp/con.out)" = "0" ]; check "stdout stayed clean; diagnostics went to stderr" $?
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
