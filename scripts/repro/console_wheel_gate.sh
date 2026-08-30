#!/usr/bin/env bash
# The acceptance gate: the console driving real dual-GPU inference from an
# installed wheel, on the qualified RTX 5060 Ti and RX 9060 XT.
set -uo pipefail
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
DIST=${OM_DIST:-$REPO/dist}
# The version under test is a parameter; the default is unchanged, so running
# this gate with no arguments still tests exactly the artifact it always did.
# Without this the gate can only ever test one version, and "run the unchanged
# gate against the new build" would be impossible to say truthfully.
OM_VERSION=${OM_VERSION:-0.2.0a5}
MCCL_VERSION=${OM_MCCL_VERSION:-0.2.0a3}
WHEEL="$DIST/openmycelium-$OM_VERSION-py3-none-any.whl"
MCCL="$DIST/openmycelium_mccl-$MCCL_VERSION-py3-none-any.whl"
if [ ! -f "$WHEEL" ]; then
  echo "  the wheel under test does not exist: $WHEEL" >&2
  echo "  set OM_DIST to the dist directory and OM_VERSION to the build" >&2
  exit 78
fi
OM=/opt/om/venv
# The default is the console's own port, so an unadorned run tests exactly what
# it always did. Overridable because the gate must be able to run beside a
# console an operator is already using -- the alternative is killing their
# session, and a gate that demands that will be run less often.
PORT=${OM_CONSOLE_PORT:-11501}
MODEL=Mistral-Nemo-Instruct-2407
FAIL=0
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == install the wheel =="
"$OM/bin/pip" install -q --upgrade --force-reinstall --no-deps "$MCCL" "$WHEEL" > /tmp/inst.log 2>&1
check "installed" $?
openmycelium version 2>/dev/null | head -3 | sed 's/^/    /'

echo
echo "  == the port must be free before we claim anything =="
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "    port $PORT is already in use; a stale console would answer for us"
  ss -ltnp 2>/dev/null | grep ":$PORT " | sed "s/^/      /"
  echo "    refusing to run a gate against a process this script did not start"
  exit 2
fi
check "port $PORT is free" $?

echo
echo "  == the console starts from the installed entry point =="
openmycelium console --port "$PORT" > /tmp/w.out 2> /tmp/w.err &
CONSOLE=$!
READY=1
for _ in $(seq 1 30); do
  sleep 1
  curl -sf -m 3 "http://127.0.0.1:$PORT/api/workloads" >/dev/null 2>&1 && { READY=0; break; }
done
check "console reachable on 127.0.0.1:$PORT" $READY
kill -0 "$CONSOLE" 2>/dev/null
check "the console we started is the one still running" $?

echo "    assets served from inside the package:"
for a in / /assets/app.js /assets/styles.css; do
  printf '      %-18s HTTP %s\n' "$a" \
    "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT$a")"
done

echo
echo "  == gate, then one real run on both GPUs =="
curl -s -m 240 -o /tmp/g.json "http://127.0.0.1:$PORT/api/run/gate?model=$MODEL"
"$OM/bin/python" -c "
import json,sys; g=json.load(open('/tmp/g.json'))
for x in g['gates']: print(f'      {\"ok \" if x[\"ok\"] else \"NO \"}{x[\"label\"]}')
for a in (g.get('expectedAllocation') or []):
    print(f'      {a[\"role\"]:<5} layers {a[\"layers\"][0]}-{a[\"layers\"][-1]}  '
          f'{a[\"weightBytes\"]/(1<<30):.2f} GiB on {a[\"deviceIdentity\"][:28]}')
sys.exit(0 if g['canRun'] else 1)"
check "gate permits the run" $?

( curl -sN -m 400 "http://127.0.0.1:$PORT/api/run/events" > /tmp/sse.txt ) &
SSE=$!
sleep 1
curl -s -m 30 -X POST "http://127.0.0.1:$PORT/api/run/start" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"Explain cross-vendor GPU inference in one sentence.\",\"maxNewTokens\":24}" \
  -o /tmp/start.json > /dev/null
"$OM/bin/python" -c "
import json,sys; d=json.load(open('/tmp/start.json')); sys.exit(0 if d.get('runId') else 1)"
check "run accepted" $?
for _ in $(seq 1 120); do
  sleep 3
  grep -qE '\"kind\": ?\"finished\"' /tmp/sse.txt 2>/dev/null && break
done
kill "$SSE" 2>/dev/null

"$OM/bin/python" - <<'PY'
import json
result = None
kinds = {}
for line in open("/tmp/sse.txt", errors="ignore"):
    if line.startswith("data: "):
        try:
            e = json.loads(line[6:])
        except ValueError:
            continue
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
        if e.get("kind") == "result":
            result = e.get("result")
print("      events: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
if result:
    r = result.get("result") or {}
    t = r.get("generatedTokens")
    print(f"      TTFT {r.get('ttftMs')} ms · decode {r.get('decodeTokensPerSecond')} tok/s "
          f"· {len(t) if isinstance(t, list) else t} tokens")
    print(f"      text {(r.get('text') or '')[:64]!r}")
PY
grep -qE '\"kind\": ?\"finished\"' /tmp/sse.txt
check "streamed through to completion" $?

echo
echo "  == idle and released =="
sleep 4
ORPHANS=$(pgrep -fc pipeline_run 2>/dev/null); ORPHANS=${ORPHANS:-0}
[ "$ORPHANS" = "0" ]; check "zero orphan workers" $?
curl -s -m 60 "http://127.0.0.1:$PORT/api/residency" -o /tmp/res.json
"$OM/bin/python" -c "
import json
for d in json.load(open('/tmp/res.json'))['devices']:
    print(f'      {d[\"vendor\"]:<5} {d[\"usedMiB\"]:>6} MiB of {d[\"totalMiB\"]}')"

kill "$CONSOLE" 2>/dev/null; wait "$CONSOLE" 2>/dev/null
[ "$(stat -c%s /tmp/w.out)" = "0" ]
check "stdout clean, diagnostics on stderr" $?
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
