set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=$D/runtime/cli:$D/runtime/serving:$D/runtime/scheduler:$D/runtime/fabric
echo "########## all suites ##########"
(cd $D/runtime/mccl && PYTHONPATH=src /opt/hetenv/bin/python -m pytest -q 2>&1 | tail -1)
/opt/hetenv/bin/python -m pytest -q $D/runtime/fabric $D/runtime/scheduler $D/runtime/cli 2>&1 | tail -2
echo "########## CPU smoke ##########"
bash $D/scripts/repro/wsl_pipeline_smoke.sh prefill 2>&1 | tail -2
echo "########## two-GPU smoke ##########"
printf 'Explain cross-vendor GPU inference in two sentences.\nName one limitation.\n/bye\n' \
  | /opt/hetenv/bin/python $D/runtime/cli/chat.py --model mistral-nemo \
      --max-new-tokens 40 --port 32021
