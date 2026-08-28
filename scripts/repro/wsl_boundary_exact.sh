#!/usr/bin/env bash
# Integrated boundary verification: does the bridge preserve the tensor exactly?
#
#   CUDA boundary immediately BEFORE send   ==   ROCm boundary immediately AFTER receive
#
# The generic transport suite cannot answer this: it tests the transport with its
# own buffers, not the model path's dtype, shape, strides and framing. Only a
# comparison of the two contracts on either side of the real bridge does.
#
# Coordinated: the receiver binds and signals readiness, the sender waits for that
# signal, and every wait is bounded so a missing peer fails instead of hanging.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
FP="$S/runtime/serving/forward_pass.py"

MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
BUDGETS=${BUDGETS:-}
TOKEN=${TOKEN:-1234}
REPEAT=${REPEAT:-2}
PORT=${PORT:-31600}
LOAD_TIMEOUT=${LOAD_TIMEOUT:-900}
READY=/opt/boundary_ready.$PORT

rm -f "$READY" /opt/b_cuda.json /opt/b_rocm.json

echo "starting receiver (ROCm) ..."
timeout $LOAD_TIMEOUT /opt/rocmenv/bin/python "$FP" --model "$MODEL" --role rocm \
    --token $TOKEN --repeat $REPEAT --port $PORT --ready-file "$READY" \
    --accept-timeout 300 $BUDGETS >/opt/b_rocm.json 2>/opt/b_rocm.err &
R=$!

# Bounded wait for readiness; a receiver that dies during load must not hang us.
DEADLINE=$((SECONDS + LOAD_TIMEOUT))
while [ ! -f "$READY" ]; do
  if ! kill -0 $R 2>/dev/null; then
    echo "receiver exited before signalling readiness:"
    tail -5 /opt/b_rocm.err 2>/dev/null | grep -vE 'NumPy|conversion_method|Warning'
    exit 1
  fi
  [ $SECONDS -gt $DEADLINE ] && { echo "receiver never became ready"; kill $R; exit 1; }
  sleep 2
done
echo "receiver ready on port $PORT; starting sender (CUDA) ..."

timeout $LOAD_TIMEOUT /opt/hetenv/bin/python "$FP" --model "$MODEL" --role cuda \
    --token $TOKEN --repeat $REPEAT --port $PORT --peer 127.0.0.1 \
    --ready-file "$READY" --accept-timeout 300 $BUDGETS \
    >/opt/b_cuda.json 2>/opt/b_cuda.err
CRC=$?
wait $R 2>/dev/null; RRC=$?
rm -f "$READY"

for f in /opt/b_cuda.err /opt/b_rocm.err; do
  if [ -s "$f" ] && grep -qiE 'traceback|error' "$f"; then
    echo "--- $(basename $f) ---"
    grep -vE 'NumPy|conversion_method|Warning: Resource' "$f" | tail -8
  fi
done

/opt/hetenv/bin/python - "$CRC" "$RRC" <<'PY'
import json, sys
crc, rrc = int(sys.argv[1]), int(sys.argv[2])
def last(p):
    try:
        return json.loads(open(p).read().strip().splitlines()[-1])
    except Exception as e:
        return {"error": str(e)}
cuda, rocm = last("/opt/b_cuda.json"), last("/opt/b_rocm.json")
if "error" in cuda or "error" in rocm:
    print("  cuda:", cuda.get("error", "ok"), "\n  rocm:", rocm.get("error", "ok"))
    raise SystemExit(1)

sent = cuda.get("sentContract", [])
got = rocm.get("receivedContract", [])
if not sent or not got or len(sent) != len(got):
    print(f"  contracts missing or unequal counts: sent {len(sent)}, received {len(got)}")
    raise SystemExit(1)

print(f"  transfers compared: {len(sent)}")
all_ok = True
for i, (s, g) in enumerate(zip(sent, got)):
    checks = {
        "shape": s["shape"] == g["shape"],
        "dtype": s["dtype"] == g["dtype"],
        "contiguous": s["contiguous"] and g["contiguous"],
        "strides": s["strides"] == g["strides"],
        "elements": s["elements"] == g["elements"],
        "byteCount": s["bytes"] == g["bytes"],
        "sha256": s["sha256"] == g["sha256"],
    }
    ok = all(checks.values())
    all_ok = all_ok and ok
    print(f"\n  --- transfer {i} ---")
    print(f"    sent      {s['shape']} {s['dtype']} strides={s['strides']} "
          f"{s['bytes']} B")
    print(f"    received  {g['shape']} {g['dtype']} strides={g['strides']} "
          f"{g['bytes']} B")
    print(f"    sha256 sent      {s['sha256']}")
    print(f"    sha256 received  {g['sha256']}")
    failed = [k for k, v in checks.items() if not v]
    print(f"    {'all checks pass' if ok else 'FAILED: ' + ', '.join(failed)}")

print()
ok = all_ok and crc == 0 and rrc == 0
print("RESULT:", "PASS - the integrated bridge preserves the boundary tensor "
      "byte-for-byte" if ok else "FAIL - the bridge altered the boundary tensor")
raise SystemExit(0 if ok else 1)
PY
