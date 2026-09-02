#!/usr/bin/env bash
# Prove the branch scope review fails when it should, in both directions.
#
# A gate is not accepted until it has been shown to fail with the defect
# present. This one has passed vacuously twice already: once because the checker
# was still untracked and so absent from its own diff, and once because nothing
# had been committed, so `main...HEAD` was empty and every check ran against no
# files at all.
#
# Both directions matter now that two branches are open at once. A safety branch
# must reject Memory OS code, and a memory branch must reject enforcement code:
# the scopes police each other, and a checker that only enforces one of those is
# half a gate.
set -uo pipefail

REPO=${OM_REPO:-/mnt/c/Users/User/Documents/Open_Mycelium}
PY=${OM_PY:-/opt/hetenv/bin/python3}
CHECKER="$REPO/scripts/repro/scope_review_branch.py"
FAILURES=0

probe() {
    local label=$1 scope=$2 path=$3 body=$4
    local full="$REPO/$path"
    mkdir -p "$(dirname "$full")"
    printf '%s\n' "$body" > "$full"
    ( cd "$REPO" && git add -- "$path" >/dev/null 2>&1 \
      && git -c user.email=gate@local -c user.name=gate \
             commit -q -m "probe: deliberate scope creep" -- "$path" )

    if ( cd "$REPO" && "$PY" "$CHECKER" main HEAD "--scope=$scope" >/dev/null 2>&1 ); then
        printf '  VACUOUS  %s -- the review PASSED with the defect present\n' "$label"
        FAILURES=$((FAILURES + 1))
    else
        printf '  ok       %s -- the review fails, as it must\n' "$label"
    fi

    ( cd "$REPO" && git reset -q --hard HEAD~1 && rm -f "$full" )
}

printf '\nScope review, negative tests\n\n'

probe "safety branch rejects Memory OS code" \
      "feature/safety-enforcement-d3" \
      "runtime/safety/_probe_scope.py" \
      "class MemoryObject:
    def bind_working_set(self):
        pass"

probe "memory branch rejects enforcement code" \
      "feature/memory-os-m1" \
      "runtime/memory/_probe_scope.py" \
      "ENFORCE_QUARANTINE = 'enforce_quarantine'
def refuse_admission():
    pass"

printf '\n  undeclared branch\n'
if ( cd "$REPO" && "$PY" "$CHECKER" main HEAD --scope=feature/undeclared >/dev/null 2>&1 ); then
    printf '  VACUOUS  an undeclared branch PASSED\n'
    FAILURES=$((FAILURES + 1))
else
    printf '  ok       an undeclared branch fails rather than defaulting\n'
fi

printf '\n'
if [ "$FAILURES" = "0" ]; then
    printf '  == the scope review is not vacuous ==\n'
else
    printf '  == %s check(s) passed with a defect present ==\n' "$FAILURES"
    exit 1
fi
