#!/usr/bin/env bash
# Exercise the console's service layer and HTTP surface on the qualified
# machine, from the checkout, before building a wheel.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli
PY=/opt/om/venv/bin/python
PORT=11501
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == service layer, called directly =="
"$PY" - <<PY
import sys, json
sys.path.insert(0, "$SRC")
import console_service as s

ok = True
for name, call in (("readiness", s.readiness),
                   ("fabric", lambda: s.fabric(use_cache=True)),
                   ("models", s.models),
                   ("workloads", s.workloads),
                   ("residency", s.gpu_residency),
                   ("provenance", s.provenance)):
    try:
        data = call()
        assert data.get("schemaVersion") == 1, "missing schemaVersion"
        print(f"    {name:<12} ok   keys: {', '.join(sorted(data))[:70]}")
    except Exception as error:
        ok = False
        print(f"    {name:<12} FAIL {type(error).__name__}: {error}")

try:
    v = s.verify_model("Mistral-Nemo-Instruct-2407")
    print(f"    verify       ok={v.get('ok')} shards={len(v.get('shards', []))} "
          f"problems={v.get('problems')}")
    p = s.placement("Mistral-Nemo-Instruct-2407")
    if p.get("ok"):
        print(f"    placement    boundary={p['pipeline']['boundaryAfterLayer']} "
              f"tensors={p['totalTensors']} exclusive={p['exclusive']}")
    else:
        ok = False
        print(f"    placement    FAILED {p.get('detail')}")
except Exception as error:
    ok = False
    print(f"    verify/plan  FAIL {type(error).__name__}: {error}")
sys.exit(0 if ok else 1)
PY
check "service layer returns typed responses" $?

echo
echo "  == HTTP surface =="
"$PY" "$SRC/console.py" --port "$PORT" > /tmp/console.out 2> /tmp/console.err &
CONSOLE=$!
for _ in $(seq 1 30); do
  sleep 1
  curl -sf -m 3 "http://127.0.0.1:$PORT/api/workloads" >/dev/null 2>&1 && break
done

for path in /api/readiness /api/fabric /api/models /api/workloads /api/residency /api/provenance; do
  code=$(curl -s -o /tmp/r.json -w '%{http_code}' -m 60 "http://127.0.0.1:$PORT$path")
  ver=$("$PY" -c "import json;print(json.load(open('/tmp/r.json')).get('schemaVersion'))" 2>/dev/null)
  printf '    %-18s HTTP %s  schemaVersion %s\n' "$path" "$code" "$ver"
  [ "$code" = "200" ] && [ "$ver" = "1" ] || FAIL=$((FAIL+1))
done
check "every endpoint answers with schemaVersion 1" $([ "$FAIL" = "0" ] && echo 0 || echo 1)

echo
echo "  == static assets =="
for asset in / /assets/app.js /assets/styles.css; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT$asset")
  printf '    %-18s HTTP %s\n' "$asset" "$code"
done

echo
echo "  == it binds loopback only =="
ss -ltnp 2>/dev/null | grep ":$PORT" | sed 's/^/    /'

echo
echo "  == the run gate =="
curl -s -m 180 "http://127.0.0.1:$PORT/api/run/gate?model=Mistral-Nemo-Instruct-2407" \
  -o /tmp/gate.json
"$PY" - <<'PY'
import json
g = json.load(open("/tmp/gate.json"))
print(f"    canRun={g.get('canRun')}")
for gate in g.get("gates", []):
    print(f"      {'PASS' if gate['ok'] else 'FAIL'}  {gate['label']}: {gate['detail'][:60]}")
for a in (g.get("expectedAllocation") or []):
    print(f"      {a['role']:<5} layers {a['layers'][0]}-{a['layers'][-1]}  "
          f"{a['weightBytes'] / (1 << 30):.2f} GiB")
PY

echo
echo "  == a bad path is refused, not guessed =="
curl -s -o /dev/null -w '    /assets/../console.py -> HTTP %{http_code}\n' \
  -m 10 "http://127.0.0.1:$PORT/assets/../console.py"

kill "$CONSOLE" 2>/dev/null
wait "$CONSOLE" 2>/dev/null
echo
echo "  console stderr (diagnostics only; stdout must stay clean):"
head -6 /tmp/console.err | sed 's/^/    /'
echo "  console stdout bytes: $(stat -c%s /tmp/console.out)"
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
