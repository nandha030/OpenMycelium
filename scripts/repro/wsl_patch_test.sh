#!/usr/bin/env bash
export LD_LIBRARY_PATH=/opt/rocm/lib:/usr/lib/wsl/lib
P=/mnt/c/Users/User/Documents/Open_Mycelium/runtime/bridge/rocm_torch_patch.sh
echo "################ status (currently hand-patched) ################"
bash "$P" status
echo
echo "################ restore, then prove torch is broken again ################"
bash "$P" restore
/opt/rocmenv/bin/python -c "import torch; print('  after restore -> is_available:', torch.cuda.is_available(), 'count:', torch.cuda.device_count())" 2>&1 | grep -vE 'NumPy|conversion_method'
echo
echo "################ apply (with ABI check + smoke test) ################"
bash "$P" apply
echo
echo "################ status again ################"
bash "$P" status | head -16
