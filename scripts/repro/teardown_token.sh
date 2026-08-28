#!/usr/bin/env bash
# Destroy the smoke token and stop the endpoint until the smoke actually runs.
#
# A secret that exists for hours before it is needed is a secret with a much
# larger window than it needs. It is cheap to mint another.
set -uo pipefail
OM=/opt/om/venv
SECRET=/run/openmycelium/openwebui.token
LOG=/var/log/om-webui
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1

echo "  == stop the endpoint =="
"$OM/bin/openmycelium" stop > /dev/null 2>&1
sleep 4
LEFT=$(pgrep -fc "pipeline_run" 2>/dev/null); LEFT=${LEFT:-0}
echo "    worker processes remaining: $LEFT"

echo
echo "  == destroy the token =="
if [ -f "$SECRET" ]; then
  shred -u "$SECRET" 2>/dev/null || rm -f "$SECRET"
fi
[ -e "$SECRET" ] && echo "    STILL PRESENT" || echo "    $SECRET destroyed"
rmdir /run/openmycelium 2>/dev/null && echo "    /run/openmycelium removed"

echo
echo "  == no token anywhere on disk =="
for d in /var/log /run /tmp /root; do
  found=$(grep -rlE '^[0-9a-f]{48}$' "$d" 2>/dev/null | head -3)
  [ -n "$found" ] && echo "    stray in $d: $found" || echo "    $d clean"
done

echo
echo "  == GPU released =="
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    CUDA in use: /'
