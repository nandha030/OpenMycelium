#!/usr/bin/env bash
# Copy the harness into the distribution, then run it from there.
#
# The previous attempt executed the bootstrap directly off /mnt/c and died at
# "unexpected EOF" three quarters of the way through, because bash reads a
# script incrementally by byte offset and the file was edited while running.
# Copying first makes the running copy immutable for the duration.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
DST=/root/repro
rm -rf "$DST"
mkdir -p "$DST"
cp "$SRC"/clean_bootstrap.sh "$SRC"/serve_check.sh \
   "$SRC"/check_provenance.py "$SRC"/check_placement.py "$SRC"/api_check.py "$DST"/
sed -i 's/\r$//' "$DST"/*.sh
chmod +x "$DST"/*.sh
export OM_HARNESS="$DST"
exec bash "$DST/clean_bootstrap.sh"
