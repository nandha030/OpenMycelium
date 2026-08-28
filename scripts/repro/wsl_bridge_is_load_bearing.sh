#!/usr/bin/env bash
# Proves the cross-group payload really travels over MCCL and not over the
# global Gloo group. With no coordinator running, a multi-group reduction must
# fail; a single-group one (which needs no bridge) must still succeed.
set -uo pipefail
VENV=/opt/hetenv
REPO=/mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH="$REPO/runtime"
cd "$REPO/runtime"
DEAD_PORT=29599   # deliberately nothing listening here

echo "=== A: two groups, coordinator DOWN -- must FAIL ==="
timeout 90 $VENV/bin/python -m mycelium.launch_hierarchy \
    --topology cpu:2,cpu:2 --coordinator-port $DEAD_PORT --master-port 29410 \
    -- $VENV/bin/python -m mycelium.hierarchy_check >/dev/null 2>&1
A=$?
echo "exit=$A  (non-zero means the bridge is load-bearing)"

echo "=== B: one group, coordinator DOWN -- must PASS (no bridge needed) ==="
timeout 90 $VENV/bin/python -m mycelium.launch_hierarchy \
    --topology cpu:4 --coordinator-port $DEAD_PORT --master-port 29411 \
    -- $VENV/bin/python -m mycelium.hierarchy_check >/dev/null 2>&1
B=$?
echo "exit=$B  (zero means single-group reduction is unaffected)"

if [ "$A" -ne 0 ] && [ "$B" -eq 0 ]; then
  echo "RESULT: PASS -- cross-group payload depends on MCCL"
  exit 0
fi
echo "RESULT: FAIL -- A=$A B=$B"
exit 1
