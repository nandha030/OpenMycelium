#!/usr/bin/env bash
# Step 3 of the 0.1.0a7 validation: install the AMD ROCm-for-WSL prerequisite
# manually, then follow the substitution the tool's own remediation prints.
#
# This installs the minimal sufficient set rather than AMD's full
# `--usecase=wsl,rocm`: torch's ROCm wheel already bundles hip, comgr, rocblas
# and the rest, and the only thing it cannot carry is the WSL build of the HSA
# runtime. Measured, that is two packages and 0.5 MiB against roughly 30 GB for
# the full usecase. The packages come from AMD's own repository at the versions
# the working distribution runs, and the signing key was verified against it.
set -uo pipefail
LOG=/var/log/om-a7
KNOWN_RUNTIME_SHA=$1        # sha256 of the working distribution's runtime
mkdir -p "$LOG"
export DEBIAN_FRONTEND=noninteractive
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == install the WSL HSA runtime =="
apt-get install -y -qq hsa-runtime-rocr4wsl-amdgpu > "$LOG/amd-install.log" 2>&1
check "hsa-runtime-rocr4wsl-amdgpu installed" $?
dpkg -l 2>/dev/null | awk '/rocm-core|rocr4wsl/ {printf "    %-32s %s\n", $2, $3}'
du -sh --dereference /opt/rocm 2>/dev/null | sed 's/^/    /opt/rocm  /'

echo
echo "  == the runtime it provides =="
SYS=/opt/rocm/lib/libhsa-runtime64.so.1
[ -e "$SYS" ]
check "$SYS exists" $?
if [ -e "$SYS" ]; then
  GOT=$(sha256sum -b "$(readlink -f "$SYS")" | cut -d' ' -f1)
  printf '    sha256 %s\n' "$GOT"
  printf '    known  %s\n' "$KNOWN_RUNTIME_SHA"
  [ "$GOT" = "$KNOWN_RUNTIME_SHA" ]
  check "identical to the runtime on the working distribution" $?
fi

echo
echo "  == substitute it where torch will load it =="
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
for name in libhsa-runtime64.so libhsa-runtime64.so.1; do
  target="$T/$name"
  if [ -e "$target" ] && [ ! -e "$target.orig" ]; then
    cp -a "$target" "$target.orig"
    echo "    backed up $name -> $name.orig"
  fi
  cp -f "$(readlink -f "$SYS")" "$target"
  echo "    installed $name"
done
"$(dirname "$T")/../../../../bin/python" -c "print()" 2>/dev/null || true
NEW=$(sha256sum -b "$T/libhsa-runtime64.so.1" | cut -d' ' -f1)
[ "$NEW" = "$KNOWN_RUNTIME_SHA" ]
check "torch will now load the WSL runtime" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
