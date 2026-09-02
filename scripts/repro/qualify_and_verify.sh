#!/usr/bin/env bash
# After a build: install it, qualify it, and prove every console gate is green.
#
# A new build always invalidates qualification -- the tuple pins
# openmyceliumContent, so a new artifact is a new situation and the runtime
# fails closed. That is correct, and it means every build needs this before the
# console will run anything.
#
# It used to be a manual sequence that nobody had run end to end, and every step
# of it was broken: `run --json` wrote prose ahead of the document, `qualify
# record` refused a run document for stating no verdict, and on Windows the
# variables never reached the process. One command, run every time, is what
# stops that happening again -- a path exercised on every build cannot rot
# silently.
#
# Refuses to guess: if the installed content does not match the checkout, the
# qualification would name a situation that is not the one on disk.
#
# Usage:  qualify_and_verify.sh <wheel> [actor]
set -uo pipefail

WHEEL=${1:?usage: qualify_and_verify.sh <wheel> [actor]}
ACTOR=${2:-${OM_QUALIFICATION_ACTOR:-operator}}
VENV=${OM_VENV:-/opt/om/venv}
MODEL=${OM_MODEL:-Mistral-Nemo-Instruct-2407}
PORT=${OM_CONSOLE_PORT:-11501}
WORK=${OM_WORK:-/root/qualify-verify}
OM="$VENV/bin/openmycelium"
PY="$VENV/bin/python"

fail() { printf '\n  == FAILED: %s ==\n' "$1"; exit 1; }

[ -f "$WHEEL" ] || fail "no wheel at $WHEEL"
rm -rf "$WORK"; mkdir -p "$WORK"

printf '\n  1. stopping anything already running\n'
# The pattern is bracketed so it cannot match this script's own command line --
# an unbracketed pkill matches the shell running it and kills itself.
pkill -f "[o]penmycelium console" 2>/dev/null
sleep 1

printf '  2. installing %s\n' "$(basename "$WHEEL")"
"$VENV/bin/pip" install --no-deps --force-reinstall --no-index "$WHEEL" \
    > "$WORK/install.log" 2>&1 || fail "install failed; see $WORK/install.log"
version=$("$OM" version 2>/dev/null | sed -n 's/^ *openmycelium *//p' | head -1)
printf '     installed %s\n' "$version"

printf '  3. installed content matches the checkout\n'
if ! "$PY" "$(dirname "$0")/verify_installed_matches_checkout.py" \
        > "$WORK/matches.log" 2>&1; then
    tail -6 "$WORK/matches.log"
    fail "the installed wheel is not this checkout; qualifying it would name a
       situation that is not the one on disk"
fi
printf '     %s\n' "$(tail -3 "$WORK/matches.log" | head -1 | sed 's/^ *//')"

printf '  4. qualification run\n'
export OM_QUALIFICATION_MODE=qualify
export OM_QUALIFICATION_ACTOR="$ACTOR"
export OM_QUALIFICATION_REASON="qualify $version after build"
if ! "$OM" run --model "$MODEL" --prompt hello --max-new-tokens 24 --json \
        > "$WORK/gate.json" 2> "$WORK/run.err"; then
    tail -6 "$WORK/run.err"
    fail "the qualification run did not complete"
fi
# stdout must be the document and nothing else.
first=$(head -c 1 "$WORK/gate.json")
[ "$first" = "{" ] || fail "gate.json does not begin with '{' -- something is
       writing to stdout alongside the document"
printf '     run complete, gate.json starts at the document\n'

printf '  5. recording the qualification\n'
"$OM" qualify record --model "$MODEL" --evidence "$WORK/gate.json" \
    > "$WORK/record.log" 2>&1 || { tail -6 "$WORK/record.log"; fail "record refused"; }
printf '     %s\n' "$(grep -o 'recorded [a-f0-9]*' "$WORK/record.log" | head -1)"

printf '  6. every console gate\n'
unset OM_QUALIFICATION_MODE OM_QUALIFICATION_ACTOR OM_QUALIFICATION_REASON
nohup "$OM" console > "$WORK/console.log" 2>&1 &
sleep 6
curl -s "http://127.0.0.1:$PORT/api/run/gate?model=$MODEL" > "$WORK/gates.json" \
    || fail "the console did not answer on port $PORT"

"$PY" - "$WORK/gates.json" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
gates = document.get("gates", [])
for gate in gates:
    mark = "ok " if gate.get("ok") else "NO "
    print(f"     {mark} {gate.get('label'):<26} {str(gate.get('detail'))[:52]}")
if not gates:
    print("     no gates reported"); sys.exit(1)
sys.exit(0 if all(g.get("ok") for g in gates) else 1)
PYEOF
[ $? -eq 0 ] || fail "not every gate passes"

printf '\n  == %s installed, qualified, every gate green ==\n' "$version"
printf '     console: http://127.0.0.1:%s\n' "$PORT"
