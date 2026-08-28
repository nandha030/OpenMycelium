#!/usr/bin/env bash
# Freeze 0.1.0a6: record the wheel hash and the installed-content hash, and
# stage the artifacts the clean bootstrap will install.
#
# 0.1.0a5 is left exactly as it was. It is the version that failed clean
# provisioning, and that record is worth keeping.
set -uo pipefail
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
OUT=$REPO/release/0.1.0a6
SCRATCH=/tmp/a6probe
mkdir -p "$OUT"

echo "  == wheel hashes =="
cd "$REPO/dist" || exit 1
sha256sum openmycelium-0.1.0a6-py3-none-any.whl \
          openmycelium_mccl-0.2.0a2-py3-none-any.whl \
          openmycelium_mccl-0.2.0a2.tar.gz > "$OUT/SHA256SUMS.frozen"
sed 's/^/    /' "$OUT/SHA256SUMS.frozen"

echo
echo "  == stage the artifacts (never overwriting) =="
for w in openmycelium-0.1.0a6-py3-none-any.whl \
         openmycelium_mccl-0.2.0a2-py3-none-any.whl \
         openmycelium_mccl-0.2.0a2.tar.gz; do
  if [ -e "$OUT/$w" ]; then
    echo "    refusing to overwrite $w"
  else
    cp "$REPO/dist/$w" "$OUT/$w" && echo "    staged $w"
  fi
done

echo
echo "  == installed-content hash, from a scratch install =="
rm -rf "$SCRATCH"
python3 -m venv "$SCRATCH" >/dev/null 2>&1
"$SCRATCH/bin/pip" install -q "$OUT"/*.whl >/dev/null 2>&1
cd /tmp || exit 1
"$SCRATCH/bin/openmycelium" version --json > "$OUT/provenance.json" 2>/dev/null
"$SCRATCH/bin/python" -c "
import json
r = json.load(open('$OUT/provenance.json'))
for k in ('openmycelium', 'installedContentSha256', 'pythonFiles',
          'mcclVersion', 'transport', 'eventSchemaVersion',
          'placementSchemaVersion'):
    print(f'    {k:<26} {r.get(k)}')
print()
print('    CONTENT_HASH=' + str(r.get('installedContentSha256')))
"
rm -rf "$SCRATCH"
