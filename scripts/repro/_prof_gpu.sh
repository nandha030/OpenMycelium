set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=$D/runtime/serving:$D/runtime/scheduler:$D/runtime/fabric:$D/runtime/mccl/src
/opt/hetenv/bin/python $D/scripts/repro/profiler_validate.py /opt/models/Mistral-Nemo-Instruct-2407 --gpu
