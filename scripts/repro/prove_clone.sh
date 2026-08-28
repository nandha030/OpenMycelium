#!/usr/bin/env bash
# Establish, from a pristine Linux clone, whether the CRLF problem is in the
# repository or only in the Windows working tree.
set -uo pipefail
CLONE=${CLONE:-/root/om-proof}
REMOTE=${REMOTE:-https://github.com/nandha030/OpenMycelium.git}
BRANCH=feature/partitioned-model-runner
CR=$(printf '\r')
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

rm -rf "$CLONE"
git clone -q --branch "$BRANCH" "$REMOTE" "$CLONE"
cd "$CLONE" || exit 1
echo "  clone at $(git log --oneline -1)"
echo "  core.autocrlf in clone: $(git config core.autocrlf || echo '(unset)')"

echo
echo "  == 1. no tracked live .sh or .py contains CRLF =="
bad=$(git ls-files -- '*.sh' '*.py' | grep -v '^release/' | while IFS= read -r f; do
        grep -lq "$CR\$" "$f" 2>/dev/null && echo "$f"; done | wc -l)
total=$(git ls-files -- '*.sh' '*.py' | grep -v '^release/' | wc -l)
echo "    $bad of $total live scripts contain CRLF"
[ "$bad" = "0" ]
check "no live script carries CRLF in a Linux clone" $?

echo
echo "  == 2. scripts run directly, no sed preprocessing =="
OM_REPO="$CLONE" bash scripts/repro/verify_sanitizer.sh > /tmp/vs.txt 2>&1
rc=$?
grep -cE '^  \[PASS\]' /tmp/vs.txt | sed 's/^/    sanitizer checks passing: /'
grep -E 'invalid option name|syntax error' /tmp/vs.txt | head -2 | sed 's/^/    /'
[ "$rc" = "0" ]
check "verify_sanitizer.sh runs directly and passes" $?
bash -n scripts/repro/console_wheel_gate.sh 2>&1 | head -2 | sed 's/^/    /'
bash -n scripts/repro/console_wheel_gate.sh
check "console_wheel_gate.sh parses directly" $?

echo
echo "  == 3. openmycelium.cmd line endings in the clone =="
n=$(grep -c "$CR\$" openmycelium.cmd 2>/dev/null || echo 0)
echo "    openmycelium.cmd: $n CRLF lines in a Linux checkout"
echo "    (Windows clones get CRLF via core.autocrlf=true; a .gitattributes"
echo "     rule would guarantee it regardless of the cloner's config)"

echo
echo "  == 4. sanitizer finds all fixtures and is idempotent =="
cd /tmp && python3 "$CLONE/scripts/repro/sanitize_fixtures.py" | sed 's/^/    /'
cd /tmp && python3 "$CLONE/scripts/repro/sanitize_fixtures.py" | grep changed | sed 's/^/    /'
cd "$CLONE" || exit 1
changed=$(cd / && python3 "$CLONE/scripts/repro/sanitize_fixtures.py" | grep -oE 'changed  [0-9]+' | grep -oE '[0-9]+')
[ "${changed:-9}" = "0" ]
check "sanitizer idempotent from an unrelated cwd" $?

echo
echo "  == 5. MCCL tests =="
if [ -x /opt/omfresh/venv/bin/python ]; then
  (cd runtime/mccl && PYTHONPATH=src /opt/omfresh/venv/bin/python -m pytest tests -q 2>&1 | tail -2 | sed 's/^/    /')
  (cd runtime/mccl && PYTHONPATH=src /opt/omfresh/venv/bin/python -m pytest tests -q > /dev/null 2>&1)
  check "MCCL tests pass in the clone" $?
else
  echo "    pytest environment unavailable here"
fi

echo
echo "  == 6. frozen hashes in the clone =="
for v in 0.1.0 0.2.0a5; do
  if [ -f "release/$v/SHA256SUMS.frozen" ]; then
    (cd "release/$v" && sha256sum -c SHA256SUMS.frozen 2>&1 | sed "s/^/    $v  /")
  fi
done
(cd release/0.1.0 && sha256sum -c SHA256SUMS.frozen > /dev/null 2>&1) && \
(cd release/0.2.0a5 && sha256sum -c SHA256SUMS.frozen > /dev/null 2>&1)
check "frozen artifacts verify in the clone" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
