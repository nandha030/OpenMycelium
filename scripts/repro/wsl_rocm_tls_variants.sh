#!/usr/bin/env bash
UCX=/opt/ucx-both; ROOT=/opt/cudaroot; ROCM=/opt/rocm-7.2.0
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
export UCX_MEMTYPE_CACHE=n
export UCX_LOG_LEVEL=error
BIN=/opt/bin/ucx_conformance
P=19800

try() {
  local tls=$1 send=$2 recv=$3 size=$4
  P=$((P+1))
  UCX_TLS="$tls" $BIN --role server --mem "$recv" --size "$size" --port $P >/opt/r.log 2>&1 &
  local s=$!; sleep 0.4
  UCX_TLS="$tls" timeout 60 $BIN --role client --mem "$send" --size "$size" --port $P --peer 127.0.0.1 >/opt/c.log 2>&1
  wait $s 2>/dev/null
  local v=$(grep -h RESULT /opt/r.log | head -1)
  case "$v" in *PASS*) v="PASS";; "") v="CRASH/none";; *) v="FAIL";; esac
  printf '  TLS=%-28s %-5s -> %-5s %8s : %s\n' "$tls" "$send" "$recv" "$size" "$v"
}

echo "### baseline (includes rocm_copy) ###"
try "tcp,self,sm,cuda_copy,rocm_copy" host rocm 1048576

echo "### rocm_copy EXCLUDED -- forces host staging ###"
try "tcp,self,sm,cuda_copy" host rocm 1048576
try "tcp"                   host rocm 1048576
try "tcp"                   cuda rocm 1048576
try "tcp"                   rocm cuda 1048576
try "tcp"                   rocm rocm 1048576

echo "### and with memtype cache ON, tcp only ###"
UCX_MEMTYPE_CACHE=y try "tcp" cuda rocm 1048576
