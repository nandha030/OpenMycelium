#!/usr/bin/env bash
# Build MCCL 0.2.0a3 and OpenMycelium 0.2.0a5, then check the pin and the
# installed imports before any hardware is involved.
set -uo pipefail
# Repository root, derived rather than hard-coded. These scripts are sometimes
# copied elsewhere before running (line-ending fixes), so BASH_SOURCE alone is
# not enough: fall back to OM_REPO, and fail loudly rather than guess.
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
export OM_DIST="${OM_DIST:-$REPO/dist}"
if [ ! -f "$REPO/packaging/launcher.py" ]; then
  echo "  cannot locate the repository root; set OM_REPO to the checkout" >&2
  exit 78
fi
PY=/opt/omfresh/venv/bin/python
SCRATCH=/tmp/pin-check
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == build MCCL 0.2.0a3 =="
cd "$REPO/runtime/mccl" || exit 1
rm -rf build/lib build/bdist.* *.egg-info src/*.egg-info 2>/dev/null
"$PY" -m build --outdir "$REPO/dist" > /tmp/mccl-build.log 2>&1
check "mccl built" $?
ls -1 "$REPO/dist" | grep '0\.2\.0a3' | sed 's/^/    /'

echo
echo "  == build OpenMycelium 0.2.0a5 =="
cd "$REPO" || exit 1
"$PY" scripts/build_openmycelium_wheel.py 2>&1 | tail -5 | sed 's/^/    /'

echo
echo "  == hashes =="
cd "$REPO/dist" || exit 1
sha256sum openmycelium-0.2.0a5-py3-none-any.whl \
          openmycelium_mccl-0.2.0a3-py3-none-any.whl \
          openmycelium_mccl-0.2.0a3.tar.gz 2>/dev/null | sed 's/^/    /'

echo
echo "  == the wheel pins MCCL 0.2.0a3 =="
"$PY" - <<'PY'
import glob, os, re, sys, zipfile
path = glob.glob(os.environ["OM_DIST"] + "/openmycelium-0.2.0a5-*.whl")[0]
z = zipfile.ZipFile(path)
meta = [n for n in z.namelist() if n.endswith("METADATA")][0]
text = z.read(meta).decode()
requires = [l for l in text.splitlines() if l.startswith("Requires-Dist")]
for line in requires:
    print(f"    {line}")
ok = any("openmycelium-mccl==0.2.0a3" in l.replace("_", "-") for l in requires)
print(f"    version: {re.search(r'^Version: (.+)$', text, re.M).group(1)}")
sys.exit(0 if ok else 1)
PY
check "the built wheel requires openmycelium-mccl==0.2.0a3" $?

echo
echo "  == install both into a clean prefix and check imports =="
rm -rf "$SCRATCH"
python3 -m venv "$SCRATCH" > /dev/null 2>&1
"$SCRATCH/bin/pip" install -q --no-index --find-links "$REPO/dist" \
  "openmycelium==0.2.0a5" > /tmp/pin-install.log 2>&1
check "openmycelium 0.2.0a5 installs and resolves its pin offline" $?
"$SCRATCH/bin/pip" list --format=freeze 2>/dev/null | grep -i openmycelium | sed 's/^/    /'

"$SCRATCH/bin/python" - <<'PY'
import sys
import mccl
ns = {}
exec("from mccl import *", ns)                                    # noqa: S102
print(f"    mccl.__version__        {mccl.__version__}")
print(f"    MRouter in root         {hasattr(mccl, 'MRouter')}")
print(f"    HetRouter importable    {hasattr(mccl, 'HetRouter')}")
print(f"    HetRouter in __all__    {'HetRouter' in mccl.__all__}")
print(f"    star import offers      MRouter={'MRouter' in ns} HetRouter={'HetRouter' in ns}")
from mccl.router import HetRouter, MRouter
ok = (mccl.__version__ == "0.2.0a3"
      and hasattr(mccl, "MRouter") and hasattr(mccl, "HetRouter")
      and "HetRouter" not in mccl.__all__
      and "MRouter" in ns and "HetRouter" not in ns
      and HetRouter is MRouter)
sys.exit(0 if ok else 1)
PY
check "installed package: MRouter public, legacy alias present but unadvertised" $?

rm -rf "$SCRATCH"
printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
