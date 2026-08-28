#!/usr/bin/env bash
# Exercise the whole driver on a tiny CPU checkpoint before spending twenty
# minutes loading the real one. Same processes, same socket, same protocol.
set -uo pipefail
S=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$S/runtime/serving:$S/runtime/scheduler:$S/runtime/mccl/src"
PY=/opt/hetenv/bin/python
MODEL=${MODEL:-/opt/models/tiny-mistral}
MODE=${1:-prefill}
PORT=${PORT:-31955}
READY=/opt/smoke_ready.$PORT
PLACEMENT=/opt/smoke_placement.$PORT.json
rm -f "$READY" "$PLACEMENT" /opt/smoke_cuda.json /opt/smoke_rocm.json

[ -f "$MODEL/model.safetensors" ] || \
  $PY "$S/scripts/repro/make_tiny_checkpoint.py" 2>&1 | grep -vE 'NumPy|conversion'

$PY "$S/runtime/cli/scheduler_cli.py" --model "$MODEL" --allow-cpu \
    --context-length 512 --cuda-budget 48MiB --rocm-budget 48MiB \
    --output "$PLACEMENT" >/opt/smoke_plan.log || exit 1

# One run id for both workers: two ids would be two runs.
RUN_ID=${RUN_ID:-$(cat /proc/sys/kernel/random/uuid)}
COMMON="--model $MODEL --mode $MODE --allow-cpu --seq-lens ${SEQ_LENS:-1,8,64} \
        --prompt-len ${PROMPT_LEN:-8} --decode-steps ${DECODE_STEPS:-6} \
        --placement $PLACEMENT --context-length 512 \
        --run-id $RUN_ID --port $PORT --ready-file $READY --accept-timeout 120"

timeout 600 $PY "$S/runtime/serving/pipeline_run.py" --role rocm $COMMON \
    >/opt/smoke_rocm.json 2>/opt/smoke_rocm.err &
R=$!
D=$((SECONDS + 300))
while [ ! -f "$READY" ]; do
  kill -0 $R 2>/dev/null || { echo "receiver died:"; \
      grep -vE 'NumPy|conversion|Warning|tokenizer you are' /opt/smoke_rocm.err | tail -12; exit 1; }
  [ $SECONDS -gt $D ] && { echo "receiver never ready"; kill $R; exit 1; }
  sleep 1
done
timeout 600 $PY "$S/runtime/serving/pipeline_run.py" --role cuda --peer 127.0.0.1 \
    $COMMON >/opt/smoke_cuda.json 2>/opt/smoke_cuda.err
CRC=$?
wait $R 2>/dev/null; RRC=$?
rm -f "$READY" "$PLACEMENT"
for f in /opt/smoke_cuda.err /opt/smoke_rocm.err; do
  grep -qiE 'traceback' "$f" && { echo "--- $(basename $f) ---"; \
      grep -vE 'NumPy|conversion|Warning|tokenizer you are' "$f" | tail -14; }
done
$PY "$S/scripts/repro/pipeline_report.py" /opt/smoke_cuda.json /opt/smoke_rocm.json \
    "$MODE" "$CRC" "$RRC"
