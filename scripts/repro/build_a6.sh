#!/usr/bin/env bash
# Build 0.1.0a6 and confirm the provisioning fix resolves what it claims to.
set -uo pipefail
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
PY=/opt/omfresh/venv/bin/python

echo "  == the resolution that failed on 0.1.0a5 =="
echo "  PyPI, with no vendor index in the picture:"
$PY -m pip install --dry-run --ignore-installed --quiet \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy \
    --report /tmp/pypi_report.json > /tmp/pypi.log 2>&1
RC=$?
if [ "$RC" = "0" ]; then
  $PY -c "
import json
r = json.load(open('/tmp/pypi_report.json'))
names = sorted(i['metadata']['name'] for i in r['install'])
print(f'    resolved {len(names)} packages: ' + ', '.join(names[:8]))
"
  echo "    [PASS] the four non-torch pins resolve from PyPI"
else
  echo "    [FAIL] still unresolvable:"
  tail -4 /tmp/pypi.log | sed 's/^/      /'
fi

echo
echo "  == vendor index still answers for torch alone =="
for pair in "cu128 torch==2.11.0" "rocm7.0 torch==2.10.0"; do
  idx=${pair%% *}; pkg=${pair#* }
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 40 \
         "https://download.pytorch.org/whl/$idx/torch/")
  echo "    $idx index HTTP $code for $pkg"
done

echo
echo "  == build =="
cd "$REPO" || exit 1
$PY scripts/build_openmycelium_wheel.py 2>&1 | tail -14 | sed 's/^/    /'
echo
ls -l "$REPO/dist" | grep -E '0\.1\.0a6|mccl' | sed 's/^/    /'
