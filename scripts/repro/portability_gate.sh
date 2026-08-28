#!/usr/bin/env bash
# The Portability and Provisioning Gate, run entirely from the installed wheel.
#
# Every command is exercised through the installed console script, from a
# directory outside the checkout, with PYTHONPATH cleared. The point is that a
# path baked into the source tree shows up here as a failure rather than at
# somebody else's first run.
set -uo pipefail

VENV=/opt/omfresh/venv
WORK=/opt/omportable
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
FAIL=0

say()   { printf '\n  == %s ==\n' "$1"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

rm -rf "$WORK"; mkdir -p "$WORK"
export PATH="$VENV/bin:$PATH"
unset PYTHONPATH
unset OPENMYCELIUM_STORE OPENMYCELIUM_STATE OM_XVENDOR_LEDGER
export OPENMYCELIUM_STATE="$WORK"
cd "$WORK" || exit 1

say "no machine-specific paths remain in the installed package"
PKG=$("$VENV/bin/python" -c "import openmycelium,os;print(openmycelium.__path__[0])")
# config.py names the historical paths on purpose, in two places: the docstring
# explaining why they stopped being constants, and the discovery table that
# keeps an existing installation working. Anywhere else is a defect. The
# exclusion is by file, not by a substring like "discovery", because matching on
# a word in the line would also hide a real hard-coded path that happened to sit
# near a comment.
HITS=$(grep -rn "Documents/Open_Mycelium\|/opt/hetenv\|/opt/rocmenv" "$PKG" \
       --include='*.py' 2>/dev/null | grep -vc "runtime/cli/config.py:" || true)
echo "    scanned $PKG"
echo "    hard-coded references outside discovery tables: $HITS"
[ "$HITS" = "0" ]
check "no checkout or hand-built venv path is hard-coded" $?

say "config: resolution and provenance"
openmycelium config 2>&1 | sed 's/^/  /' | head -14
openmycelium config --check >/dev/null 2>&1
check "config --check verifies both runtimes" $?

say "version --verbose reports provenance"
openmycelium version --verbose 2>&1 | sed 's/^/  /' | head -14
openmycelium version --json 2>/dev/null | "$VENV/bin/python" -c "
import json,sys
r=json.load(sys.stdin)
need=['openmycelium','installedContentSha256','mcclVersion','transport','configuration']
missing=[k for k in need if not r.get(k)]
print(f'    version {r.get(\"openmycelium\")}  content {str(r.get(\"installedContentSha256\"))[:16]}')
sys.exit(1 if missing else 0)"
check "provenance carries version, content hash and configuration" $?

say "provision is idempotent"
openmycelium provision --dry-run 2>&1 | grep -E 'already usable|would create' | sed 's/^/  /'
openmycelium provision --dry-run >/dev/null 2>&1
check "provision --dry-run succeeds against existing environments" $?

say "every command runs from the wheel"
for cmd in "doctor" "fabric list" "model list" "version" "ps"; do
  if openmycelium $cmd >/dev/null 2>&1; then
    printf '  [PASS] openmycelium %s\n' "$cmd"
  else
    printf '  [FAIL] openmycelium %s\n' "$cmd"; FAIL=$((FAIL+1))
  fi
done
openmycelium model inspect Mistral-Nemo-Instruct-2407 >/dev/null 2>&1
check "openmycelium model inspect" $?
openmycelium plan --model Mistral-Nemo-Instruct-2407 \
  --output "$WORK/placement.json" >/dev/null 2>&1
check "openmycelium plan" $?
"$VENV/bin/python" -c "
import json,sys
m=json.load(open('$WORK/placement.json'))
c=[len(s['tensors']) for s in m['stages']]
print(f'    boundaryAfterLayer={m[\"pipeline\"][\"boundaryAfterLayer\"]} tensors={sorted(c)}')
sys.exit(0 if sorted(c)==[181,182] else 1)"
check "plan yields exclusive 181/182 ownership" $?

say "event storage is bounded"
"$VENV/bin/python" -c "
import os,sys
sys.path.insert(0, os.path.join('$PKG','runtime','serving'))
import audit
print(f'    MAX_EVENT_BYTES={audit.MAX_EVENT_BYTES}  '
      f'EVENT_RETENTION={audit.EVENT_RETENTION}')
sys.exit(0 if audit.MAX_EVENT_BYTES > 0 and hasattr(audit.EventWriter,'_rotate_if_large') else 1)"
check "installed package rotates events" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit $FAIL
