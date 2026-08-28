#!/usr/bin/env bash
# Copy the post-provision harness into the distribution and run the named stage.
# Copying first keeps the running copy immutable, the way the bootstrap does.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
DST=/root/repro
mkdir -p "$DST"
cp "$SRC"/post_provision.sh "$SRC"/reboot_gate.sh "$SRC"/serve_check.sh \
   "$SRC"/a7_gate7.sh "$SRC"/gpu_identity.py "$SRC"/fingerprint.py \
   "$SRC"/report_gpu.py "$SRC"/compare_fingerprints.py \
   "$SRC"/check_placement.py "$SRC"/api_check.py "$DST"/
sed -i 's/\r$//' "$DST"/*.sh
chmod +x "$DST"/*.sh
export OM_HARNESS="$DST"
exec bash "$DST/${1:-post_provision.sh}" "${2:-all}"
