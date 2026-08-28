#!/usr/bin/env bash
set -uo pipefail
VENV=/opt/hetenv
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$REPO/runtime"
TOPO="${1:-cpu:2,cpu:2}"
PORT="${2:-29500}"

echo "=== starting MCCL coordinator on 127.0.0.1:$PORT ==="
$VENV/bin/mccl serve --host 127.0.0.1 --port "$PORT" &
COORD=$!
trap 'kill $COORD 2>/dev/null' EXIT
sleep 2

echo "=== hierarchical all-reduce: topology $TOPO ==="
cd "$REPO/runtime"
$VENV/bin/python -m mycelium.launch_hierarchy \
    --topology "$TOPO" --coordinator-port "$PORT" --master-port 29400 \
    -- $VENV/bin/python -m mycelium.hierarchy_check
STATUS=$?
echo "=== launcher exit: $STATUS ==="
exit $STATUS
