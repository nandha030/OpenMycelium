#!/usr/bin/env bash
# Confirm a fresh distribution can actually reach both GPUs before committing
# to a two-hour provisioning run. The driver libraries are projected into the
# distribution by WSL, not installed by us, so their absence would make the
# whole bootstrap fail at the last step instead of the first.
echo "  /usr/lib/wsl/lib contents:"
ls /usr/lib/wsl/lib 2>/dev/null | sed 's/^/    /' || echo "    DIRECTORY MISSING"
echo
echo "  the two libraries that matter:"
for lib in libcuda.so.1 libdxcore.so libhsa-runtime64.so.1; do
  if [ -e "/usr/lib/wsl/lib/$lib" ]; then
    echo "    present  $lib"
  else
    echo "    absent   $lib"
  fi
done
echo
echo "  ld.so configuration for the WSL library path:"
grep -rl "wsl/lib" /etc/ld.so.conf.d/ 2>/dev/null | sed 's/^/    /' || echo "    not on the loader path"
echo
echo "  disk:"
df -h / | tail -1 | awk '{printf "    %s free of %s on /\n", $4, $2}'
df -h /mnt/c | tail -1 | awk '{printf "    %s free of %s on /mnt/c\n", $4, $2}'
echo
echo "  memory:"
free -g | awk '/^Mem:/ {printf "    %s GiB total, %s GiB available\n", $2, $7}'
