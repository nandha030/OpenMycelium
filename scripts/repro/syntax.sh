#!/usr/bin/env bash
# Syntax-check the harness. Nested quoting through wsl.exe eats $variables, so
# this lives in a file rather than being passed as a -c string.
cd /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro || exit 1
rc=0
for f in *.py; do
  if python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f"; then
    echo "  OK   $f"
  else
    echo "  FAIL $f"; rc=1
  fi
done
for f in *.sh; do
  sed 's/\r$//' "$f" > /tmp/chk.sh
  if bash -n /tmp/chk.sh 2>/dev/null; then echo "  OK   $f"; else echo "  FAIL $f"; rc=1; fi
done
exit "$rc"
