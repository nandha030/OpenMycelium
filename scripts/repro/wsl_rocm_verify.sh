#!/usr/bin/env bash
echo "=== which libhsa-runtime64 candidates exist ==="
ldconfig -p | grep libhsa-runtime64
echo
echo "=== does the WSL runtime provide DXG symbols? ==="
for L in /lib/x86_64-linux-gnu/libhsa-runtime64.so.1 /opt/rocm-7.2.0/lib/libhsa-runtime64.so.1; do
  [ -f "$L" ] || continue
  printf '%-55s ' "$L"
  if nm -D "$L" 2>/dev/null | grep -q hsaKmtOpenKFD; then echo -n "hsaKmtOpenKFD:defined "; else echo -n "hsaKmtOpenKFD:UNDEF "; fi
  dpkg -S "$L" 2>/dev/null | cut -d: -f1 || echo "(unowned)"
done
echo
echo "=== rocminfo (plain) -- GPU agent ==="
/opt/rocm/bin/rocminfo 2>/dev/null | grep -E 'Name:|Marketing|Compute Unit|Uuid' | head -12
echo
echo "=== hipconfig --full (abridged) ==="
/opt/rocm/bin/hipconfig --full 2>&1 | grep -iE 'HIP version|HIP_PLATFORM|ROCM_PATH|HIP_COMPILER|Target|GPU|arch' | head -12
echo
echo "=== nvidia-smi still healthy ==="
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
