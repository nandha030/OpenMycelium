#!/usr/bin/env bash
# Byte-exact boundary verification on the clean distribution, from the
# installed wheel.
#
# The activation leaving the CUDA stage and the one arriving at the ROCm stage
# are compared by SHA-256, along with shape, dtype, strides and byte count. A
# transport that dropped, reordered or reinterpreted a single byte fails here.
#
# This runs adjacent to the throughput sessions, never inside a timed request:
# hashing a full activation costs far more than the transfer it is checking, so
# doing it during a measured request would corrupt the number being measured.
set -uo pipefail
P=/opt/om/venv/lib/python3.12/site-packages/openmycelium
FP="$P/runtime/serving/forward_pass.py"
CUDA=/var/lib/openmycelium/state/env/cuda/bin/python
ROCM=/var/lib/openmycelium/state/env/rocm/bin/python
MODEL=${MODEL:-/var/lib/openmycelium/models/Mistral-Nemo-Instruct-2407}
OUT=${OUT:-/var/log/om-a8}
LABEL=${1:-boundary}
PORT=${PORT:-31640}
TOKEN=${TOKEN:-1234}
REPEAT=${REPEAT:-2}
READY=/tmp/boundary_ready.$PORT
mkdir -p "$OUT"
export PYTHONPATH="$P/runtime/serving"
export LD_LIBRARY_PATH=/opt/rocm/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
rm -f "$READY" "$OUT/$LABEL-cuda.json" "$OUT/$LABEL-rocm.json"

timeout 900 "$ROCM" "$FP" --model "$MODEL" --role rocm --token $TOKEN \
  --repeat $REPEAT --port $PORT --ready-file "$READY" --accept-timeout 300 \
  > "$OUT/$LABEL-rocm.json" 2> "$OUT/$LABEL-rocm.err" &
R=$!
DEADLINE=$((SECONDS + 900))
while [ ! -f "$READY" ]; do
  if ! kill -0 $R 2>/dev/null; then
    echo "  receiver exited before signalling readiness:"
    grep -vE 'NumPy|conversion_method|Warning' "$OUT/$LABEL-rocm.err" | tail -6 \
      | sed 's/^/    /'
    exit 1
  fi
  [ $SECONDS -gt $DEADLINE ] && { echo "  receiver never ready"; kill $R; exit 1; }
  sleep 2
done

timeout 900 "$CUDA" "$FP" --model "$MODEL" --role cuda --token $TOKEN \
  --repeat $REPEAT --port $PORT --peer 127.0.0.1 --ready-file "$READY" \
  --accept-timeout 300 > "$OUT/$LABEL-cuda.json" 2> "$OUT/$LABEL-cuda.err"
wait $R 2>/dev/null
rm -f "$READY"

"$CUDA" - "$OUT/$LABEL-cuda.json" "$OUT/$LABEL-rocm.json" <<'PY'
import json, sys

def last(path):
    try:
        return json.loads(open(path).read().strip().splitlines()[-1])
    except Exception as error:
        return {"error": str(error)}

cuda, rocm = last(sys.argv[1]), last(sys.argv[2])
if "error" in cuda or "error" in rocm:
    print(f"    cuda: {cuda.get('error', 'ok')}")
    print(f"    rocm: {rocm.get('error', 'ok')}")
    raise SystemExit(1)
sent, got = cuda.get("sentContract", []), rocm.get("receivedContract", [])
if not sent or len(sent) != len(got):
    print(f"    contracts unequal: sent {len(sent)}, received {len(got)}")
    raise SystemExit(1)

all_ok = True
for index, (s, g) in enumerate(zip(sent, got)):
    checks = {
        "shape": s["shape"] == g["shape"],
        "dtype": s["dtype"] == g["dtype"],
        "strides": s["strides"] == g["strides"],
        "elements": s["elements"] == g["elements"],
        "byteCount": s["bytes"] == g["bytes"],
        "sha256": s["sha256"] == g["sha256"],
    }
    ok = all(checks.values())
    all_ok = all_ok and ok
    print(f"    transfer {index}: {s['shape']} {s['dtype']} {s['bytes']} B")
    print(f"      sha256 sent     {s['sha256'][:32]}")
    print(f"      sha256 received {g['sha256'][:32]}")
    print(f"      {'byte-exact' if ok else 'MISMATCH: ' + str(checks)}")
print(f"    {len(sent)} transfer(s), byteExact={all_ok}")
raise SystemExit(0 if all_ok else 1)
PY
