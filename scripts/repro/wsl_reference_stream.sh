#!/usr/bin/env bash
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export CUDA_VISIBLE_DEVICES=""
/opt/hetenv/bin/python "$S/runtime/serving/reference_stream.py" \
    --model "$MODEL" --token "${TOKEN:-1234}" --progress 2>&1 \
    | grep -vE 'NumPy|conversion_method|Warning: Resource'
