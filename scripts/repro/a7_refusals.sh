#!/usr/bin/env bash
# Both refusal paths, from the rebuilt wheel, on a machine where the AMD system
# runtime IS installed.
#
#   A. no prior qualification recorded  -> "system-runtime-missing", full
#      remediation including the repository steps
#   B. a prior qualification recorded   -> "incompatible-runtime-restored",
#      remediation that skips the repository steps because the system
#      components are still there
#
# Distinguishing these is the point: telling somebody to reinstall ROCm when a
# single file was overwritten wastes an afternoon.
set -uo pipefail
LOG=/var/log/om-a7
OM=/opt/om/venv
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
LEDGER=/var/lib/openmycelium/xvendor_qualification.json
FAIL=0
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}
break_runtime() {
  cp -f "$T/libhsa-runtime64.so.1" /tmp/wsl-good.so
  cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so.1"
  cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so"
}
fix_runtime() {
  cp -f /tmp/wsl-good.so "$T/libhsa-runtime64.so.1"
  cp -f /tmp/wsl-good.so "$T/libhsa-runtime64.so"
}

echo "  == establish a qualified baseline and record it =="
openmycelium provision > "$LOG/w-qualify.log" 2>&1
check "provision qualifies both environments" $?
grep -E 'already usable|matmul finite' "$LOG/w-qualify.log" | sed 's/^/    /'

echo
echo "  == path A: no prior qualification (a machine being set up) =="
cp -f "$LEDGER" /tmp/ledger.bak
rm -f "$LEDGER"
break_runtime
openmycelium provision > "$LOG/w-pathA.log" 2>&1
[ "$?" != "0" ]; check "refuses" $?
grep -q "ROCm WSL system runtime          no" "$LOG/w-pathA.log"
check "A: reports the system runtime as missing" $?
grep -q "amdgpu-install --usecase=wsl,rocm --no-dkms" "$LOG/w-pathA.log"
check "A: gives the full remediation, including the repository steps" $?
if grep -q "A torch reinstall or upgrade" "$LOG/w-pathA.log"; then false; else true; fi
check "A: does NOT claim a reinstall caused it" $?

echo
echo "  == path B: previously qualified (a machine that regressed) =="
cp -f /tmp/ledger.bak "$LEDGER"
openmycelium provision > "$LOG/w-pathB.log" 2>&1
[ "$?" != "0" ]; check "refuses" $?
grep -q "A torch reinstall or upgrade replaced the WSL HSA runtime" "$LOG/w-pathB.log"
check "B: identifies a torch reinstall as the cause" $?
grep -q "system components are still" "$LOG/w-pathB.log"
check "B: states the system components are still installed" $?
if grep -q "amdgpu-install --usecase=wsl,rocm --no-dkms" "$LOG/w-pathB.log"; then false; else true; fi
check "B: does NOT repeat the repository installation steps" $?
sed -n '/A torch reinstall/,/openmycelium provision  /p' "$LOG/w-pathB.log" \
  | head -12 | sed 's/^/    /'

echo
echo "  == restore =="
fix_runtime
openmycelium provision > "$LOG/w-restored.log" 2>&1
check "provision succeeds once the WSL runtime is back" $?
grep -E 'already usable' "$LOG/w-restored.log" | sed 's/^/    /'

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
