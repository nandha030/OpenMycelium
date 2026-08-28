#!/usr/bin/env bash
ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
BIN=/opt/bin/bridge
P=21100
BYTES=$((256*1024*1024))

sweep() {
  local sv=$1 rv=$2 chunk=$3 slots=$4 win=$5 extra=${6:-}
  P=$((P+1))
  $BIN --role recv --vendor "$rv" --bytes $BYTES --chunk "$chunk" --slots "$slots" --port $P $extra >/opt/recv.json 2>/dev/null &
  local r=$!
  sleep 0.6
  timeout 240 $BIN --role send --vendor "$sv" --bytes $BYTES --chunk "$chunk" --slots "$slots" --window "$win" --port $P --peer 127.0.0.1 $extra >/opt/send.json 2>/dev/null
  wait $r 2>/dev/null
  local mb=$(grep -oE '"MBps":[0-9.]+' /opt/send.json | cut -d: -f2)
  local ok=$(grep -oE '"verified":(true|false)' /opt/recv.json | cut -d: -f2)
  printf '  chunk=%-7s slots=%d win=%d  %-9s verified=%s\n' \
    "$(numfmt --to=iec-i $chunk)B" "$slots" "$win" "${mb:-?} MB/s" "${ok:-?}"
}

echo "### cuda -> rocm, 256 MiB, chunk size sweep (slots=4, window=4) ###"
for C in 1 2 4 8 16; do sweep cuda rocm $((C*1024*1024)) 4 4; done
echo
echo "### depth sweep at 4 MiB chunks ###"
for S in 2 4 8; do sweep cuda rocm $((4*1024*1024)) $S $S; done
echo
echo "### checksum cost (qualification on vs production off), 4 MiB ###"
sweep cuda rocm $((4*1024*1024)) 4 4
sweep cuda rocm $((4*1024*1024)) 4 4 --no-checksum
