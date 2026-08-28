#!/usr/bin/env bash
# Prove the .cmd rule: a clone that does NOT have core.autocrlf=true must still
# get CRLF for Windows batch, while shell scripts stay LF.
set -uo pipefail
SRC=${SRC:-https://github.com/nandha030/OpenMycelium.git}
BRANCH=${BRANCH:-feature/partitioned-model-runner}
CLONE=${CLONE:-/root/om-cmd-check}
CR=$(printf '\r')
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == clone with core.autocrlf=false, the hostile case =="
rm -rf "$CLONE"
git -c core.autocrlf=false clone -q --branch "$BRANCH" "$SRC" "$CLONE" 2>&1 | tail -2
cd "$CLONE" || exit 1

echo "    at $(git log --oneline -1)"
echo "    core.autocrlf: $(git config core.autocrlf || echo '(unset)')"

echo
echo "  == the launcher gets CRLF anyway =="
n=$(grep -c "$CR\$" openmycelium.cmd 2>/dev/null || echo 0)
total=$(wc -l < openmycelium.cmd)
echo "    openmycelium.cmd: $n of $total lines end CRLF"
[ "${n:-0}" -gt 0 ]
check "openmycelium.cmd is CRLF without relying on autocrlf" $?

echo
echo "  == shell scripts stay LF in the same clone =="
bad=$(git ls-files -- '*.sh' '*.py' | grep -v '^release/' | while IFS= read -r f; do
        grep -lq "$CR\$" "$f" 2>/dev/null && echo "$f"; done | wc -l)
count=$(git ls-files -- '*.sh' '*.py' | grep -v '^release/' | wc -l)
echo "    $bad of $count live scripts carry CRLF"
[ "$bad" = "0" ]
check "shell and Python scripts remain LF" $?

echo
echo "  == a script runs directly in that clone =="
OM_REPO="$CLONE" bash scripts/repro/verify_sanitizer.sh > /tmp/cmdcheck.txt 2>&1
rc=$?
grep -cE '^  \[PASS\]' /tmp/cmdcheck.txt | sed 's/^/    sanitizer checks passing: /'
[ "$rc" = "0" ]
check "verify_sanitizer.sh runs directly, no preprocessing" $?

echo
echo "  == frozen hashes unchanged in that clone =="
for v in 0.1.0 0.2.0a5; do
  (cd "release/$v" && sha256sum -c SHA256SUMS.frozen 2>&1 | sed "s/^/    $v  /")
done
(cd release/0.1.0 && sha256sum -c SHA256SUMS.frozen > /dev/null 2>&1) && \
(cd release/0.2.0a5 && sha256sum -c SHA256SUMS.frozen > /dev/null 2>&1)
check "frozen artifacts verify" $?

echo
echo "  == the rule as stored =="
grep -A1 '^\*\.cmd' .gitattributes | sed 's/^/    /'
sha=$(git rev-parse HEAD:openmycelium.cmd)
blob_cr=$(git cat-file blob "$sha" | grep -c "$CR\$" || true)
echo "    blob stores ${blob_cr:-0} CRLF (LF in the repository is correct;"
echo "     eol=crlf converts on checkout)"

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
