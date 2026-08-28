#!/usr/bin/env bash
# Capture the real JSON every read-only command emits on the qualified machine.
#
# This is phase one of the console plan: the UI cannot be designed against
# guessed shapes. Nothing here starts a worker or touches a GPU beyond what
# `doctor` and `fabric` already probe.
set -uo pipefail
OM=/opt/om/venv/bin/openmycelium
OUT=/mnt/c/Users/User/Documents/Open_Mycelium/docs/ui/contracts
MODEL=Mistral-Nemo-Instruct-2407
mkdir -p "$OUT"
export PATH=/opt/om/venv/bin:$PATH
unset PYTHONPATH
cd /root || exit 1

capture() {                       # name  command...
  local name=$1; shift
  if "$@" > "$OUT/$name.json" 2>/dev/null && [ -s "$OUT/$name.json" ]; then
    local keys
    keys=$(/opt/om/venv/bin/python -c "
import json,sys
try:
    d=json.load(open('$OUT/$name.json'))
except Exception as e:
    print('NOT JSON'); sys.exit()
if isinstance(d,list):
    print(f'list[{len(d)}] of ' + (', '.join(sorted(d[0])[:8]) if d and isinstance(d[0],dict) else '?'))
else:
    print(', '.join(sorted(d)[:10]))
" 2>/dev/null)
    printf '  %-18s %5s B   %s\n' "$name" "$(stat -c%s "$OUT/$name.json")" "$keys"
  else
    printf '  %-18s no JSON output\n' "$name"
    rm -f "$OUT/$name.json"
  fi
}

echo "  == read-only command contracts =="
capture version      "$OM" version --json
capture config       "$OM" config --json
capture fabric       "$OM" fabric list --json
capture models       "$OM" model list --json
capture model-inspect "$OM" model inspect "$MODEL" --json
capture ps           "$OM" ps --json
capture stats        "$OM" stats --json
capture provision    "$OM" provision --dry-run --json

echo
echo "  == placement manifest (the richest artifact) =="
"$OM" plan --model "$MODEL" --output "$OUT/placement.json" >/dev/null 2>&1
/opt/om/venv/bin/python - "$OUT/placement.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(f"    top-level keys   {', '.join(sorted(m))}")
print(f"    pipeline         {', '.join(sorted(m['pipeline']))}")
print(f"    stage keys       {', '.join(sorted(m['stages'][0]))}")
print(f"    stages           {len(m['stages'])}, tensors "
      f"{[len(s['tensors']) for s in m['stages']]}")
PY
echo
echo "  written to $OUT"
ls -1 "$OUT" | sed 's/^/    /'
