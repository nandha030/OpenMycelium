#!/usr/bin/env bash
# Contract tests: the contract agrees with itself, and the implementation obeys it.
#
#   transition table   must match the document  -- prose and data cannot drift
#   contract data      must PASS                -- the contract is self-consistent
#   governor behaviour must PASS                -- the implementation obeys it
#   packaging + suite  must include safety      -- see below
#
# Through Gate C.1 the behaviour tests were *expected red*: the contract existed
# and the implementation did not, and this command asserted that they failed for
# exactly that reason. Gate D.1 implemented the Governor and paid the two debts
# the redness stood in for, so they are required-green from here.
#
# The last two checks are the debts, and they are checked rather than remembered
# because both fail silently. A Governor missing from the wheel passes every
# test from a checkout and is absent from every build; a directory missing from
# the suite still lets it report zero failures.
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
  report "contract data (required PASS)" "ok    ${ran:-0} tests"
else
  report "contract data (required PASS)" "FAIL  ${ran:-0} tests"
  printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
  FAILURES=$((FAILURES + 1))
fi

# ---------------------------------------------- governor behaviour: green
# Was expected-red through Gate C.1, when no implementation existed. Gate D.1
# implemented it and paid the two debts that redness was standing in for --
# runtime/safety is now in the wheel's INCLUDE and in the suite's UNITTEST_DIRS
# -- so this is required-green from here on.
output=$(cd "$SAFETY" && "$PY" -m unittest test_governor_contract 2>&1)
status=$?
ran=$(printf '%s' "$output" | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
if [ "$status" = "0" ]; then
  report "governor behaviour (required PASS)" "ok    ${ran:-0} tests"
else
  report "governor behaviour (required PASS)" "FAIL  ${ran:-0} tests"
  printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
  FAILURES=$((FAILURES + 1))
fi

# The debts the expected-red arrangement was holding open. Checked here rather
# than remembered, because both fail silently: a Governor absent from the wheel
# passes every test from a checkout, and a suite that skips a directory still
# reports zero failures.
if grep -q '"safety"' "$REPO/scripts/build_openmycelium_wheel.py"; then
  report "runtime/safety in the wheel" "ok"
else
  report "runtime/safety in the wheel" "FAIL -- it would be absent from every build"
  FAILURES=$((FAILURES + 1))
fi
if grep -q 'runtime/safety' "$REPO/scripts/repro/run_unit_suite.sh"; then
  report "runtime/safety in the suite" "ok"
else
  report "runtime/safety in the suite" "FAIL -- zero failures would mean a skipped group"
  FAILURES=$((FAILURES + 1))
fi

echo
if [ "$FAILURES" = "0" ]; then
  echo "  == contract tests PASSED: contract self-consistent, implementation obeys it =="
else
  echo "  == contract tests FAILED: $FAILURES =="
fi
exit "$FAILURES"
