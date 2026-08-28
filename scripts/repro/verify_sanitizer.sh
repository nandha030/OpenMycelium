#!/usr/bin/env bash
# Verify the sanitizer is portable and idempotent, from a directory that is
# not the repository.
set -uo pipefail
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
if [ ! -f "$REPO/packaging/launcher.py" ]; then
  echo "  cannot locate the repository root; set OM_REPO to the checkout" >&2
  exit 78
fi
SCRIPT="$REPO/scripts/repro/sanitize_fixtures.py"
CONTRACTS="$REPO/docs/ui/contracts"
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == 1. run from a different working directory =="
cd /tmp || exit 1
echo "    cwd: $(pwd)"
python3 "$SCRIPT" | sed 's/^/    /'
FIRST=$?
[ "$FIRST" = "0" ]
check "sanitizer located the fixtures from an unrelated cwd" $?

echo
echo "  == 2. every JSON fixture parses =="
python3 - "$CONTRACTS" <<'PY'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
bad = 0
files = sorted(base.glob("*.json"))
for path in files:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        print(f"    ok    {path.name}")
    except Exception as error:
        print(f"    FAIL  {path.name}: {error}")
        bad += 1
print(f"    {len(files)} JSON fixture(s), {bad} unparseable")
sys.exit(1 if bad or len(files) != 8 else 0)
PY
check "all eight JSON fixtures parse" $?

echo
echo "  == 3. a second run changes nothing =="
cd /root || exit 1
echo "    cwd: $(pwd)"
SECOND=$(python3 "$SCRIPT" | grep 'changed' | grep -oE '[0-9]+' | head -1)
python3 "$SCRIPT" | sed 's/^/    /'
[ "${SECOND:-99}" = "0" ]
check "second run made zero changes (idempotent)" $?

echo
echo "  == 4. no machine paths or raw identities in the fixtures =="
if grep -rqE '/mnt/c/Users/[A-Za-z]+' "$CONTRACTS" 2>/dev/null; then
  grep -rlE '/mnt/c/Users/[A-Za-z]+' "$CONTRACTS" | sed 's/^/      /'
  false
else true; fi
check "no machine-specific user paths in fixtures" $?
if grep -rqE 'GPU-[0-9a-f]{8}-|pci-0000:[0-9a-f]{2}' "$CONTRACTS" 2>/dev/null; then
  grep -rloE 'GPU-[0-9a-f]{8}-|pci-0000:[0-9a-f]{2}' "$CONTRACTS" | sed 's/^/      /'
  false
else true; fi
check "no raw device identities in fixtures" $?

echo
echo "  == 5. the script itself carries no machine path =="
if grep -qE '/mnt/c/Users/[A-Za-z]+' "$SCRIPT"; then false; else true; fi
check "sanitize_fixtures.py has no hard-coded path" $?
grep -n 'ROOT = ' "$SCRIPT" | sed 's/^/      /'

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
