#!/usr/bin/env bash
# Are any worker processes still alive? Standalone, so the answer is auditable.
#
# A worker is a python interpreter running one of the runtime's worker modules.
# Matching the bare module names matches any shell whose command line mentions
# them -- including the pgrep that looks for them, and including a gate script
# that names them in a comment. That turns "zero orphan workers" into a claim
# about nothing, which is exactly what happened once: the check reported an
# orphan that was the shell running the check.
set -uo pipefail

PATTERN='python[^ ]* .*(stage_model|pipeline_run|forward_pass)'

found=$(pgrep -a -f "$PATTERN" 2>/dev/null \
        | awk -v self="$$" -v parent="$PPID" '$1 != self && $1 != parent' \
        | grep -v "check_orphans" || true)

if [ -n "$found" ]; then
    printf '  ORPHAN WORKERS:\n%s\n' "$found"
    exit 1
fi
printf '  none\n'
