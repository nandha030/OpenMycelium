#!/usr/bin/env bash
# Is the HSA runtime swap load-bearing, or a leftover experiment?
#
# torch's ROCm wheel ships libhsa-runtime64.so.1 built to talk to /dev/kfd. WSL
# has no /dev/kfd, only /dev/dxg. If the live library differs from the .orig
# beside it, then somebody replaced the shipped runtime with a WSL build, and
# `pip install torch` alone can never reproduce this environment.
D=/opt/rocmenv/lib/python3.12/site-packages/torch/lib
cd "$D" || exit 1
echo "  == the shipped runtime versus the one actually loaded =="
for f in libhsa-runtime64.so.1 libhsa-runtime64.so.1.orig; do
  printf '    %-34s %10s bytes  %s\n' "$f" "$(stat -c%s "$f")" \
    "$(sha256sum "$f" | cut -c1-16)"
done
echo
if cmp -s libhsa-runtime64.so.1 libhsa-runtime64.so.1.orig; then
  echo "    identical -- the swap is NOT load-bearing"
else
  echo "    DIFFERENT -- the shipped runtime was replaced"
fi
echo
echo "  == which device node each one reaches for =="
for f in libhsa-runtime64.so.1 libhsa-runtime64.so.1.orig; do
  kfd=$(strings "$f" 2>/dev/null | grep -c '/dev/kfd')
  dxg=$(strings "$f" 2>/dev/null | grep -c 'dxcore\|/dev/dxg')
  printf '    %-34s /dev/kfd refs %-4s dxcore refs %s\n' "$f" "$kfd" "$dxg"
done
echo
echo "  == is the live library a symlink or a copy from the system ROCm? =="
ls -l libhsa-runtime64.so.1 | sed 's/^/    /'
SYS=/usr/lib/x86_64-linux-gnu/libhsa-runtime64.so.1
if [ -e "$SYS" ]; then
  if cmp -s libhsa-runtime64.so.1 "$SYS"; then
    echo "    the live library is a copy of the system ROCm runtime ($SYS)"
  else
    echo "    the live library differs from the system ROCm runtime too"
    printf '    system: %10s bytes  %s\n' "$(stat -c%s "$SYS")" \
      "$(sha256sum "$SYS" | cut -c1-16)"
  fi
fi
