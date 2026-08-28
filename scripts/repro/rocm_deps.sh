#!/usr/bin/env bash
# What does the working ROCm environment actually depend on outside its venv?
#
# The fresh distribution has no libhsa-runtime64.so.1 projected in by WSL, only
# NVIDIA libraries. If the working ROCm stack needs system packages that
# `openmycelium provision` does not install, then provisioning is incomplete and
# the clean bootstrap will fail at the AMD probe. Better to know now.
echo "  == system-level ROCm on this working distro =="
for p in /opt/rocm /opt/rocm-7.0.0 /usr/lib/x86_64-linux-gnu/libhsa-runtime64.so.1; do
  if [ -e "$p" ]; then echo "    present  $p"; else echo "    absent   $p"; fi
done
echo
echo "  == ROCm-related apt packages installed =="
dpkg -l 2>/dev/null | awk '/rocm|hsa|amdgpu|hip/ {printf "    %-34s %s\n", $2, $3}' | head -20
if ! dpkg -l 2>/dev/null | grep -qE 'rocm|hsa|amdgpu|hip'; then
  echo "    none -- nothing ROCm was installed through apt"
fi
echo
echo "  == where the venv finds its HSA runtime =="
find /opt/rocmenv -name 'libhsa-runtime64*' 2>/dev/null | sed 's/^/    /' | head
echo
echo "  == what torch's HIP library actually links against =="
LIB=$(find /opt/rocmenv -name 'libtorch_hip.so' 2>/dev/null | head -1)
echo "    $LIB"
if [ -n "$LIB" ]; then
  ldd "$LIB" 2>/dev/null | awk '/not found/ {printf "    MISSING %s\n", $1}'
  ldd "$LIB" 2>/dev/null | awk '/hsa|amd|dxcore/ {printf "    %s\n", $0}' | head -8
fi
echo
echo "  == WSL-projected libraries on the working distro =="
ls /usr/lib/wsl/lib 2>/dev/null | grep -iE 'hsa|amd|dxcore' | sed 's/^/    /' \
  || echo "    no AMD-specific library projected here either"
