#!/usr/bin/env bash
# Steps 4-7: full stages, concurrent load, budget check.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
REAL=${MODEL:-/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407}
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
CLI="$S/runtime/serving/cli_infer.py"
clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "############ 4. full CUDA stage ############"
/opt/hetenv/bin/python "$CLI" load-check --model "$REAL" --stage cuda 2>&1 | clean
echo
echo "############ 5. full ROCm stage ############"
/opt/rocmenv/bin/python "$CLI" load-check --model "$REAL" --stage rocm 2>&1 | clean
