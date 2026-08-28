#!/usr/bin/env bash
# Step 15 and the cleanup the smoke promised: lifecycle, then destroy the
# temporary token. The container and volume are removed separately, from the
# Windows side, after the persistence evidence has been recorded.
set -uo pipefail
OM=/opt/om/venv
SECRET=/run/openmycelium/openwebui.token
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == 15. ps =="
openmycelium ps 2>&1 | sed 's/^/    /'
check "openmycelium ps" $?

echo
echo "  == 15. stop =="
openmycelium stop 2>&1 | tail -3 | sed 's/^/    /'
check "openmycelium stop" $?
sleep 5

ORPHANS=$(pgrep -fc "pipeline_run" 2>/dev/null); ORPHANS=${ORPHANS:-0}
echo "    worker processes remaining: $ORPHANS"
[ "$ORPHANS" = "0" ]
check "zero orphan workers" $?

echo
echo "  == VRAM released =="
nvidia-smi --query-gpu=name,memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    /'
USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
[ "${USED:-99999}" -lt 3000 ]
check "CUDA VRAM released (under 3 GiB in use)" $?

echo
echo "  == destroy the temporary token =="
[ -f "$SECRET" ] && { shred -u "$SECRET" 2>/dev/null || rm -f "$SECRET"; }
[ -e "$SECRET" ] && echo "    STILL PRESENT" || echo "    token destroyed"
rmdir /run/openmycelium 2>/dev/null && echo "    /run/openmycelium removed"
rm -f /root/webui.jwt /tmp/webui.jwt
for d in /var/log /run /tmp /root; do
  hit=$(grep -rlE '^[0-9a-f]{48}$' "$d" 2>/dev/null | head -2)
  [ -n "$hit" ] && echo "    stray secret in $d: $hit" || echo "    $d clean"
done

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
