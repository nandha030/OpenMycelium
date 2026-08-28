#!/usr/bin/env bash
# Prefill on one vendor, decode on another, and prove the split run produces
# exactly what a single device would have produced.
set -uo pipefail
VENV=/opt/hetenv
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$REPO/runtime"
export MCCL_KV_AUTH_TOKEN="demo-token"
cd "$REPO/runtime"

PREFILL_VENDOR="${1:-cuda}"
DECODE_VENDOR="${2:-rocm}"
PORT="${3:-29600}"

echo "=== starting MCCL KV-cache broker on 127.0.0.1:$PORT ==="
$VENV/bin/mccl kv-serve --host 127.0.0.1 --port "$PORT" --max-mib 256 --ttl 120 &
BROKER=$!
trap 'kill $BROKER 2>/dev/null' EXIT
sleep 2

echo "=== 1. reference: whole request on one device ==="
REF=$($VENV/bin/python -m mycelium.cross_vendor_infer --role reference --vendor "$PREFILL_VENDOR" 2>/dev/null)
echo "$REF"

echo "=== 2. prefill stage on $PREFILL_VENDOR ==="
PRE=$($VENV/bin/python -m mycelium.cross_vendor_infer --role prefill --vendor "$PREFILL_VENDOR" --broker-port "$PORT" 2>/dev/null)
echo "$PRE" | $VENV/bin/python -c "import json,sys; d=json.load(sys.stdin); d.pop('lastHidden',None); print(json.dumps(d,sort_keys=True))"

export DEMO_LAST_HIDDEN=$(echo "$PRE" | $VENV/bin/python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['lastHidden']))")

echo "=== 3. decode stage on $DECODE_VENDOR ==="
DEC=$($VENV/bin/python -m mycelium.cross_vendor_infer --role decode --vendor "$DECODE_VENDOR" --broker-port "$PORT" 2>/dev/null)
echo "$DEC"

echo "=== 4. verdict ==="
REF_T=$(echo "$REF" | $VENV/bin/python -c "import json,sys; print(json.load(sys.stdin)['tokens'])")
DEC_T=$(echo "$DEC" | $VENV/bin/python -c "import json,sys; print(json.load(sys.stdin)['tokens'])")
echo "single-device tokens : $REF_T"
echo "split-vendor tokens  : $DEC_T"
if [ "$REF_T" = "$DEC_T" ] && [ -n "$REF_T" ]; then
  echo "RESULT: PASS -- the split request produced identical output"
  exit 0
fi
echo "RESULT: FAIL"
exit 1
