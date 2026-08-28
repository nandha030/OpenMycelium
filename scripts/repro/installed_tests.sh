#!/usr/bin/env bash
# Prove the installed wheel is the fixed build, then test *it* -- not the checkout.
#
# The isolation result from the previous run is only meaningful if it came from
# a wheel built after the OPENMYCELIUM_STORE fix. A checkout file edited between
# build and run would produce the same passing output for the wrong reason, so
# provenance is checked from site-packages content and from the wheel's hash
# before any test runs.
set -uo pipefail

REPO=/mnt/c/Users/User/Documents/Open_Mycelium
VENV=/opt/omfresh/venv
WORK=/opt/omfresh/work
STORE=/opt/omfresh/models
STATE=/opt/omfresh/state
WHEEL="$REPO/dist/openmycelium-0.1.0a1-py3-none-any.whl"
FAIL=0

say() { printf '\n  == %s ==\n' "$1"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

# --- normal command discovery, as a user would have it -------------------
export PATH="$VENV/bin:$PATH"
cd "$WORK" || exit 1
unset PYTHONPATH
export OPENMYCELIUM_STORE="$STORE"
export OPENMYCELIUM_STATE="$STATE"

say "command discovery without an absolute path"
RESOLVED=$(command -v openmycelium || true)
printf '    command -v openmycelium -> %s\n' "${RESOLVED:-not found}"
[ -n "$RESOLVED" ] && [ "${RESOLVED#$VENV/bin/}" != "$RESOLVED" ]
check "openmycelium resolves inside the venv" $?
openmycelium version 2>&1 | sed 's/^/    /' | head -4
openmycelium version >/dev/null 2>&1
check "'openmycelium version' runs with no path" $?

say "the installed wheel is the fixed build"
python - <<'PY'
import hashlib, os, sys
wheel = "/mnt/c/Users/User/Documents/Open_Mycelium/dist/openmycelium-0.1.0a1-py3-none-any.whl"
with open(wheel, "rb") as handle:
    digest = hashlib.sha256(handle.read()).hexdigest()
recorded = ""
sums = os.path.join(os.path.dirname(wheel), "SHA256SUMS")
for line in open(sums, encoding="utf-8"):
    if os.path.basename(wheel) in line:
        recorded = line.split()[0]
print(f"    wheel sha256    {digest}")
print(f"    recorded        {recorded}")
sys.exit(0 if digest == recorded and digest else 1)
PY
check "wheel hash matches SHA256SUMS" $?

python - <<'PY'
import inspect, os, sys
import openmycelium
root = os.path.join(openmycelium.__path__[0], "runtime", "cli")
sys.path.insert(0, root)
import models, control
# The fix must be present in the INSTALLED copy, not merely in the checkout.
source = inspect.getsource(models)
has_env = "OPENMYCELIUM_STORE" in source
print(f"    site-packages models.py reads OPENMYCELIUM_STORE: {has_env}")
print(f"    resolved store  {models.STORE}")
print(f"    resolved state  {control.CONTROL_DIR}")
ok = (has_env and models.STORE.startswith("/opt/omfresh")
      and control.CONTROL_DIR.startswith("/opt/omfresh"))
sys.exit(0 if ok else 1)
PY
check "installed copy contains the store/state fix" $?

say "every OpenMycelium module loads from site-packages"
python - <<'PY'
import os, sys
import openmycelium
root = os.path.join(openmycelium.__path__[0], "runtime")
for part in ("cli", "serving", "scheduler", "fabric"):
    sys.path.insert(0, os.path.join(root, part))
import audit, control, coordinator, event_gate, fabric, health          # noqa
import lifecycle, model_cmds, models, paths, placement, profiler        # noqa
import puller, sampling, serve, stage_model                             # noqa

names = ("audit", "control", "coordinator", "event_gate", "fabric", "health",
         "lifecycle", "model_cmds", "models", "paths", "placement",
         "profiler", "puller", "sampling", "serve", "stage_model")
bad = []
for name in names:
    path = getattr(sys.modules[name], "__file__", "") or ""
    if "site-packages" not in path:
        bad.append((name, path))
print(f"    checked {len(names)} modules")
for name, path in bad:
    print(f"    OUTSIDE site-packages: {name} -> {path}")
print(f"    runtime_root() -> {paths.runtime_root()}")
sys.exit(1 if bad else 0)
PY
check "no module resolves outside site-packages" $?

say "the checkout is unreachable from here"
python - <<'PY'
import os, sys
leaked = [p for p in sys.path if "Documents/Open_Mycelium" in p]
print(f"    sys.path entries pointing at the checkout: {leaked or 'none'}")
print(f"    PYTHONPATH: {os.environ.get('PYTHONPATH', '(unset)')!r}")
print(f"    cwd: {os.getcwd()}")
sys.exit(1 if leaked else 0)
PY
check "no checkout path is importable" $?

say "installed-package tests"
# Run from the installed tree so an import that only works in the checkout fails
# here rather than passing on borrowed files.
PKG=$("$VENV/bin/python" -c "import openmycelium,os;print(os.path.join(openmycelium.__path__[0],'runtime'))")
"$VENV/bin/pip" install -q pytest 2>&1 | tail -1
PYTHONPATH="$PKG/cli:$PKG/serving:$PKG/scheduler:$PKG/fabric" \
  "$VENV/bin/python" -m pytest -q "$PKG/cli" "$PKG/scheduler" "$PKG/fabric" 2>&1 | tail -4
LAST=${PIPESTATUS[0]}
check "tests pass against the installed package" "$LAST"

say "mccl tests from the installed distribution"
"$VENV/bin/python" -c "import mccl,os;print('    mccl at',os.path.dirname(mccl.__file__))"
"$VENV/bin/python" -c "
import mccl.xvendor as x
h = x.ActivationHeader('bf16', (1, 8, 5120)); h.validate()
print('    ActivationHeader byte_size', h.byte_size)
"
check "installed mccl is importable and functional" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit $FAIL
