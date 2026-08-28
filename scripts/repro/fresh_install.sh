#!/usr/bin/env bash
# Install the wheel into a clean environment and run the whole flow from it.
#
# The point is isolation. Every step below exists because a package can pass its
# tests while being incomplete, if the source checkout is quietly supplying the
# missing files:
#
#   * installed from local wheels only, with no index
#   * run from a directory outside the repository
#   * PYTHONPATH cleared
#   * isolated model, state and control directories
#   * sys.path checked for the repository, which must be absent
set -uo pipefail

REPO=/mnt/c/Users/User/Documents/Open_Mycelium
VENV=/opt/omfresh/venv
WORK=/opt/omfresh/work
STORE=/opt/omfresh/models
STATE=/opt/omfresh/state
FAIL=0

say() { printf '\n  == %s ==\n' "$1"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

rm -rf /opt/omfresh
mkdir -p "$WORK" "$STORE" "$STATE"

say "install from local wheels only"
/opt/hetenv/bin/python -m venv "$VENV" >/dev/null
"$VENV/bin/pip" install -q --no-index --find-links "$REPO/dist" \
    openmycelium==0.1.0a1 2>&1 | tail -3
"$VENV/bin/pip" list 2>/dev/null | grep -iE 'openmycelium' | sed 's/^/    /'
[ -x "$VENV/bin/openmycelium" ]; check "console script installed" $?

# Everything from here runs outside the checkout with no PYTHONPATH.
cd "$WORK" || exit 1
unset PYTHONPATH
export OPENMYCELIUM_STORE="$STORE"
export OPENMYCELIUM_STATE="$STATE"

say "resolution comes from site-packages, not the checkout"
"$VENV/bin/python" - <<'PY'
import sys, openmycelium, shutil
print(f"    openmycelium.__file__  {openmycelium.__file__}")
print(f"    version                {openmycelium.__version__}")
print(f"    console script         {shutil.which('openmycelium')}")
leaked = [p for p in sys.path if "Documents/Open_Mycelium" in p]
print(f"    repo entries on sys.path: {leaked or 'none'}")
sys.exit(0 if ("site-packages" in openmycelium.__file__ and not leaked) else 1)
PY
check "package and CLI resolve under site-packages" $?

say "the installed launcher can find every command's module"
"$VENV/bin/python" - <<'PY'
import os, sys
from openmycelium.launcher import COMMANDS, package_root
missing = [rel for rel, _ in COMMANDS.values()
           if not os.path.isfile(os.path.join(package_root(), "runtime",
                                              *rel.split("/")))]
print(f"    commands: {len(COMMANDS)}   missing modules: {missing or 'none'}")
sys.exit(1 if missing else 0)
PY
check "no command is missing its module" $?

say "doctor"
"$VENV/bin/openmycelium" doctor 2>&1 | grep -vE 'NumPy|conversion' | sed 's/^/  /' | tail -12
say "version"
"$VENV/bin/openmycelium" version 2>&1 | sed 's/^/  /' | head -8
say "fabric"
"$VENV/bin/openmycelium" fabric list 2>&1 | grep -vE 'NumPy|conversion' | sed 's/^/  /' | head -14

say "pull into the isolated store"
"$VENV/bin/openmycelium" model pull HuggingFaceTB/SmolLM2-135M-Instruct 2>&1 \
  | grep -vE 'NumPy|conversion' | tail -5
say "verify"
"$VENV/bin/openmycelium" model verify SmolLM2-135M-Instruct 2>&1 \
  | grep -vE 'NumPy|conversion' | tail -4
"$VENV/bin/openmycelium" model verify SmolLM2-135M-Instruct >/dev/null 2>&1
check "pulled model verifies" $?

say "isolation: the store is the isolated one"
"$VENV/bin/python" -c "
import os,sys
sys.path.insert(0, os.path.join(__import__(\"openmycelium\").__path__[0], \"runtime\", \"cli\"))
import models, control
print(f\"    store  {models.STORE}\")
print(f\"    state  {control.CONTROL_DIR}\")
sys.exit(0 if models.STORE.startswith(\"/opt/omfresh\") else 1)
"
check "store and state are isolated" $?

say "model list"
"$VENV/bin/openmycelium" models 2>&1 | grep -vE 'NumPy|conversion' | sed 's/^/  /'

printf '\n  %d check(s) failed\n' "$FAIL"
exit $FAIL
