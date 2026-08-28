#!/usr/bin/env bash
# Freeze a candidate: record the wheel hash and the installed-content hash, and
# stage the artifacts a clean bootstrap will install. Earlier candidates are
# left exactly as they are.
set -uo pipefail
V=${1:?usage: freeze_version.sh 0.1.0aN}
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
OUT=$REPO/release/$V
SCRATCH=/tmp/${V}probe
mkdir -p "$OUT"

echo "  == wheel hashes =="
cd "$REPO/dist" || exit 1
sha256sum "openmycelium-$V-py3-none-any.whl" \
          openmycelium_mccl-0.2.0a2-py3-none-any.whl \
          openmycelium_mccl-0.2.0a2.tar.gz > "$OUT/SHA256SUMS.frozen"
sed 's/^/    /' "$OUT/SHA256SUMS.frozen"

echo
echo "  == stage (never overwriting) =="
for w in "openmycelium-$V-py3-none-any.whl" \
         openmycelium_mccl-0.2.0a2-py3-none-any.whl \
         openmycelium_mccl-0.2.0a2.tar.gz; do
  if [ -e "$OUT/$w" ]; then echo "    refusing to overwrite $w"
  else cp "$REPO/dist/$w" "$OUT/$w" && echo "    staged $w"; fi
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
