#!/usr/bin/env bash
# 0.1.0a8 validation. Same validated code as the two 0.1.0a7 builds apart from
# the version string, the provenance metadata and the ledger identity tuple.
#
# No network download: the Python environments already exist, and the wheelhouse
# is exercised separately to prove offline installation works.
set -uo pipefail
REL=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a8
WH=/mnt/c/Users/User/Documents/Open_Mycelium/release/wheelhouse
OM=/opt/om/venv
LOG=/var/log/om-a8
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
LEDGER=/var/lib/openmycelium/xvendor_qualification.json
CONTENT=b430eff6e79cd56b30841716431014c7b566942704b8e4c1df5c9336ad43981a
FAIL=0
mkdir -p "$LOG"
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH OPENMYCELIUM_WSL_DISTRO

say()   { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/a8.log"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/a8.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/a8.log"; FAIL=$((FAIL+1)); fi
}
echo "0.1.0a8 validation $(date -Is) in ${WSL_DISTRO_NAME:-unknown}" > "$LOG/a8.log"

say "installed-wheel test"
( cd "$REL" && sha256sum -c <(grep '\.whl$' SHA256SUMS.frozen) ) > "$LOG/sums.txt" 2>&1
if grep -q FAILED "$LOG/sums.txt"; then false; else true; fi
check "frozen wheels match their recorded SHA-256" $?
"$OM/bin/pip" install -q --upgrade --force-reinstall --no-deps \
  "$REL"/openmycelium-0.1.0a8-py3-none-any.whl >> "$LOG/install.log" 2>&1
check "installed from the frozen wheel" $?
openmycelium version --json > "$LOG/provenance.json" 2>/dev/null
"$OM/bin/python" -c "
import json, sys
r = json.load(open('$LOG/provenance.json'))
print(f\"    version  {r.get('openmycelium')}\")
print(f\"    content  {r.get('installedContentSha256')}\")
h = r.get('hsaRuntime') or {}
print(f\"    hsa      {h.get('flavor')}  {h.get('source')}  {str(h.get('sha256'))[:32]}\")
sys.exit(0 if r.get('installedContentSha256') == '$CONTENT'
         and r.get('openmycelium') == '0.1.0a8' else 1)"
check "installed content equals the frozen 0.1.0a8 build" $?
"$OM/bin/python" -c "
import json, sys
h = (json.load(open('$LOG/provenance.json')).get('hsaRuntime') or {})
sys.exit(0 if h.get('flavor') == 'dxcore' and h.get('source') == 'system-provided' else 1)"
check "provenance records the active HSA runtime and its origin" $?

say "configuration"
openmycelium config > "$LOG/config.log" 2>&1
grep -E 'wsl_distro|cuda_python|rocm_python|state_dir' "$LOG/config.log" | sed 's/^/  /'
grep -q "wsl_distro *${WSL_DISTRO_NAME}.*WSL_DISTRO_NAME" "$LOG/config.log"
check "distribution discovered from WSL_DISTRO_NAME" $?
if grep -qE 'wsl_distro +Ubuntu-24\.04 +default' "$LOG/config.log"; then false; else true; fi
check "no silent Ubuntu-24.04 default" $?

say "both GPU probes"
openmycelium doctor > "$LOG/doctor.log" 2>&1
sed 's/^/  /' "$LOG/doctor.log" | head -12
[ "$(grep -c '\[FAIL\]' "$LOG/doctor.log")" = "0" ]
check "doctor reports no failures" $?
grep -q "NVIDIA GeForce RTX 5060 Ti" "$LOG/doctor.log"
check "CUDA drives the RTX 5060 Ti" $?
grep -q "AMD Radeon RX 9060 XT" "$LOG/doctor.log"
check "ROCm drives the RX 9060 XT" $?

say "idempotent provisioning"
S=$(date +%s)
openmycelium provision > "$LOG/provision.log" 2>&1
RC=$?; E=$(date +%s)
grep -E 'already usable' "$LOG/provision.log" | sed 's/^/    /'
printf '    rerun took %s s\n' "$(( E - S ))"
[ "$RC" = "0" ]; check "provision succeeds" $?
[ "$(grep -c 'already usable' "$LOG/provision.log")" = "2" ]
check "both environments already usable" $?
if grep -qE 'Downloading|Collecting' "$LOG/provision.log"; then false; else true; fi
check "nothing was downloaded" $?

say "ledger identity tuple"
"$OM/bin/python" -c "
import json, sys
led = json.load(open('$LEDGER'))
q = led['provision']['environments'].get('rocmLastQualified') or {}
ident = q.get('identity') or {}
need = ['wslDistro','rocmPython','stateDir','torchVersion','torchLibSha256',
        'rocmSystemVersion','amdPciId','deviceName']
for k in need:
    print(f'    {k:<20} {str(ident.get(k))[:56]}')
missing = [k for k in need if not ident.get(k)]
print(f'    hsa sha256           {str((q.get(\"activeHsaRuntime\") or {}).get(\"sha256\"))[:32]}')
if missing: print(f'    MISSING: {missing}')
sys.exit(1 if missing else 0)"
check "qualification is tied to the full identity tuple" $?

printf '\n  %d check(s) failed so far\n' "$FAIL" | tee -a "$LOG/a8.log"
echo "$FAIL" > "$LOG/fail.txt"
exit "$FAIL"
