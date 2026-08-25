#!/usr/bin/env bash
UCX=/opt/ucx-both; ROOT=/opt/cudaroot; ROCM=/opt/rocm-7.2.0
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
export UCX_MEMTYPE_CACHE=n
export UCX_HANDLE_ERRORS=bt
ulimit -c unlimited
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
mkdir -p /opt/bin
BIN=/opt/bin/repro

echo "=== build ==="
gcc "$S/ucx_rocm_recv_repro.c" -o $BIN \
  -I$UCX/include -I$ROCM/include -L$UCX/lib -lucp -lucs \
  -L$ROCM/lib -lamdhip64 2>&1 | head -15
[ -x $BIN ] || { echo "COMPILE FAILED"; exit 1; }
echo "built"
echo

for TLS in "self" "tcp" "rocm_copy,self" "rocm_copy,tcp"; do
  echo "################ UCX_TLS=$TLS ################"
  for MEM in malloc hiphost hipmalloc; do
    OUT=$(UCX_TLS="$TLS" UCX_LOG_LEVEL=error timeout 30 $BIN --mem $MEM --size 4096 2>&1)
    RC=$?
    VERDICT=$(echo "$OUT" | grep -oE 'RESULT (PASS|FAIL|SKIP)[^$]*' | head -1)
    if [ -z "$VERDICT" ]; then
      if [ $RC -eq 139 ]; then VERDICT="SIGSEGV in $(echo "$OUT" | grep -oE 'uct_[a-z_]+' | head -1)"
      elif [ $RC -eq 124 ]; then VERDICT="HANG"
      else VERDICT="no verdict (rc=$RC): $(echo "$OUT" | grep -iE 'error' | head -1 | cut -c1-60)"; fi
    fi
    printf '  %-14s : %s\n' "$MEM" "$VERDICT"
  done
  echo
done
