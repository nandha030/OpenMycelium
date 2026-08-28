#!/usr/bin/env bash
# Prove the CUDA wheelhouse offline, to the same standard as the ROCm one.
#
# The ROCm half was proven earlier; describing "the wheelhouse" as proven on
# that basis would have covered 36 of 90 files. This installs the CUDA set into
# a clean prefix with the network refused, and finishes with a real BF16 matmul
# on the RTX 5060 Ti -- because an installed package that cannot reach the card
# is not a provisioned environment.
#
# System-level packages are out of scope by construction: the wheelhouse holds
# Python wheels. The AMD ROCm apt packages are a separate, documented manual
# prerequisite and are not cached here.
set -uo pipefail
WH=/mnt/c/Users/User/Documents/Open_Mycelium/release/wheelhouse
SCRATCH=/opt/wh-cuda-test
LOG=/var/log/om-a8
FAIL=0
mkdir -p "$LOG"
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == verify the CUDA wheelhouse before using it =="
( cd "$WH/cuda" && sha256sum -c SHA256SUMS ) > "$LOG/wh-cuda-verify.txt" 2>&1
bad=$(grep -c FAILED "$LOG/wh-cuda-verify.txt")
ok=$(grep -c ': OK' "$LOG/wh-cuda-verify.txt")
printf '    %s verified, %s failed\n' "$ok" "$bad"
[ "$bad" = "0" ]; check "CUDA wheelhouse intact" $?

echo
echo "  == install into a clean prefix, network refused =="
rm -rf "$SCRATCH"
python3 -m venv "$SCRATCH" > "$LOG/wh-cuda-install.log" 2>&1
S=$(date +%s)
"$SCRATCH/bin/pip" install -q --no-index --find-links "$WH/cuda" \
  torch==2.11.0 transformers==5.15.1 tokenizers==0.22.2 safetensors numpy \
  >> "$LOG/wh-cuda-install.log" 2>&1
RC=$?
E=$(date +%s)
printf '    took %s min %s s\n' "$(( (E-S)/60 ))" "$(( (E-S)%60 ))"
[ "$RC" = "0" ]; check "pip installed the whole CUDA set with --no-index" $?
[ "$RC" = "0" ] || tail -8 "$LOG/wh-cuda-install.log" | sed 's/^/      /'

if [ "$RC" = "0" ]; then
  n=$("$SCRATCH/bin/pip" list --format=freeze | wc -l)
  printf '    %s packages installed from local files\n' "$n"
  "$SCRATCH/bin/python" -c "
import torch
print(f'    torch        {torch.__version__}')
import sys; sys.exit(0 if torch.__version__ == '2.11.0+cu128' else 1)"
  check "the offline-installed torch is the pinned CUDA build" $?

  echo
  echo "  == a real BF16 operation on the RTX 5060 Ti =="
  "$SCRATCH/bin/python" - <<'PY'
import sys
import torch
ok = torch.cuda.is_available()
print(f"    available     {ok}")
if not ok:
    sys.exit(1)
name = torch.cuda.get_device_name(0)
print(f"    device        {name}")
left = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
right = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
product = left @ right
torch.cuda.synchronize()
print(f"    result device {product.device}  dtype {product.dtype}")
print(f"    finite        {bool(torch.isfinite(product).all().item())}")
print(f"    mean abs      {float(product.abs().mean().item()):.3f}")
print(f"    allocated     {torch.cuda.memory_allocated(0)} bytes")
sys.exit(0 if (product.device.type == "cuda"
               and torch.isfinite(product).all().item()
               and "5060" in name
               and torch.cuda.memory_allocated(0) > 0) else 1)
PY
  check "real BF16 matmul on the RTX 5060 Ti from the offline install" $?
fi

echo
rm -rf "$SCRATCH"
check "scratch environment removed" $?
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
