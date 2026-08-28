#!/usr/bin/env bash
# Compare the HSA runtime pip ships against the one the working environment
# actually loads.
#
# The earlier check compared libhsa-runtime64.so.1 with its own .orig backup and
# found them identical, and I read that as "nothing was replaced". That was the
# wrong pair: if the replacement procedure ran twice, the second run overwrites
# the backup with the replacement, so both copies are the WSL build and pip's
# original is gone. The comparison that answers the question is pip's file in
# the fresh environment against the loaded file in the working one.
F=$1
echo "  $F"
for n in libhsa-runtime64.so libhsa-runtime64.so.1; do
  P="$F/$n"
  if [ -e "$P" ]; then
    printf '    %-28s %9s bytes  %s\n' "$n" "$(stat -Lc%s "$P")" \
      "$(sha256sum -b "$P" | cut -c1-16)"
    printf '    %-28s /dev/kfd refs %s\n' "" "$(strings "$P" | grep -c '/dev/kfd')"
    printf '    %-28s dxcore refs   %s\n' "" "$(strings "$P" | grep -ci 'dxcore')"
    printf '    %-28s links: %s\n' "" \
      "$(ldd "$P" 2>/dev/null | awk '/dxcore|drm/ {printf "%s ", $1}')"
  else
    printf '    %-28s absent\n' "$n"
  fi
done
