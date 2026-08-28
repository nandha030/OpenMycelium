#!/usr/bin/env bash
# Verify the reinstall-detection fix from source, before deciding what to build.
#
# Sequence: qualify (which now records the working runtime in the ledger),
# restore pip's /dev/kfd runtime the way a torch upgrade would, and check that
# provision says "this regressed" rather than "this was never set up".
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli
LOG=/var/log/om-a7
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
LEDGER=/var/lib/openmycelium/xvendor_qualification.json
FAIL=0
cd "$SRC" || exit 1
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}
prov() { /opt/om/venv/bin/python "$SRC/provision.py" "$@"; }

echo "  == 1. qualify, and record the working runtime =="
prov > "$LOG/src-qualify.log" 2>&1
check "provision succeeds" $?
/opt/om/venv/bin/python -c "
import json
led = json.load(open('$LEDGER'))
env = led['provision']['environments']
q = env.get('rocmLastQualified') or {}
r = (q.get('activeHsaRuntime') or {})
print(f\"    rocmLastQualified recorded: {bool(q)}\")
print(f\"      flavour {r.get('flavor')}  sha256 {str(r.get('sha256'))[:32]}\")
import sys; sys.exit(0 if q.get('gpuQualified') and r.get('flavor')=='dxcore' else 1)"
check "the ledger holds the qualified WSL runtime" $?

echo
echo "  == 2. restore pip's /dev/kfd runtime, as a torch upgrade would =="
cp -f "$T/libhsa-runtime64.so.1" /tmp/wsl-good.so
cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so.1"
cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so"
printf '    now %s bytes (pip original)\n' "$(stat -c%s "$T/libhsa-runtime64.so.1")"

prov > "$LOG/src-regressed.log" 2>&1
RC=$?
sed -n '/rocm environment/,/^  *$/p' "$LOG/src-regressed.log" | head -20 | sed 's/^/    /'
[ "$RC" != "0" ]
check "provision refuses" $?
grep -q "A torch reinstall or upgrade replaced the WSL HSA runtime" "$LOG/src-regressed.log"
check "it identifies a torch reinstall as the cause, not a missing setup" $?
grep -q "system components are still" "$LOG/src-regressed.log"
check "it states the system components are still installed" $?
if grep -q "will not add system repositories" "$LOG/src-regressed.log"; then false; else true; fi
check "it does NOT tell the user to install system components again" $?

echo
echo "  == 3. restore =="
cp -f /tmp/wsl-good.so "$T/libhsa-runtime64.so.1"
cp -f /tmp/wsl-good.so "$T/libhsa-runtime64.so"
prov > "$LOG/src-restored.log" 2>&1
check "provision succeeds again" $?
grep -E 'already usable' "$LOG/src-restored.log" | sed 's/^/    /'

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
