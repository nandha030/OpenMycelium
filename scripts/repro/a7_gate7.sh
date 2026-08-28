#!/usr/bin/env bash
# Step 7 of the 0.1.0a7 validation: the complete command chain, on the clean
# distribution, from the installed wheel, with both GPUs qualified.
set -uo pipefail
LOG=/var/log/om-a7
OM=/opt/om/venv
H=${OM_HARNESS:-/root/repro}
MODEL=Mistral-Nemo-Instruct-2407
FAIL=0
mkdir -p "$LOG"
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH

say()   { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/gate7.log"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/gate7.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/gate7.log"; FAIL=$((FAIL+1)); fi
}
echo "gate7 $(date -Is)" > "$LOG/gate7.log"

say "inventory"
openmycelium version > "$LOG/version.log" 2>&1;      check "version" $?
openmycelium fabric list > "$LOG/fabric.log" 2>&1;   check "fabric list" $?
sed -n '1,14p' "$LOG/fabric.log" | sed 's/^/    /'
openmycelium model list > "$LOG/models.log" 2>&1;    check "model list" $?
sed 's/^/    /' "$LOG/models.log"
openmycelium model inspect "$MODEL" > "$LOG/inspect.log" 2>&1
check "model inspect" $?
openmycelium model verify "$MODEL" > "$LOG/verify.log" 2>&1
check "model verify" $?

say "placement"
openmycelium plan --model "$MODEL" --output /root/placement.json \
  > "$LOG/plan.log" 2>&1
check "plan" $?
"$OM/bin/python" "$H/check_placement.py" /root/placement.json
check "exclusive 181/182 ownership" $?

say "one-shot generation across both vendors"
S=$(date +%s)
openmycelium run --model "$MODEL" \
  --prompt "Explain cross-vendor GPU inference in one sentence." \
  --max-new-tokens 24 > "$LOG/run.log" 2>&1
RC=$?; E=$(date +%s)
sed -n '/model  /,$p' "$LOG/run.log" | head -20 | sed 's/^/    /'
printf '    wall clock %s s\n' "$(( E - S ))"
[ "$RC" = "0" ]
check "openmycelium run" $?
grep -q "boundary" "$LOG/run.log"
check "the run reported a pipeline boundary" $?

say "multi-turn chat"
printf 'Name one limitation of this approach.\n/bye\n' \
  | openmycelium chat --model "$MODEL" --max-new-tokens 24 \
    > "$LOG/chat.log" 2>&1
check "openmycelium chat" $?
sed -n '/ready in/,$p' "$LOG/chat.log" | head -12 | sed 's/^/    /'

say "no leftovers"
sleep 3
# pgrep -c prints 0 *and* exits non-zero when it matches nothing, so a
# `|| echo 0` fallback appends a second zero and the comparison fails on a
# perfectly clean system.
ORPHANS=$(pgrep -fc "pipeline_run" 2>/dev/null); ORPHANS=${ORPHANS:-0}
printf '    worker processes: %s\n' "$ORPHANS"
[ "$ORPHANS" = "0" ]
check "no orphan workers" $?
openmycelium ps > "$LOG/ps.log" 2>&1
check "ps" $?
sed 's/^/    /' "$LOG/ps.log" | head -6

printf '\n  %d check(s) failed in step 7\n' "$FAIL" | tee -a "$LOG/gate7.log"
exit "$FAIL"
