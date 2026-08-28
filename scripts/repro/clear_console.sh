#!/usr/bin/env bash
# Stop any running console, let its in-flight run drain, and confirm the
# machine is idle and the port is free before a gate runs.
set -uo pipefail
OM=/opt/om/venv

echo "  == stop any running console =="
if pkill -f "openmycelium console" 2>/dev/null; then
  echo "    stopped a running console"
else
  echo "    none was running"
fi

echo "  == let any in-flight run drain =="
for _ in $(seq 1 60); do
  n=$(pgrep -fc pipeline_run 2>/dev/null); n=${n:-0}
  [ "$n" = "0" ] && break
  sleep 3
done
n=$(pgrep -fc pipeline_run 2>/dev/null); n=${n:-0}
echo "    workers remaining: $n"

"$OM/bin/openmycelium" stop > /dev/null 2>&1
"$OM/bin/openmycelium" stop --prune > /dev/null 2>&1
sleep 3

n=$(pgrep -fc pipeline_run 2>/dev/null); n=${n:-0}
echo "    after stop --prune: $n worker(s)"
listeners=$(ss -ltn 2>/dev/null | grep -c '11501')
echo "    listeners on 11501: $listeners"
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    CUDA: /'
[ "$n" = "0" ] && [ "$listeners" = "0" ]
