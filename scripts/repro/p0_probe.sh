#!/usr/bin/env bash
# Reproduce the one P0 assertion that reported a false failure, in isolation.
set -uo pipefail
echo "  cwd            $(pwd)"
echo "  python3        $(command -v python3)"
python3 -c "import openmycelium" 2>/tmp/p0err.txt
echo "  import rc      $?"
echo "  stderr         $(head -c 120 /tmp/p0err.txt)"
if python3 -c "import openmycelium" 2>/dev/null; then false; else true; fi
echo "  guarded rc     $?"
echo "  sys.path[0..3] $(python3 -c 'import sys;print(sys.path[:4])')"
