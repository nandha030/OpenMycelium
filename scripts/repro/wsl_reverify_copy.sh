#!/usr/bin/env bash
# Re-hash only the two shards whose source read failed, with no other I/O load.
SRC=/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407
DST=/opt/models/Mistral-Nemo-Instruct-2407
for n in model-00002-of-00005.safetensors model-00003-of-00005.safetensors; do
  echo "=== $n ==="
  printf '  src size %s   dst size %s\n' "$(stat -c%s "$SRC/$n")" "$(stat -c%s "$DST/$n")"
  a=$(sha256sum "$SRC/$n" 2>&1 | cut -d' ' -f1)
  b=$(sha256sum "$DST/$n" 2>&1 | cut -d' ' -f1)
  printf '  src %s\n  dst %s\n' "${a:-<read failed>}" "${b:-<read failed>}"
  [ -n "$a" ] && [ "$a" = "$b" ] && echo "  MATCH" || echo "  MISMATCH OR UNREADABLE"
done
