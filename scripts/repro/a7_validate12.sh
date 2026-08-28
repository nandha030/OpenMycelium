#!/usr/bin/env bash
# 0.1.0a7 validation steps 1 and 2, from the installed wheel.
#
#   1. the actual distribution name is discovered
#   2. the missing ROCm system runtime produces the intended refusal
#
# Run in om-clean2, which is the negative control: ROCm Python packages are
# installed and the system runtime is not.
set -uo pipefail
REL=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a7
OM=/opt/om/venv
LOG=/var/log/om-a7
FAIL=0
mkdir -p "$LOG"
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH OPENMYCELIUM_WSL_DISTRO

check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/a7.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/a7.log"; FAIL=$((FAIL+1)); fi
}
say() { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/a7.log"; }

echo "0.1.0a7 validation $(date -Is) in ${WSL_DISTRO_NAME:-unknown}" > "$LOG/a7.log"

say "install the frozen 0.1.0a7 wheel"
"$OM/bin/pip" install -q --upgrade --force-reinstall --no-deps \
  "$REL"/openmycelium-0.1.0a7-py3-none-any.whl >> "$LOG/install.log" 2>&1
check "installed" $?
openmycelium version --json > "$LOG/provenance.json" 2>/dev/null
"$OM/bin/python" -c "
import json
r = json.load(open('$LOG/provenance.json'))
print(f\"    version  {r.get('openmycelium')}\")
print(f\"    content  {r.get('installedContentSha256')}\")
import sys
sys.exit(0 if r.get('installedContentSha256') ==
         'e5f06b44439925943fe1c5c36bd953102213332d8559a190d32421f8ab6e2210' else 1)"
check "installed content equals the frozen 0.1.0a7 build" $?

say "step 1  the actual distribution is discovered"
openmycelium config > "$LOG/config.log" 2>&1
grep -E 'wsl_distro|cuda_python|rocm_python|state_dir' "$LOG/config.log" \
  | sed 's/^/  /' | tee -a "$LOG/a7.log"
grep -q "wsl_distro *${WSL_DISTRO_NAME}" "$LOG/config.log"
check "wsl_distro resolves to ${WSL_DISTRO_NAME}, this distribution" $?
grep -q "wsl_distro.*WSL_DISTRO_NAME" "$LOG/config.log"
check "and records WSL_DISTRO_NAME as the source" $?
if grep -qE 'wsl_distro +Ubuntu-24\.04 +default' "$LOG/config.log"; then false; else true; fi
check "no silent Ubuntu-24.04 default" $?
grep -q "cuda_python.*state/env/cuda" "$LOG/config.log"
check "the provisioned CUDA environment is discovered" $?
grep -q "rocm_python.*state/env/rocm" "$LOG/config.log"
check "the provisioned ROCm environment is discovered" $?

say "step 2  the missing system runtime produces the intended refusal"
openmycelium provision > "$LOG/provision.log" 2>&1
RC=$?
sed -n '/rocm environment/,$p' "$LOG/provision.log" | head -34 \
  | sed 's/^/  /' | tee -a "$LOG/a7.log"
[ "$RC" != "0" ]
check "provision refuses rather than reporting success" $?
grep -q "ROCm Python packages installed   yes" "$LOG/provision.log"
check "reports ROCm Python packages as installed" $?
grep -q "ROCm WSL system runtime          no" "$LOG/provision.log"
check "reports the WSL system runtime as missing" $?
grep -q "ROCm GPU operation qualified     no" "$LOG/provision.log"
check "reports the GPU operation as unqualified" $?
grep -q "flavour            kfd" "$LOG/provision.log"
check "names the active HSA runtime flavour as kfd" $?
grep -q "will not add system" "$LOG/provision.log"
check "states that it will not add system repositories automatically" $?
grep -q "rocm.docs.amd.com" "$LOG/provision.log"
check "cites AMD's official documentation" $?
# A working environment is short-circuited as "already usable" and its probe is
# not re-run, which is the idempotency behaviour. So the CUDA line to look for
# on a rerun is that one, not a fresh GPU operation.
grep -q "already usable NVIDIA GeForce RTX 5060 Ti" "$LOG/provision.log"
check "the CUDA environment is recognised as already usable on the same run" $?

say "doctor says the same thing"
openmycelium doctor > "$LOG/doctor.log" 2>&1
sed 's/^/  /' "$LOG/doctor.log" | head -26 | tee -a "$LOG/a7.log"
grep -q "ROCm WSL system runtime          no" "$LOG/doctor.log"
check "doctor separates the three ROCm states" $?
if grep -q "/opt/models does not exist" "$LOG/doctor.log"; then false; else true; fi
check "doctor no longer reports a hard-coded model store" $?

printf '\n  %d check(s) failed\n' "$FAIL" | tee -a "$LOG/a7.log"
exit "$FAIL"
