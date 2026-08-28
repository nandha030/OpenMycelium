#!/usr/bin/env bash
# Prefill sweep and 32-step cached decode, both stages on their own vendor.
#
#   ./wsl_prefill_decode.sh prefill      sequence lengths 1..2048, no cache
#   ./wsl_prefill_decode.sh oracle       prefill + cached decode vs recompute
#
# Same coordination as the boundary test: the receiver binds, writes a readiness
# file, and only then does the sender connect. Every wait is bounded, so a stage
# that dies during its ten-minute weight load fails the run instead of hanging it.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$S/runtime/serving:$S/runtime/scheduler:$S/runtime/fabric:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
RUN="$S/runtime/serving/pipeline_run.py"

MODE=${1:-prefill}
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
SEQ_LENS=${SEQ_LENS:-1,8,64,256,2048}
PROMPT_LEN=${PROMPT_LEN:-16}
DECODE_STEPS=${DECODE_STEPS:-32}
PORT=${PORT:-31900}
LIMIT=${LIMIT:-2400}
READY=/opt/pipeline_ready.$PORT
PLACEMENT=/opt/pipeline_placement.$PORT.json
OUT_C=/opt/pipeline_cuda.$MODE.json
OUT_R=/opt/pipeline_rocm.$MODE.json

rm -f "$READY" "$PLACEMENT" "$OUT_C" "$OUT_R"
/opt/hetenv/bin/python "$S/runtime/cli/scheduler_cli.py" --model "$MODEL" \
    --context-length 4096 --cuda-budget 14GiB --rocm-budget 14GiB \
    --output "$PLACEMENT" >/opt/pipeline_plan.$PORT.log || exit 1
# One run id for both workers: two ids would be two runs.
RUN_ID=${RUN_ID:-$(cat /proc/sys/kernel/random/uuid)}
COMMON="--model $MODEL --mode $MODE --seq-lens $SEQ_LENS --prompt-len $PROMPT_LEN \
        --decode-steps $DECODE_STEPS --run-id $RUN_ID --port $PORT --ready-file $READY \
        --placement $PLACEMENT --accept-timeout 1200 --progress"

echo "mode=$MODE  seq-lens=$SEQ_LENS  decode-steps=$DECODE_STEPS"
echo "starting ROCm stage (layers 20-39 + head) ..."
timeout $LIMIT /opt/rocmenv/bin/python "$RUN" --role rocm $COMMON \
    >"$OUT_R" 2>/opt/pipeline_rocm.err &
R=$!

DEADLINE=$((SECONDS + LIMIT))
while [ ! -f "$READY" ]; do
  if ! kill -0 $R 2>/dev/null; then
    echo "ROCm stage exited before signalling readiness:"
    grep -vE 'NumPy|conversion_method|Warning' /opt/pipeline_rocm.err | tail -8
    exit 1
  fi
  [ $SECONDS -gt $DEADLINE ] && { echo "ROCm stage never became ready"; kill $R; exit 1; }
  sleep 3
done
echo "ROCm stage ready; starting CUDA stage (embedding + layers 0-19) ..."

timeout $LIMIT /opt/hetenv/bin/python "$RUN" --role cuda --peer 127.0.0.1 $COMMON \
    >"$OUT_C" 2>/opt/pipeline_cuda.err
CRC=$?
wait $R 2>/dev/null; RRC=$?
rm -f "$READY" "$PLACEMENT"

for f in /opt/pipeline_cuda.err /opt/pipeline_rocm.err; do
  if grep -qiE 'traceback|error:' "$f" 2>/dev/null; then
    echo "--- $(basename $f) ---"
    grep -vE 'NumPy|conversion_method|Warning|tokenizer you are loading' "$f" | tail -12
  fi
done

/opt/hetenv/bin/python "$S/scripts/repro/pipeline_report.py" "$OUT_C" "$OUT_R" \
    "$MODE" "$CRC" "$RRC"
