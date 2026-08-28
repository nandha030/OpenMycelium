#!/usr/bin/env bash
# Syntax-check the 0.1.0a7 changes, and confirm the batch file kept CRLF.
#
# The .cmd matters: an earlier LF-only version made batch label resolution fail
# intermittently -- `rm` broke while `models` worked -- which is the kind of
# defect that looks like a code bug for hours.
cd /mnt/c/Users/User/Documents/Open_Mycelium || exit 1
rc=0
for f in runtime/cli/rocm_prereq.py runtime/cli/config.py \
         runtime/cli/provision.py runtime/cli/provenance.py \
         runtime/cli/doctor.py packaging/launcher.py; do
  if python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f"; then
    echo "  OK   $f"
  else
    echo "  FAIL $f"; rc=1
  fi
done

echo
crlf=$(grep -c $'\r$' openmycelium.cmd)
total=$(wc -l < openmycelium.cmd)
echo "  openmycelium.cmd: $crlf of $total lines end CRLF"
if [ "$crlf" = "$total" ]; then
  echo "  OK   line endings"
else
  echo "  FIXING line endings"
  sed -i 's/$/\r/; s/\r\r$/\r/' openmycelium.cmd
  crlf=$(grep -c $'\r$' openmycelium.cmd)
  echo "  now $crlf of $total lines end CRLF"
fi

echo
echo "  version strings:"
grep -rn 'VERSION = "0.1.0a' packaging/launcher.py runtime/cli/lifecycle.py \
  scripts/build_openmycelium_wheel.py | sed 's/^/    /'
exit "$rc"
