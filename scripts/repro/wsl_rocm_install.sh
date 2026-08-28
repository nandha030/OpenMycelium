#!/usr/bin/env bash
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
cd /tmp

echo "=== [1/4] amdgpu-install package (ROCm 7.2, noble) ==="
wget -q https://repo.radeon.com/amdgpu-install/7.2/ubuntu/noble/amdgpu-install_7.2.70200-1_all.deb -O amdgpu-install.deb
echo "download exit: $?  size: $(du -h amdgpu-install.deb 2>/dev/null | cut -f1)"
apt-get install -y -qq ./amdgpu-install.deb 2>&1 | tail -3

echo "=== [2/4] apt update with radeon repo ==="
apt-get update -qq 2>&1 | tail -3

echo "=== [3/4] amdgpu-install --usecase=wsl,rocm --no-dkms ==="
amdgpu-install -y --usecase=wsl,rocm --no-dkms 2>&1 | tail -25
echo "install exit: $?"

echo "=== [4/4] verification ==="
ls -l /opt/rocm/lib/librocdxg.so* 2>&1 | head -3
ldconfig -p | grep -E 'rocdxg|hsa-runtime' | head -5
echo "--- rocminfo ---"
export LD_LIBRARY_PATH=/opt/rocm/lib:${LD_LIBRARY_PATH:-}
/opt/rocm/bin/rocminfo 2>&1 | grep -iE 'Agent|Name:|gfx|Marketing|Uuid' | head -30
echo "--- rocminfo with DXG detection ---"
HSA_ENABLE_DXG_DETECTION=1 /opt/rocm/bin/rocminfo 2>&1 | grep -iE 'gfx|Marketing Name|error|fail' | head -15
