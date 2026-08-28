set -u
cd /mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=runtime/serving:runtime/scheduler:runtime/fabric:runtime/cli:runtime/mccl/src
mkdir -p /opt/blsmoke && rm -rf /opt/blsmoke/* 
/opt/hetenv/bin/python - <<PY 2>&1 | grep -vE "NumPy|conversion"
import sys
sys.path[:0]=["runtime/scheduler","runtime/serving","runtime/fabric"]
from placement import create_placement, write_placement
from model_inspect import parse_size
f={"schemaVersion":2,"identitiesUnique":True,"devices":[],"probedAt":0.0,"fromCache":False}
m=create_placement("/opt/models/tiny-mistral",f,parse_size("48MiB"),parse_size("48MiB"),512,allow_cpu=True)
write_placement("/opt/blsmoke/placement.json",m)
print("boundaryAfterLayer:",m["pipeline"]["boundaryAfterLayer"])
PY
/opt/hetenv/bin/python scripts/repro/baseline_campaign.py \
  --model /opt/models/tiny-mistral --placement /opt/blsmoke/placement.json \
  --expect-boundary 3 --allow-cpu --sessions 3 --warmups 2 --steady 4 \
  --prompt-lengths 8,32 --max-new-tokens 4 --context-length 512 \
  --port 32300 --out /opt/blsmoke/out 2>&1 | grep -vE "NumPy|conversion|tokenizer you are"
