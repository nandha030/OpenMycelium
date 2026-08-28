#!/usr/bin/env bash
# openmycelium-infer against a local safetensors checkpoint.
#   bash wsl_infer.sh inspect
#   bash wsl_infer.sh plan --context-length 8192
S=/mnt/c/Users/User/Documents/Open_Mycelium
M=${MODEL:-/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407}
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
exec /opt/hetenv/bin/python "$S/runtime/serving/cli_infer.py" "$@" --model "$M"
