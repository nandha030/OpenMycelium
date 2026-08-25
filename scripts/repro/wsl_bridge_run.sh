#!/usr/bin/env bash
# NVIDIA VRAM -> pinned host -> TCP -> pinned host -> AMD VRAM
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
mkdir -p /opt/bin
BIN=/opt/bin/bridge

echo "=== build (no vendor headers needed; runtimes are dlopen'd) ==="
gcc "$S/runtime/bridge/cross_vendor_bridge.c" -o $BIN -O2 -ldl 2>&1 | head -15
[ -x $BIN ] || { echo "COMPILE FAILED"; exit 1; }
echo built
echo

run() {
  local send_v=$1 recv_v=$2 bytes=$3 chunk=$4 slots=$5 win=$6 port=$7 extra=${8:-}
  $BIN --role recv --vendor "$recv_v" --bytes "$bytes" --chunk "$chunk" \
       --slots "$slots" --port "$port" $extra >/opt/recv.json 2>/opt/recv.err &
  local r=$!
  sleep 0.6
  timeout 180 $BIN --role send --vendor "$send_v" --bytes "$bytes" --chunk "$chunk" \
       --slots "$slots" --window "$win" --port "$port" --peer 127.0.0.1 $extra >/opt/send.json 2>/opt/send.err
  wait $r 2>/dev/null; local rrc=$?
  echo "  $send_v -> $recv_v  ($(numfmt --to=iec-i $bytes)B, chunk $(numfmt --to=iec-i $chunk)B, slots=$slots win=$win)"
  echo "    send: $(cat /opt/send.json 2>/dev/null | head -1)"
  echo "    recv: $(cat /opt/recv.json 2>/dev/null | head -1)"
  [ -s /opt/recv.err ] && echo "    recv stderr: $(head -2 /opt/recv.err)"
  echo "    receiver exit: $rrc"
}

echo "############ THE CROSS-VENDOR BRIDGE: NVIDIA -> AMD ############"
run cuda rocm $((64*1024*1024)) $((4*1024*1024)) 4 4 21001
echo
echo "############ REVERSE: AMD -> NVIDIA ############"
run rocm cuda $((64*1024*1024)) $((4*1024*1024)) 4 4 21002
