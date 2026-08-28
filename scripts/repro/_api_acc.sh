set -u
cd /mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=runtime/serving:runtime/scheduler:runtime/fabric:runtime/cli:runtime/mccl/src
pkill -f 'serve.py --model' 2>/dev/null || true
rm -rf /opt/openmycelium/run /opt/openmycelium/acc* 2>/dev/null || true
/opt/hetenv/bin/python scripts/repro/api_acceptance.py
