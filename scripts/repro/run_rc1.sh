#!/usr/bin/env bash
# Stage the RC1 gate harness into the distribution and run it.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
DST=/root/repro
mkdir -p "$DST"
cp "$SRC"/rc1_gate.sh "$SRC"/replay_audit.py "$SRC"/check_placement.py \
   "$SRC"/boundary_exact_clean.sh "$SRC"/api_check.py "$SRC"/serve_check.sh "$DST"/
sed -i 's/\r$//' "$DST"/*.sh
chmod +x "$DST"/*.sh
export OM_HARNESS="$DST"
exec bash "$DST/${1:-rc1_gate.sh}"
