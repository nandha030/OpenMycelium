#!/usr/bin/env bash
echo "=== /dev/dxg ==="
ls -l /dev/dxg 2>&1
echo
echo "=== ldconfig: rocdxg / dxcore / hsa-runtime ==="
ldconfig -p | grep -E 'rocdxg|dxcore|hsa-runtime|amdhip|amd_comgr' 2>&1 || echo "(none registered)"
echo
echo "=== WSL lib passthrough: dxcore ==="
ls -l /usr/lib/wsl/lib/libdxcore.so* 2>&1
echo
echo "=== is ROCm installed at all? ==="
ls -ld /opt/rocm* 2>&1 || echo "no /opt/rocm"
command -v rocminfo rocm-smi 2>&1 || echo "no rocm tools on PATH"
echo
echo "=== full WSL lib passthrough (looking for AMD entries) ==="
ls /usr/lib/wsl/lib/ 2>/dev/null | grep -iE 'amd|rocm|dx' || echo "(no amd/rocm entries)"
echo
echo "=== amdgpu / dri devices ==="
ls -l /dev/dri 2>&1 | head -5
