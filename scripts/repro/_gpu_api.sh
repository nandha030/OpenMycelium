set -u
mkdir -p /opt/omgpu && cd /opt/omgpu
unset PYTHONPATH
PATH="/opt/omfresh/venv/bin:$PATH"; export PATH
export OPENMYCELIUM_STATE=/opt/omgpu
pkill -f 'serve.py --model' 2>/dev/null || true
pkill -f 'pipeline_run.py' 2>/dev/null || true
rm -rf /opt/omgpu/run /opt/omgpu/events.jsonl 2>/dev/null || true
# The client SDK is a test dependency, not a package dependency: a server has no
# business depending on the client library that talks to it.
/opt/omfresh/venv/bin/pip install -q openai 2>&1 | tail -1
/opt/omfresh/venv/bin/python /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro/gpu_api_validation.py
