#!/usr/bin/env bash
ROOT=/opt/cudaroot
UCX=/opt/ucx-cuda
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro

echo "=== compile ==="
gcc "$S/ucx_atomic_repro.c" -o /tmp/repro \
  -I$UCX/include -I$ROOT/include \
  -L$UCX/lib -lucp -lucs -L$ROOT/lib64 -lcudart 2>&1 | head -10
[ -x /tmp/repro ] || { echo "COMPILE FAILED"; exit 1; }
echo "compiled"

echo "=== run with diagnostics (per the suggested protocol) ==="
ulimit -c unlimited
export UCX_HANDLE_ERRORS=bt
/tmp/repro 2>&1 | tail -20
echo "exit: $?"
