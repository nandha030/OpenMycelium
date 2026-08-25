#!/usr/bin/env bash
export LD_LIBRARY_PATH=/opt/cudaroot/lib64:/usr/lib/wsl/lib
PT=/opt/ucx-cuda/bin/ucx_perftest
P=16000
for T in ucp_add ucp_fadd ucp_swap ucp_cswap; do
  for MEM in host cuda; do
    P=$((P+1))
    $PT -c 0 -p $P -t $T -m $MEM >/tmp/s.log 2>&1 &
    S=$!; sleep 1
    timeout 20 $PT 127.0.0.1 -p $P -t $T -m $MEM -n 200 -s 8 >/tmp/c.log 2>&1
    RC=$?
    kill $S 2>/dev/null; wait $S 2>/dev/null
    if [ $RC -eq 0 ] && grep -q 'Final:' /tmp/c.log; then
      printf '  %-10s %-6s PASS\n' "$T" "$MEM"
    else
      printf '  %-10s %-6s FAIL  %s\n' "$T" "$MEM" "$(grep -ihoE '(error|fatal).{0,55}' /tmp/c.log /tmp/s.log | head -1)"
    fi
  done
done
