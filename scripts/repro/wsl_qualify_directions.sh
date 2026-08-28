#!/usr/bin/env bash
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
export PATH=/opt/rocm/bin:$PATH
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
S=/mnt/c/Users/User/Documents/Open_Mycelium
rm -f "$OM_XVENDOR_LEDGER"

for D in "cuda rocm 22001" "rocm cuda 22002"; do
  set -- $D
  /opt/hetenv/bin/python "$S/runtime/bridge/qualify_direction.py" \
     --send "$1" --recv "$2" --bytes $((64*1024*1024)) --port "$3" 2>&1 | grep -vE 'Warning: Resource'
  echo
done

echo "=== ledger on disk ==="
cat "$OM_XVENDOR_LEDGER" 2>/dev/null | head -30

echo
echo "=== fail-closed check: an unqualified direction must be refused ==="
PYTHONPATH="$S/runtime/mccl/src" /opt/hetenv/bin/python - <<'PY'
from mccl.xvendor import QualificationLedger, TransportError
led = QualificationLedger.load("/opt/xvendor_qualification.json")
print("  qualified directions:", led.directions())
for a, b in (("cuda", "rocm"), ("rocm", "cuda"), ("cuda", "oneapi")):
    try:
        r = led.assert_permitted(a, b)
        print(f"  {a}->{b}: PERMITTED ({r.throughput_mbps} MB/s, chunk {r.chunk_bytes})")
    except TransportError as e:
        print(f"  {a}->{b}: REFUSED - {str(e)[:80]}")
PY
