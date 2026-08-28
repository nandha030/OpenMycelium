#!/usr/bin/env bash
for f in /opt/t_cuda.json /opt/t_rocm.json; do
  echo "=== $f ($(stat -c%s "$f" 2>/dev/null || echo missing) bytes) ==="
  [ -s "$f" ] && tail -1 "$f" | cut -c1-400
  echo
done
echo "still running: $(ps -eo args | grep -c '[f]orward_pass.py')"
