#!/usr/bin/env bash
# Syntax-check the console and confirm the wheel build would ship its assets.
cd /mnt/c/Users/User/Documents/Open_Mycelium || exit 1
rc=0
for f in runtime/cli/console.py runtime/cli/console_service.py \
         packaging/launcher.py scripts/build_openmycelium_wheel.py; do
  if python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f"; then
    echo "  OK   $f"
  else
    echo "  FAIL $f"; rc=1
  fi
done
echo
echo "  console assets present:"
ls -1 runtime/cli/console_assets 2>/dev/null | sed 's/^/    /'
echo
echo "  launcher wiring:"
grep -n '"console"' packaging/launcher.py | sed 's/^/    /'
exit "$rc"
