#!/usr/bin/env bash
# Every unit test in the repository, in one command, with one verdict.
#
# It exists because "the suite passes" was not previously checkable: the modules
# live in five directories with three different import roots, one needs pytest,
# and two only resolved when run from a particular working directory. A gate
# that reports zero failures while quietly skipping a directory is worse than no
# gate, so this enumerates the directories explicitly and fails if any is empty.
#
#   PY=/opt/omfresh/venv/bin/python scripts/repro/run_unit_suite.sh
set -uo pipefail
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
PY=${PY:-python3}
PYTEST_PY=${PYTEST_PY:-$PY}

if [ ! -d "$REPO/runtime" ]; then
  echo "  cannot locate the repository root; set OM_REPO to the checkout" >&2
  exit 78
fi

# unittest directories, and the one pytest module. mccl is run from its own
# source root because it is a separate distribution with its own layout.
UNITTEST_DIRS="runtime/serving runtime/scheduler runtime/fabric runtime/mycelium"
PYTEST_FILES="runtime/cli/test_event_gate.py"

TOTAL=0
FAILED=0
report() { printf '  %-26s %s\n' "$1" "$2"; }

for relative in $UNITTEST_DIRS; do
  directory="$REPO/$relative"
  count=$(find "$directory" -maxdepth 1 -name 'test_*.py' | wc -l)
  if [ "$count" = "0" ]; then
    report "$relative" "NO TEST MODULES -- the suite would silently skip this"
    FAILED=$((FAILED + 1))
    continue
  fi
  output=$(cd "$directory" && "$PY" -m unittest discover -p 'test_*.py' 2>&1)
  status=$?
  ran=$(printf '%s' "$output" | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
  ran=${ran:-0}
  TOTAL=$((TOTAL + ran))
  if [ "$status" = "0" ]; then
    report "$relative" "ok    $ran tests"
  else
    report "$relative" "FAIL  $ran tests"
    printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
    FAILED=$((FAILED + 1))
  fi
done

for relative in $PYTEST_FILES; do
  if ! "$PYTEST_PY" -c 'import pytest' 2>/dev/null; then
    report "$relative" "FAIL  pytest is not installed for $PYTEST_PY"
    FAILED=$((FAILED + 1))
    continue
  fi
  output=$(cd "$REPO/$(dirname "$relative")" && \
           "$PYTEST_PY" -m pytest "$(basename "$relative")" -q 2>&1)
  status=$?
  ran=$(printf '%s' "$output" | sed -n 's/^\([0-9]*\) passed.*/\1/p' | tail -1)
  ran=${ran:-0}
  TOTAL=$((TOTAL + ran))
  if [ "$status" = "0" ]; then
    report "$relative" "ok    $ran tests"
  else
    report "$relative" "FAIL"
    printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
    FAILED=$((FAILED + 1))
  fi
done

# MCCL ships as its own distribution; its tests import from its own src root.
if [ -d "$REPO/runtime/mccl/tests" ]; then
  output=$(cd "$REPO/runtime/mccl" && \
           PYTHONPATH="$REPO/runtime/mccl/src" "$PY" -m unittest discover \
           -s tests -p 'test_*.py' 2>&1)
  status=$?
  ran=$(printf '%s' "$output" | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
  ran=${ran:-0}
  TOTAL=$((TOTAL + ran))
  if [ "$status" = "0" ]; then
    report "runtime/mccl/tests" "ok    $ran tests"
  else
    report "runtime/mccl/tests" "FAIL  $ran tests"
    printf '%s\n' "$output" | tail -25 | sed 's/^/      /'
    FAILED=$((FAILED + 1))
  fi
fi

echo
if [ "$FAILED" = "0" ]; then
  echo "  == unit suite PASSED: $TOTAL tests, 0 failures =="
else
  echo "  == unit suite FAILED: $FAILED group(s), $TOTAL tests ran =="
fi
exit "$FAILED"
