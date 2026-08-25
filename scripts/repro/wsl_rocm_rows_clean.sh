#!/usr/bin/env bash
UCX=/opt/ucx-both; ROOT=/opt/cudaroot; ROCM=/opt/rocm-7.2.0
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
export UCX_TLS=tcp,self,sm,cuda_copy,rocm_copy
export UCX_MEMTYPE_CACHE=n UCX_LOG_LEVEL=error
BIN=/opt/bin/conf_ucx-both
P=20200
row() {
  P=$((P+1))
  $BIN --role server --mem "$2" --size 4096 --port $P >/opt/r.log 2>&1 &
  local s=$!; sleep 0.4
  timeout 25 $BIN --role client --mem "$1" --size 4096 --port $P --peer 127.0.0.1 >/opt/c.log 2>&1
  wait $s 2>/dev/null; local src=$?
  local v=$(grep -h RESULT /opt/r.log | head -1)
  local reason=""
  case "$v" in
    *PASS*) v=PASS;;
    "")     v="CRASH"; reason=$(grep -oE 'uct_rocm[a-z_]*|uct_cuda[a-z_]*' /opt/r.log | head -1);;
    *)      v=FAIL;;
  esac
  printf '  %-5s -> %-5s : %-6s %s\n' "$1" "$2" "$v" "$reason"
}
echo "ROCm rows, dual-vendor UCX, no server timeout (kill race excluded):"
row host rocm; row rocm host; row rocm rocm; row cuda rocm; row rocm cuda
