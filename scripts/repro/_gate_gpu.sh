set -u
VENV=/opt/omfresh/venv
WORK=/opt/omportable
export PATH="$VENV/bin:$PATH"
unset PYTHONPATH
export OPENMYCELIUM_STATE="$WORK"
mkdir -p "$WORK"; cd "$WORK"
pkill -f 'serve.py --model' 2>/dev/null || true
pkill -f 'pipeline_run.py' 2>/dev/null || true
FAIL=0
chk() { if [ "$2" = 0 ]; then echo "  [PASS] $1"; else echo "  [FAIL] $1"; FAIL=$((FAIL+1)); fi; }

echo "  == openmycelium run (one-shot, from the wheel) =="
START=$(date +%s)
openmycelium run --model Mistral-Nemo-Instruct-2407 \
  --prompt "Explain cross-vendor GPU inference in one sentence." \
  --max-new-tokens 24 --work-dir "$WORK" 2>&1 \
  | grep -vE 'NumPy|conversion|tokenizer you are' | tail -18
RC=${PIPESTATUS[0]}
chk "openmycelium run" $RC
echo "  wall clock: $(( $(date +%s) - START ))s"

echo
echo "  == openmycelium chat (multi-turn, from the wheel) =="
printf 'Explain cross-vendor GPU inference in one sentence.\nName one limitation.\n/bye\n' \
 | openmycelium chat --model Mistral-Nemo-Instruct-2407 --max-new-tokens 24 \
     --port 32105 --work-dir "$WORK/chat" 2>&1 \
 | grep -vE 'NumPy|conversion|tokenizer you are' | tail -14
chk "openmycelium chat" ${PIPESTATUS[0]}

echo
echo "  == no leftovers =="
LEFT=$(ps -eo args | grep -E 'pipeline_run|serve.py' | grep -v grep | wc -l)
chk "no orphan workers ($LEFT)" $([ "$LEFT" = 0 ] && echo 0 || echo 1)
VRAM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
chk "CUDA VRAM released (${VRAM} MiB)" $([ "$VRAM" -lt 2000 ] && echo 0 || echo 1)
echo
echo "  $FAIL check(s) failed"
exit $FAIL
