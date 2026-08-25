#!/usr/bin/env bash
UCX=/opt/ucx-cuda; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:/opt/rocm/lib:/usr/lib/wsl/lib
export UCX_TLS=tcp,self,sm,cuda_copy
export UCX_MEMTYPE_CACHE=n
export UCX_LOG_LEVEL=error
BIN=/tmp/ucx_conformance
PORT=19200

row() {
  local send=$1 recv=$2 size=$3
  PORT=$((PORT+1))
  $BIN --role server --mem "$recv" --size "$size" --port $PORT >/tmp/r.log 2>&1 &
  local s=$!
  sleep 0.4
  timeout 240 $BIN --role client --mem "$send" --size "$size" --port $PORT --peer 127.0.0.1 >/tmp/c.log 2>&1
  wait $s 2>/dev/null
  local v=$(grep -h 'RESULT' /tmp/r.log | head -1)
  local tag=FAIL
  case "$v" in *PASS*) tag=PASS;; *SKIP*) tag=SKIP;; esac
  printf '  %-5s -> %-5s  %8s   %s\n' "$send" "$recv" "$(numfmt --to=iec-i $size)B" "$tag"
}

echo "UCX_TLS=$UCX_TLS  MEMTYPE_CACHE=$UCX_MEMTYPE_CACHE  (byte-for-byte verified)"
for SIZE in 1 4096 1048576 268435456; do
  echo "--- $(numfmt --to=iec-i $SIZE)B ---"
  row host host $SIZE
  row host cuda $SIZE
  row cuda host $SIZE
  row cuda cuda $SIZE
done
