#!/usr/bin/env bash
# Read-only progress view of a running bootstrap. Safe to call at any time.
LOG=/var/log/om-bootstrap
tail -30 "$LOG/bootstrap.log" 2>/dev/null
echo
echo "  -- provisioning --"
if [ -f "$LOG/provision.log" ]; then
  tail -6 "$LOG/provision.log" | sed 's/^/    /'
fi
for v in cuda rocm; do
  D=/var/lib/openmycelium/state/env/$v
  if [ -d "$D" ]; then
    printf '    %-5s %s\n' "$v" "$(du -sh "$D" 2>/dev/null | cut -f1) on disk"
  fi
done
echo "  -- network since boot --"
awk '/eth0/ {printf "    eth0 rx %.2f GiB\n", $2/1073741824}' /proc/net/dev
