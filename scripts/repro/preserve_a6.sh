#!/usr/bin/env bash
# Preserve everything the 0.1.0a6 clean bootstrap produced.
set -uo pipefail
D=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a6/clean-bootstrap
mkdir -p "$D"
cp -r /var/log/om-bootstrap/. "$D"/ 2>/dev/null
rm -f "$D/serve-token.txt"
echo "  preserved to $D"
ls -1 "$D" | sed 's/^/    /'
