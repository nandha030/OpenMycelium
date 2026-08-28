#!/usr/bin/env bash
# Everything that must hold once provisioning reports success.
#
# Runs as a separate stage from the bootstrap so that "pip finished" is never
# what closes the gate. The gate is closed by two GPUs each doing real work,
# a rerun that changes nothing, and a reboot that changes nothing.
set -uo pipefail

LOG=/var/log/om-bootstrap
OM=/opt/om/venv
H=${OM_HARNESS:-/root/repro}
CUDA_ENV=/var/lib/openmycelium/state/env/cuda
ROCM_ENV=/var/lib/openmycelium/state/env/rocm
FAIL=0
# "probe" stops after the package inventory and the two GPU probes, so that
# result can be reported before idempotency is attempted. "all" continues.
STAGE=${1:-all}
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1

say()   { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/post.log"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/post.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/post.log"; FAIL=$((FAIL+1)); fi
}

echo "post-provision $(date -Is)" > "$LOG/post.log"

# ------------------------------------------------- installed package inventory
say "G1  installed packages and their source index"
for pair in "cuda $CUDA_ENV" "rocm $ROCM_ENV"; do
  vendor=${pair%% *}; env=${pair#* }
  "$env/bin/pip" list --format=freeze > "$LOG/packages-$vendor.txt" 2>/dev/null
  n=$(wc -l < "$LOG/packages-$vendor.txt")
  printf '  %s: %s packages\n' "$vendor" "$n" | tee -a "$LOG/post.log"
  # torch carries a local version tag naming the vendor index it came from;
  # everything else has a plain version, which is what PyPI serves.
  grep -E '^(torch|pytorch-triton)' "$LOG/packages-$vendor.txt" \
    | sed 's/^/      /' | tee -a "$LOG/post.log"
done
grep -q '^torch==2\.11\.0+cu128$' "$LOG/packages-cuda.txt"
check "CUDA environment holds torch 2.11.0+cu128 from the vendor index" $?
grep -q '^torch==2\.10\.0+rocm7\.0$' "$LOG/packages-rocm.txt"
check "ROCm environment holds torch 2.10.0+rocm7.0 from the vendor index" $?
for vendor in cuda rocm; do
  grep -q '^transformers==5\.15\.1$' "$LOG/packages-$vendor.txt"
  check "$vendor environment holds transformers 5.15.1 from PyPI" $?
done

# ----------------------------------------------------------------- GPU probes
say "G2  each environment drives its own GPU"
"$CUDA_ENV/bin/python" "$H/gpu_identity.py" cuda > "$LOG/gpu-cuda.json" 2> "$LOG/gpu-cuda.err"
CUDA_RC=$?
"$ROCM_ENV/bin/python" "$H/gpu_identity.py" rocm > "$LOG/gpu-rocm.json" 2> "$LOG/gpu-rocm.err"
ROCM_RC=$?
"$OM/bin/python" "$H/report_gpu.py" "$LOG/gpu-cuda.json" "$LOG/gpu-rocm.json" \
  | tee -a "$LOG/post.log"
GATE_RC=$?
[ "$CUDA_RC" = "0" ]
check "CUDA environment qualified (real BF16 on the device, CUDA build)" $?
[ "$ROCM_RC" = "0" ]
check "ROCm environment qualified (real BF16 on the device, HIP build)" $?
[ "$GATE_RC" = "0" ]
check "device identities match the expected cards, no fallback" $?

if [ "$STAGE" = "probe" ]; then
  printf '\n  %d check(s) failed in G1-G2\n' "$FAIL" | tee -a "$LOG/post.log"
  echo "$FAIL" > "$LOG/postfail.txt"
  exit "$FAIL"
fi

# ---------------------------------------------------------------- idempotency
say "G3  provision is idempotent"
"$OM/bin/python" "$H/fingerprint.py" "$CUDA_ENV" cuda-before > "$LOG/fp-cuda-1.json"
"$OM/bin/python" "$H/fingerprint.py" "$ROCM_ENV" rocm-before > "$LOG/fp-rocm-1.json"
S=$(date +%s)
openmycelium provision > "$LOG/provision-rerun.log" 2>&1
RC=$?
E=$(date +%s)
printf '  rerun took %s s\n' "$(( E - S ))" | tee -a "$LOG/post.log"
grep -E 'already usable' "$LOG/provision-rerun.log" | sed 's/^/    /' | tee -a "$LOG/post.log"
[ "$RC" = "0" ]
check "rerun succeeded" $?
[ "$(grep -c 'already usable' "$LOG/provision-rerun.log")" = "2" ]
check "rerun reported both environments already usable" $?
if grep -qE 'installing|Downloading|Collecting' "$LOG/provision-rerun.log"; then false; else true; fi
check "rerun installed nothing" $?
[ "$(( E - S ))" -lt 120 ]
check "rerun completed quickly, consistent with no reinstall" $?

"$OM/bin/python" "$H/fingerprint.py" "$CUDA_ENV" cuda-after > "$LOG/fp-cuda-2.json"
"$OM/bin/python" "$H/fingerprint.py" "$ROCM_ENV" rocm-after > "$LOG/fp-rocm-2.json"
"$OM/bin/python" "$H/compare_fingerprints.py" \
  "$LOG/fp-cuda-1.json" "$LOG/fp-cuda-2.json" \
  "$LOG/fp-rocm-1.json" "$LOG/fp-rocm-2.json" | tee -a "$LOG/post.log"
check "environment fingerprints unchanged by the rerun" $?

printf '\n  %d check(s) failed in the post-provision gate\n' "$FAIL" | tee -a "$LOG/post.log"
echo "$FAIL" > "$LOG/postfail.txt"
exit "$FAIL"
