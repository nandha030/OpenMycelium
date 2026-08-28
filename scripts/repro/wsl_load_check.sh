#!/usr/bin/env bash
# Selective stage loader: synthetic first, then the real 23 GiB checkpoint.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
REAL=${MODEL:-/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407}
TINY=/opt/tiny-nemo
CUDA_PY=/opt/hetenv/bin/python
ROCM_PY=/opt/rocmenv/bin/python
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
CLI="$S/runtime/serving/cli_infer.py"

clean() { grep -vE 'NumPy|conversion_method|Warning: Resource|^$'; }

echo "############ 1. synthetic tiny checkpoint ############"
$CUDA_PY "$S/runtime/serving/make_synthetic.py" "$TINY" --layers 8 --hidden 256 --shards 3 | clean
echo
echo "--- inspect ---"
$CUDA_PY "$CLI" inspect --model "$TINY" --cuda-budget 1GiB --rocm-budget 1GiB \
    --context-length 512 2>&1 | clean | head -12
echo
echo "--- load cuda stage (real device) ---"
$CUDA_PY "$CLI" load-check --model "$TINY" --stage cuda --cuda-budget 1GiB \
    --rocm-budget 1GiB --context-length 512 2>&1 | clean
echo
echo "--- load rocm stage (real device) ---"
$ROCM_PY "$CLI" load-check --model "$TINY" --stage rocm --cuda-budget 1GiB \
    --rocm-budget 1GiB --context-length 512 2>&1 | clean

echo
echo "############ 2. metadata-only dry run, real checkpoint ############"
for STAGE in cuda rocm; do
  $CUDA_PY "$CLI" load-check --model "$REAL" --stage $STAGE --dry-run 2>&1 | clean \
    | grep -E 'Stage|Assigned tensors|Assigned weight|Every tensor|Missing|Unexpected|Duplicated'
  echo
done

echo "############ 3. one real layer on each GPU ############"
echo "--- cuda, first 9 tensors ---"
$CUDA_PY "$CLI" load-check --model "$REAL" --stage cuda --limit 9 2>&1 | clean \
  | grep -E 'GPU identity|Loaded tensors|Actual GPU|Peak CPU|Device allocation'
echo "--- rocm, first 9 tensors ---"
$ROCM_PY "$CLI" load-check --model "$REAL" --stage rocm --limit 9 2>&1 | clean \
  | grep -E 'GPU identity|Loaded tensors|Actual GPU|Peak CPU|Device allocation'

echo
echo "############ 4. full CUDA stage, then unload ############"
$CUDA_PY "$CLI" load-check --model "$REAL" --stage cuda 2>&1 | clean

echo
echo "############ 5. full ROCm stage, then unload ############"
$ROCM_PY "$CLI" load-check --model "$REAL" --stage rocm 2>&1 | clean
