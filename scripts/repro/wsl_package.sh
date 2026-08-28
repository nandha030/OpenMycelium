#!/usr/bin/env bash
# Build openmycelium-mccl, then validate it in a clean venv.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
export PATH=/opt/rocm/bin:$PATH
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
S=/mnt/c/Users/User/Documents/Open_Mycelium
PKG=$S/runtime/mccl
DIST=/opt/dist
CLEAN=/opt/mccl-clean

echo "############ 1. build wheel and sdist ############"
rm -rf "$DIST" "$PKG/build" "$PKG"/src/*.egg-info
/opt/hetenv/bin/pip install -q --upgrade build >/dev/null 2>&1
cd "$PKG"
/opt/hetenv/bin/python -m build --outdir "$DIST" 2>&1 | tail -4
ls -l "$DIST"

echo
echo "############ 2. SHA-256 ############"
cd "$DIST" && sha256sum * | tee SHA256SUMS

echo
echo "############ 3. clean-environment install ############"
rm -rf "$CLEAN"
python3 -m venv "$CLEAN"
"$CLEAN/bin/pip" install -q --upgrade pip
WHL=$(ls "$DIST"/*.whl | head -1)
"$CLEAN/bin/pip" install -q "$WHL"
echo "installed: $("$CLEAN/bin/mccl" --version)"
echo "third-party deps pulled in:"
"$CLEAN/bin/pip" list --format=freeze | grep -viE '^(pip|setuptools|wheel|openmycelium)' | sed 's/^/  /' || echo "  (none)"

echo
echo "############ 4. build native components from the installed package ############"
NATIVE=$("$CLEAN/bin/python" -c "import mccl,os;print(os.path.join(os.path.dirname(mccl.__file__),'native'))")
echo "native sources: $NATIVE"
sh "$NATIVE/build.sh" /opt/mccl-bin 2>&1 | tail -4
export OM_BRIDGE_BIN=/opt/mccl-bin/bridge
export OM_PROBE_BIN=/opt/mccl-bin/host_access_probe

echo
echo "############ 5. mccl diagnose ############"
"$CLEAN/bin/mccl" diagnose > /opt/qualification_report.json 2>&1
"$CLEAN/bin/python" -c "
import json;d=json.load(open('/opt/qualification_report.json'))
print('  package        :', d['package'])
print('  protocol       :', d['protocol'])
print('  gpus           :', d['gpus'])
print('  qualified      :', d['qualifiedDirections'])
print('  receive policy :', {k:v['transport'] for k,v in d['receivePolicy'].items()})
print('  limitations    :', len(d['knownLimitations']), 'disclosed')
"

echo
echo "############ 6. mccl qualify ############"
for D in cuda-to-rocm rocm-to-cuda; do
  "$CLEAN/bin/mccl" qualify --direction "$D" >/opt/q.json 2>&1
  RC=$?
  "$CLEAN/bin/python" -c "
import json,sys
d=json.load(open('/opt/q.json'))
r=d.get('record',{})
print(f\"  {d['direction']:<14} qualified={d['qualified']} \" + (f\"{r.get('throughput_mbps')} MB/s  {r.get('send_gpu')} -> {r.get('recv_gpu')}\" if d['qualified'] else str(d.get('error'))[:60]))
"
done

echo
echo "############ 7. mccl smoke-test ############"
"$CLEAN/bin/mccl" smoke-test
SMOKE=$?

echo
echo "############ summary ############"
echo "  artifacts : $DIST"
echo "  report    : /opt/qualification_report.json"
echo "  smoke-test exit: $SMOKE"
exit $SMOKE
