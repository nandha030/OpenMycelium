#!/usr/bin/env bash
# Run the shipped tests against the INSTALLED wheel, never the checkout.
#
# The checkout suite proves the source is correct. This proves the artifact is:
# a file that only ever worked because the checkout supplied it shows up here
# rather than at a user's first run. PYTHONPATH is cleared for the same reason --
# with the checkout on the path the run would pass for the wrong reason.
set -uo pipefail

SITE=${OM_SITE:-/opt/om/venv/lib/python3.12/site-packages/openmycelium}
PY=${OM_PY:-/opt/om/venv/bin/python}
TOTAL=0
EXCLUDED=0
FAILED=0

# Two kinds of test cannot run against a wheel, and both are scope, not waiver:
#
#   - `test_manifest_schema.py` needs `release/0.1.0a5/placement.json`, frozen
#     evidence the wheel correctly does not ship. Named below and its count
#     reported.
#   - `ProseMatchesDataTests` in `test_contract_data.py` checks the safety
#     document against the transition table. A wheel ships no document for the
#     data to drift from, so the class skips itself -- but only after proving
#     positively that it is inside an installed package. That skip is counted
#     and printed here rather than absorbed into a green total.
#
# Checkout-only tests, named rather than quietly skipped.
#
# `test_manifest_schema.py` verifies that `release/0.1.0a5/placement.json` still
# validates -- frozen evidence that the wheel correctly does not ship, because
# release evidence is not product content. It asserts hard when the fixture is
# absent, deliberately: a test that silently passes without its evidence proves
# nothing. So it cannot run here, and excluding it is a statement about scope,
# not a waiver. It runs in full in the checkout suite.
CHECKOUT_ONLY="test_manifest_schema.py"

# Shipped, and not loadable by `unittest`: bare pytest functions, and the
# installed venv has no pytest -- it is a runtime environment, not a test one.
# Named separately from CHECKOUT_ONLY because the reason is different, and a
# single "excluded" bucket would hide that one of them is about evidence and
# the other about a test framework.
PYTEST_ONLY="test_event_gate.py"

report() { printf '  %-28s %s\n' "$1" "$2"; }

unset PYTHONPATH

printf '\n  installed wheel: %s\n' "$SITE"
"$PY" -c "import openmycelium, sys; print('  version:', openmycelium.__version__)"

for group in safety serving scheduler fabric cli; do
    directory="$SITE/runtime/$group"
    [ -d "$directory" ] || { report "runtime/$group" "absent"; continue; }
    if ! ls "$directory"/test_*.py >/dev/null 2>&1; then
        report "runtime/$group" "no shipped tests"
        continue
    fi
    modules=""
    for file in "$directory"/test_*.py; do
        name=$(basename "$file")
        case " $PYTEST_ONLY " in
            *" $name "*)
                report "  excluded" "$name (pytest module, no pytest installed)"
                continue
                ;;
        esac
        case " $CHECKOUT_ONLY " in
            *" $name "*)
                count=$(cd "$directory" && "$PY" -m unittest "${name%.py}" -q 2>&1 \
                        | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
                EXCLUDED=$((EXCLUDED + ${count:-0}))
                report "  excluded" "$name (${count:-?} tests, needs repo evidence)"
                ;;
            *) modules="$modules ${name%.py}" ;;
        esac
    done
    [ -n "$modules" ] || { report "runtime/$group" "all tests are checkout-only"; continue; }
    output=$(cd "$directory" && "$PY" -m unittest $modules -q 2>&1)
    status=$?
    ran=$(printf '%s' "$output" | sed -n 's/^Ran \([0-9]*\) test.*/\1/p' | tail -1)
    ran=${ran:-0}
    TOTAL=$((TOTAL + ran))
    # A class-level skip removes its tests from "Ran" entirely, so a silently
    # scoped-out class would shrink the total with nothing to show for it.
    # Surface every skip: the number has to be explainable, not just green.
    skipped=$(printf '%s' "$output" | sed -n 's/.*skipped=\([0-9]*\).*/\1/p' | tail -1)
    if [ "$status" = "0" ]; then
        report "runtime/$group" "ok    $ran tests${skipped:+  ($skipped skipped)}"
    else
        report "runtime/$group" "FAIL  $ran tests"
        printf '%s\n' "$output" | tail -20 | sed 's/^/      /'
        FAILED=$((FAILED + 1))
    fi
done

printf '\n'
if [ "$FAILED" = "0" ]; then
    printf '  == installed-wheel tests PASSED: %s tests, 0 failures' "$TOTAL"
    [ "$EXCLUDED" = "0" ] || printf ' (%s checkout-only, excluded)' "$EXCLUDED"
    printf ' ==\n'
else
    printf '  == installed-wheel tests FAILED: %s group(s), %s tests ran ==\n' \
        "$FAILED" "$TOTAL"
    exit 1
fi
