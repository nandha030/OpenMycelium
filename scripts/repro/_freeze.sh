set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium
OUT=$D/release/0.1.0a5
mkdir -p "$OUT"
cd "$D/dist" || exit 1
echo "  == wheel hashes (full) =="
sha256sum openmycelium-0.1.0a5-py3-none-any.whl \
          openmycelium_mccl-0.2.0a2-py3-none-any.whl \
          openmycelium_mccl-0.2.0a2.tar.gz | tee "$OUT/SHA256SUMS.frozen" | sed 's/^/    /'
echo
echo "  == sizes =="
stat -c '    %s bytes  %n' openmycelium-0.1.0a5-py3-none-any.whl \
     openmycelium_mccl-0.2.0a2-py3-none-any.whl
echo
echo "  == provenance =="
export PATH=/opt/omfresh/venv/bin:$PATH
unset PYTHONPATH
openmycelium version --json > "$OUT/provenance.json" 2>/dev/null
/opt/omfresh/venv/bin/python -c "
import json
r=json.load(open('$OUT/provenance.json'))
for k in ('openmycelium','installedContentSha256','pythonFiles','mcclVersion',
          'transport','eventSchemaVersion','placementSchemaVersion'):
    print(f'    {k:<26} {r.get(k)}')
"
cp -f /opt/omportable/placement.json "$OUT/placement.json" 2>/dev/null && \
  echo "    placement manifest preserved"
echo
echo "  == headroom for a fresh distro + two torch environments =="
df -h / | tail -1 | awk '{printf "    WSL rootfs: %s free of %s\n", $4, $2}'
df -h /mnt/c | tail -1 | awk '{printf "    Windows C:  %s free of %s\n", $4, $2}'
echo
echo "  == measured download rate (from the earlier pull) =="
echo "    huggingface.co: 1.4 MB/s"
echo "    a torch CUDA wheel is ~2.5 GiB, ROCm ~3.5 GiB, plus deps"
ls -l "$OUT" | sed 's/^/    /'
