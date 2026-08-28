#!/usr/bin/env bash
export PATH=/opt/rocm/bin:$PATH
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
echo "=== compiling with hipcc for gfx1200 ==="
/opt/rocm/bin/hipcc --offload-arch=gfx1200 "$S/hip_smoke.cpp" -o /tmp/hip_smoke 2>&1 | head -15
[ -x /tmp/hip_smoke ] || { echo "COMPILE FAILED"; exit 1; }
echo "compiled"
echo "=== running on the RX 9060 XT ==="
/tmp/hip_smoke
echo "exit: $?"
