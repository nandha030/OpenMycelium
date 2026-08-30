#!/usr/bin/env bash
# Contract tests: the ones that pass today, and the ones expected to be red.
#
# A contract written before its implementation produces failing tests on
# purpose. Leaving them out of the suite with a comment makes them invisible;
# putting them in turns the suite red and blocks everything behind it. So they
# get their own named command, and it asserts the *expected* outcome rather than
# merely running them:
#
#   contract data     must PASS   -- the contract must not contradict itself
#   governor behaviour must FAIL  -- and only with ModuleNotFoundError
#
# Both directions are checked. If the behaviour tests start passing, an
# implementation arrived without the packaging and suite debts being paid
# (SAFETY_GOVERNOR.md section 1), and this fails so that is noticed. If they fail
# for any reason other than the missing module -- a syntax error, a bad import, a
# genuine contradiction -- this fails too, because "expected red" must not become
# a place broken tests hide.
#
#   scripts/repro/contract_tests.sh
set -uo pipefail

_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
PY=${PY:-python3}
SAFETY="$REPO/runtime/safety"

if [ ! -d "$SAFETY" ]; then
  echo "  cannot locate runtime/safety; set OM_REPO to the checkout" >&2
  exit 78
fi

FAILURES=0
report() { printf '  %-34s %s\n' "$1" "$2"; }

echo "  == contract tests =="

# ------------------------------------------------- the table agrees with prose
output=$("$PY" "$REPO/scripts/repro/render_safety_table.py" --check 2>&1)
if [ $? = 0 ]; then
  report "transition table vs document" "ok  $(echo "$output" | tr -d '\n' | sed 's/^ *//')"
else
  report "transition table vs document" "FAIL"
  printf '%s\n' "$output" | sed 's/^/      /'
  FAILURES=$((FAILURES + 1))
fi

# ------------------------------------------------------- contract data: green
output=$(cd "$SAFETY" && "$PY" -m unittest test_contract_data 2>&1)
status=$?
ran=$(printf '%s' "$output" | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
if [ "$status" = "0" ]; then
  report "contract data (expected PASS)" "ok    ${ran:-0} tests"
else
  report "contract data (expected PASS)" "FAIL  ${ran:-0} tests"
  printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
  FAILURES=$((FAILURES + 1))
fi

# -------------------------------------------------- governor behaviour: red
output=$(cd "$SAFETY" && "$PY" -m unittest test_governor_contract 2>&1)
status=$?
if [ "$status" = "0" ]; then
  report "governor behaviour (expected RED)" "FAIL -- these passed"
  echo "      An implementation exists. Before this can go green here it must" >&2
  echo "      join the unit suite and the wheel: see SAFETY_GOVERNOR.md #1." >&2
  FAILURES=$((FAILURES + 1))
elif printf '%s' "$output" | grep -q "No module named 'governor'"; then
  report "governor behaviour (expected RED)" "ok    red for the right reason"
  echo "      ModuleNotFoundError: No module named 'governor' -- Gate D implements it"
else
  report "governor behaviour (expected RED)" "FAIL -- red for the WRONG reason"
  printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
  FAILURES=$((FAILURES + 1))
fi

echo
if [ "$FAILURES" = "0" ]; then
  echo "  == contract tests PASSED: data green, behaviour red as designed =="
else
  echo "  == contract tests FAILED: $FAILURES =="
fi
exit "$FAILURES"
