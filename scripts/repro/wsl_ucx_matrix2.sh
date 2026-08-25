#!/usr/bin/env bash
export LD_LIBRARY_PATH=/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
PT=/opt/ucx-cuda/bin/ucx_perftest
PORT=14400

run_case() {
  local test=$1 mem=$2 label=$3
  PORT=$((PORT+1))
  $PT -c 0 -p $PORT -t "$test" -m "$mem" >/tmp/srv.log 2>&1 &
  local srv=$!
  sleep 1
  timeout 30 $PT 127.0.0.1 -p $PORT -t "$test" -m "$mem" -n 500 -s 65536 >/tmp/cli.log 2>&1
  local rc=$?
  kill $srv 2>/dev/null; wait $srv 2>/dev/null
  if [ $rc -eq 0 ] && grep -qE '[0-9]+\.[0-9]+' /tmp/cli.log; then
    local bw=$(tail -2 /tmp/cli.log | grep -oE '[0-9]+\.[0-9]+' | tail -3 | head -1)
    printf '  %-20s %-12s PASS   %s MB/s\n' "$label" "$test" "${bw:-?}"
  else
    local err=$(grep -ihoE '(error|fatal).{0,60}' /tmp/cli.log /tmp/srv.log | head -1)
    printf '  %-20s %-12s FAIL   %s\n' "$label" "$test" "${err:-exit $rc}"
  fi
}

echo "###  A. memory-type matrix (tag API, 64KB x500)"
run_case tag_bw "host"      "host -> host"
run_case tag_bw "cuda"      "cuda -> cuda"
run_case tag_bw "host,cuda" "host -> cuda"
run_case tag_bw "cuda,host" "cuda -> host"
echo
echo "###  B. API paths carrying CUDA memory"
run_case tag_bw     "cuda" "tag"
run_case stream_bw  "cuda" "stream"
run_case ucp_am_bw  "cuda" "active-message"
run_case ucp_put_bw "cuda" "RMA put"
run_case ucp_get    "cuda" "RMA get"
echo
echo "###  C. cross-vendor rows (no ROCm on this host)"
run_case tag_bw "cuda,rocm" "cuda -> rocm"
run_case tag_bw "rocm,cuda" "rocm -> cuda"
run_case tag_bw "rocm"      "rocm -> rocm"
