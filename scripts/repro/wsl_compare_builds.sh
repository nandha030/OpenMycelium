#!/usr/bin/env bash
# Same harness source, same rows, two UCX builds. No timeout on the server so
# the kill race that produced an earlier false negative cannot recur.
ROOT=/opt/cudaroot; ROCM=/opt/rocm-7.2.0
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
export UCX_MEMTYPE_CACHE=n UCX_LOG_LEVEL=error
P=20100

test_build() {
  local ucx=$1 tls=$2 label=$3
  export LD_LIBRARY_PATH=$ucx/lib:$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
  local bin=/opt/bin/conf_$(basename $ucx)
  gcc "$S/ucx_conformance.c" -o $bin -I$ucx/include -L$ucx/lib -lucp -lucs -ldl 2>/dev/null
  [ -x $bin ] || { echo "  [$label] COMPILE FAILED"; return; }
  echo "  --- $label (TLS=$tls) ---"
  for pair in "host host" "host cuda" "cuda host" "cuda cuda"; do
    set -- $pair
    P=$((P+1))
    UCX_TLS="$tls" $bin --role server --mem "$2" --size 4096 --port $P >/opt/r.log 2>&1 &
    local s=$!; sleep 0.4
    UCX_TLS="$tls" timeout 25 $bin --role client --mem "$1" --size 4096 --port $P --peer 127.0.0.1 >/opt/c.log 2>&1
    wait $s 2>/dev/null
    local v=$(grep -h RESULT /opt/r.log | head -1)
    case "$v" in *PASS*) v=PASS;; "") v="CRASH/none";; *) v=FAIL;; esac
    printf '    %-5s -> %-5s : %s\n' "$1" "$2" "$v"
  done
}

test_build /opt/ucx-cuda "tcp,self,sm,cuda_copy"            "CUDA-only build"
test_build /opt/ucx-both "tcp,self,sm,cuda_copy,rocm_copy"  "CUDA+ROCm build"
