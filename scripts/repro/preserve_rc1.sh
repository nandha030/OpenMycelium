#!/usr/bin/env bash
# Preserve the RC1 gate evidence beside the release it qualified.
set -uo pipefail
D=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0rc1/gate
mkdir -p "$D"
cp -r /var/log/om-rc1/. "$D"/ 2>/dev/null
rm -f "$D"/*token* 2>/dev/null
echo "  preserved to $D"
ls -1 "$D" | sed 's/^/    /'
