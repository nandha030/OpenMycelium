set -u
cd /mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=runtime/serving:runtime/scheduler:runtime/fabric:runtime/cli:runtime/mccl/src
mkdir -p /opt/openmycelium/baseline
/opt/hetenv/bin/python runtime/cli/scheduler_cli.py --model mistral-nemo \
    --output /opt/openmycelium/baseline/placement.json 2>&1 \
  | grep -vE "NumPy|conversion" | head -12
echo
/opt/hetenv/bin/python scripts/repro/baseline_campaign.py \
  --model /opt/models/Mistral-Nemo-Instruct-2407 \
  --placement /opt/openmycelium/baseline/placement.json \
  --sessions 5 --warmups 3 --steady 12 --prompt-lengths 8,64,256 \
  --max-new-tokens 16 --port 32400 \
  --out /opt/openmycelium/baseline 2>&1 | grep -vE "NumPy|conversion|tokenizer you are"
