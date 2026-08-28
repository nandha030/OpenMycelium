#!/usr/bin/env bash
set -euo pipefail
VENV=/opt/hetenv
REPO=/mnt/c/Users/User/Documents/Open_Mycelium

echo "=== installing CPU PyTorch ==="
$VENV/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu

echo "=== installing mccl from the repo ==="
$VENV/bin/pip install -q "$REPO/runtime/mccl"

echo "=== versions ==="
$VENV/bin/python - <<'PY'
import torch, mccl
print("torch:", torch.__version__)
print("gloo available:", torch.distributed.is_gloo_available())
print("nccl available:", torch.distributed.is_nccl_available())
print("cuda available:", torch.cuda.is_available())
print("mccl:", mccl.__version__)
PY
