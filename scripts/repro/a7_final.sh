#!/usr/bin/env bash
# Close out the 0.1.0a7 gate: confirm the two harness-fixed checks, then test
# the reinstall detection, which is the one 0.1.0a7 requirement not yet
# exercised.
set -uo pipefail
LOG=/var/log/om-a7
OM=/opt/om/venv
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
FAIL=0
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == leftovers, counted correctly =="
ORPHANS=$(pgrep -fc "pipeline_run" 2>/dev/null); ORPHANS=${ORPHANS:-0}
printf '    worker processes: %s\n' "$ORPHANS"
[ "$ORPHANS" = "0" ]
check "no orphan workers after serve and stop" $?
FREE=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null)
printf '    CUDA memory in use: %s\n' "${FREE:-unknown}"

echo
echo "  == GPU still qualified after the whole chain =="
openmycelium doctor 2>&1 | grep -E '\[PASS\]|\[FAIL\]|ready' | sed 's/^/    /'
openmycelium doctor > /tmp/doc.log 2>&1
[ "$(grep -c '\[FAIL\]' /tmp/doc.log)" = "0" ]
check "doctor still reports no failures" $?

echo
echo "  == reinstall detection: put the /dev/kfd runtime back =="
# This is what `pip install --upgrade torch` does to the environment. The
# backups beside the live files are pip's originals, so restoring them
# reproduces the post-upgrade state exactly, without downloading anything.
for n in libhsa-runtime64.so libhsa-runtime64.so.1; do
  if [ -e "$T/$n.orig" ]; then
    cp -f "$T/$n.orig" "$T/$n.restored-wsl" 2>/dev/null || true
  fi
done
cp -f "$T/libhsa-runtime64.so.1" /tmp/wsl-runtime.so.1        # keep the good one
cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so"
cp -f "$T/libhsa-runtime64.so.orig" "$T/libhsa-runtime64.so.1"
printf '    restored pip original: %s bytes\n' "$(stat -c%s "$T/libhsa-runtime64.so.1")"

openmycelium provision > "$LOG/provision-regressed.log" 2>&1
RC=$?
sed -n '/rocm environment/,/^$/p' "$LOG/provision-regressed.log" | head -14 | sed 's/^/    /'
[ "$RC" != "0" ]
check "provision refuses after the runtime was replaced" $?
grep -qE "incompatible-runtime-restored|qualified previously" "$LOG/provision-regressed.log"
check "it identifies this as a restored incompatible runtime, not a fresh setup" $?
grep -q "flavour            kfd" "$LOG/provision-regressed.log"
check "it names the flavour that came back" $?

echo
echo "  == restore the working runtime =="
cp -f /tmp/wsl-runtime.so.1 "$T/libhsa-runtime64.so.1"
cp -f /tmp/wsl-runtime.so.1 "$T/libhsa-runtime64.so"
openmycelium provision > "$LOG/provision-restored.log" 2>&1
check "provision succeeds again once the WSL runtime is back" $?
grep -E 'already usable' "$LOG/provision-restored.log" | sed 's/^/    /'

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
