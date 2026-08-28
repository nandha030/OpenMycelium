#!/usr/bin/env bash
# What exactly did the working distribution install to get ROCm on WSL?
#
# Replicating the same versions is better than picking the latest: this pair is
# already known to drive an RX 9060 XT through /dev/dxg on this machine.
echo "  == amdgpu-install package =="
dpkg -s amdgpu-install 2>/dev/null | grep -E '^(Package|Version|Maintainer)' \
  | sed 's/^/    /'

echo
echo "  == apt sources naming AMD =="
grep -rEl 'repo\.radeon\.com|amdgpu' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null \
  | while read -r f; do
      echo "    $f"
      grep -vE '^\s*#|^\s*$' "$f" | sed 's/^/      /'
    done

echo
echo "  == signing keys =="
ls -1 /etc/apt/keyrings/*rocm* /etc/apt/trusted.gpg.d/*rocm* 2>/dev/null | sed 's/^/    /'
for k in /etc/apt/keyrings/rocm.gpg /etc/apt/keyrings/rocm.asc; do
  [ -e "$k" ] && printf '    %s  sha256 %s\n' "$k" "$(sha256sum "$k" | cut -c1-32)"
done

echo
echo "  == installed ROCm version =="
cat /opt/rocm/.info/version 2>/dev/null | sed 's/^/    /' || echo "    (no version file)"
dpkg -l 2>/dev/null | awk '/rocm-core/ {printf "    rocm-core %s\n", $3}'

echo
echo "  == the file that matters, and where it came from =="
R=/opt/rocm/lib/libhsa-runtime64.so.1
if [ -e "$R" ]; then
  printf '    %s\n      %s bytes  sha256 %s\n' "$R" "$(stat -Lc%s "$R")" \
    "$(sha256sum -b "$R" | cut -c1-32)"
  printf '      owned by package: %s\n' "$(dpkg -S "$(readlink -f "$R")" 2>/dev/null | cut -d: -f1)"
fi

echo
echo "  == disk used by the ROCm system components =="
du -sh /opt/rocm 2>/dev/null | sed 's/^/    /'
