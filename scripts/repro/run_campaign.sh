#!/usr/bin/env bash
# The throughput campaign of record, bracketed by invariant snapshots.
#
#   invariants -> byte-exact qualification -> 5 sessions -> qualification -> invariants
#
# The campaign is one claim assembled from seven executions. It is only a single
# result if they all ran against the same system, so the snapshots are taken
# either side and compared. If anything moved -- a reboot, a reinstall, a
# changed model -- the campaign is split or rerun rather than averaged.
set -uo pipefail
H=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
LOG=/var/log/om-a8
OM=/opt/om/venv
mkdir -p "$LOG"
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
OM_TOKEN=$(/opt/om/venv/bin/python -c "import secrets; print(secrets.token_hex(16))")
export OM_TOKEN
cd /root || exit 1

echo "  == invariants, before =="
"$OM/bin/python" "$H/campaign_invariants.py" > "$LOG/invariants-before.json"
"$OM/bin/python" -c "
import json
r = json.load(open('$LOG/invariants-before.json'))
print(f\"    boot {r['bootId']}  uptime {r['uptimeSeconds']} s\")"

"$OM/bin/python" "$H/throughput2.py"
RC=$?

echo
echo "  == invariants, after =="
"$OM/bin/python" "$H/campaign_invariants.py" > "$LOG/invariants-after.json"
"$OM/bin/python" "$H/compare_invariants.py" \
  "$LOG/invariants-before.json" "$LOG/invariants-after.json"
IRC=$?

echo
if [ "$RC" = "0" ] && [ "$IRC" = "0" ]; then
  echo "  campaign valid: measurements clean and every invariant held"
else
  echo "  campaign NOT valid (throughput rc=$RC, invariants rc=$IRC)"
fi
exit $(( RC + IRC ))
