#!/usr/bin/env bash
# G4: the environments still drive their GPUs after the VM has been torn down
# and rebuilt.
#
# WSL discards the whole utility VM on shutdown, so this re-establishes /dev/dxg
# and re-opens both drivers from cold. An environment that only worked because
# something was already resident in memory fails here.
set -uo pipefail
LOG=/var/log/om-bootstrap
OM=/opt/om/venv
H=${OM_HARNESS:-/root/repro}
CUDA_ENV=/var/lib/openmycelium/state/env/cuda
ROCM_ENV=/var/lib/openmycelium/state/env/rocm
FAIL=0
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1

check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/reboot.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/reboot.log"; FAIL=$((FAIL+1)); fi
}

{
  echo "reboot gate $(date -Is)"
  echo "  uptime      $(cut -d. -f1 /proc/uptime) s since this VM booted"
  echo "  boot id     $(cat /proc/sys/kernel/random/boot_id)"
} | tee "$LOG/reboot.log"

[ "$(cut -d. -f1 /proc/uptime)" -lt 600 ]
check "the VM really did restart (uptime under ten minutes)" $?

[ -e /dev/dxg ]
check "/dev/dxg reappeared after the restart" $?

"$CUDA_ENV/bin/python" "$H/gpu_identity.py" cuda > "$LOG/gpu-cuda-reboot.json" 2>/dev/null
check "CUDA environment qualified after restart" $?
"$ROCM_ENV/bin/python" "$H/gpu_identity.py" rocm > "$LOG/gpu-rocm-reboot.json" 2>/dev/null
check "ROCm environment qualified after restart" $?

"$OM/bin/python" "$H/report_gpu.py" \
  "$LOG/gpu-cuda-reboot.json" "$LOG/gpu-rocm-reboot.json" | tee -a "$LOG/reboot.log"
check "device identities unchanged after restart" $?

openmycelium doctor > "$LOG/doctor-reboot.log" 2>&1
sed 's/^/    /' "$LOG/doctor-reboot.log" | tee -a "$LOG/reboot.log"
[ "$(grep -c '\[FAIL\]' "$LOG/doctor-reboot.log")" = "0" ]
check "doctor reports no failures after restart" $?

printf '\n  %d check(s) failed in the reboot gate\n' "$FAIL" | tee -a "$LOG/reboot.log"
exit "$FAIL"
