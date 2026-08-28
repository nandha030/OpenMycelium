#!/usr/bin/env bash
# The final abbreviated hardware gate for 0.1.0rc1, from its own wheel.
#
# Same code as 0.1.0a8 apart from the version string, so this is a
# confirmation that the artifact about to be promoted behaves, not a fresh
# qualification of the runtime.
set -uo pipefail
REL=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0rc1
OM=/opt/om/venv
H=${OM_HARNESS:-/root/repro}
LOG=/var/log/om-rc1
MODEL=Mistral-Nemo-Instruct-2407
CONTENT=818b330afcf6c3375d93e8b43bcde974db9d411990b95633aafc87a40d5ad3b8
FAIL=0
mkdir -p "$LOG"
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH

say()   { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/rc1.log"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/rc1.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/rc1.log"; FAIL=$((FAIL+1)); fi
}
echo "0.1.0rc1 gate $(date -Is)" > "$LOG/rc1.log"

say "install RC1 from its own wheel"
( cd "$REL" && sha256sum -c <(grep '\.whl$' SHA256SUMS.frozen) ) > "$LOG/sums.txt" 2>&1
if grep -q FAILED "$LOG/sums.txt"; then false; else true; fi
check "frozen wheels match their recorded SHA-256" $?
"$OM/bin/pip" install -q --upgrade --force-reinstall --no-deps \
  "$REL"/openmycelium-0.1.0rc1-py3-none-any.whl >> "$LOG/install.log" 2>&1
check "installed" $?
openmycelium version --json > "$LOG/provenance.json" 2>/dev/null
"$OM/bin/python" -c "
import json, sys
r = json.load(open('$LOG/provenance.json'))
print(f\"    version {r.get('openmycelium')}  content {r.get('installedContentSha256')}\")
h = r.get('hsaRuntime') or {}
print(f\"    hsa {h.get('flavor')} {h.get('source')} {str(h.get('sha256'))[:24]}\")
sys.exit(0 if r.get('installedContentSha256') == '$CONTENT'
         and r.get('openmycelium') == '0.1.0rc1' else 1)"
check "installed content equals the frozen RC1 build" $?

say "doctor"
openmycelium doctor > "$LOG/doctor.log" 2>&1
sed 's/^/  /' "$LOG/doctor.log" | head -12
[ "$(grep -c '\[FAIL\]' "$LOG/doctor.log")" = "0" ]
check "doctor reports no failures" $?

say "fabric"
openmycelium fabric list > "$LOG/fabric.log" 2>&1
check "fabric list" $?
grep -E 'RTX 5060 Ti|RX 9060 XT' "$LOG/fabric.log" | sed 's/^/    /'
[ "$(grep -cE 'RTX 5060 Ti|RX 9060 XT' "$LOG/fabric.log")" -ge 2 ]
check "both accelerators enumerated" $?

say "model verify and plan"
openmycelium model verify "$MODEL" > "$LOG/verify.log" 2>&1
check "model verify" $?
openmycelium plan --model "$MODEL" --output /root/rc1-placement.json > "$LOG/plan.log" 2>&1
check "plan" $?
"$OM/bin/python" "$H/check_placement.py" /root/rc1-placement.json
check "exclusive 181/182 tensor ownership" $?

say "dual-GPU run"
openmycelium run --model "$MODEL" \
  --prompt "Explain cross-vendor GPU inference in one sentence." \
  --max-new-tokens 24 > "$LOG/run.log" 2>&1
RC=$?
sed -n '/model  /,$p' "$LOG/run.log" | head -18 | sed 's/^/    /'
[ "$RC" = "0" ]
check "openmycelium run" $?

say "multi-turn chat"
printf 'Remember the number 4471.\nWhat number did I give you?\n/bye\n' \
  | openmycelium chat --model "$MODEL" --max-new-tokens 24 > "$LOG/chat.log" 2>&1
check "openmycelium chat" $?
sed -n '/ready in/,$p' "$LOG/chat.log" | head -10 | sed 's/^/    /'

say "byte-exact boundary qualification"
bash "$H/boundary_exact_clean.sh" rc1-boundary 2>&1 | sed 's/^/  /'
check "boundary is byte-exact" $?

say "audit trail replay"
"$OM/bin/python" "$H/replay_audit.py" 2>&1 | sed 's/^/  /'
check "audit trail replays and validates" $?

printf '\n  %d check(s) failed before serve\n' "$FAIL" | tee -a "$LOG/rc1.log"
echo "$FAIL" > "$LOG/fail.txt"
exit "$FAIL"
