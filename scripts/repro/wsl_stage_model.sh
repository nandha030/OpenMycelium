#!/usr/bin/env bash
# Copy the checkpoint onto WSL's native filesystem and verify it byte-for-byte.
#
# Reading 11.4 GiB per stage through /mnt/c costs ~90 s per load. The Windows
# copy stays as the source of truth; this is the working copy.
set -uo pipefail
SRC=/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407
DST=${DST:-/opt/models/Mistral-Nemo-Instruct-2407}

echo "=== free space ==="
df -h "$(dirname "$DST")" 2>/dev/null | tail -1

mkdir -p "$DST"
echo "=== copying $(du -sh "$SRC" | cut -f1) ==="
time cp -a "$SRC/." "$DST/"

echo
echo "=== size comparison ==="
printf '  source      %s\n' "$(du -sb "$SRC" | cut -f1)"
printf '  destination %s\n' "$(du -sb "$DST" | cut -f1)"

echo
echo "=== verifying every shard by SHA-256 (source vs copy) ==="
FAIL=0
for f in "$SRC"/*.safetensors "$SRC"/*.json; do
  name=$(basename "$f")
  # An unreadable source is not a mismatch. Hashing while other work hammers
  # /mnt/c can fail the read outright, and reporting that as "files differ"
  # sends you hunting a corruption that never happened.
  a=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
  b=$(sha256sum "$DST/$name" 2>/dev/null | cut -d' ' -f1)
  if [ -z "$a" ] || [ -z "$b" ]; then
    printf '  ????  %-42s unreadable (src:%s dst:%s) -- retry with the source idle\n' \
      "$name" "$([ -n "$a" ] && echo ok || echo fail)" \
      "$([ -n "$b" ] && echo ok || echo fail)"
    FAIL=1
  elif [ "$a" = "$b" ]; then
    printf '  OK    %-42s %s\n' "$name" "${a:0:16}"
  else
    printf '  DIFF  %-42s %s != %s\n' "$name" "${a:0:16}" "${b:0:16}"
    FAIL=1
  fi
done
echo
[ $FAIL -eq 0 ] && echo "COPY VERIFIED: every file matches the source" \
                || echo "COPY FAILED: at least one file differs"
exit $FAIL
