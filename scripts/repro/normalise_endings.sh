#!/usr/bin/env bash
# Shell and Python files must use LF: a CRLF script fails on Linux with
# "set: pipefail: invalid option name" and a syntax error at the first
# function, which is a confusing way to learn about line endings.
#
# openmycelium.cmd is deliberately excluded. It is Windows batch, and an
# LF-only .cmd made label resolution fail intermittently once already.
set -uo pipefail
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
[ -f "$REPO/packaging/launcher.py" ] || { echo "  set OM_REPO" >&2; exit 78; }
cd "$REPO" || exit 1

changed=0
for f in "$@"; do
  [ -f "$f" ] || continue
  case "$f" in *.cmd|*.bat) echo "  skipped (Windows batch): $f"; continue ;; esac
  before=$(grep -c $'\r$' "$f" 2>/dev/null || true)
  before=${before:-0}
  if [ "$before" -gt 0 ]; then
    sed -i 's/\r$//' "$f"
    printf '  %4s CRLF lines removed  %s\n' "$before" "$f"
    changed=$((changed + 1))
  fi
done
echo "  $changed file(s) normalised"

echo
echo "  verification:"
for f in "$@"; do
  [ -f "$f" ] || continue
  n=$(grep -c $'\r$' "$f" 2>/dev/null || true)
  printf '    %-48s %s CRLF\n' "$f" "${n:-0}"
done
printf '    %-48s %s CRLF (must stay > 0)\n' "openmycelium.cmd" \
  "$(grep -c $'\r$' openmycelium.cmd 2>/dev/null || echo 0)"
